from __future__ import annotations

import pytest

pytest.importorskip("langgraph")  # the agent is the optional [agent] extra

from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    StructuredPropertiesClass,
)

from janus.agent.graph import ApprovalRequiredError, run_agent
from janus.agent.pipeline import ScanReport
from janus.config import ScanConfig
from janus.models import Severity
from tests.conftest import (
    DEPLOYMENT_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    NOW_MS,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    active_incident,
    emitted_about_the_graph,
    lineage_result,
    make_connection,
)

FEATURE_TABLE = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
)
HOUR = 3_600_000


def _stale() -> tuple[FakeGraph, FakeClient]:
    """A live model 30h downstream of a stale table: one CRITICAL freshness finding."""
    graph = FakeGraph(
        {
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                name="Credit Risk v3", deployments=[DEPLOYMENT_URN], mlFeatures=[LEAK_FEATURE_URN]
            ),
            (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): (
                MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
            ),
        },
        timeseries={
            (TABLE_URN, OperationClass): OperationClass(
                timestampMillis=NOW_MS,
                operationType=OperationTypeClass.UPDATE,
                lastUpdatedTimestamp=NOW_MS - 30 * HOUR,
                actor="urn:li:corpuser:datahub",
            )
        },
    )
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc", "updateIncidentStatus": True}
    client = FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(LEAK_FEATURE_URN, 2),
            lineage_result(MODEL_URN, 3),
        ]
    )
    return graph, client


def _run(graph, client, approve) -> ScanReport:
    return run_agent(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        approve=approve,
        now_ms=NOW_MS,
        run_id="scan-fixed",
    )


# --------------------------------------------------------------------------
# The interrupt is a real gate: nothing is written until the caller approves
# --------------------------------------------------------------------------


def test_the_caller_sees_the_findings_before_anything_is_written():
    """The approve callback runs at the interrupt, with the graph still untouched."""
    graph, client = _stale()
    observed: dict = {}

    def approve(preview) -> bool:
        # The whole point of the gate: the preview describes a real finding, yet at
        # the instant the human is asked, not one aspect or incident has been sent.
        observed["severity"] = preview.writes[0].finding.severity
        observed["emitted_at_prompt"] = list(graph.emitted)
        observed["graphql_at_prompt"] = list(graph.graphql_calls)
        return False

    report = _run(graph, client, approve)

    assert observed["severity"] is Severity.CRITICAL
    assert observed["emitted_at_prompt"] == [], "an aspect was emitted before approval"
    assert observed["graphql_at_prompt"] == [], "an incident was raised before approval"
    # Declined: still nothing written, and the report says so.
    assert report.dry_run is True
    assert graph.emitted == []
    assert graph.graphql_calls == []
    assert client.entities.upserted == []


def test_approving_writes_the_incident_and_the_trust_score():
    graph, client = _stale()
    report = _run(graph, client, lambda _preview: True)

    assert report.dry_run is False
    # The incident landed on the table itself, typed FRESHNESS.
    assert len(graph.graphql_calls) == 1
    _, variables = graph.graphql_calls[0]
    assert variables["input"]["resourceUrn"] == TABLE_URN
    assert variables["input"]["type"] == "FRESHNESS"
    # The trust score was persisted as a structured property on the model.
    props = [
        mcp.aspect for mcp in graph.emitted if isinstance(mcp.aspect, StructuredPropertiesClass)
    ]
    final = {a.propertyUrn.rsplit(":", 1)[-1]: a.values for a in props[-1].properties}
    assert final["janus.trust_score"] == [35.0]


def test_omitting_approval_is_rejected_before_any_write():
    graph, client = _stale()
    with pytest.raises(ApprovalRequiredError, match="approval callback is required"):
        run_agent(
            make_connection(graph, client),
            ScanConfig(),
            table_urn=TABLE_URN,
            llm=None,
            now_ms=NOW_MS,
            run_id="scan-requires-approval",
        )
    assert graph.graphql_calls == []
    assert graph.emitted == []


def test_auto_approve_writes_only_when_explicitly_requested():
    graph, client = _stale()
    report = run_agent(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        approve=None,
        auto_approve=True,  # unattended / recorded-demo path
        now_ms=NOW_MS,
    )
    assert report.dry_run is False
    assert len(graph.graphql_calls) == 1


def _fresh() -> tuple[FakeGraph, FakeClient]:
    """The stale fixture with the table made fresh (1h lag against a 6h SLA)."""
    graph, client = _stale()
    graph._timeseries[(TABLE_URN, OperationClass)] = OperationClass(
        timestampMillis=NOW_MS,
        operationType=OperationTypeClass.UPDATE,
        lastUpdatedTimestamp=NOW_MS - 1 * HOUR,
        actor="urn:li:corpuser:datahub",
    )
    return graph, client


def test_a_clean_scan_prompts_because_it_may_still_have_a_recovery_to_write():
    """A clean scan is the recovery path, so it is gated like any other write.

    Nothing is stale here and no incident is open, so approving writes nothing.
    The point is that the approval is asked for at all: the same run against a
    graph that does hold a stale incident is what resolves it.
    """
    graph, client = _fresh()
    prompted = {"called": False}

    def approve(_preview) -> bool:
        prompted["called"] = True
        return True

    report = _run(graph, client, approve)

    assert report.clean is True
    assert prompted["called"] is True, "a clean scan can still write a recovery"
    assert emitted_about_the_graph(graph) == []
    assert graph.graphql_calls == []


def test_a_declined_clean_scan_resolves_nothing():
    """Declining a clean scan leaves the stale incident open. The gate is real."""
    graph, client = _fresh()
    active_incident(
        graph,
        resource_urn=TABLE_URN,
        incident_urn="urn:li:incident:stale-table",
        incident_type="FRESHNESS",
        title="Stale upstream data in ecommerce.public.loans_raw",
    )
    graph.emitted.clear()

    report = _run(graph, client, lambda _preview: False)

    assert report.clean is True
    assert [q for q, _ in graph.graphql_calls if "updateIncidentStatus" in q] == []


def test_an_approved_clean_scan_resolves_a_stale_incident_on_the_agent_path():
    """The agent path reconciles exactly as run_scan does.

    Before this, ``scan --review``/``--auto-approve`` routed a clean scan
    straight to decline, so a fixed problem's incident stayed ACTIVE forever on
    the one path a human had explicitly approved writes on.
    """
    graph, client = _fresh()
    incident_urn = "urn:li:incident:stale-table"
    active_incident(
        graph,
        resource_urn=TABLE_URN,
        incident_urn=incident_urn,
        incident_type="FRESHNESS",
        title="Stale upstream data in ecommerce.public.loans_raw",
    )
    graph.emitted.clear()

    report = _run(graph, client, lambda _preview: True)

    assert report.clean is True
    resolved = [
        variables for query, variables in graph.graphql_calls if "updateIncidentStatus" in query
    ]
    assert len(resolved) == 1
    assert resolved[0]["urn"] == incident_urn
