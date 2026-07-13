"""The Phase 1 gate: the core loop, end to end, against a live DataHub.

docs/plan/02-implementation-plan.md section 4.3 states it in prose: ``modelguard
scan --table loans_raw`` produces an incident, a tag, a guarding assertion, and a
report, all visible in the UI, with the same result on every run.

This module is that criterion, executable. It also proves the two directions a
detector must have: a planted failure is caught, and a reverted one is not.

Run it against a live Quickstart::

    datahub docker quickstart
    pytest -m integration -v

Reads are asynchronous. DataHub writes aspects synchronously but indexes lineage,
relationships, and timeseries aspects through Kafka and Elasticsearch, so the
assertions poll rather than assume read-after-write consistency.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TypeVar

import pytest
from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import (
    AssertionInfoClass,
    AssertionResultTypeClass,
    AssertionRunEventClass,
    AssertionSourceTypeClass,
    DocumentInfoClass,
    IncidentInfoClass,
    IncidentStateClass,
)

from modelguard.agent.narrate import NarrativeSource
from modelguard.agent.pipeline import FindingWrites, ScanReport, run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import blast_radius
from modelguard.models import Severity
from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import plant_stale_source, revert_stale_source
from modelguard.seed.seed_ml_graph import SeedResult, seed_ml_graph
from modelguard.writeback.incidents import INCIDENT_ON_RELATIONSHIP, resolve_incident
from modelguard.writeback.labels import read_tags
from modelguard.writeback.properties import RISK_FLAGS, RUN_ID, read_properties

pytestmark = pytest.mark.integration

T = TypeVar("T")

#: Generous: a cold Elasticsearch index can take a while to catch up.
_INDEX_TIMEOUT_SECONDS = 90.0

#: Comfortably past the 6 hour default SLA.
_LAG_HOURS = 30.0


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
def seeded(conn: DataHubConnection) -> SeedResult:
    """Seed the ML graph once for the module."""
    return seed_ml_graph(conn)


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    return ScanConfig()


@pytest.fixture(scope="module")
def stale(conn: DataHubConnection, seeded: SeedResult, config: ScanConfig) -> None:
    """Plant the failure, and wait until the detector can actually see it.

    The operation aspect is a timeseries aspect, indexed asynchronously. Without
    the wait, the first scan races the index and finds a healthy table.
    """
    plant_stale_source(conn, lag_hours=_LAG_HOURS)
    _eventually(
        lambda: blast_radius(conn, seeded.source_table, config) is not None,
        "the planted operation aspect to be indexed",
    )


@pytest.fixture(scope="module")
def run_id() -> str:
    return f"gate1-{uuid.uuid4().hex[:8]}"


def _incidents_on(conn: DataHubConnection, resource_urn: str) -> list[str]:
    return [
        entity.urn
        for entity in conn.graph.get_related_entities(
            entity_urn=resource_urn,
            relationship_types=[INCIDENT_ON_RELATIONSHIP],
            direction=RelationshipDirection.INCOMING,
        )
    ]


def _active_freshness_incidents(
    conn: DataHubConnection, resource_urn: str, title: str
) -> list[str]:
    matching = []
    for urn in _incidents_on(conn, resource_urn):
        info = conn.graph.get_aspect(urn, IncidentInfoClass)
        if info is None:
            continue
        if (
            info.status.state == IncidentStateClass.ACTIVE
            and info.type == "FRESHNESS"
            and info.title == title
        ):
            matching.append(urn)
    return matching


# --------------------------------------------------------------------------
# Detection: both directions
# --------------------------------------------------------------------------


def test_a_fresh_table_produces_no_finding(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
):
    """False-positive control, run before anything is planted."""
    revert_stale_source(conn)
    _eventually(
        lambda: blast_radius(conn, seeded.source_table, config) is None,
        "the refreshed operation aspect to be indexed",
    )
    assert blast_radius(conn, seeded.source_table, config) is None


def test_the_planted_failure_reaches_the_live_model_across_the_ml_boundary(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, stale: None
):
    """One downstream lineage call crosses dataset -> mlFeature -> mlModel."""
    radius = blast_radius(conn, seeded.source_table, config)
    assert radius is not None

    assert radius.signal.lag_hours > config.freshness_sla_hours
    assert seeded.feature_table_dataset in radius.downstream_datasets
    assert set(seeded.features) <= set(radius.downstream_features)

    assert [model.urn for model in radius.models] == [seeded.model]
    model = radius.models[0]
    assert model.is_live is True, "the seeded deployment is IN_SERVICE"
    assert seeded.deployment in model.live_deployments
    assert radius.severity is Severity.CRITICAL


# --------------------------------------------------------------------------
# The full loop
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def no_prior_incident(conn: DataHubConnection, seeded: SeedResult) -> None:
    """Resolve any incident an earlier scan left open on the table.

    Without this the gate is not hermetic: a previous run's incident is correctly
    *reused* rather than re-raised, so ``created`` would be False and the
    description would carry that older run's id. Resolving first means the
    assertions below describe this run, and it exercises ``resolve_incident``.
    """
    title = f"Stale upstream data in {spec.SOURCE_TABLE}"
    for urn in _active_freshness_incidents(conn, seeded.source_table, title):
        resolve_incident(conn, urn, "Superseded: the Phase 1 gate is starting from a clean slate.")

    _eventually(
        lambda: not _active_freshness_incidents(conn, seeded.source_table, title),
        "the pre-existing incident to be resolved",
    )


@pytest.fixture(scope="module")
def scanned(
    conn: DataHubConnection,
    seeded: SeedResult,
    config: ScanConfig,
    stale: None,
    no_prior_incident: None,
    run_id: str,
):
    """Run the scan once for the module. Every write below comes from this run."""
    return run_scan(conn, config, table_urn=seeded.source_table, run_id=run_id, llm=None)


def _only(report: ScanReport) -> FindingWrites:
    """The single finding's writes. A freshness scan of one table produces one."""
    assert len(report.writes) == 1, f"expected one finding, got {len(report.writes)}"
    return report.writes[0]


def test_the_scan_narrates_without_an_llm_when_none_is_configured(scanned):
    """The judge's path: no LLM configured at all, and the loop still completes."""
    assert _only(scanned).narrative is not None
    assert _only(scanned).narrative.source is NarrativeSource.TEMPLATE
    assert "Credit Risk v3" in _only(scanned).narrative.assessment


def test_the_incident_lands_on_the_failing_table_and_is_active(
    conn: DataHubConnection, seeded: SeedResult, scanned
):
    assert _only(scanned).incident is not None
    assert _only(scanned).incident.created is True, "the gate must exercise the create path"

    info = conn.graph.get_aspect(_only(scanned).incident.urn, IncidentInfoClass)
    assert info is not None

    assert info.type == "FRESHNESS"
    assert seeded.source_table in info.entities
    assert info.status.state == IncidentStateClass.ACTIVE
    # Provenance, stamped by the run that raised it. Not part of the dedup key.
    assert scanned.run_id in (info.description or "")


def test_the_incident_body_states_the_measured_lag(scanned):
    assert _only(scanned).finding is not None
    assert _only(scanned).finding.evidence["lag_hours"].startswith("30.")


def test_the_at_risk_model_is_tagged(conn: DataHubConnection, seeded: SeedResult, scanned):
    tags = _eventually(
        lambda: read_tags(conn, seeded.model),
        "the model-at-risk tag to land on the model",
    )
    assert "urn:li:tag:model-at-risk" in tags


def test_the_model_carries_the_risk_flag_and_the_run_id(
    conn: DataHubConnection, seeded: SeedResult, scanned
):
    """Model-level risk lives on structured properties, because incidents cannot."""
    stored = read_properties(conn, seeded.model)
    assert stored[RISK_FLAGS] == ["upstream-freshness"]
    assert stored[RUN_ID] == [scanned.run_id]


def test_the_guarding_assertion_exists_on_the_failing_table_and_is_inferred(
    conn: DataHubConnection, seeded: SeedResult, scanned
):
    assert _only(scanned).assertion is not None
    info = conn.graph.get_aspect(_only(scanned).assertion.urn, AssertionInfoClass)
    assert info is not None

    assert info.type == "FRESHNESS"
    assert info.freshnessAssertion.entity == seeded.source_table
    assert info.source.type == AssertionSourceTypeClass.INFERRED
    # The SLA must survive the timedelta.seconds truncation bug: 6h == 21600s.
    assert info.freshnessAssertion.schedule.fixedInterval.multiple == 6 * 3600


def test_the_assertion_run_records_the_failure_this_scan_actually_measured(
    conn: DataHubConnection, scanned
):
    """The result is our detector's verdict, not a fabricated one."""
    assert _only(scanned).assertion is not None
    event = _eventually(
        lambda: conn.graph.get_latest_timeseries_value(
            _only(scanned).assertion.urn, AssertionRunEventClass, {}
        ),
        "the assertion run event to be indexed",
    )
    assert event.result.type == AssertionResultTypeClass.FAILURE
    assert event.result.actualAggValue == pytest.approx(_LAG_HOURS, abs=0.5)
    assert event.result.nativeResults["evaluated_from"].startswith("datahub operation aspect")


def test_the_impact_report_is_published_as_a_document_on_the_model(
    conn: DataHubConnection, seeded: SeedResult, scanned
):
    assert len(_only(scanned).documents) == 1
    document = _only(scanned).documents[0]
    assert document.model_urn == seeded.model

    info = conn.graph.get_aspect(document.urn, DocumentInfoClass)
    assert info is not None
    assert "impact report" in info.title.lower()
    assert "Credit Risk v3" in info.contents.text
    # related_assets is what makes the report reachable from the model's page.
    assert seeded.model in [asset.asset for asset in info.relatedAssets or []]


# --------------------------------------------------------------------------
# Idempotency: the property that makes reruns safe
# --------------------------------------------------------------------------


def test_rescanning_reuses_the_incident_rather_than_duplicating_it(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, scanned
):
    """A second scan of an unchanged graph must not raise a second incident."""
    assert _only(scanned).finding is not None
    title = _only(scanned).finding.title

    # Poll with a read-only lookup, never by scanning again: a scan-based probe
    # would itself create the duplicates this test exists to rule out.
    _eventually(
        lambda: _active_freshness_incidents(conn, seeded.source_table, title),
        "the first incident to be indexed on the IncidentOn relationship",
    )

    second = run_scan(conn, config, table_urn=seeded.source_table, llm=None)
    assert _only(second).incident is not None
    assert _only(second).incident.created is False, "a second scan duplicated the incident"
    assert _only(second).incident.urn == _only(scanned).incident.urn

    matching = _active_freshness_incidents(conn, seeded.source_table, title)
    assert matching == [_only(scanned).incident.urn], (
        f"expected one active incident, found {matching}"
    )


def test_rescanning_reuses_the_assertion_and_the_document(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, scanned
):
    second = run_scan(conn, config, table_urn=seeded.source_table, llm=None)

    assert _only(second).assertion is not None and _only(scanned).assertion is not None
    assert _only(second).assertion.urn == _only(scanned).assertion.urn
    assert _only(second).assertion.created is False

    assert [d.urn for d in _only(second).documents] == [d.urn for d in _only(scanned).documents]


def test_rescanning_does_not_tag_an_already_tagged_model(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, scanned
):
    second = run_scan(conn, config, table_urn=seeded.source_table, llm=None)
    assert _only(second).tagged_models == (), "the model was tagged twice"
    assert "urn:li:tag:model-at-risk" in read_tags(conn, seeded.model)


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------


def test_once_the_table_refreshes_a_scan_writes_nothing(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, scanned
):
    """The loop is not sticky: a recovered table produces a clean scan."""
    revert_stale_source(conn)
    _eventually(
        lambda: blast_radius(conn, seeded.source_table, config) is None,
        "the refreshed operation aspect to be indexed",
    )

    report = run_scan(conn, config, table_urn=seeded.source_table, llm=None)
    assert report.clean is True
    assert report.writes == ()
