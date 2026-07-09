"""Incident writes must validate their inputs and never duplicate a finding."""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    AuditStampClass,
    IncidentInfoClass,
    IncidentsSummaryClass,
    IncidentStateClass,
    IncidentStatusClass,
    IncidentSummaryDetailsClass,
)

from modelguard.writeback.incidents import (
    INCIDENT_TYPES,
    IncidentWriteError,
    find_active_incident,
    raise_incident,
    resolve_incident,
)
from tests.conftest import FakeGraph, make_connection

MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
INCIDENT = "urn:li:incident:abc-123"
TITLE = "Target leakage in feature prior_default_flag"


def _incident_info(title: str, incident_type: str, state: str) -> IncidentInfoClass:
    stamp = AuditStampClass(time=0, actor="urn:li:corpuser:datahub")
    return IncidentInfoClass(
        type=incident_type,
        entities=[MODEL],
        title=title,
        description="body",
        status=IncidentStatusClass(state=state, lastUpdated=stamp),
        created=stamp,
    )


def _graph_with_active_incident(title: str, incident_type: str = "FIELD") -> FakeGraph:
    summary = IncidentsSummaryClass(
        activeIncidentDetails=[
            IncidentSummaryDetailsClass(urn=INCIDENT, type=incident_type, createdAt=0)
        ]
    )
    return FakeGraph(
        aspects={
            (MODEL, IncidentsSummaryClass): summary,
            (INCIDENT, IncidentInfoClass): _incident_info(
                title, incident_type, IncidentStateClass.ACTIVE
            ),
        },
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
            resource_urn=MODEL,
            incident_type="COLUMN",
            title=TITLE,
            description="body",
            run_id="run-1",
        )
    assert graph.graphql_calls == []


def test_incident_on_a_missing_resource_is_rejected():
    graph = FakeGraph(exists=False)
    with pytest.raises(ValueError, match="does not exist"):
        raise_incident(
            make_connection(graph),
            resource_urn=MODEL,
            incident_type="FIELD",
            title=TITLE,
            description="body",
            run_id="run-1",
        )
    assert graph.graphql_calls == []


def test_first_raise_creates_and_stamps_the_run_id():
    graph = FakeGraph(graphql_response={"raiseIncident": INCIDENT})
    result = raise_incident(
        make_connection(graph),
        resource_urn=MODEL,
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
    assert payload["resourceUrn"] == MODEL
    assert payload["type"] == "FIELD"
    assert "Raised by ModelGuard run run-1." in payload["description"]


def test_second_raise_of_the_same_finding_reuses_the_open_incident():
    # This is the idempotency contract: scanning twice must not duplicate.
    graph = _graph_with_active_incident(TITLE)
    result = raise_incident(
        make_connection(graph),
        resource_urn=MODEL,
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
            resource_urn=MODEL,
            incident_type="FIELD",
            title=TITLE,
            description="body",
            run_id=run_id,
        )
        assert result.created is False
    assert graph.graphql_calls == []


def test_a_different_finding_on_the_same_resource_still_raises():
    graph = _graph_with_active_incident(TITLE)
    result = raise_incident(
        make_connection(graph),
        resource_urn=MODEL,
        incident_type="FRESHNESS",
        title="Upstream table is stale",
        description="body",
        run_id="run-2",
    )
    assert result.created is True


def test_a_resolved_incident_does_not_suppress_a_new_one():
    summary = IncidentsSummaryClass(
        resolvedIncidentDetails=[
            IncidentSummaryDetailsClass(urn=INCIDENT, type="FIELD", createdAt=0, resolvedAt=1)
        ]
    )
    graph = FakeGraph(
        aspects={(MODEL, IncidentsSummaryClass): summary},
        graphql_response={"raiseIncident": "urn:li:incident:new"},
    )
    result = raise_incident(
        make_connection(graph),
        resource_urn=MODEL,
        incident_type="FIELD",
        title=TITLE,
        description="body",
        run_id="run-2",
    )
    assert result.created is True


def test_find_active_incident_returns_none_when_the_resource_has_no_summary():
    assert find_active_incident(make_connection(FakeGraph()), MODEL, "FIELD", TITLE) is None


def test_raise_incident_errors_when_the_mutation_returns_nothing():
    graph = FakeGraph(graphql_response={})
    with pytest.raises(IncidentWriteError, match="returned no URN"):
        raise_incident(
            make_connection(graph),
            resource_urn=MODEL,
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
