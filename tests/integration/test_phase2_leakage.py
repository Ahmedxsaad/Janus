"""The Phase 2 gate: the leakage loop, end to end, against a live DataHub.

docs/plan/02-implementation-plan.md section 5.1 states it: ``janus scan
--model credit_risk_v3`` traces the model's features back through column-level
lineage, finds the one derived from the label, and writes back a FIELD incident
on the leaking column, a leakage-risk term on the feature, and a tag plus risk
flag on the model.

This module is that criterion, executable. It proves both directions a detector
must have: the seeded leak is caught, and a model whose label declaration has been
removed produces a clean scan that writes nothing.

Reads are asynchronous. DataHub writes aspects synchronously but indexes lineage
and relationships through Kafka and Elasticsearch, so the assertions poll rather
than assume read-after-write consistency.
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

from janus.agent.pipeline import run_scan
from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.detect.leakage import leakage_findings
from janus.models import FindingType, Severity
from janus.seed import graph_spec as spec
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.incidents import INCIDENT_ON_RELATIONSHIP, resolve_incident
from janus.writeback.labels import read_tags
from janus.writeback.properties import RISK_FLAGS, read_properties
from janus.writeback.terms import read_terms

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
def seeded(conn: DataHubConnection, config: ScanConfig) -> SeedResult:
    """Seed the ML graph, then wait until the leak is actually visible.

    The label term and the column lineage are indexed asynchronously. Without the
    wait, the first scan races the index and finds a clean model.
    """
    result = seed_ml_graph(conn)
    _eventually(
        lambda: bool(leakage_findings(conn, result.model, config)),
        "the seeded leak to be indexed and detectable",
    )
    return result


@pytest.fixture(scope="module")
def run_id() -> str:
    return f"gate2-{uuid.uuid4().hex[:8]}"


def _incidents_on(conn: DataHubConnection, resource_urn: str) -> list[str]:
    return [
        entity.urn
        for entity in conn.graph.get_related_entities(
            entity_urn=resource_urn,
            relationship_types=[INCIDENT_ON_RELATIONSHIP],
            direction=RelationshipDirection.INCOMING,
        )
    ]


def _active_leakage_incidents(conn: DataHubConnection, column_urn: str, title: str) -> list[str]:
    matching = []
    for urn in _incidents_on(conn, column_urn):
        info = conn.graph.get_aspect(urn, IncidentInfoClass)
        if (
            info is not None
            and info.title == title
            and info.status.state == IncidentStateClass.ACTIVE
        ):
            matching.append(urn)
    return matching


@pytest.fixture(scope="module")
def clean_slate(conn: DataHubConnection, seeded: SeedResult, config: ScanConfig) -> str:
    """Resolve any leakage incident an earlier run left open, so we test the create path.

    Returns the leaking column URN, which is where the incident lands.
    """
    findings = leakage_findings(conn, seeded.model, config)
    assert findings, "the leak must be detectable before the gate runs"
    column_urn = findings[0].resource_urn

    title = findings[0].title
    for urn in _active_leakage_incidents(conn, column_urn, title):
        resolve_incident(conn, urn, message="cleared by the phase 2 gate setup")
    return column_urn


# ---------------------------------------------------------------------------
# The detector reads the live graph correctly
# ---------------------------------------------------------------------------


def test_the_seeded_leak_is_detected_on_the_live_model(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    findings = leakage_findings(conn, seeded.model, config)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type is FindingType.TARGET_LEAKAGE
    assert finding.severity is Severity.CRITICAL, "the seeded model is live"
    assert finding.leak.label_column_name == spec.LABEL_SOURCE_COLUMN
    assert finding.leak.source_column_name == spec.LEAKAGE_FEATURE
    # The path is the proof: it must actually connect the two columns.
    assert finding.leak.column_path[-1] == spec.LABEL_SOURCE_COLUMN


def test_the_clean_feature_is_not_flagged(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """applicant_income does not derive from the label, so it must not be reported."""
    findings = leakage_findings(conn, seeded.model, config)
    flagged_columns = {finding.leak.source_column_name for finding in findings}

    assert "applicant_income" not in flagged_columns


# ---------------------------------------------------------------------------
# The full write-back loop
# ---------------------------------------------------------------------------


def test_scan_writes_the_incident_term_tag_and_flag(
    conn: DataHubConnection,
    seeded: SeedResult,
    config: ScanConfig,
    clean_slate: str,
    run_id: str,
) -> None:
    column_urn = clean_slate

    report = run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)

    assert len(report.writes) == 1
    write = report.writes[0]

    # The incident lands on the leaking column, not on the model.
    assert write.incident is not None
    assert write.incident.created is True
    title = write.finding.title
    _eventually(
        lambda: _active_leakage_incidents(conn, column_urn, title),
        "the leakage incident to be indexed on the column",
    )

    # The feature carries the leakage-risk term.
    feature_urn = str(spec.feature_urn(spec.LEAKAGE_FEATURE))
    _eventually(
        lambda: config.leakage_risk_term_urn in read_terms(conn, feature_urn),
        "the leakage-risk term to land on the feature",
    )

    # The model is tagged and flagged.
    _eventually(
        lambda: f"urn:li:tag:{config.model_at_risk_tag}" in read_tags(conn, seeded.model),
        "the model-at-risk tag to land on the model",
    )
    flags = read_properties(conn, seeded.model).get(RISK_FLAGS, [])
    assert str(FindingType.TARGET_LEAKAGE) in flags


def test_a_second_scan_raises_no_duplicate_incident(
    conn: DataHubConnection,
    seeded: SeedResult,
    config: ScanConfig,
    clean_slate: str,
    run_id: str,
) -> None:
    """Idempotency: the dedup key is (column, FIELD, title), and a rerun converges."""
    column_urn = clean_slate

    first = run_scan(conn, config, model_urn=seeded.model, run_id=f"{run_id}-a", llm=None)
    title = first.writes[0].finding.title
    _eventually(
        lambda: _active_leakage_incidents(conn, column_urn, title),
        "the first leakage incident to index",
    )

    run_scan(conn, config, model_urn=seeded.model, run_id=f"{run_id}-b", llm=None)

    # Give any duplicate a chance to show up before asserting there is none.
    time.sleep(5.0)
    assert len(_active_leakage_incidents(conn, column_urn, title)) == 1


# ---------------------------------------------------------------------------
# The other direction: no label declared means no finding
# ---------------------------------------------------------------------------


def test_a_model_with_no_declared_label_scans_clean(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Point the detector at a config whose label term nothing carries.

    The leak is a fact of the lineage, but leakage is only *defined* relative to a
    declared label. With no column declared, there is no target to leak, so the
    scan must be silent. This is the false-positive control on the real graph.
    """
    no_label = ScanConfig(label_term_urn="urn:li:glossaryTerm:janus.nonexistent-label")

    report = run_scan(conn, no_label, model_urn=seeded.model, llm=None, dry_run=True)

    assert report.clean is True
    assert leakage_findings(conn, seeded.model, no_label) == ()
