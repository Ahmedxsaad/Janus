"""Incident writes must validate their inputs and never duplicate a finding."""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    AuditStampClass,
    BooleanTypeClass,
    IncidentInfoClass,
    IncidentStateClass,
    IncidentStatusClass,
    OtherSchemaClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
)

from modelguard.writeback.incidents import (
    INCIDENT_ENTITY_TYPES,
    INCIDENT_TYPES,
    IncidentWriteError,
    find_active_incident,
    raise_incident,
    resolve_incident,
)
from tests.conftest import FakeGraph, make_connection

DATASET = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
#: Incidents attach to the column, not to the model that consumes it.
COLUMN = f"urn:li:schemaField:({DATASET},prior_default_flag)"
MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
INCIDENT = "urn:li:incident:abc-123"
TITLE = "Target leakage in feature prior_default_flag"


def _incident_info(title: str, incident_type: str, state: str) -> IncidentInfoClass:
    stamp = AuditStampClass(time=0, actor="urn:li:corpuser:datahub")
    return IncidentInfoClass(
        type=incident_type,
        entities=[COLUMN],
        title=title,
        description="body",
        status=IncidentStatusClass(state=state, lastUpdated=stamp),
        created=stamp,
    )


def _graph_with_active_incident(title: str, incident_type: str = "FIELD") -> FakeGraph:
    """A column carrying one active incident, reachable via the IncidentOn edge."""
    return FakeGraph(
        aspects={
            # The column must resolve through its parent's schema, or the raise
            # is rejected before dedup is ever consulted.
            (DATASET, SchemaMetadataClass): _schema("prior_default_flag"),
            (INCIDENT, IncidentInfoClass): _incident_info(
                title, incident_type, IncidentStateClass.ACTIVE
            ),
        },
        related={COLUMN: [INCIDENT]},
        graphql_response={"raiseIncident": "urn:li:incident:new"},
    )


def test_incident_types_come_from_the_installed_model_not_the_plan():
    # The plan says COLUMN; the metadata model calls it FIELD. If this ever
    # flips, the leakage detector's incident type must change with it.
    assert "FIELD" in INCIDENT_TYPES
    assert "COLUMN" not in INCIDENT_TYPES
    assert {"OPERATIONAL", "FRESHNESS", "VOLUME", "SQL", "DATA_SCHEMA", "CUSTOM"} <= INCIDENT_TYPES


def test_unknown_incident_type_is_rejected_before_any_call():
    graph = FakeGraph()
    with pytest.raises(ValueError, match="not a DataHub incident type"):
        raise_incident(
            make_connection(graph),
            resource_urn=COLUMN,
            incident_type="COLUMN",
            title=TITLE,
            description="body",
            run_id="run-1",
        )
    assert graph.graphql_calls == []


def _schema(*columns: str) -> SchemaMetadataClass:
    return SchemaMetadataClass(
        schemaName="customer_features",
        platform="urn:li:dataPlatform:snowflake",
        version=0,
        hash="",
        platformSchema=OtherSchemaClass(rawSchema=""),
        fields=[
            SchemaFieldClass(
                fieldPath=c,
                type=SchemaFieldDataTypeClass(type=BooleanTypeClass()),
                nativeDataType="BOOLEAN",
            )
            for c in columns
        ],
    )


def _graph_with_column(*columns: str, **kwargs: object) -> FakeGraph:
    """A graph where the dataset carries the given columns in its schemaMetadata."""
    aspects = {(DATASET, SchemaMetadataClass): _schema(*columns)}
    aspects.update(kwargs.pop("aspects", {}))  # type: ignore[arg-type]
    return FakeGraph(aspects=aspects, **kwargs)  # type: ignore[arg-type]


def test_incident_on_a_missing_dataset_is_rejected():
    graph = FakeGraph(exists=False)
    with pytest.raises(ValueError, match="does not exist"):
        raise_incident(
            make_connection(graph),
            resource_urn=DATASET,
            incident_type="FRESHNESS",
            title=TITLE,
            description="body",
            run_id="run-1",
        )
    assert graph.graphql_calls == []


def test_a_column_is_resolved_through_its_parent_schema_not_graph_exists():
    # graph.exists() is False for every schemaField: columns are not standalone
    # entities. Resolving through schemaMetadata is what makes a column incident
    # possible at all.
    graph = _graph_with_column(
        "prior_default_flag", exists=False, graphql_response={"raiseIncident": INCIDENT}
    )
    result = raise_incident(
        make_connection(graph),
        resource_urn=COLUMN,
        incident_type="FIELD",
        title=TITLE,
        description="body",
        run_id="run-1",
    )
    assert result.created is True


def test_an_incident_on_a_column_that_is_not_in_the_schema_is_rejected():
    # Stricter than an entity-level existence check: this catches a typo'd column.
    graph = _graph_with_column("applicant_income", graphql_response={"raiseIncident": INCIDENT})
    with pytest.raises(ValueError, match="does not exist"):
        raise_incident(
            make_connection(graph),
            resource_urn=COLUMN,
            incident_type="FIELD",
            title=TITLE,
            description="body",
            run_id="run-1",
        )
    assert graph.graphql_calls == []


def test_first_raise_creates_and_stamps_the_run_id():
    graph = _graph_with_column("prior_default_flag", graphql_response={"raiseIncident": INCIDENT})
    result = raise_incident(
        make_connection(graph),
        resource_urn=COLUMN,
        incident_type="FIELD",
        title=TITLE,
        description="body",
        run_id="run-1",
    )
    assert result.created is True
    assert result.urn == INCIDENT

    _, variables = graph.graphql_calls[0]
    assert variables is not None
    payload = variables["input"]
    assert payload["resourceUrn"] == COLUMN
    assert payload["type"] == "FIELD"
    assert "Raised by ModelGuard run run-1." in payload["description"]


def test_second_raise_of_the_same_finding_reuses_the_open_incident():
    # This is the idempotency contract: scanning twice must not duplicate.
    graph = _graph_with_active_incident(TITLE)
    result = raise_incident(
        make_connection(graph),
        resource_urn=COLUMN,
        incident_type="FIELD",
        title=TITLE,
        description="body",
        run_id="run-2",
    )
    assert result == type(result)(urn=INCIDENT, created=False)
    assert graph.graphql_calls == []


def test_a_different_run_id_does_not_create_a_second_incident():
    # run_id is provenance, not part of the dedup key.
    graph = _graph_with_active_incident(TITLE)
    for run_id in ("run-2", "run-3", "run-4"):
        result = raise_incident(
            make_connection(graph),
            resource_urn=COLUMN,
            incident_type="FIELD",
            title=TITLE,
            description="body",
            run_id=run_id,
        )
        assert result.created is False
    assert graph.graphql_calls == []


def test_a_different_finding_type_on_the_same_resource_still_raises():
    graph = _graph_with_active_incident(TITLE)
    result = raise_incident(
        make_connection(graph),
        resource_urn=COLUMN,
        incident_type="FRESHNESS",
        title="Upstream table is stale",
        description="body",
        run_id="run-2",
    )
    assert result.created is True


def test_a_second_finding_of_the_same_type_still_raises():
    # Two features can leak on the same model. Deduplicating on type alone would
    # silently swallow the second finding, so the title must be part of the key.
    graph = _graph_with_active_incident(TITLE, incident_type="FIELD")
    result = raise_incident(
        make_connection(graph),
        resource_urn=COLUMN,
        incident_type="FIELD",
        title="Target leakage in feature applicant_income",
        description="body",
        run_id="run-2",
    )
    assert result.created is True
    assert graph.graphql_calls, "a distinct finding must reach the raiseIncident mutation"


def test_a_resolved_incident_does_not_suppress_a_new_one():
    # The relationship still points at a resolved incident, so the status on the
    # incident itself is what must be checked, not merely its presence.
    graph = FakeGraph(
        aspects={
            (DATASET, SchemaMetadataClass): _schema("prior_default_flag"),
            (INCIDENT, IncidentInfoClass): _incident_info(
                TITLE, "FIELD", IncidentStateClass.RESOLVED
            ),
        },
        related={COLUMN: [INCIDENT]},
        graphql_response={"raiseIncident": "urn:li:incident:new"},
    )
    result = raise_incident(
        make_connection(graph),
        resource_urn=COLUMN,
        incident_type="FIELD",
        title=TITLE,
        description="body",
        run_id="run-2",
    )
    assert result.created is True


def test_find_active_incident_returns_none_when_the_resource_has_no_incidents():
    assert find_active_incident(make_connection(FakeGraph()), COLUMN, "FIELD", TITLE) is None


def test_dedup_reads_the_incident_on_relationship_not_the_summary_aspect():
    # incidentsSummary is never written by a Quickstart GMS. A dedup that read it
    # would find nothing and duplicate every finding on every run.
    graph = _graph_with_active_incident(TITLE)
    assert find_active_incident(make_connection(graph), COLUMN, "FIELD", TITLE) == INCIDENT


def test_incident_entity_types_exclude_the_model():
    # GMS rejects an incident on an mlModel with a 500. The plan assumed the model
    # was the target; it cannot be. Findings go on the data, trust scores on the model.
    assert "mlModel" not in INCIDENT_ENTITY_TYPES
    assert {"dataset", "schemaField"} <= INCIDENT_ENTITY_TYPES


def test_an_incident_on_a_model_is_rejected_locally_not_by_the_server():
    graph = FakeGraph(graphql_response={"raiseIncident": INCIDENT})
    with pytest.raises(ValueError, match="cannot raise an incident on a mlModel"):
        raise_incident(
            make_connection(graph),
            resource_urn=MODEL,
            incident_type="FIELD",
            title=TITLE,
            description="body",
            run_id="run-1",
        )
    # Catching it here beats a 500 from GMS with a Java stack trace in it.
    assert graph.graphql_calls == []


def test_raise_incident_errors_when_the_mutation_returns_nothing():
    graph = _graph_with_column("prior_default_flag", graphql_response={})
    with pytest.raises(IncidentWriteError, match="returned no URN"):
        raise_incident(
            make_connection(graph),
            resource_urn=COLUMN,
            incident_type="FIELD",
            title=TITLE,
            description="body",
            run_id="run-1",
        )


def test_resolve_incident_reports_the_servers_answer():
    graph = FakeGraph(graphql_response={"updateIncidentStatus": True})
    assert resolve_incident(make_connection(graph), INCIDENT, "fixed upstream") is True

    graph = FakeGraph(graphql_response={"updateIncidentStatus": False})
    assert resolve_incident(make_connection(graph), INCIDENT, "nope") is False
