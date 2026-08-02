"""The sensitive-source detector against a live DataHub, both directions.

Why this file exists and a unit test does not replace it (F8): the detector reads
``globalTags`` from a ``schemaField`` entity, and nothing has ever confirmed that
a real GMS serves that aspect from that entity type. ``FakeGraph`` answers
because it was written by the same people as the detector and to the same mental
model of DataHub; the failures that have actually bitten this project are all of
the shape "the server does not behave the way the fake does" (``get_aspect``
raising for a timeseries aspect, ``exists`` always False for a schemaField,
``incidentsSummary`` never being written). This is the test that would catch the
next one.

Both directions are covered, because a detector that fires on everything passes
the positive half. The negative writes ``globalTags`` with an empty list rather
than deleting the aspect, which is what the scenario does and what a classifier
withdrawing a classification does.

Reads are asynchronous: DataHub indexes lineage and relationships through Kafka
and Elasticsearch, so every assertion polls rather than assuming read-after-write.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar

import pytest
from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import IncidentInfoClass, IncidentStateClass

from modelguard.agent.pipeline import run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.governance import sensitive_source_findings
from modelguard.models import FindingType
from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import (
    SENSITIVE_SOURCE_COLUMN,
    SENSITIVE_TAG_URN,
    plant_sensitive_source,
    revert_sensitive_source,
)
from modelguard.seed.seed_ml_graph import SeedResult, seed_ml_graph
from modelguard.writeback.incidents import INCIDENT_ON_RELATIONSHIP, resolve_incident
from modelguard.writeback.properties import RISK_FLAGS, read_properties

pytestmark = pytest.mark.integration

T = TypeVar("T")

_INDEX_TIMEOUT_SECONDS = 90.0

#: The feature-table column that descends from the classified one. The incident
#: lands here, on the column this model actually reads.
EXPOSED_FEATURE_COLUMN = "applicant_income"


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
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    """The scan config with the demo classification configured.

    Set explicitly rather than read from the environment: the check reports
    itself unevaluated when nothing is configured, so a machine with an empty
    MODELGUARD_SENSITIVE_TAG_URNS would turn this file green without running the
    detector once.
    """
    return replace(ScanConfig(), sensitive_tag_urns=(SENSITIVE_TAG_URN,))


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection, config: ScanConfig) -> SeedResult:
    """Seed the graph, classify the upstream column, and wait until it is visible."""
    result = seed_ml_graph(conn)
    plant_sensitive_source(conn)
    _eventually(
        lambda: bool(sensitive_source_findings(conn, result.model, config)),
        "the classified upstream column to be indexed and detectable",
    )
    return result


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


def test_a_classified_upstream_column_is_read_off_a_live_schema_field(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The read this whole file exists for: globalTags on a schemaField entity."""
    findings = sensitive_source_findings(conn, seeded.model, config)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type is FindingType.SENSITIVE_SOURCE
    assert finding.exposure.sensitive_column_name == SENSITIVE_SOURCE_COLUMN
    assert finding.exposure.marker_urn == SENSITIVE_TAG_URN
    # The path is the evidence: it must actually reach the classified column.
    assert finding.exposure.column_path[-1] == SENSITIVE_SOURCE_COLUMN


def test_the_scan_writes_the_incident_on_the_feature_own_source_column(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The incident lands on the model's own column, not on the classified ancestor.

    Deliberate, and worth pinning: the classified column may be several joins
    upstream and owned by another team, and the actionable column is the one this
    model reads. The ancestor is named in the title instead.
    """
    run_id = f"sensitive-{uuid.uuid4().hex[:8]}"
    column_urn = str(spec.feature_column_urn(EXPOSED_FEATURE_COLUMN))
    # Start from a clean slate so this exercises the create path, not a rerun.
    for entity in conn.graph.get_related_entities(
        entity_urn=column_urn,
        relationship_types=[INCIDENT_ON_RELATIONSHIP],
        direction=RelationshipDirection.INCOMING,
    ):
        info = conn.graph.get_aspect(entity.urn, IncidentInfoClass)
        if info is not None and info.status.state == IncidentStateClass.ACTIVE:
            resolve_incident(conn, entity.urn, message="cleared by the sensitive-source setup")

    report = run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)

    sensitive = [
        write
        for write in report.writes
        if write.finding.finding_type is FindingType.SENSITIVE_SOURCE
    ]
    assert len(sensitive) == 1
    assert sensitive[0].finding.resource_urn == column_urn
    title = sensitive[0].finding.title
    # The title names the classified ancestor, which is what makes the incident
    # readable by somebody who has never heard of this model.
    assert SENSITIVE_SOURCE_COLUMN in title
    assert SENSITIVE_TAG_URN.rsplit(":", 1)[-1] in title

    _eventually(
        lambda: title in _incident_titles(conn, column_urn, active=True),
        "the sensitive-source incident to be indexed on the column",
    )
    flags = read_properties(conn, seeded.model).get(RISK_FLAGS, [])
    assert str(FindingType.SENSITIVE_SOURCE) in flags


def test_withdrawing_the_classification_resolves_the_incident(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The recovery half, and the reason the negative is written as an empty tag list.

    Nothing about the column, the derivation or the model changes: only the
    organization's declaration is withdrawn, which is how a real reclassification
    arrives. A detector keying on the aspect's presence rather than its contents
    would keep firing here.
    """
    column_urn = str(spec.feature_column_urn(EXPOSED_FEATURE_COLUMN))
    title = _eventually(
        lambda: next(iter(_incident_titles(conn, column_urn, active=True)), ""),
        "an active sensitive-source incident to resolve",
    )

    revert_sensitive_source(conn)
    _eventually(
        lambda: not sensitive_source_findings(conn, seeded.model, config),
        "the withdrawn classification to stop producing a finding",
    )
    run_scan(
        conn, config, model_urn=seeded.model, run_id=f"sensitive-{uuid.uuid4().hex[:8]}", llm=None
    )

    _eventually(
        lambda: title in _incident_titles(conn, column_urn, active=False),
        "the sensitive-source incident to be resolved",
    )

    # Leave the graph as the demo expects to find it: the classification is an
    # anomaly planted on top of the seed, and the seed's baseline is unclassified.
    revert_sensitive_source(conn)
