from __future__ import annotations

import json
from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    OperationClass,
    OperationTypeClass,
)
from datahub.metadata.urns import SchemaFieldUrn

from janus.seed import graph_spec as spec
from janus.seed.scenarios import (
    BACKUP_LABEL_COLUMN,
    COMMON_ANCESTOR_LABEL,
    LOOKALIKE_COLUMN,
    SCENARIO_PROPERTY,
    SCHEMA_DRIFT,
    STALE_SOURCE,
    plant_common_ancestor_label,
    plant_label_lookalike,
    plant_leakage,
    plant_schema_drift,
    plant_second_leak_path,
    plant_stale_source,
    revert_common_ancestor_label,
    revert_label_lookalike,
    revert_leakage,
    revert_schema_drift,
    revert_second_leak_path,
    revert_stale_source,
)
from tests.conftest import FakeClient, FakeGraph, make_connection

NOW = 1_800_000_000_000
HOUR = 3_600_000


def _emitted_operation(graph: FakeGraph) -> OperationClass:
    """The one aspect the scenario sent to DataHub."""
    assert len(graph.emitted) == 1, f"expected exactly one MCP, got {len(graph.emitted)}"
    aspect = graph.emitted[0].aspect
    assert isinstance(aspect, OperationClass)
    return aspect


def test_planting_backdates_the_tables_last_change_by_the_requested_lag():
    graph = FakeGraph()
    conn = make_connection(graph)

    plant_stale_source(conn, lag_hours=30.0, now_ms=NOW)

    operation = _emitted_operation(graph)
    # Assert on what was sent to DataHub, not on the value the caller passed back.
    assert operation.lastUpdatedTimestamp == NOW - 30 * HOUR
    assert operation.timestampMillis == NOW
    assert operation.operationType == OperationTypeClass.UPDATE


def test_the_planted_operation_lands_on_the_label_bearing_source_table():
    graph = FakeGraph()
    plant_stale_source(make_connection(graph), lag_hours=30.0, now_ms=NOW)
    assert graph.emitted[0].entityUrn == str(spec.source_table_urn())


def test_the_planted_operation_declares_itself_as_a_scenario():
    """A reader of the graph must be able to tell a planted failure from a real one."""
    graph = FakeGraph()
    plant_stale_source(make_connection(graph), lag_hours=30.0, now_ms=NOW)
    assert _emitted_operation(graph).customProperties == {SCENARIO_PROPERTY: STALE_SOURCE}


def test_reverting_announces_a_refresh_rather_than_deleting_the_planted_event():
    """Operation is a timeseries aspect: recovery is a newer event, not a delete."""
    graph = FakeGraph()
    revert_stale_source(make_connection(graph), now_ms=NOW)

    operation = _emitted_operation(graph)
    assert operation.lastUpdatedTimestamp == NOW
    assert operation.timestampMillis == NOW


def test_a_reverted_table_reports_zero_lag():
    result = revert_stale_source(make_connection(FakeGraph()), now_ms=NOW)
    assert result.lag_hours == 0.0
    assert result.last_updated_ms == NOW


@pytest.mark.parametrize("lag", [0.0, -1.0])
def test_planting_a_non_positive_lag_is_refused(lag: float):
    """A lag of zero plants nothing; failing loudly beats a scenario that does not fire."""
    graph = FakeGraph()
    with pytest.raises(ValueError, match="must be positive"):
        plant_stale_source(make_connection(graph), lag_hours=lag, now_ms=NOW)
    assert graph.emitted == []


# --------------------------------------------------------------------------
# The schema-drift scenario (P3)
# --------------------------------------------------------------------------


def _upserted_dataset(client: FakeClient) -> Any:
    assert len(client.entities.upserted) == 1
    return client.entities.upserted[0]


def _schema_of(dataset: Any) -> dict[str, str]:
    return {field.field_path: field.native_type for field in dataset.schema}


def test_planting_drift_changes_the_feature_tables_live_schema_from_training():
    client = FakeClient()
    plant_schema_drift(make_connection(FakeGraph(), client))

    schema = _schema_of(_upserted_dataset(client))
    training = spec.training_schema_fingerprint()[str(spec.feature_table_dataset_urn())]

    # A retype, a drop, and an add, versus the training snapshot.
    assert schema != training
    assert schema["applicant_income"] == "VARCHAR"  # retyped from NUMBER
    assert "updated_at" not in schema  # dropped
    assert "debt_to_income" in schema  # added


def test_drift_leaves_the_leakage_columns_untouched():
    """The two Phase 2 scenarios must be able to coexist on one graph."""
    client = FakeClient()
    plant_schema_drift(make_connection(FakeGraph(), client))
    schema = _schema_of(_upserted_dataset(client))

    assert "prior_default_flag" in schema
    assert "applicant_id" in schema


def test_planted_drift_declares_itself_a_scenario():
    client = FakeClient()
    plant_schema_drift(make_connection(FakeGraph(), client))
    assert _upserted_dataset(client).custom_properties == {SCENARIO_PROPERTY: SCHEMA_DRIFT}


def test_reverting_restores_the_training_schema_and_clears_the_marker():
    client = FakeClient()
    result = revert_schema_drift(make_connection(FakeGraph(), client))

    dataset = _upserted_dataset(client)
    schema = _schema_of(dataset)
    training = spec.training_schema_fingerprint()[str(spec.feature_table_dataset_urn())]

    assert schema == training
    assert dataset.custom_properties == {}
    assert result.name == SCHEMA_DRIFT


# --------------------------------------------------------------------------
# The target-leakage scenario (P1), the flagship detector's negative control
# --------------------------------------------------------------------------


def _sent_column_lineage(client: FakeClient) -> dict[str, list[str]]:
    """Decode the column lineage the scenario actually sent to DataHub.

    Reads the built JSON patch rather than any value the scenario handed back, so
    a scenario that returned the right answer while sending the wrong edges would
    fail here (tests/CLAUDE.md rule 6).
    """
    assert len(client.entities.updated) == 1, "expected exactly one patch"
    mcps = client.entities.updated[0].build()
    assert len(mcps) == 1
    patches = json.loads(mcps[0].aspect.value)

    edges: dict[str, list[str]] = {}
    for patch in patches:
        assert patch["path"] == "/fineGrainedLineages"
        for edge in patch["value"]:
            for downstream in edge["downstreams"]:
                column = SchemaFieldUrn.from_string(downstream).field_path
                edges[column] = [
                    SchemaFieldUrn.from_string(up).field_path for up in edge["upstreams"]
                ]
    return edges


def _sent_transform_operations(client: FakeClient) -> dict[str, str]:
    """The transformOperation on each edge the scenario wrote. Expected empty."""
    mcps = client.entities.updated[0].build()
    marks: dict[str, str] = {}
    for patch in json.loads(mcps[0].aspect.value):
        for edge in patch["value"]:
            marker = edge.get("transformOperation")
            if marker is None:
                continue
            for downstream in edge["downstreams"]:
                marks[SchemaFieldUrn.from_string(downstream).field_path] = marker
    return marks


def test_reverting_leakage_cuts_the_leaking_features_derivation_from_the_label():
    client = FakeClient()
    result = revert_leakage(make_connection(FakeGraph(), client))

    assert spec.LEAKAGE_FEATURE not in _sent_column_lineage(client)
    assert result.upstream_columns == ()
    assert result.leaking is False


def test_reverting_leakage_keeps_every_benign_edge():
    """The negative control must isolate the label edge, not blank the lineage.

    A revert that dropped all the column lineage would silence the detector for
    the wrong reason, and the benchmark would score a graph nobody would ship.
    """
    client = FakeClient()
    revert_leakage(make_connection(FakeGraph(), client))

    sent = _sent_column_lineage(client)
    expected = {
        column: upstreams
        for column, upstreams in spec.COLUMN_LINEAGE.items()
        if column != spec.LEAKAGE_FEATURE
    }
    assert sent == expected


def test_planting_leakage_restores_the_edge_from_the_label():
    client = FakeClient()
    result = plant_leakage(make_connection(FakeGraph(), client))

    assert _sent_column_lineage(client) == dict(spec.COLUMN_LINEAGE)
    assert result.upstream_columns == (spec.LABEL_SOURCE_COLUMN,)
    assert result.leaking is True


def test_no_edge_carries_a_scenario_marker():
    """A marker would fork the edge and make the seeder accumulate a duplicate.

    ``transformOperation`` is part of what GMS keys a fine-grained edge on, so a
    marked edge and the seeder's unmarked one are two different edges: the next
    ``janus-seed`` adds its own alongside and the column lineage grows. The
    Week 1 gate's byte-for-byte test is what caught that; this is its offline
    twin, so the marker cannot come back without something going red first.
    """
    for scenario in (plant_leakage, revert_leakage):
        client = FakeClient()
        scenario(make_connection(FakeGraph(), client))
        assert _sent_transform_operations(client) == {}, scenario.__name__


def test_planting_writes_exactly_what_the_seeder_wrote():
    """Planting restores the baseline, so re-seeding afterwards is a no-op."""
    client = FakeClient()
    plant_leakage(make_connection(FakeGraph(), client))

    sent = _sent_column_lineage(client)
    assert sent == dict(spec.COLUMN_LINEAGE)
    assert len(sent) == len(spec.COLUMN_LINEAGE), "an extra edge would accumulate on reseed"


def test_the_leakage_scenario_patches_the_feature_table():
    client = FakeClient()
    plant_leakage(make_connection(FakeGraph(), client))

    mcps = client.entities.updated[0].build()
    assert mcps[0].entityUrn == str(spec.feature_table_dataset_urn())
    assert mcps[0].aspectName == "upstreamLineage"


# --------------------------------------------------------------------------
# The multi-path scenario (T-03), the counterfactual's negative control
# --------------------------------------------------------------------------


def test_the_second_path_gives_the_leaking_feature_two_declared_label_upstreams():
    """One finding reached twice, which is the case a one-edge remedy gets wrong."""
    client = FakeClient()
    result = plant_second_leak_path(make_connection(FakeGraph(), client))

    edges = _sent_column_lineage(client)
    assert edges[spec.LEAKAGE_FEATURE] == [
        spec.LABEL_SOURCE_COLUMN,
        BACKUP_LABEL_COLUMN.name,
    ]
    assert result.upstream_columns == (spec.LABEL_SOURCE_COLUMN, BACKUP_LABEL_COLUMN.name)


def test_cutting_the_first_path_leaves_the_feature_deriving_from_the_second():
    """The half-fixed state, planted as a state rather than assembled by a caller.

    A caller building it out of two other calls would be the benchmark writing
    its own graph shape, and the trial would stop measuring the scenario.
    """
    client = FakeClient()
    plant_second_leak_path(make_connection(FakeGraph(), client), keep_first=False)

    assert _sent_column_lineage(client)[spec.LEAKAGE_FEATURE] == [BACKUP_LABEL_COLUMN.name]


def test_the_second_path_declares_its_column_a_label_with_the_term_the_seeder_uses():
    """A different term would make the column invisible to the detector.

    The scenario would then plant a second path nothing walks, and the trial
    would pass by finding one path where it meant to find two.
    """
    graph = FakeGraph()
    plant_second_leak_path(make_connection(graph, FakeClient()))

    backup_column = str(spec.source_column_urn(BACKUP_LABEL_COLUMN.name))
    declared = [
        mcp
        for mcp in graph.emitted
        if mcp.entityUrn == backup_column and isinstance(mcp.aspect, GlossaryTermsClass)
    ]
    assert declared, "the backup column must be declared a label"
    assert [term.urn for term in declared[-1].aspect.terms] == [spec.LABEL_TERM_URN]


def test_reverting_the_second_path_undeclares_the_column_before_dropping_it():
    """A term on a schemaField outlives the column's removal from the schema.

    Dropping the column first would leave a declared label behind on a column
    nobody can see, which is the sort of thing a later scan trips over long after
    the benchmark that caused it has finished.
    """
    graph = FakeGraph(
        aspects={
            (
                str(spec.source_column_urn(BACKUP_LABEL_COLUMN.name)),
                GlossaryTermsClass,
            ): GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=spec.LABEL_TERM_URN)], auditStamp=None
            )
        }  # type: ignore[arg-type]
    )
    client = FakeClient()

    revert_second_leak_path(make_connection(graph, client))

    backup_column = str(spec.source_column_urn(BACKUP_LABEL_COLUMN.name))
    terms = [
        mcp.aspect
        for mcp in graph.emitted
        if mcp.entityUrn == backup_column and isinstance(mcp.aspect, GlossaryTermsClass)
    ]
    assert terms and terms[-1].terms == []
    # And the column itself is gone from the schema the revert wrote.
    schema = _schema_of(_upserted_dataset(client))
    assert BACKUP_LABEL_COLUMN.name not in schema


def test_reverting_the_second_path_restores_the_seeded_single_path_leak():
    """The baseline the demo expects to find (D-032), not a cleaned graph."""
    client = FakeClient()
    result = revert_second_leak_path(make_connection(FakeGraph(), client))

    assert _sent_column_lineage(client)[spec.LEAKAGE_FEATURE] == [spec.LABEL_SOURCE_COLUMN]
    assert result.leaking is True


# --------------------------------------------------------------------------
# The confusable-negative scenarios (T-09, 09 section 2.2)
# --------------------------------------------------------------------------


def test_the_common_ancestor_label_derives_from_the_same_column_as_a_clean_feature():
    """Both children of one ancestor, neither descending from the other."""
    client = FakeClient()
    result = plant_common_ancestor_label(make_connection(FakeGraph(), client))

    edges = _sent_column_lineage(client)
    assert edges[COMMON_ANCESTOR_LABEL.name] == ["income"]
    assert edges["applicant_income"] == ["income"]
    assert result.upstream_columns == ("income",)
    assert result.leaking is False


def test_planting_the_common_ancestor_label_also_cuts_the_flagship_leak():
    """This scenario is about applicant_income, not a second unrelated leak.

    _set_column_lineage replaces the whole mapping rather than merging into
    it, so spreading the raw seeded mapping here would silently reintroduce
    prior_default_flag's own derivation from the label alongside this one.
    """
    client = FakeClient()
    plant_common_ancestor_label(make_connection(FakeGraph(), client))

    assert spec.LEAKAGE_FEATURE not in _sent_column_lineage(client)


def test_the_common_ancestor_label_is_declared_with_the_term_the_seeder_uses():
    graph = FakeGraph()
    plant_common_ancestor_label(make_connection(graph, FakeClient()))

    labeled_column = str(spec.feature_column_urn(COMMON_ANCESTOR_LABEL.name))
    declared = [
        mcp
        for mcp in graph.emitted
        if mcp.entityUrn == labeled_column and isinstance(mcp.aspect, GlossaryTermsClass)
    ]
    assert declared, "the common-ancestor column must be declared a label"
    assert [term.urn for term in declared[-1].aspect.terms] == [spec.LABEL_TERM_URN]


def test_reverting_the_common_ancestor_label_undeclares_it_before_dropping_it():
    graph = FakeGraph(
        aspects={
            (
                str(spec.feature_column_urn(COMMON_ANCESTOR_LABEL.name)),
                GlossaryTermsClass,
            ): GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=spec.LABEL_TERM_URN)], auditStamp=None
            )
        }  # type: ignore[arg-type]
    )
    client = FakeClient()

    revert_common_ancestor_label(make_connection(graph, client))

    labeled_column = str(spec.feature_column_urn(COMMON_ANCESTOR_LABEL.name))
    terms = [
        mcp.aspect
        for mcp in graph.emitted
        if mcp.entityUrn == labeled_column and isinstance(mcp.aspect, GlossaryTermsClass)
    ]
    assert terms and terms[-1].terms == []
    schema = _schema_of(_upserted_dataset(client))
    assert COMMON_ANCESTOR_LABEL.name not in schema


def test_reverting_the_common_ancestor_label_restores_the_seeded_single_path_leak():
    client = FakeClient()
    result = revert_common_ancestor_label(make_connection(FakeGraph(), client))

    assert _sent_column_lineage(client)[spec.LEAKAGE_FEATURE] == [spec.LABEL_SOURCE_COLUMN]
    assert COMMON_ANCESTOR_LABEL.name not in _sent_column_lineage(client)
    assert result.leaking is False


def test_the_lookalike_column_feeds_the_feature_and_carries_no_term():
    """Only the upstream column's name changed from the clean baseline."""
    graph = FakeGraph()
    client = FakeClient()
    result = plant_label_lookalike(make_connection(graph, client))

    assert _sent_column_lineage(client)["applicant_income"] == [LOOKALIKE_COLUMN.name]
    assert result.upstream_columns == (LOOKALIKE_COLUMN.name,)
    assert result.leaking is False
    lookalike_column = str(spec.source_column_urn(LOOKALIKE_COLUMN.name))
    assert not any(
        mcp.entityUrn == lookalike_column and isinstance(mcp.aspect, GlossaryTermsClass)
        for mcp in graph.emitted
    )


def test_planting_the_lookalike_also_cuts_the_flagship_leak():
    """prior_default_flag's own unrelated leak must not ride along.

    Same reasoning as the common-ancestor scenario: one column changes.
    """
    client = FakeClient()
    plant_label_lookalike(make_connection(FakeGraph(), client))

    assert spec.LEAKAGE_FEATURE not in _sent_column_lineage(client)


def test_the_lookalike_column_is_added_to_the_source_tables_schema():
    client = FakeClient()
    plant_label_lookalike(make_connection(FakeGraph(), client))

    schema = _schema_of(_upserted_dataset(client))
    assert LOOKALIKE_COLUMN.name in schema
    assert schema[LOOKALIKE_COLUMN.name] == LOOKALIKE_COLUMN.native_type


def test_reverting_the_lookalike_restores_the_seeded_single_path_leak():
    client = FakeClient()
    result = revert_label_lookalike(make_connection(FakeGraph(), client))

    assert _sent_column_lineage(client)["applicant_income"] == ["income"]
    schema = _schema_of(_upserted_dataset(client))
    assert LOOKALIKE_COLUMN.name not in schema
    assert result.leaking is False
