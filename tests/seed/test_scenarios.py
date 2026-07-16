from __future__ import annotations

from typing import Any

import pytest
from datahub.metadata.schema_classes import OperationClass, OperationTypeClass

from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import (
    SCENARIO_PROPERTY,
    SCHEMA_DRIFT,
    STALE_SOURCE,
    plant_schema_drift,
    plant_stale_source,
    revert_schema_drift,
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
