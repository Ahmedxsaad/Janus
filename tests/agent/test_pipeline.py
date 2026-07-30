from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    AssertionInfoClass,
    AssertionRunEventClass,
    AuditStampClass,
    BooleanTypeClass,
    DeploymentStatusClass,
    GlobalTagsClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    IncidentStateClass,
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
    StructuredPropertyValueAssignmentClass,
)
from datahub.metadata.urns import StructuredPropertyUrn

from modelguard.agent.pipeline import FindingWrites, ScanReport, new_run_id, run_scan
from modelguard.config import ScanConfig
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from modelguard.models import FindingType, Severity
from modelguard.writeback.properties import RISK_FLAGS, RUN_ID
from tests.conftest import (
    CLEAN_COLUMN_URN,
    CLEAN_FEATURE_URN,
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
    active_incident,
    column_path,
    lineage_result,
    make_connection,
    make_schema_drift_finding,
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


# --------------------------------------------------------------------------
# Reconciling a stale incident: graph-driven, not a diff against a process's
# own prior report (D-067). Every test below calls run_scan exactly once,
# with no earlier call and no carried-over state, on a graph state where the
# incident was raised by something else entirely, proving reconciliation does
# not depend on this process having been the one that raised it.
# --------------------------------------------------------------------------


def test_a_stale_freshness_incident_resolves_with_no_prior_process_state():
    """A plain, one-shot run_scan resolves an incident it never raised itself."""
    graph = _graph(lag_hours=1.0)  # within the 6h default SLA: currently fresh
    client = _client()
    incident_urn = "urn:li:incident:orphaned-freshness"
    active_incident(
        graph,
        resource_urn=TABLE_URN,
        incident_urn=incident_urn,
        incident_type="FRESHNESS",
        title="Stale upstream data in ecommerce.public.loans_raw",
    )
    graph.set_aspect(MODEL_URN, GlobalTagsClass(tags=[]))
    graph.emitted.clear()

    report = run_scan(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert report.clean
    resolved = [
        variables for query, variables in graph.graphql_calls if "updateIncidentStatus" in query
    ]
    assert len(resolved) == 1
    assert resolved[0]["urn"] == incident_urn
    assert resolved[0]["input"]["state"] == IncidentStateClass.RESOLVED


def test_a_stale_leakage_incident_resolves_and_clears_the_models_risk_state():
    """A plain run_scan resolves a leakage incident with no leak path today."""
    graph = _graph(lag_hours=1.0)
    graph.set_aspect(
        LEAK_FEATURE_URN,
        MLFeaturePropertiesClass(
            sources=[FEATURE_TABLE_URN],
            customProperties={SOURCE_COLUMN_PROPERTY: LEAK_COLUMN_URN},
        ),
    )
    # No lineage_by_column entry for the leaking column: leak_path finds nothing.
    client = FakeClient()
    incident_urn = "urn:li:incident:orphaned-leakage"
    active_incident(
        graph,
        resource_urn=LEAK_COLUMN_URN,
        incident_urn=incident_urn,
        incident_type="FIELD",
        title="Target leakage: prior_default_flag derives from label default_status",
    )
    graph.set_aspect(
        MODEL_URN,
        StructuredPropertiesClass(
            properties=[
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(RISK_FLAGS)),
                    values=[str(FindingType.TARGET_LEAKAGE)],
                )
            ]
        ),
    )
    graph.emitted.clear()
    graph.graphql_calls.clear()

    report = run_scan(
        make_connection(graph, client),
        ScanConfig(),
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert report.clean
    resolved = [
        variables for query, variables in graph.graphql_calls if "updateIncidentStatus" in query
    ]
    assert len(resolved) == 1
    assert resolved[0]["urn"] == incident_urn

    properties = graph.get_aspect(MODEL_URN, StructuredPropertiesClass)
    assert properties is not None
    risk_flags_urn = str(StructuredPropertyUrn(RISK_FLAGS))
    assert all(p.propertyUrn != risk_flags_urn for p in properties.properties)


def test_a_stale_schema_drift_incident_resolves_with_no_prior_process_state():
    """A plain run_scan resolves a drift incident once the schema matches again."""
    import json

    from datahub.metadata.schema_classes import (
        DataProcessInstanceInputClass,
        DataProcessInstancePropertiesClass,
    )

    run_urn = "urn:li:dataProcessInstance:credit_risk_v3_run"
    matching = {"applicant_income": "NUMBER", "prior_default_flag": "BOOLEAN"}
    graph = FakeGraph(
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
                    ScanConfig().training_schema_property: json.dumps({FEATURE_TABLE_URN: matching})
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
                    for path, native in matching.items()
                ],
            ),
        }
    )
    incident_urn = "urn:li:incident:orphaned-drift"
    active_incident(
        graph,
        resource_urn=FEATURE_TABLE_URN,
        incident_urn=incident_urn,
        incident_type="DATA_SCHEMA",
        title=(
            "Training-serving schema drift in ecommerce.public.customer_features "
            "for Credit Risk v3"
        ),
    )
    graph.emitted.clear()

    report = run_scan(
        make_connection(graph, FakeClient()),
        ScanConfig(),
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert report.clean
    resolved = [
        variables for query, variables in graph.graphql_calls if "updateIncidentStatus" in query
    ]
    assert len(resolved) == 1
    assert resolved[0]["urn"] == incident_urn


def test_one_feature_recovering_does_not_clear_a_model_another_still_endangers():
    """Partial recovery must not wipe the risk state of a model still failing.

    A risk flag names a finding *type*, and a model can carry one type from
    several resources at once. This model has two features: one still leaks and
    is re-raised this very run, the other's leak is gone and its incident is
    resolved. Subtracting the recovered type wholesale left the model with no
    risk flags, no at-risk tag, and a from-scratch "no findings" trust score,
    overwriting the score the same run had just written (D-070).
    """
    graph, client = _dual_target_scan(lag_hours=1.0)  # fresh table: leakage only
    graph.set_aspect(
        MODEL_URN,
        MLModelPropertiesClass(
            name="Credit Risk v3",
            deployments=[DEPLOYMENT_URN],
            mlFeatures=[LEAK_FEATURE_URN, CLEAN_FEATURE_URN],
        ),
    )
    # The second feature: a source column with no leak path today, carrying an
    # incident from a run that is long gone.
    graph.set_aspect(
        CLEAN_FEATURE_URN,
        MLFeaturePropertiesClass(
            sources=[FEATURE_TABLE_URN],
            customProperties={SOURCE_COLUMN_PROPERTY: CLEAN_COLUMN_URN},
        ),
    )
    active_incident(
        graph,
        resource_urn=CLEAN_COLUMN_URN,
        incident_urn="urn:li:incident:orphaned-leakage",
        incident_type="FIELD",
        title="Target leakage: applicant_income derives from label default_status",
    )
    graph.graphql_response = {"raiseIncident": "urn:li:incident:still-leaking"}
    graph.emitted.clear()
    graph.graphql_calls.clear()

    report = run_scan(
        make_connection(graph, client),
        ScanConfig(),
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    # The scan really did find the surviving leak, and really did resolve the
    # other one. Both halves must happen for this test to mean anything.
    assert [write.finding.finding_type for write in report.writes] == [FindingType.TARGET_LEAKAGE]
    resolved = [
        variables for query, variables in graph.graphql_calls if "updateIncidentStatus" in query
    ]
    assert len(resolved) == 1
    assert resolved[0]["urn"] == "urn:li:incident:orphaned-leakage"

    # The model is still leaking, so its risk state must survive intact.
    properties = graph.get_aspect(MODEL_URN, StructuredPropertiesClass)
    assert properties is not None
    assigned = {
        assignment.propertyUrn.rsplit(":", 1)[-1]: assignment.values
        for assignment in properties.properties
    }
    assert assigned[RISK_FLAGS] == [str(FindingType.TARGET_LEAKAGE)]
    assert assigned["modelguard.trust_score"] == [report.trust[0].score.value]
    assert assigned["modelguard.trust_band"] != ["healthy"]

    tags = graph.get_aspect(MODEL_URN, GlobalTagsClass)
    assert tags is not None
    assert [association.tag for association in tags.tags] == ["urn:li:tag:model-at-risk"]


def test_a_drift_incident_raised_for_another_model_is_left_alone():
    """Drift is a property of the (model, dataset) pair, not of the dataset.

    Two models trained on the same input at different times genuinely disagree
    about whether it drifted. When the incident title named only the dataset,
    scanning the model that no longer drifts resolved the incident belonging to
    the model that still does (D-070).
    """
    import json

    from datahub.metadata.schema_classes import (
        DataProcessInstanceInputClass,
        DataProcessInstancePropertiesClass,
    )

    run_urn = "urn:li:dataProcessInstance:credit_risk_v3_run"
    matching = {"applicant_income": "NUMBER", "prior_default_flag": "BOOLEAN"}
    graph = FakeGraph(
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
                    ScanConfig().training_schema_property: json.dumps({FEATURE_TABLE_URN: matching})
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
                    for path, native in matching.items()
                ],
            ),
        }
    )
    # The live incident belongs to a different model, which still drifts. Its
    # title is taken from the production Finding.title of a v4 drift finding
    # rather than written out here, so this test tracks the real dedup key: when
    # the title named only the dataset, this was byte-for-byte the title the v3
    # scan below looks up, and the v3 recovery closed v4's live incident.
    v3 = make_schema_drift_finding()
    v4_title = replace(v3, model=replace(v3.model, name="Credit Risk v4")).title
    active_incident(
        graph,
        resource_urn=FEATURE_TABLE_URN,
        incident_urn="urn:li:incident:v4-drift",
        incident_type="DATA_SCHEMA",
        title=v4_title,
    )
    graph.emitted.clear()

    report = run_scan(
        make_connection(graph, FakeClient()),
        ScanConfig(),
        model_urn=MODEL_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    assert report.clean
    assert [
        variables for query, variables in graph.graphql_calls if "updateIncidentStatus" in query
    ] == [], "another model's live drift incident was resolved"


def test_a_recovered_table_records_the_passing_assertion_run():
    """Resolving the incident without recording the pass leaves the check red.

    The guarding assertion's latest run event is the FAILURE written when the
    table went stale. A recovery that never writes a SUCCESS leaves the table
    reading, in the DataHub UI, as permanently breaching the very assertion
    ModelGuard wrote to guard it (D-070).
    """
    graph = _graph(lag_hours=1.0)
    client = _client()
    active_incident(
        graph,
        resource_urn=TABLE_URN,
        incident_urn="urn:li:incident:orphaned-freshness",
        incident_type="FRESHNESS",
        title="Stale upstream data in ecommerce.public.loans_raw",
    )
    graph.set_aspect(MODEL_URN, GlobalTagsClass(tags=[]))
    graph.emitted.clear()

    run_scan(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        llm=None,
        now_ms=NOW_MS,
    )

    events = _aspects_of(graph, AssertionRunEventClass)
    assert len(events) == 1
    assert events[0].result is not None
    assert events[0].result.type == "SUCCESS"
    assert events[0].asserteeUrn == TABLE_URN
