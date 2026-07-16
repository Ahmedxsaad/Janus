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

from modelguard.agent.graph import run_agent
from modelguard.agent.pipeline import ScanReport
from modelguard.config import ScanConfig
from modelguard.models import Severity
from tests.conftest import (
    DEPLOYMENT_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    NOW_MS,
    TABLE_URN,
    FakeClient,
    FakeGraph,
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
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
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
        mcp.aspect
        for mcp in graph.emitted
        if isinstance(mcp.aspect, StructuredPropertiesClass)
    ]
    final = {a.propertyUrn.rsplit(":", 1)[-1]: a.values for a in props[-1].properties}
    assert final["modelguard.trust_score"] == [35.0]


def test_auto_approve_writes_without_a_callback():
    graph, client = _stale()
    report = run_agent(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        approve=None,  # unattended / recorded-demo path
        now_ms=NOW_MS,
    )
    assert report.dry_run is False
    assert len(graph.graphql_calls) == 1


def test_a_clean_scan_never_prompts_and_writes_nothing():
    """A fresh table has nothing to approve: the interrupt is skipped entirely."""
    graph, client = _stale()
    # Make the table fresh (1h < 6h SLA); reuse the rest of the fixture.
    graph._timeseries[(TABLE_URN, OperationClass)] = OperationClass(
        timestampMillis=NOW_MS,
        operationType=OperationTypeClass.UPDATE,
        lastUpdatedTimestamp=NOW_MS - 1 * HOUR,
        actor="urn:li:corpuser:datahub",
    )
    prompted = {"called": False}

    def approve(_preview) -> bool:
        prompted["called"] = True
        return True

    report = _run(graph, client, approve)

    assert report.clean is True
    assert prompted["called"] is False, "a clean scan must not ask for approval"
    assert graph.emitted == []
    assert graph.graphql_calls == []
