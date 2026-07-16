from __future__ import annotations

from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    AssertionInfoClass,
    AssertionRunEventClass,
    BooleanTypeClass,
    DeploymentStatusClass,
    GlobalTagsClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    OtherSchemaClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StructuredPropertiesClass,
    StructuredPropertyDefinitionClass,
)

from modelguard.agent.pipeline import FindingWrites, ScanReport, new_run_id, run_scan
from modelguard.config import ScanConfig
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from modelguard.models import FindingType, Severity
from modelguard.writeback.properties import RISK_FLAGS, RUN_ID
from tests.conftest import (
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    LABEL_COLUMN_URN,
    LABEL_TERM_URN,
    LEAK_COLUMN_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    NOW_MS,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    column_path,
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
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        now_ms=NOW_MS,
        **kwargs,
    )


def _only(report: ScanReport) -> FindingWrites:
    """The single finding's writes. A freshness scan of one table produces one."""
    assert len(report.writes) == 1, f"expected one finding, got {len(report.writes)}"
    return report.writes[0]


def _aspects_of(graph: FakeGraph, aspect_type: type) -> list[Any]:
    return [mcp.aspect for mcp in graph.emitted if isinstance(mcp.aspect, aspect_type)]


# --------------------------------------------------------------------------
# The approval gate
# --------------------------------------------------------------------------


def test_a_dry_run_detects_and_explains_but_writes_absolutely_nothing():
    graph, client = _graph(30.0), _client()
    report = _scan(graph, client, dry_run=True)

    write = _only(report)
    assert write.finding.severity is Severity.CRITICAL
    assert write.narrative is not None

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
    assert report.writes == ()
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

    assert _only(report).incident is not None
    assert _only(report).incident.created is True
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

    assert _only(report).tagged_models == (MODEL_URN,)
    tags = _aspects_of(graph, GlobalTagsClass)
    assert [association.tag for association in tags[0].tags] == ["urn:li:tag:model-at-risk"]

    properties = _aspects_of(graph, StructuredPropertiesClass)
    assigned = {
        assignment.propertyUrn.rsplit(":", 1)[-1]: assignment.values
        for assignment in properties[0].properties
    }
    assert assigned[RISK_FLAGS] == ["upstream-freshness"]
    assert assigned[RUN_ID] == ["scan-fixed"]


def test_the_at_risk_model_gets_a_trust_score_and_band():
    """P4: a model a scan finds a risk about is scored, worst risks driving it down."""
    graph, client = _graph(30.0), _client()  # live, 30h stale, unowned
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    report = _scan(graph, client)

    assert len(report.trust) == 1
    trust = report.trust[0]
    assert trust.model_urn == MODEL_URN
    # 100 - 40 (upstream failure) - 15 (freshness at/over SLA) - 10 (unowned) = 35.
    assert trust.score.value == 35
    assert str(trust.score.band) == "at-risk"

    # The score and band land on the model as structured properties, merged
    # alongside the risk flags an earlier per-finding write set.
    properties = _aspects_of(graph, StructuredPropertiesClass)
    final = {a.propertyUrn.rsplit(":", 1)[-1]: a.values for a in properties[-1].properties}
    assert final["modelguard.trust_score"] == [35.0]
    assert final["modelguard.trust_band"] == ["at-risk"]
    assert final["modelguard.risk_flags"] == ["upstream-freshness"]


def test_the_guarding_assertion_and_its_measured_result_are_both_written():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client)

    assert _only(report).assertion is not None
    assert _only(report).assertion_result == "FAILURE"
    assert len(_aspects_of(graph, AssertionInfoClass)) == 1

    events = _aspects_of(graph, AssertionRunEventClass)
    assert len(events) == 1
    assert events[0].result.actualAggValue == pytest.approx(30.0)


def test_the_impact_report_is_published_against_the_model():
    graph, client = _graph(30.0), _client()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = _scan(graph, client)

    assert len(_only(report).documents) == 1
    assert _only(report).documents[0].model_urn == MODEL_URN
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

    write = _only(report)
    assert write.finding.severity is Severity.MEDIUM
    assert write.tagged_models == ()
    assert write.documents == ()
    assert any("no model consumes it" in warning for warning in report.warnings)


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_every_run_gets_a_distinct_run_id():
    assert new_run_id() != new_run_id()
    assert new_run_id().startswith("scan-")


# --------------------------------------------------------------------------
# One model, two findings in the same scan: nothing may overwrite the other
# --------------------------------------------------------------------------


def _dual_target_scan(*, lag_hours: float = 30.0) -> tuple[FakeGraph, FakeClient]:
    """The seeded demo shape.

    One model is both downstream of a stale table and independently leaking
    its own label, scanned with --table and --model together.
    """
    graph = _graph(lag_hours)
    graph.set_aspect(
        LEAK_FEATURE_URN,
        MLFeaturePropertiesClass(
            sources=[FEATURE_TABLE_URN],
            customProperties={SOURCE_COLUMN_PROPERTY: LEAK_COLUMN_URN},
        ),
    )
    graph.set_aspect(
        LABEL_COLUMN_URN,
        GlossaryTermsClass(
            terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
        ),
    )
    # raise_incident resolves a schemaField target through the parent dataset's
    # schemaMetadata, never through graph.exists() (D-017).
    graph.set_aspect(
        FEATURE_TABLE_URN,
        SchemaMetadataClass(
            schemaName="customer_features",
            platform="urn:li:dataPlatform:snowflake",
            version=0,
            hash="",
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=[
                SchemaFieldClass(
                    fieldPath="prior_default_flag",
                    type=SchemaFieldDataTypeClass(type=BooleanTypeClass()),
                    nativeDataType="BOOLEAN",
                )
            ],
        ),
    )

    client = FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(LEAK_FEATURE_URN, 2),
            lineage_result(MODEL_URN, 3),
        ],
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ]
        },
    )
    return graph, client


def test_a_model_hit_by_two_findings_keeps_both_risk_flags():
    """Reproduces the overwrite this design must avoid.

    assign_properties replaces RISK_FLAGS, so the second finding's write must
    union with the first's, not erase it.
    """
    graph, client = _dual_target_scan()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = run_scan(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert len(report.writes) == 2
    properties = _aspects_of(graph, StructuredPropertiesClass)
    final_flags = {
        assignment.propertyUrn.rsplit(":", 1)[-1]: assignment.values
        for assignment in properties[-1].properties
    }[RISK_FLAGS]
    assert set(final_flags) == {"upstream-freshness", "target-leakage"}


def test_a_model_hit_by_two_findings_gets_two_impact_reports_not_one():
    """Reproduces the document collision.

    publish_impact_report must not let the second finding's write silently
    overwrite the first's report.
    """
    graph, client = _dual_target_scan()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = run_scan(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    document_urns = {write.documents[0].urn for write in report.writes if write.documents}
    assert len(document_urns) == 2


# --------------------------------------------------------------------------
# Schema drift on a model scan (P3) end to end
# --------------------------------------------------------------------------


def _drift_graph() -> FakeGraph:
    """A model whose one training run's input schema has drifted since training."""
    import json

    from datahub.metadata.schema_classes import (
        AuditStampClass,
        DataProcessInstanceInputClass,
        DataProcessInstancePropertiesClass,
    )

    run_urn = "urn:li:dataProcessInstance:credit_risk_v3_run"
    training = {"applicant_income": "NUMBER", "prior_default_flag": "BOOLEAN"}
    current = {"applicant_income": "VARCHAR", "prior_default_flag": "BOOLEAN"}
    return FakeGraph(
        {
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                name="Credit Risk v3",
                deployments=[DEPLOYMENT_URN],
                trainingJobs=[run_urn],
            ),
            (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): (
                MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
            ),
            (run_urn, DataProcessInstancePropertiesClass): DataProcessInstancePropertiesClass(
                name="credit_risk_v3_run",
                created=AuditStampClass(time=0, actor="urn:li:corpuser:datahub"),
                customProperties={
                    ScanConfig().training_schema_property: json.dumps({FEATURE_TABLE_URN: training})
                },
            ),
            (run_urn, DataProcessInstanceInputClass): DataProcessInstanceInputClass(
                inputs=[FEATURE_TABLE_URN]
            ),
            (FEATURE_TABLE_URN, SchemaMetadataClass): SchemaMetadataClass(
                schemaName="customer_features",
                platform="urn:li:dataPlatform:snowflake",
                version=0,
                hash="",
                platformSchema=OtherSchemaClass(rawSchema=""),
                fields=[
                    SchemaFieldClass(
                        fieldPath=path,
                        type=SchemaFieldDataTypeClass(type=BooleanTypeClass()),
                        nativeDataType=native,
                    )
                    for path, native in current.items()
                ],
            ),
        }
    )


def test_a_model_scan_raises_a_schema_drift_incident_on_the_input_dataset():
    graph = _drift_graph()
    graph.graphql_response = {"raiseIncident": "urn:li:incident:drift"}

    report = run_scan(
        make_connection(graph, FakeClient()),
        ScanConfig(),
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert len(report.writes) == 1
    finding = report.writes[0].finding
    assert finding.finding_type is FindingType.INPUT_SCHEMA_DRIFT
    assert finding.severity is Severity.HIGH  # live model

    # The incident lands on the drifted dataset, typed DATA_SCHEMA.
    _, variables = graph.graphql_calls[0]
    assert variables["input"]["resourceUrn"] == FEATURE_TABLE_URN
    assert variables["input"]["type"] == "DATA_SCHEMA"

    # And the model is scored: 100 - 15 (drift) - 10 (unowned) = 75.
    assert report.trust[0].score.value == 75


def test_a_leakage_only_scan_never_claims_a_freshness_assertion_was_written():
    """assertion_yaml must stay empty when no FreshnessFinding fired in this scan."""
    graph, client = _dual_target_scan(lag_hours=1.0)  # within the 6h default SLA
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}

    report = run_scan(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert len(report.writes) == 1
    assert report.writes[0].finding.finding_type is FindingType.TARGET_LEAKAGE
    assert report.assertion_yaml == ""
