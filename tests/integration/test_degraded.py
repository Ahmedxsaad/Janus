"""The degraded mode against a live DataHub, including what it writes (T-07).

Two things can only be checked here. Whether a real GMS serves the aspects this
mode reads for a model whose features have been taken away, which is the state
every mlflow ingest leaves behind (D-074); and whether the incident it raises on
the training table deduplicates on a rerun the way every other write does
(tests/CLAUDE.md rule 3).

The scenario is the real one rather than a contrivance: the model's features are
stripped exactly as an ingestion run strips them, and the table it trains on is
deprecated. Re-declaring the features has to silence this mode completely, which
is the same claim ``benchmarks/counterfactuals.py`` measures and the one thing
that keeps a weak answer from sitting beside a strong one.

Reads are asynchronous, so every assertion polls.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import TypeVar

import pytest
from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import IncidentInfoClass, IncidentStateClass

from modelguard.agent.pipeline import new_run_id, run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.degraded import table_level_findings
from modelguard.models import FindingType, Severity, TableRisk
from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import (
    plant_delinked_model,
    plant_deprecated_input,
    revert_delinked_model,
    revert_deprecated_input,
)
from modelguard.seed.seed_ml_graph import SeedResult, seed_ml_graph
from modelguard.writeback.incidents import INCIDENT_ON_RELATIONSHIP

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
def seeded(conn: DataHubConnection) -> SeedResult:
    return seed_ml_graph(conn)


@pytest.fixture
def unlinked(conn: DataHubConnection, seeded: SeedResult, config: ScanConfig) -> Iterator[None]:
    """A deprecated training table and a model whose features an ingest dropped."""
    plant_deprecated_input(conn)
    plant_delinked_model(conn)
    _eventually(
        lambda: bool(table_level_findings(conn, seeded.model, config)),
        "the de-linked model and its deprecated input to be readable",
    )
    yield
    revert_delinked_model(conn)
    revert_deprecated_input(conn)


def _incident_titles(conn: DataHubConnection, resource_urn: str, *, active: bool) -> list[str]:
    """Titles of the incidents attached to a resource, filtered by state."""
    wanted = IncidentStateClass.ACTIVE if active else IncidentStateClass.RESOLVED
    titles = []
    for entity in conn.graph.get_related_entities(
        entity_urn=resource_urn,
        relationship_types=[INCIDENT_ON_RELATIONSHIP],
        direction=RelationshipDirection.INCOMING,
    ):
        info = conn.graph.get_aspect(entity.urn, IncidentInfoClass)
        if info is not None and info.status.state == wanted:
            titles.append(info.title)
    return titles


def test_a_model_an_ingest_unlinked_still_gets_an_answer(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, unlinked: None
) -> None:
    """The whole point of the mode: silence replaced by something actionable."""
    findings = table_level_findings(conn, seeded.model, config)

    assert [finding.risk for finding in findings] == [TableRisk.DEPRECATED]
    finding = findings[0]
    assert finding.finding_type is FindingType.TABLE_LEVEL_RISK
    assert finding.resource_urn == str(spec.feature_table_dataset_urn())
    # Live model, and still capped below the column-level detectors' range.
    assert finding.severity is Severity.MEDIUM
    assert "declares no features" in finding.mode_note


def test_declaring_the_features_again_silences_the_mode(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, unlinked: None
) -> None:
    """The gate, against a live graph: a linked model is answered at column level.

    The table is still deprecated here, and the column-level deprecation detector
    still reports it. What must not happen is this mode reporting it a second
    time, at a weaker confidence, beside the finding that can name the model's
    own inputs.
    """
    revert_delinked_model(conn)

    silent = _eventually(
        lambda: not table_level_findings(conn, seeded.model, config),
        "the table-level mode to stand down once the model is linked again",
    )
    assert silent


def test_the_scan_raises_one_incident_on_the_training_table_and_reuses_it(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig, unlinked: None
) -> None:
    """The write, and its idempotency (tests/CLAUDE.md rule 3).

    Two scans with different run ids, exactly one incident: the run id is
    provenance and never part of the dedup key (D-013).
    """
    table_urn = str(spec.feature_table_dataset_urn())
    report = run_scan(conn, config, model_urn=seeded.model, run_id=new_run_id(), llm=None)

    degraded = [
        write
        for write in report.writes
        if write.finding.finding_type is FindingType.TABLE_LEVEL_RISK
    ]
    assert len(degraded) == 1
    title = degraded[0].finding.title
    assert "no column link" in title

    _eventually(
        lambda: title in _incident_titles(conn, table_urn, active=True),
        "the table-level incident to be indexed on the training table",
    )

    run_scan(conn, config, model_urn=seeded.model, run_id=new_run_id(), llm=None)
    assert _incident_titles(conn, table_urn, active=True).count(title) == 1
