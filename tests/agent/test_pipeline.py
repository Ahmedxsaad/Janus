from __future__ import annotations

from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    AssertionInfoClass,
    AssertionRunEventClass,
    DeploymentStatusClass,
    GlobalTagsClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    StructuredPropertiesClass,
    StructuredPropertyDefinitionClass,
)

from modelguard.agent.pipeline import ScanReport, new_run_id, run_scan
from modelguard.config import ScanConfig
from modelguard.models import Severity
from modelguard.writeback.properties import RISK_FLAGS, RUN_ID
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


def _graph(lag_hours: float) -> FakeGraph:
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


def _scan(graph: FakeGraph, client: FakeClient, **kwargs: Any) -> ScanReport:
    return run_scan(
        make_connection(graph, client),
        TABLE_URN,
        ScanConfig(),
        llm=None,
        now_ms=NOW_MS,
        **kwargs,
    )


def _aspects_of(graph: FakeGraph, aspect_type: type) -> list[Any]:
    return [mcp.aspect for mcp in graph.emitted if isinstance(mcp.aspect, aspect_type)]


# --------------------------------------------------------------------------
# The approval gate
# --------------------------------------------------------------------------


def test_a_dry_run_detects_and_explains_but_writes_absolutely_nothing():
    graph, client = _graph(30.0), _client()
    report = _scan(graph, client, dry_run=True)

    assert report.finding is not None
    assert report.finding.severity is Severity.CRITICAL
    assert report.narrative is not None

    assert graph.emitted == [], "a dry run must not emit a single aspect"
    assert graph.graphql_calls == [], "a dry run must not raise an incident"
    assert client.entities.upserted == [], "a dry run must not upsert a tag or a document"


def test_a_dry_run_still_renders_the_assertion_it_would_have_written():
    report = _scan(_graph(30.0), _client(), dry_run=True)
    assert "type: freshness" in report.assertion_yaml
    assert TABLE_URN in report.assertion_yaml


# --------------------------------------------------------------------------
# A healthy graph is silent
# --------------------------------------------------------------------------


def test_scanning_a_fresh_table_writes_nothing_and_reports_clean():
    graph, client = _graph(1.0), _client()
    report = _scan(graph, client)

    assert report.clean is True
    assert report.finding is None
    assert graph.emitted == []
    assert graph.graphql_calls == []
    assert client.entities.upserted == []


# --------------------------------------------------------------------------
# The full loop
# --------------------------------------------------------------------------


def test_a_stale_table_raises_one_incident_on_the_table_itself():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client)

    assert report.incident is not None
    assert report.incident.created is True
    assert len(graph.graphql_calls) == 1

    _, variables = graph.graphql_calls[0]
    assert variables["input"]["resourceUrn"] == TABLE_URN
    assert variables["input"]["type"] == "FRESHNESS"
    assert variables["input"]["title"] == "Stale upstream data in ecommerce.public.loans_raw"


def test_the_incident_body_carries_the_run_id_and_the_measured_lag():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client, run_id="scan-fixed")
    _, variables = graph.graphql_calls[0]
    description = variables["input"]["description"]

    assert "scan-fixed" in description
    assert "30.0 hours ago" in description
    assert report.run_id == "scan-fixed"


def test_the_at_risk_model_is_tagged_and_flagged():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client, run_id="scan-fixed")

    assert report.tagged_models == (MODEL_URN,)
    tags = _aspects_of(graph, GlobalTagsClass)
    assert [association.tag for association in tags[0].tags] == ["urn:li:tag:model-at-risk"]

    properties = _aspects_of(graph, StructuredPropertiesClass)
    assigned = {
        assignment.propertyUrn.rsplit(":", 1)[-1]: assignment.values
        for assignment in properties[0].properties
    }
    assert assigned[RISK_FLAGS] == ["upstream-freshness"]
    assert assigned[RUN_ID] == ["scan-fixed"]


def test_no_trust_score_is_invented_before_the_detector_that_computes_it_exists():
    """Phase 1 records the risk flag. Writing a made-up score would be fabrication."""
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    _scan(graph, client)

    properties = _aspects_of(graph, StructuredPropertiesClass)
    written = {a.propertyUrn.rsplit(":", 1)[-1] for a in properties[0].properties}
    assert "modelguard.trust_score" not in written


def test_the_guarding_assertion_and_its_measured_result_are_both_written():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client)

    assert report.assertion is not None
    assert report.assertion_result == "FAILURE"
    assert len(_aspects_of(graph, AssertionInfoClass)) == 1

    events = _aspects_of(graph, AssertionRunEventClass)
    assert len(events) == 1
    assert events[0].result.actualAggValue == pytest.approx(30.0)


def test_the_impact_report_is_published_against_the_model():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client)

    assert len(report.documents) == 1
    assert report.documents[0].model_urn == MODEL_URN
    documents = [e for e in client.entities.upserted if hasattr(e, "text")]
    assert len(documents) == 1
    assert "Credit Risk v3" in documents[0].text


def test_property_definitions_are_emitted_before_any_value_is_assigned():
    """Assigning a value to an undefined property is rejected by DataHub."""
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    _scan(graph, client)

    kinds = [type(mcp.aspect) for mcp in graph.emitted]
    assert kinds.index(StructuredPropertyDefinitionClass) < kinds.index(StructuredPropertiesClass)


def test_the_assertion_entity_is_written_before_its_run_event():
    """A run event referencing an assertion that does not exist yet is dangling."""
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    _scan(graph, client)

    kinds = [type(mcp.aspect) for mcp in graph.emitted]
    assert kinds.index(AssertionInfoClass) < kinds.index(AssertionRunEventClass)


# --------------------------------------------------------------------------
# A stale table nobody consumes
# --------------------------------------------------------------------------


def test_a_stale_table_with_no_model_downstream_warns_and_tags_nothing():
    graph = _graph(30.0)
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    client = FakeClient(lineage_results=[lineage_result(FEATURE_TABLE, 1)])

    report = _scan(graph, client)

    assert report.finding is not None
    assert report.finding.severity is Severity.MEDIUM
    assert report.tagged_models == ()
    assert report.documents == ()
    assert any("no model consumes it" in warning for warning in report.warnings)


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_every_run_gets_a_distinct_run_id():
    assert new_run_id() != new_run_id()
    assert new_run_id().startswith("scan-")
