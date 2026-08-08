"""The Week 1 kill-criterion, as an executable gate.

docs/02-architecture.md states the gate in prose: by the end
of Week 1 Janus must be able to (a) programmatically read column-level ML
lineage and (b) write one incident plus one structured property back. If it
cannot, the project pivots to MigrationCopilot.

This module is that criterion, executable. It doubles as the judge's smoke test.

Run it against a live Quickstart::

    datahub docker quickstart
    pytest -m integration -v

Reads are asynchronous. DataHub writes aspects synchronously but indexes lineage
and the incidents summary through Kafka and Elasticsearch, so the assertions poll
rather than assume read-after-write consistency.
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
    MLFeaturePropertiesClass,
    MLFeatureTablePropertiesClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
    UpstreamLineageClass,
)

from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.seed import graph_spec as spec
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.incidents import (
    INCIDENT_ON_RELATIONSHIP,
    IncidentWrite,
    find_active_incident,
    raise_incident,
)
from janus.writeback.properties import (
    RISK_FLAGS,
    RUN_ID,
    TRUST_SCORE,
    assign_properties,
    define_properties,
    read_properties,
)

pytestmark = pytest.mark.integration

T = TypeVar("T")

#: Generous: a cold Elasticsearch index can take a while to catch up.
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
def seeded(conn: DataHubConnection) -> SeedResult:
    """Seed the ML graph once for the module."""
    return seed_ml_graph(conn)


@pytest.fixture(scope="module")
def run_id() -> str:
    """A fresh run id, as a real scan would generate."""
    return f"gate-{uuid.uuid4().hex[:8]}"


def _leakage_column_urn() -> str:
    """The column that leaks the label: where a leakage incident belongs."""
    return str(spec.feature_column_urn(spec.LEAKAGE_FEATURE))


def _incidents_on(conn: DataHubConnection, resource_urn: str) -> list[str]:
    """Every incident URN attached to a resource, via the IncidentOn relationship."""
    return [
        entity.urn
        for entity in conn.graph.get_related_entities(
            entity_urn=resource_urn,
            relationship_types=[INCIDENT_ON_RELATIONSHIP],
            direction=RelationshipDirection.INCOMING,
        )
    ]


def _is_active_incident(
    conn: DataHubConnection, incident_urn: str, incident_type: str, title: str
) -> bool:
    """Whether the incident is open and describes exactly this finding."""
    info = conn.graph.get_aspect(incident_urn, IncidentInfoClass)
    if info is None:
        return False
    return (
        info.status.state == IncidentStateClass.ACTIVE
        and info.type == incident_type
        and info.title == title
    )


# --------------------------------------------------------------------------
# Gate part A: read column-level ML lineage
# --------------------------------------------------------------------------


def test_seeded_entities_all_exist(conn: DataHubConnection, seeded: SeedResult):
    for urn in (
        seeded.source_table,
        seeded.feature_table_dataset,
        seeded.feature_table,
        seeded.training_run,
        seeded.model_group,
        seeded.model,
        seeded.deployment,
        *seeded.features,
    ):
        assert conn.graph.exists(urn), f"{urn} was not created"


def test_column_level_lineage_reaches_the_label_column(conn: DataHubConnection, seeded: SeedResult):
    """The leakage feature's column cone must reach the label column's table.

    This is the read half of the gate and the exact traversal the P1 leakage
    detector will perform.
    """

    def upstream_urns() -> list[str]:
        results = conn.client.lineage.get_lineage(
            source_urn=seeded.feature_table_dataset,
            source_column=spec.LEAKAGE_FEATURE,
            direction="upstream",
            max_hops=2,
        )
        return [r.urn for r in results]

    # Wait for the label's table specifically. A partially indexed cone can be
    # non-empty while still missing the edge under test, so polling until the
    # list is merely non-empty would pass on an incomplete graph.
    _eventually(
        lambda: seeded.source_table in upstream_urns(),
        f"the upstream cone of {spec.LEAKAGE_FEATURE} to reach {seeded.source_table}",
    )
    assert seeded.source_table in upstream_urns()


def test_the_model_resolves_to_its_features_run_and_deployment(
    conn: DataHubConnection, seeded: SeedResult
):
    """Model to ML-entity traversal: the boundary the warehouse graph alone lacks."""
    properties = conn.graph.get_aspect(seeded.model, MLModelPropertiesClass)
    assert properties is not None

    assert set(properties.mlFeatures or []) == set(seeded.features)
    assert seeded.training_run in (properties.trainingJobs or [])
    assert seeded.deployment in (properties.deployments or [])
    assert seeded.model_group in (properties.groups or [])


def test_each_feature_names_the_exact_column_it_derives_from(
    conn: DataHubConnection, seeded: SeedResult
):
    """MLFeature.sources is dataset-granular, so the column lives in customProperties."""
    leakage_feature_urn = str(spec.feature_urn(spec.LEAKAGE_FEATURE))
    properties = conn.graph.get_aspect(leakage_feature_urn, MLFeaturePropertiesClass)
    assert properties is not None

    assert properties.sources == [seeded.feature_table_dataset]
    source_column = (properties.customProperties or {})[spec.SOURCE_COLUMN_PROPERTY]
    assert source_column == str(spec.feature_column_urn(spec.LEAKAGE_FEATURE))


# --------------------------------------------------------------------------
# Gate part B: write an incident and a structured property
# --------------------------------------------------------------------------


def test_structured_properties_are_defined_and_assigned(
    conn: DataHubConnection, seeded: SeedResult, run_id: str
):
    define_properties(conn)
    assign_properties(
        conn,
        seeded.model,
        {TRUST_SCORE: [62], RISK_FLAGS: ["target-leakage"], RUN_ID: [run_id]},
    )

    stored = read_properties(conn, seeded.model)
    assert stored[TRUST_SCORE] == [62.0]
    assert stored[RISK_FLAGS] == ["target-leakage"]
    assert stored[RUN_ID] == [run_id]


def test_datahub_refuses_an_incident_on_a_model(conn: DataHubConnection, seeded: SeedResult):
    """The plan targeted the model. The metadata model forbids it.

    incidentInfo.entities accepts only dataset, chart, dashboard, dataFlow,
    dataJob, and schemaField. We reject it before the call rather than let GMS
    return a 500.
    """
    with pytest.raises(ValueError, match="cannot raise an incident on a mlModel"):
        raise_incident(
            conn,
            resource_urn=seeded.model,
            incident_type="FIELD",
            title="never written",
            description="never written",
            run_id="unused",
        )


def test_an_incident_is_raised_on_the_leaking_column(
    conn: DataHubConnection, seeded: SeedResult, run_id: str
):
    """A leakage finding attaches to the column that leaks, which DataHub allows.

    The title carries the run id so this test always exercises the create path.
    Dedup of a stable title is covered separately, below.
    """
    result = raise_incident(
        conn,
        resource_urn=_leakage_column_urn(),
        incident_type="FIELD",
        title=f"Target leakage in feature {spec.LEAKAGE_FEATURE} [{run_id}]",
        description=(
            f"Feature {spec.LEAKAGE_FEATURE} derives from "
            f"{spec.SOURCE_TABLE}.{spec.LABEL_SOURCE_COLUMN}, which is the model's label."
        ),
        run_id=run_id,
    )
    assert result.created is True
    assert result.urn.startswith("urn:li:incident:")

    # The incident must carry the run id, and point back at the column.
    info = conn.graph.get_aspect(result.urn, IncidentInfoClass)
    assert info is not None
    assert info.type == "FIELD"
    assert _leakage_column_urn() in info.entities
    assert run_id in (info.description or "")


# --------------------------------------------------------------------------
# Idempotency: the property that makes reruns safe
# --------------------------------------------------------------------------


def _graph_fingerprint(conn: DataHubConnection, seeded: SeedResult) -> dict[str, object]:
    """Capture the seeded graph's mutable state, so a rerun can be compared to it.

    Comparing the returned SeedResult would prove nothing: it is built from fixed
    constants and would match even if the seeder wrote nothing at all. These are
    the aspects a non-convergent seeder would actually corrupt, by appending a
    duplicate feature, a second upstream edge, or a repeated schema field.
    """
    model = conn.graph.get_aspect(seeded.model, MLModelPropertiesClass)
    feature_table = conn.graph.get_aspect(seeded.feature_table, MLFeatureTablePropertiesClass)
    schema = conn.graph.get_aspect(seeded.feature_table_dataset, SchemaMetadataClass)
    lineage = conn.graph.get_aspect(seeded.feature_table_dataset, UpstreamLineageClass)
    assert model is not None and feature_table is not None
    assert schema is not None and lineage is not None

    return {
        "ml_features": sorted(model.mlFeatures or []),
        "training_jobs": sorted(model.trainingJobs or []),
        "deployments": sorted(model.deployments or []),
        "groups": sorted(model.groups or []),
        "table_features": sorted(feature_table.mlFeatures or []),
        "table_keys": sorted(feature_table.mlPrimaryKeys or []),
        "schema_fields": sorted(f.fieldPath for f in schema.fields),
        "upstreams": sorted(u.dataset for u in lineage.upstreams),
        "column_edges": sorted(
            str(fg.downstreams) + "<-" + str(sorted(fg.upstreams or []))
            for fg in (lineage.fineGrainedLineages or [])
        ),
    }


def test_seeding_twice_leaves_the_graph_byte_for_byte_identical(
    conn: DataHubConnection, seeded: SeedResult
):
    """The seeder must converge, not accumulate.

    An UPSERT that appends rather than replaces shows up here as a duplicated
    feature, upstream, or schema field on the second run.
    """
    before = _graph_fingerprint(conn, seeded)
    seed_ml_graph(conn)
    after = _graph_fingerprint(conn, seeded)

    assert after == before

    # Guard the specific failure mode: list-valued aspects growing on each run.
    assert len(after["ml_features"]) == len(spec.MODEL_FEATURES)
    assert len(after["upstreams"]) == 1
    assert len(after["column_edges"]) == len(spec.COLUMN_LINEAGE)
    assert len(after["schema_fields"]) == len(spec.FEATURE_COLUMNS)


def test_the_seeder_reports_exactly_what_it_wrote(conn: DataHubConnection, seeded: SeedResult):
    """SeedResult must describe the graph, not just restate its own constants."""
    model = conn.graph.get_aspect(seeded.model, MLModelPropertiesClass)
    assert model is not None
    assert sorted(model.mlFeatures or []) == sorted(seeded.features)


def test_raising_the_same_finding_twice_reuses_the_incident(
    conn: DataHubConnection, seeded: SeedResult, run_id: str
):
    """A second scan of an unchanged graph must not duplicate the incident.

    The dedup read goes through the resource's incidentsSummary aspect, which
    DataHub populates asynchronously, so wait for the first incident to surface
    before asserting that the second call reuses it.
    """
    title = f"Target leakage in feature {spec.LEAKAGE_FEATURE}"
    column = _leakage_column_urn()

    def raise_again(new_run_id: str) -> IncidentWrite:
        return raise_incident(
            conn,
            resource_urn=column,
            incident_type="FIELD",
            title=title,
            description="body",
            run_id=new_run_id,
        )

    first = raise_again(run_id)

    # Poll with the read-only lookup, never by raising again: a raise-based probe
    # would itself create the duplicates this test exists to rule out.
    _eventually(
        lambda: find_active_incident(conn, column, "FIELD", title) is not None,
        "the incidents summary to index the first incident",
    )

    second = raise_again(f"{run_id}-second")
    assert second.created is False, "a second identical finding created a duplicate incident"
    assert second.urn == first.urn

    # And the graph itself must hold exactly one active incident for this finding.
    matching = [
        urn for urn in _incidents_on(conn, column) if _is_active_incident(conn, urn, "FIELD", title)
    ]
    assert matching == [first.urn], f"expected one active incident, found {matching}"


def test_assigning_the_same_properties_twice_is_stable(
    conn: DataHubConnection, seeded: SeedResult, run_id: str
):
    assign_properties(conn, seeded.model, {TRUST_SCORE: [62]})
    assign_properties(conn, seeded.model, {TRUST_SCORE: [62]})
    stored = read_properties(conn, seeded.model)
    assert stored[TRUST_SCORE] == [62.0]
    # The earlier assignment of the other two properties must survive.
    assert stored[RISK_FLAGS] == ["target-leakage"]
