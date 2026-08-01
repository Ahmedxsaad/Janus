"""The governance detectors, against fixture graphs. Offline: no DataHub.

Two detectors, and the same two obligations for each (tests/CLAUDE.md rule 1): a
graph carrying the planted declaration must flag exactly the affected feature or
input, and a graph without it must flag nothing.

The one that matters most here is what happens when nothing is configured. A
detector that quietly returns "no findings" because it was never told what
"sensitive" means in this organization is reporting a clean bill of health it
never measured, which is the single failure mode
:mod:`modelguard.detect.coverage` exists to prevent. So the configured and
unconfigured cases are asserted separately, and both are asserted against the
coverage gap the scan reports.

Like the leakage fixtures, these put the *dataset* in ``LineageResult.urn`` and
the columns only in ``LineageResult.paths``, because that is what a live GMS
returns for a column-level query.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DeploymentStatusClass,
    DeprecationClass,
    EditableSchemaFieldInfoClass,
    EditableSchemaMetadataClass,
    GlobalTagsClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    TagAssociationClass,
)

from modelguard.config import ScanConfig
from modelguard.detect.coverage import coverage_gaps
from modelguard.detect.governance import (
    deprecated_input_findings,
    model_input_datasets,
    sensitive_source_findings,
)
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from modelguard.models import FindingType, Severity
from tests.conftest import (
    CLEAN_COLUMN_URN,
    CLEAN_FEATURE_URN,
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    INCOME_COLUMN_URN,
    MODEL_URN,
    TABLE_URN,
    TRAINING_RUN_URN,
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
    make_connection,
)

SENSITIVE_TAG_URN = "urn:li:tag:pii"
SENSITIVE_TERM_URN = "urn:li:glossaryTerm:classification.restricted"

#: Configured to look for the tag only, which is the common case on a real
#: catalog and the one the seeder plants.
TAGGED = ScanConfig(sensitive_tag_urns=(SENSITIVE_TAG_URN,))

#: Nothing configured. The detector cannot run, and must say so rather than
#: return a clean result.
UNCONFIGURED = ScanConfig()


def _model(*feature_urns: str, run: bool = False) -> MLModelPropertiesClass:
    return MLModelPropertiesClass(
        name="Credit Risk v3",
        mlFeatures=list(feature_urns),
        deployments=[DEPLOYMENT_URN],
        trainingJobs=[TRAINING_RUN_URN] if run else None,
    )


def _feature(source_column_urn: str) -> MLFeaturePropertiesClass:
    return MLFeaturePropertiesClass(
        sources=[FEATURE_TABLE_URN],
        customProperties={SOURCE_COLUMN_PROPERTY: source_column_urn},
    )


def _live() -> MLModelDeploymentPropertiesClass:
    return MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)


def _tags(*urns: str) -> GlobalTagsClass:
    return GlobalTagsClass(tags=[TagAssociationClass(tag=urn) for urn in urns])


def _classified_graph(
    *, classified: bool = True, live: bool = True
) -> tuple[FakeGraph, FakeClient]:
    """A model whose applicant_income feature descends from loans_raw.income.

    Args:
        classified: Whether ``income`` carries the sensitive tag. When False the
            graph is otherwise identical, which is what makes a clean result mean
            "the classification is what changed" rather than "nothing derives
            from anything".
        live: Whether the model's deployment is in service.
    """
    aspects: dict[tuple[str, type], object] = {
        (MODEL_URN, MLModelPropertiesClass): _model(CLEAN_FEATURE_URN),
        (CLEAN_FEATURE_URN, MLFeaturePropertiesClass): _feature(CLEAN_COLUMN_URN),
    }
    if live:
        aspects[(DEPLOYMENT_URN, MLModelDeploymentPropertiesClass)] = _live()
    if classified:
        aspects[(INCOME_COLUMN_URN, GlobalTagsClass)] = _tags(SENSITIVE_TAG_URN)

    client = FakeClient(
        lineage_by_column={
            "applicant_income": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(CLEAN_COLUMN_URN, INCOME_COLUMN_URN),
                )
            ],
        }
    )
    return FakeGraph(aspects=aspects), client  # type: ignore[arg-type]


class TestSensitiveSource:
    def test_a_feature_descending_from_a_classified_column_is_flagged(self):
        graph, client = _classified_graph()

        findings = sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.finding_type is FindingType.SENSITIVE_SOURCE
        assert finding.exposure.sensitive_column_urn == INCOME_COLUMN_URN
        assert finding.exposure.marker_urn == SENSITIVE_TAG_URN

    def test_the_same_graph_without_the_classification_is_clean(self):
        """The narrow negative: only the organization's declaration is withdrawn."""
        graph, client = _classified_graph(classified=False)

        assert sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED) == ()

    def test_nothing_configured_is_reported_as_not_evaluated_never_as_clean(self):
        """The failure this guards: silence rendered as a clean bill of health."""
        graph, client = _classified_graph()
        conn = make_connection(graph, client)

        assert sensitive_source_findings(conn, MODEL_URN, UNCONFIGURED) == ()

        gaps = coverage_gaps(conn, UNCONFIGURED, table_urn=None, model_urn=MODEL_URN, findings=())
        sensitive = [gap for gap in gaps if gap.check == "sensitive source"]
        assert len(sensitive) == 1
        assert "no classification is configured" in sensitive[0].reason
        assert "MODELGUARD_SENSITIVE_TERM_URNS" in sensitive[0].remedy

    def test_nothing_configured_does_not_walk_the_graph_at_all(self):
        """Unconfigured is the default state, so its cost is the one that matters.

        An empty index matches nothing, so the walk would return the same answer
        either way. What it would also do is issue a lineage query per feature
        per scan of every model in the catalog, buying an answer known in
        advance. Asserting on the calls, not on the result, is what makes this a
        test of the guard rather than of the arithmetic around it.
        """
        graph, client = _classified_graph()

        sensitive_source_findings(make_connection(graph, client), MODEL_URN, UNCONFIGURED)

        assert client.lineage.lineage_calls == []

    def test_a_term_classification_is_honored_as_well_as_a_tag(self):
        """Catalogs classify through either surface; many use only one."""
        graph, client = _classified_graph(classified=False)
        graph.set_aspect(
            INCOME_COLUMN_URN,
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=SENSITIVE_TERM_URN)], auditStamp=None
            ),
        )
        config = ScanConfig(sensitive_term_urns=(SENSITIVE_TERM_URN,))

        findings = sensitive_source_findings(make_connection(graph, client), MODEL_URN, config)

        assert len(findings) == 1
        assert findings[0].exposure.marker_urn == SENSITIVE_TERM_URN

    def test_a_classification_applied_through_the_ui_is_honored(self):
        """The UI writes editableSchemaMetadata on the parent, not the schemaField."""
        graph, client = _classified_graph(classified=False)
        graph.set_aspect(
            TABLE_URN,
            EditableSchemaMetadataClass(
                created=None,
                lastModified=None,
                editableSchemaFieldInfo=[
                    EditableSchemaFieldInfoClass(
                        fieldPath="income", globalTags=_tags(SENSITIVE_TAG_URN)
                    )
                ],
            ),
        )

        findings = sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED)

        assert len(findings) == 1
        assert findings[0].exposure.marker_urn == SENSITIVE_TAG_URN

    def test_the_incident_lands_on_the_feature_column_the_team_owns(self):
        """Not on the classified column: that one is correct, and not theirs to fix."""
        graph, client = _classified_graph()

        finding = sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED)[0]

        assert finding.resource_urn == CLEAN_COLUMN_URN
        assert finding.incident_type == "FIELD"

    def test_a_live_model_is_high_but_never_critical(self):
        """CRITICAL means the model's numbers are wrong. This model's numbers are fine."""
        graph, client = _classified_graph()

        finding = sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED)[0]

        assert finding.model.is_live
        assert finding.severity is Severity.HIGH

    def test_a_model_that_is_not_serving_is_only_medium(self):
        graph, client = _classified_graph(live=False)

        finding = sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED)[0]

        assert not finding.model.is_live
        assert finding.severity is Severity.MEDIUM

    def test_the_derivation_chain_is_quoted_as_proof(self):
        """The finding is auditable because it carries the path it walked."""
        graph, client = _classified_graph()

        finding = sensitive_source_findings(make_connection(graph, client), MODEL_URN, TAGGED)[0]

        assert finding.exposure.path_text == "applicant_income <- income"
        assert finding.evidence["column_path"] == "applicant_income <- income"


def _deprecated_graph(
    *, deprecated: bool | None = True, live: bool = True, runs: int = 1
) -> FakeGraph:
    """A model whose training run reads the feature table.

    Args:
        deprecated: True plants the deprecation, False plants a *withdrawn* one
            (the aspect present with deprecated=false, which is how DataHub
            records it), and None leaves the aspect off entirely.
        live: Whether the model's deployment is in service.
        runs: How many training runs the model records, each reading the same
            input, so the dedup across runs can be exercised.
    """
    run_urns = [TRAINING_RUN_URN] + [f"{TRAINING_RUN_URN}_{n}" for n in range(2, runs + 1)]
    aspects: dict[tuple[str, type], object] = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            name="Credit Risk v3", deployments=[DEPLOYMENT_URN], trainingJobs=run_urns
        ),
    }
    if live:
        aspects[(DEPLOYMENT_URN, MLModelDeploymentPropertiesClass)] = _live()
    for run_urn in run_urns:
        aspects[(run_urn, DataProcessInstanceInputClass)] = DataProcessInstanceInputClass(
            inputs=[FEATURE_TABLE_URN]
        )
    if deprecated is not None:
        aspects[(FEATURE_TABLE_URN, DeprecationClass)] = DeprecationClass(
            deprecated=deprecated,
            note="Superseded by the v2 feature pipeline." if deprecated else "",
            actor="urn:li:corpuser:datahub",
            decommissionTime=1_900_000_000_000 if deprecated else None,
        )
    return FakeGraph(aspects=aspects)  # type: ignore[arg-type]


class TestDeprecatedInput:
    def test_a_deprecated_training_input_is_flagged(self):
        findings = deprecated_input_findings(
            make_connection(_deprecated_graph()), MODEL_URN, UNCONFIGURED
        )

        assert len(findings) == 1
        finding = findings[0]
        assert finding.finding_type is FindingType.DEPRECATED_INPUT
        assert finding.dataset_urn == FEATURE_TABLE_URN
        assert "v2 feature pipeline" in finding.note

    def test_a_withdrawn_deprecation_is_positive_evidence_of_health(self):
        """The aspect is present with deprecated=false. Presence is not the signal."""
        graph = _deprecated_graph(deprecated=False)

        assert deprecated_input_findings(make_connection(graph), MODEL_URN, UNCONFIGURED) == ()

    def test_an_input_that_never_carried_the_aspect_is_clean(self):
        graph = _deprecated_graph(deprecated=None)

        assert deprecated_input_findings(make_connection(graph), MODEL_URN, UNCONFIGURED) == ()

    def test_the_incident_lands_on_the_dataset_where_the_decision_was_made(self):
        finding = deprecated_input_findings(
            make_connection(_deprecated_graph()), MODEL_URN, UNCONFIGURED
        )[0]

        assert finding.resource_urn == FEATURE_TABLE_URN
        assert finding.incident_type == "OPERATIONAL"

    def test_it_never_exceeds_medium_because_it_is_a_deadline_not_a_defect(self):
        live = deprecated_input_findings(
            make_connection(_deprecated_graph()), MODEL_URN, UNCONFIGURED
        )[0]
        assert live.severity is Severity.MEDIUM

        idle = deprecated_input_findings(
            make_connection(_deprecated_graph(live=False)), MODEL_URN, UNCONFIGURED
        )[0]
        assert idle.severity is Severity.LOW

    def test_the_owners_note_and_decommission_time_are_quoted_not_interpreted(self):
        finding = deprecated_input_findings(
            make_connection(_deprecated_graph()), MODEL_URN, UNCONFIGURED
        )[0]

        assert finding.evidence["deprecation_note"] == "Superseded by the v2 feature pipeline."
        assert finding.evidence["decommission_time_ms"] == "1900000000000"

    def test_a_model_with_no_recorded_inputs_is_reported_as_not_evaluated(self):
        """A model nothing links to its data cannot be judged on its inputs."""
        graph = FakeGraph(
            aspects={(MODEL_URN, MLModelPropertiesClass): _model()}  # type: ignore[arg-type]
        )
        conn = make_connection(graph)

        assert deprecated_input_findings(conn, MODEL_URN, UNCONFIGURED) == ()

        gaps = coverage_gaps(conn, UNCONFIGURED, table_urn=None, model_urn=MODEL_URN, findings=())
        deprecated = [gap for gap in gaps if gap.check == "deprecated input"]
        assert len(deprecated) == 1
        assert "no training run is recorded" in deprecated[0].reason

    def test_inputs_are_deduplicated_across_training_runs(self):
        """A model retrained on the same table must not raise the finding twice."""
        graph = _deprecated_graph(runs=2)

        assert model_input_datasets(make_connection(graph), MODEL_URN) == (FEATURE_TABLE_URN,)
        assert len(deprecated_input_findings(make_connection(graph), MODEL_URN, UNCONFIGURED)) == 1
