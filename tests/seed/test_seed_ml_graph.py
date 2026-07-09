"""The seeder must emit the exact aspects the detectors will later read.

These run offline against fakes. They assert on the aspects handed to DataHub,
not on the URNs the seeder returns, because the URNs are constants and would
match even if nothing was written (see the integration gate for the live check).
"""

from __future__ import annotations

from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DeploymentStatusClass,
    MLFeaturePropertiesClass,
    MLFeatureTablePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    MLPrimaryKeyPropertiesClass,
    MLTrainingRunPropertiesClass,
    SubTypesClass,
)

from modelguard.seed import graph_spec as spec
from modelguard.seed.seed_ml_graph import (
    TRAINING_RUN_SUBTYPE,
    seed_column_lineage,
    seed_deployment,
    seed_features,
    seed_ml_graph,
    seed_training_run,
    seed_warehouse_tables,
)
from tests.conftest import FakeClient, FakeGraph, make_connection


@pytest.fixture
def fakes() -> tuple[FakeGraph, FakeClient]:
    """A graph pre-loaded with the model aspect that seed_model reads back."""
    graph = FakeGraph(
        aspects={
            (str(spec.model_urn()), MLModelPropertiesClass): MLModelPropertiesClass(
                customProperties={},
                trainingJobs=[str(spec.training_run_urn())],
                deployments=[str(spec.deployment_urn())],
                groups=[str(spec.model_group_urn())],
            )
        }
    )
    return graph, FakeClient()


def _aspects_of(graph: FakeGraph, aspect_type: type) -> list[Any]:
    return [mcp.aspect for mcp in graph.emitted if isinstance(mcp.aspect, aspect_type)]


def _aspect_for(graph: FakeGraph, urn: str, aspect_type: type) -> Any:
    for mcp in graph.emitted:
        if mcp.entityUrn == urn and isinstance(mcp.aspect, aspect_type):
            return mcp.aspect
    raise AssertionError(f"no {aspect_type.__name__} was emitted for {urn}")


# --------------------------------------------------------------------------
# Warehouse tables and column lineage
# --------------------------------------------------------------------------


def test_both_warehouse_tables_are_created_with_full_schemas(fakes):
    graph, client = fakes
    seed_warehouse_tables(make_connection(graph, client))

    by_name = {d.urn.name: d for d in client.entities.upserted}
    assert set(by_name) == {spec.SOURCE_TABLE, spec.FEATURE_TABLE}

    source_fields = {f.field_path for f in by_name[spec.SOURCE_TABLE].schema}
    assert source_fields == {c.name for c in spec.SOURCE_COLUMNS}
    # Without a schema there are no schemaField URNs, and column lineage cannot attach.
    assert spec.LABEL_SOURCE_COLUMN in source_fields

    feature_fields = {f.field_path for f in by_name[spec.FEATURE_TABLE].schema}
    assert feature_fields == {c.name for c in spec.FEATURE_COLUMNS}
    # Every column lineage edge must land on a column that actually exists.
    assert set(spec.COLUMN_LINEAGE) <= feature_fields


def test_column_lineage_carries_the_label_into_the_leakage_feature(fakes):
    graph, client = fakes
    seed_column_lineage(make_connection(graph, client))

    assert len(client.lineage.edges) == 1
    edge = client.lineage.edges[0]
    assert edge["upstream"] == str(spec.source_table_urn())
    assert edge["downstream"] == str(spec.feature_table_dataset_urn())
    # The edge P1 exists to find.
    assert edge["column_lineage"][spec.LEAKAGE_FEATURE] == [spec.LABEL_SOURCE_COLUMN]


# --------------------------------------------------------------------------
# ML features
# --------------------------------------------------------------------------


def test_features_point_at_the_dataset_and_name_their_column(fakes):
    graph, client = fakes
    seed_features(make_connection(graph, client))

    leakage = _aspect_for(
        graph, str(spec.feature_urn(spec.LEAKAGE_FEATURE)), MLFeaturePropertiesClass
    )

    # sources is dataset-granular: a schemaField URN here would be a dangling edge.
    assert leakage.sources == [str(spec.feature_table_dataset_urn())]
    for source in leakage.sources:
        assert source.startswith("urn:li:dataset:")

    # The exact column lives in customProperties; this is what P1 traverses from.
    assert leakage.customProperties[spec.SOURCE_COLUMN_PROPERTY] == str(
        spec.feature_column_urn(spec.LEAKAGE_FEATURE)
    )


def test_every_declared_feature_is_emitted_once(fakes):
    graph, client = fakes
    returned = seed_features(make_connection(graph, client))

    emitted = _aspects_of(graph, MLFeaturePropertiesClass)
    assert len(emitted) == len(spec.MODEL_FEATURES)
    assert len(returned) == len(set(returned)) == len(spec.MODEL_FEATURES)


def test_the_primary_key_is_emitted_and_sources_the_dataset(fakes):
    graph, client = fakes
    seed_features(make_connection(graph, client))

    key = _aspect_for(graph, str(spec.primary_key_urn()), MLPrimaryKeyPropertiesClass)
    assert key.sources == [str(spec.feature_table_dataset_urn())]


def test_the_feature_table_lists_its_features_and_key(fakes):
    graph, client = fakes
    features = seed_features(make_connection(graph, client))

    table = _aspect_for(graph, str(spec.feature_table_urn()), MLFeatureTablePropertiesClass)
    assert sorted(table.mlFeatures) == sorted(features)
    assert table.mlPrimaryKeys == [str(spec.primary_key_urn())]


# --------------------------------------------------------------------------
# Training run and deployment
# --------------------------------------------------------------------------


def test_the_training_run_declares_the_feature_table_as_its_input(fakes):
    graph, client = fakes
    urn = seed_training_run(make_connection(graph, client))

    inputs = _aspect_for(graph, urn, DataProcessInstanceInputClass)
    # The schema-drift detector (P3) asks the run what it trained on.
    assert inputs.inputs == [str(spec.feature_table_dataset_urn())]

    subtype = _aspect_for(graph, urn, SubTypesClass)
    assert subtype.typeNames == [TRAINING_RUN_SUBTYPE]


def test_the_training_run_records_its_metrics_and_params(fakes):
    graph, client = fakes
    urn = seed_training_run(make_connection(graph, client))

    run = _aspect_for(graph, urn, MLTrainingRunPropertiesClass)
    assert {m.name: m.value for m in run.trainingMetrics} == spec.TRAINING_METRICS
    assert {p.name: p.value for p in run.hyperParams} == spec.HYPER_PARAMS


def test_the_deployment_is_live(fakes):
    graph, client = fakes
    urn = seed_deployment(make_connection(graph, client))

    deployment = _aspect_for(graph, urn, MLModelDeploymentPropertiesClass)
    # Blast-radius severity keys off a model actually being served.
    assert deployment.status == DeploymentStatusClass.IN_SERVICE


# --------------------------------------------------------------------------
# The model, and the ordering hazard around mlFeatures
# --------------------------------------------------------------------------


def test_the_model_gets_its_features_attached_after_the_upsert(fakes):
    graph, client = fakes
    result = seed_ml_graph(make_connection(graph, client))

    # MLModel has no mlFeatures API, so the seeder upserts and then re-emits the
    # aspect. If it re-emitted before the upsert, the upsert would wipe the
    # features: assert the final emitted aspect carries them.
    final = _aspect_for(graph, result.model, MLModelPropertiesClass)
    assert sorted(final.mlFeatures) == sorted(result.features)
    # The read-modify-write must preserve what the upsert wrote.
    assert final.trainingJobs == [result.training_run]
    assert final.deployments == [result.deployment]


def test_seeding_a_model_whose_aspect_never_landed_fails_loudly(fakes):
    _, client = fakes
    empty_graph = FakeGraph()  # get_aspect returns None
    with pytest.raises(RuntimeError, match="mlModelProperties missing"):
        seed_ml_graph(make_connection(empty_graph, client))


def test_the_full_seed_reports_the_urns_it_wrote(fakes):
    graph, client = fakes
    result = seed_ml_graph(make_connection(graph, client))

    assert result.model == str(spec.model_urn())
    assert result.features == tuple(str(spec.feature_urn(f)) for f in spec.MODEL_FEATURES)
    # Seven non-feature entities, plus one line per feature.
    assert len(result.as_lines()) == 7 + len(spec.MODEL_FEATURES)
