"""The Phase 2 gate for schema drift (P3) and the trust score (P4), end to end.

docs/plan/02-implementation-plan.md sections 5.2 and 5.3 state it. After the model
is trained, its input table's schema drifts from the snapshot captured on the
training run. ``modelguard scan --model credit_risk_v3`` must detect the drift,
raise a DATA_SCHEMA incident on the drifted input dataset, and roll every risk it
found about the model into a trust score written as a structured property.

This module is that criterion, executable. It proves both directions: the planted
drift is caught, and reverting the schema to the training-time one makes the drift
detector silent again.

Unlike leakage, drift detection reads only versioned aspects (schema, training
run), which DataHub serves synchronously, so detection needs no polling. The
incident write still indexes the ``IncidentOn`` relationship through Kafka, so the
incident assertions poll.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TypeVar

import pytest
from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import (
    IncidentInfoClass,
    IncidentStateClass,
)

from modelguard.agent.pipeline import run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.schema_drift import schema_drift_findings
from modelguard.models import FindingType, SchemaDriftFinding, Severity, TrustBand
from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import plant_schema_drift, revert_schema_drift
from modelguard.seed.seed_ml_graph import SeedResult, seed_ml_graph
from modelguard.writeback.incidents import INCIDENT_ON_RELATIONSHIP, resolve_incident
from modelguard.writeback.labels import read_tags
from modelguard.writeback.properties import (
    RISK_FLAGS,
    TRUST_BAND,
    TRUST_SCORE,
    read_properties,
)

pytestmark = pytest.mark.integration

T = TypeVar("T")

_INDEX_TIMEOUT_SECONDS = 90.0


def _eventually(probe: Callable[[], T], what: str, timeout: float = _INDEX_TIMEOUT_SECONDS) -> T:
    """Poll until the probe returns something truthy, or fail with what was awaited."""
    deadline = time.monotonic() + timeout
    last: T = probe()
    while not last and time.monotonic() < deadline:
        time.sleep(2.0)
        last = probe()
    if not last:
        raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")
    return last


@pytest.fixture(scope="module")
def conn() -> DataHubConnection:
    """Connect to the local DataHub, or skip the whole module."""
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    return ScanConfig()


@pytest.fixture(scope="module")
def drifted(conn: DataHubConnection, config: ScanConfig) -> SeedResult:
    """Seed the graph, then drift the feature table's live schema from training.

    Detection reads versioned aspects synchronously, so once the plant returns the
    drift is immediately visible; the poll is a small guard against a slow write.
    """
    result = seed_ml_graph(conn)
    plant_schema_drift(conn)
    _eventually(
        lambda: bool(schema_drift_findings(conn, result.model, config)),
        "the planted schema drift to be detectable",
    )
    return result


@pytest.fixture(scope="module")
def run_id() -> str:
    return f"gate3-{uuid.uuid4().hex[:8]}"


def _drift_finding(conn: DataHubConnection, model: str, config: ScanConfig) -> SchemaDriftFinding:
    findings = schema_drift_findings(conn, model, config)
    assert findings, "the drift must be detectable before the gate runs"
    return findings[0]


def _incidents_on(conn: DataHubConnection, resource_urn: str) -> list[str]:
    return [
        entity.urn
        for entity in conn.graph.get_related_entities(
            entity_urn=resource_urn,
            relationship_types=[INCIDENT_ON_RELATIONSHIP],
            direction=RelationshipDirection.INCOMING,
        )
    ]


def _active_incidents(conn: DataHubConnection, resource_urn: str, title: str) -> list[str]:
    matching = []
    for urn in _incidents_on(conn, resource_urn):
        info = conn.graph.get_aspect(urn, IncidentInfoClass)
        if (
            info is not None
            and info.title == title
            and info.status.state == IncidentStateClass.ACTIVE
        ):
            matching.append(urn)
    return matching


@pytest.fixture(scope="module")
def clean_slate(conn: DataHubConnection, drifted: SeedResult, config: ScanConfig) -> str:
    """Resolve any drift incident an earlier run left open, so we test the create path.

    Returns the drifted dataset URN, which is where the incident lands.
    """
    finding = _drift_finding(conn, drifted.model, config)
    dataset_urn = finding.resource_urn
    for urn in _active_incidents(conn, dataset_urn, finding.title):
        resolve_incident(conn, urn, message="cleared by the phase 2 drift gate setup")
    return dataset_urn


# ---------------------------------------------------------------------------
# The detector reads the live graph correctly
# ---------------------------------------------------------------------------


def test_the_drift_is_detected_on_the_live_model(
    conn: DataHubConnection, drifted: SeedResult, config: ScanConfig
) -> None:
    finding = _drift_finding(conn, drifted.model, config)

    assert finding.finding_type is FindingType.INPUT_SCHEMA_DRIFT
    assert finding.severity is Severity.HIGH, "the seeded model is live"
    assert finding.dataset_urn == str(spec.feature_table_dataset_urn())

    # The planted drift: applicant_income retyped, updated_at removed, a column added.
    changed = {change.field_path for change in finding.changes}
    assert "applicant_income" in changed
    assert "updated_at" in changed
    # And the columns the leakage traversal depends on were left alone.
    assert "prior_default_flag" not in changed


# ---------------------------------------------------------------------------
# The full write-back loop
# ---------------------------------------------------------------------------


def test_scan_raises_the_drift_incident_and_writes_a_trust_score(
    conn: DataHubConnection,
    drifted: SeedResult,
    config: ScanConfig,
    clean_slate: str,
    run_id: str,
) -> None:
    dataset_urn = clean_slate

    report = run_scan(conn, config, model_urn=drifted.model, run_id=run_id, llm=None)

    drift_writes = [
        write
        for write in report.writes
        if write.finding.finding_type is FindingType.INPUT_SCHEMA_DRIFT
    ]
    assert len(drift_writes) == 1
    write = drift_writes[0]

    # The incident lands on the drifted dataset, typed DATA_SCHEMA.
    assert write.incident is not None
    title = write.finding.title
    _eventually(
        lambda: _active_incidents(conn, dataset_urn, title),
        "the drift incident to be indexed on the dataset",
    )

    # The model is tagged at risk.
    _eventually(
        lambda: f"urn:li:tag:{config.model_at_risk_tag}" in read_tags(conn, drifted.model),
        "the model-at-risk tag to land on the model",
    )

    # P4: a trust score and band are written, and drift is among the risk flags.
    properties = read_properties(conn, drifted.model)
    flags = properties.get(RISK_FLAGS) or []
    assert str(FindingType.INPUT_SCHEMA_DRIFT) in flags

    score = properties.get(TRUST_SCORE)
    assert score, "the trust score must be written"
    # Drift alone costs 15 points; the seeded model also leaks and is unowned, so
    # the score is well below a healthy 100. The exact value is asserted in units.
    assert float(score[0]) <= 85
    assert properties.get(TRUST_BAND) and properties[TRUST_BAND][0] in {
        str(TrustBand.WATCH),
        str(TrustBand.AT_RISK),
    }


def test_a_second_scan_raises_no_duplicate_drift_incident(
    conn: DataHubConnection,
    drifted: SeedResult,
    config: ScanConfig,
    clean_slate: str,
    run_id: str,
) -> None:
    """Idempotency: the dedup key is (dataset, DATA_SCHEMA, title), and a rerun converges."""
    dataset_urn = clean_slate

    first = run_scan(conn, config, model_urn=drifted.model, run_id=f"{run_id}-a", llm=None)
    title = next(
        write.finding.title
        for write in first.writes
        if write.finding.finding_type is FindingType.INPUT_SCHEMA_DRIFT
    )
    _eventually(
        lambda: _active_incidents(conn, dataset_urn, title),
        "the first drift incident to index",
    )

    run_scan(conn, config, model_urn=drifted.model, run_id=f"{run_id}-b", llm=None)

    time.sleep(5.0)
    assert len(_active_incidents(conn, dataset_urn, title)) == 1


# ---------------------------------------------------------------------------
# The other direction: a matching schema means no drift finding
# ---------------------------------------------------------------------------


def test_reverting_the_schema_silences_the_drift_detector(
    conn: DataHubConnection, drifted: SeedResult, config: ScanConfig
) -> None:
    """Restore the training-time schema, and the drift finding must disappear.

    Leakage still fires on the seeded model, so this asserts on the drift detector
    specifically, not on a fully clean scan.
    """
    revert_schema_drift(conn)
    try:
        _eventually(
            lambda: not schema_drift_findings(conn, drifted.model, config),
            "the drift to clear after the schema is restored",
        )
        assert schema_drift_findings(conn, drifted.model, config) == ()
    finally:
        # Leave the graph drifted for any later module reuse and for the demo.
        plant_schema_drift(conn)
