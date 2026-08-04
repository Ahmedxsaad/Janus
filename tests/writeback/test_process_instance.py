"""ModelGuard's own scan, as the process run the graph holds it in (T-04).

Driven through ``run_scan`` rather than by calling the emitter directly. What the
task promises is not that the module can build a ``dataProcessInstance``, it is
that a scan's inputs match what it read and its outputs match what it wrote, and
only the real loop can be wrong about that.
"""

from __future__ import annotations

from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DataProcessInstanceOutputClass,
    DataProcessInstancePropertiesClass,
    DataProcessInstanceRelationshipsClass,
    DataProcessInstanceRunEventClass,
    DataProcessRunStatusClass,
    DeploymentStatusClass,
    GlobalTagsClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    OwnershipClass,
    RunResultTypeClass,
)

from modelguard.agent.pipeline import run_scan
from modelguard.config import ScanConfig
from modelguard.writeback.process_instance import (
    FLOW_ENV,
    JOB_ID,
    ORCHESTRATOR,
)
from tests.conftest import (
    DEPLOYMENT_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    NOW_MS,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    active_incident,
    lineage_result,
    make_connection,
)

FEATURE_TABLE = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
)
HOUR = 3_600_000
INCIDENT_URN = "urn:li:incident:abc"


def _graph(lag_hours: float) -> FakeGraph:
    """A model fed by one table that went stale ``lag_hours`` ago."""
    return FakeGraph(
        {
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                name="Credit Risk v3",
                deployments=[DEPLOYMENT_URN],
                mlFeatures=[LEAK_FEATURE_URN],
            ),
            (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): (
                MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
            ),
        },
        timeseries={
            (TABLE_URN, OperationClass): OperationClass(
                timestampMillis=NOW_MS,
                operationType=OperationTypeClass.UPDATE,
                lastUpdatedTimestamp=NOW_MS - int(lag_hours * HOUR),
                actor="urn:li:corpuser:datahub",
            )
        },
    )


def _client() -> FakeClient:
    return FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(LEAK_FEATURE_URN, 2),
            lineage_result(MODEL_URN, 3),
        ]
    )


def _scan(graph: FakeGraph, client: FakeClient, **kwargs: Any) -> Any:
    return run_scan(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        now_ms=NOW_MS,
        **kwargs,
    )


def _aspects(graph: FakeGraph, aspect_type: type) -> list[Any]:
    return [mcp.aspect for mcp in graph.emitted if isinstance(mcp.aspect, aspect_type)]


def _run_urns(graph: FakeGraph) -> set[str]:
    """Every process-instance URN this graph was sent an aspect for."""
    return {
        str(mcp.entityUrn)
        for mcp in graph.emitted
        if str(mcp.entityUrn).startswith("urn:li:dataProcessInstance:")
    }


def _only_run_urn(graph: FakeGraph) -> str:
    urns = _run_urns(graph)
    assert len(urns) == 1, f"expected one process instance, got {urns}"
    return urns.pop()


# --------------------------------------------------------------------------
# The entity: one per run, and the same one on a replay
# --------------------------------------------------------------------------


def test_a_scan_emits_exactly_one_process_instance_under_the_agents_job():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(graph, client, run_id="scan-fixed")

    run_urn = _only_run_urn(graph)
    flow_urns = {
        str(mcp.entityUrn)
        for mcp in graph.emitted
        if str(mcp.entityUrn).startswith("urn:li:dataFlow")
    }
    job_urns = {
        str(mcp.entityUrn)
        for mcp in graph.emitted
        if str(mcp.entityUrn).startswith("urn:li:dataJob")
    }
    assert flow_urns == {f"urn:li:dataFlow:({ORCHESTRATOR},{ORCHESTRATOR},{FLOW_ENV})"}
    assert job_urns == {
        f"urn:li:dataJob:(urn:li:dataFlow:({ORCHESTRATOR},{ORCHESTRATOR},{FLOW_ENV}),{JOB_ID})"
    }

    # The instance points back at the job, which is what makes the run reachable
    # from the agent's own pipeline in the UI rather than floating unattached.
    relationships = [
        mcp.aspect
        for mcp in graph.emitted
        if str(mcp.entityUrn) == run_urn
        and isinstance(mcp.aspect, DataProcessInstanceRelationshipsClass)
    ]
    assert relationships and relationships[0].parentTemplate == job_urns.pop()


def test_two_runs_of_the_same_run_id_land_on_one_process_instance():
    """The URN is a guid over the run id, so a replay updates rather than duplicates."""
    first, second = _graph(30.0), _graph(30.0)
    first.graphql_response = {"raiseIncident": INCIDENT_URN}
    second.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(first, _client(), run_id="scan-fixed")
    _scan(second, _client(), run_id="scan-fixed")

    assert _only_run_urn(first) == _only_run_urn(second)


def test_two_different_runs_land_on_two_process_instances():
    first, second = _graph(30.0), _graph(30.0)
    first.graphql_response = {"raiseIncident": INCIDENT_URN}
    second.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(first, _client(), run_id="scan-one")
    _scan(second, _client(), run_id="scan-two")

    assert _only_run_urn(first) != _only_run_urn(second)


def test_the_instance_is_named_after_the_run_a_reader_sees_on_an_incident():
    """An incident's footer names the run id, so the entity must carry it too."""
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(graph, client, run_id="scan-fixed")

    properties = _aspects(graph, DataProcessInstancePropertiesClass)
    assert [entry.name for entry in properties] == ["scan-fixed"]


# --------------------------------------------------------------------------
# Inputs and outputs
# --------------------------------------------------------------------------


def test_the_run_reports_the_entities_it_read_and_the_entities_it_wrote():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": INCIDENT_URN}

    report = _scan(graph, client, run_id="scan-fixed")

    inputs = _aspects(graph, DataProcessInstanceInputClass)
    outputs = _aspects(graph, DataProcessInstanceOutputClass)
    assert len(inputs) == 1 and len(outputs) == 1

    # Read: the table the scan targeted, and the model the finding named as at
    # risk, which the scan only learned about by walking lineage.
    assert TABLE_URN in inputs[0].inputs
    assert MODEL_URN in inputs[0].inputs

    # Written: the table the incident and the assertion landed on, and the model
    # that was tagged, flagged and scored.
    assert TABLE_URN in outputs[0].outputs
    assert MODEL_URN in outputs[0].outputs

    # The incident, the assertion and the impact report are real writes, and they
    # are still not in the aspect: DataHub's model lets a run name datasets and
    # models only. Asserted rather than assumed, because emitting one is a 422
    # from a live GMS and the fake would happily have stored it.
    write = report.writes[0]
    assert write.incident is not None and write.assertion is not None
    for urn in (write.incident.urn, write.assertion.urn, write.documents[0].urn):
        assert urn not in outputs[0].outputs


def test_a_recovery_only_scan_still_reports_the_asset_it_cleared():
    """The clean scan is exactly the one whose outputs would otherwise be empty."""
    graph, client = _graph(lag_hours=1.0), _client()  # fresh again
    active_incident(
        graph,
        resource_urn=TABLE_URN,
        incident_urn="urn:li:incident:orphaned-freshness",
        incident_type="FRESHNESS",
        title="Stale upstream data in ecommerce.public.loans_raw",
    )
    graph.set_aspect(MODEL_URN, GlobalTagsClass(tags=[]))
    graph.emitted.clear()

    report = _scan(graph, client, run_id="scan-recovery")

    assert report.clean, "the table is fresh, so this run found nothing"
    outputs = _aspects(graph, DataProcessInstanceOutputClass)
    assert len(outputs) == 1
    # The table whose incident was resolved, and the model whose at-risk tag,
    # risk flags and trust score the same recovery cleared.
    assert sorted(outputs[0].outputs) == sorted([TABLE_URN, MODEL_URN])


def test_a_scan_that_wrote_nothing_at_all_emits_no_output_aspect():
    """No outputs is different from an empty list, and only one of them is true here."""
    graph, client = _graph(lag_hours=1.0), _client()

    report = _scan(graph, client, run_id="scan-clean")

    assert report.clean
    assert _aspects(graph, DataProcessInstanceOutputClass) == []
    assert _run_urns(graph), "a clean scan is still a run, and still says so"


# --------------------------------------------------------------------------
# How a run ends
# --------------------------------------------------------------------------


def _events(graph: FakeGraph) -> list[DataProcessInstanceRunEventClass]:
    return _aspects(graph, DataProcessInstanceRunEventClass)


def test_a_completed_scan_writes_a_started_then_a_successful_complete():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(graph, client, run_id="scan-fixed")

    events = _events(graph)
    assert [event.status for event in events] == [
        DataProcessRunStatusClass.STARTED,
        DataProcessRunStatusClass.COMPLETE,
    ]
    assert events[1].result is not None
    assert events[1].result.type == RunResultTypeClass.SUCCESS


def test_a_scan_that_dies_mid_write_records_a_failure_and_reraises():
    """Silence and success read the same in the graph; a crash must not be silent."""
    graph, client = _graph(30.0), _client()
    # No raiseIncident in the response, so the very first write of the run fails.
    graph.graphql_response = {}

    with pytest.raises(Exception):  # noqa: B017 - the type is the writeback's, not this test's concern
        _scan(graph, client, run_id="scan-doomed")

    events = _events(graph)
    assert [event.status for event in events] == [
        DataProcessRunStatusClass.STARTED,
        DataProcessRunStatusClass.COMPLETE,
    ]
    assert events[1].result is not None
    assert events[1].result.type == RunResultTypeClass.FAILURE


def test_the_run_events_carry_a_message_id_derived_from_the_run():
    """The message id is what lets a replayed event overwrite itself, not stack."""
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(graph, client, run_id="scan-fixed")

    assert [event.messageId for event in _events(graph)] == [
        "scan-fixed-started",
        "scan-fixed-complete",
    ]


def test_a_dry_run_leaves_no_trace_of_a_run_in_the_graph():
    graph, client = _graph(30.0), _client()

    _scan(graph, client, dry_run=True, run_id="scan-preview")

    assert graph.emitted == [], "a preview that catalogued itself would be a write"


# --------------------------------------------------------------------------
# What the flow and the job must not overwrite
# --------------------------------------------------------------------------


def test_the_flow_and_job_never_send_an_empty_tag_or_owner_list():
    """GlobalTags and ownership are whole-list upserts (writeback rule 9).

    The SDK's ``generate_mcp`` yields both whether or not anything was set, so
    emitting them verbatim would strip a tag or an owner somebody put on
    ModelGuard's own flow, on every poll of ``modelguard watch``.
    """
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": INCIDENT_URN}

    _scan(graph, client, run_id="scan-fixed")

    own = ("urn:li:dataFlow", "urn:li:dataJob")
    for mcp in graph.emitted:
        if str(mcp.entityUrn).startswith(own):
            assert not isinstance(mcp.aspect, GlobalTagsClass | OwnershipClass)
