"""The leakage detector, against fixture graphs. Offline: no DataHub, no network.

Two obligations, from tests/CLAUDE.md rule 1. A known-leakage fixture must flag
exactly the seeded feature, and a clean fixture must flag nothing. The second is
the one that matters most: a detector that fires on everything is worthless, and
a detector that stays silent on a leaking graph is worse than worthless because it
grants false confidence.

The fixtures below put the *dataset* in ``LineageResult.urn`` and the columns only
in ``LineageResult.paths``, because that is what a live GMS actually returns for a
column-level query. A fixture that put the label column in ``urn`` would let the
bug this detector was written to avoid pass the suite (D-031).
"""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    EditableSchemaFieldInfoClass,
    EditableSchemaMetadataClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
)

from modelguard.config import ScanConfig
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY, leakage_findings
from modelguard.models import FindingType, Severity
from tests.conftest import (
    CLEAN_COLUMN_URN,
    CLEAN_FEATURE_URN,
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    INCOME_COLUMN_URN,
    LABEL_COLUMN_URN,
    LABEL_TERM_URN,
    LEAK_COLUMN_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
    make_connection,
)

CONFIG = ScanConfig()


def _terms(*urns: str) -> GlossaryTermsClass:
    return GlossaryTermsClass(
        terms=[GlossaryTermAssociationClass(urn=urn) for urn in urns], auditStamp=None
    )


def _model(*feature_urns: str) -> MLModelPropertiesClass:
    return MLModelPropertiesClass(
        name="Credit Risk v3",
        mlFeatures=list(feature_urns),
        deployments=[DEPLOYMENT_URN],
    )


def _feature(source_column_urn: str) -> MLFeaturePropertiesClass:
    """A feature carrying the source-column bridge the detector starts from."""
    return MLFeaturePropertiesClass(
        sources=[FEATURE_TABLE_URN],
        customProperties={SOURCE_COLUMN_PROPERTY: source_column_urn},
    )


def _leaking_graph(*, label_on_field: bool = True) -> tuple[FakeGraph, FakeClient]:
    """The seeded graph: prior_default_flag derives from the label, income does not.

    Args:
        label_on_field: Declare the label on the schemaField (what ModelGuard
            writes). When False, it is declared through editableSchemaMetadata
            instead (what the DataHub UI writes). The detector must honor both.
    """
    aspects: dict[tuple[str, type], object] = {
        (MODEL_URN, MLModelPropertiesClass): _model(LEAK_FEATURE_URN, CLEAN_FEATURE_URN),
        (LEAK_FEATURE_URN, MLFeaturePropertiesClass): _feature(LEAK_COLUMN_URN),
        (CLEAN_FEATURE_URN, MLFeaturePropertiesClass): _feature(CLEAN_COLUMN_URN),
    }

    if label_on_field:
        aspects[(LABEL_COLUMN_URN, GlossaryTermsClass)] = _terms(LABEL_TERM_URN)
    else:
        aspects[(TABLE_URN, EditableSchemaMetadataClass)] = EditableSchemaMetadataClass(
            created=None,
            lastModified=None,
            editableSchemaFieldInfo=[
                EditableSchemaFieldInfoClass(
                    fieldPath="default_status", glossaryTerms=_terms(LABEL_TERM_URN)
                )
            ],
        )

    graph = FakeGraph(aspects=aspects)  # type: ignore[arg-type]

    # Both columns' cones resolve to the SAME upstream dataset, loans_raw. Only
    # the column paths differ. That is exactly the trap: the result urn cannot
    # tell a leaking feature from a clean one, and only paths can.
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ],
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
    return graph, client


def test_the_leaking_feature_is_flagged():
    graph, client = _leaking_graph()

    findings = leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type is FindingType.TARGET_LEAKAGE
    assert finding.leak.feature_urn == LEAK_FEATURE_URN
    assert finding.leak.label_column_urn == LABEL_COLUMN_URN


def test_the_incident_lands_on_the_leaking_column_not_the_model():
    """DataHub rejects an incident on an mlModel, so the column is the target (D-017)."""
    graph, client = _leaking_graph()

    finding = leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG)[0]

    assert finding.resource_urn == LEAK_COLUMN_URN
    assert finding.incident_type == "FIELD"


def test_the_finding_quotes_the_column_path_it_walked():
    """The path is the evidence. Without it the finding is a claim, not a proof."""
    graph, client = _leaking_graph()

    finding = leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG)[0]

    assert finding.leak.column_path == ("prior_default_flag", "default_status")
    assert finding.evidence["column_path"] == "prior_default_flag <- default_status"


def test_a_label_declared_through_the_ui_counts_the_same():
    """A human tagging the column in DataHub must work with no ModelGuard config."""
    graph, client = _leaking_graph(label_on_field=False)

    findings = leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG)

    assert len(findings) == 1
    assert findings[0].leak.label_column_urn == LABEL_COLUMN_URN


def test_a_live_model_is_critical_and_an_undeployed_one_is_not():
    """The same leak is graded by whether anyone is actually being served it."""
    serving_graph, client = _leaking_graph()
    serving_graph.set_aspect(
        DEPLOYMENT_URN,
        MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE),
    )

    live = leakage_findings(make_connection(serving_graph, client), MODEL_URN, CONFIG)[0]
    assert live.severity is Severity.CRITICAL
    assert live.model.is_live is True

    # The same leak on a model nobody has deployed: still serious, not yet a lie
    # told to production.
    idle_graph, client = _leaking_graph()
    idle_graph.set_aspect(
        MODEL_URN,
        MLModelPropertiesClass(
            name="Credit Risk v3",
            mlFeatures=[LEAK_FEATURE_URN, CLEAN_FEATURE_URN],
            deployments=[],
        ),
    )

    idle = leakage_findings(make_connection(idle_graph, client), MODEL_URN, CONFIG)[0]
    assert idle.severity is Severity.HIGH
    assert idle.model.is_live is False


# ---------------------------------------------------------------------------
# False-positive control. A detector that cries wolf is a detector nobody runs.
# ---------------------------------------------------------------------------


def test_a_clean_model_flags_nothing():
    """No column in the cone carries the label term, so there is nothing to report."""
    graph = FakeGraph(
        aspects={  # type: ignore[arg-type]
            (MODEL_URN, MLModelPropertiesClass): _model(CLEAN_FEATURE_URN),
            (CLEAN_FEATURE_URN, MLFeaturePropertiesClass): _feature(CLEAN_COLUMN_URN),
        }
    )
    client = FakeClient(
        lineage_by_column={
            "applicant_income": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(CLEAN_COLUMN_URN, INCOME_COLUMN_URN),
                )
            ]
        }
    )

    assert leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG) == ()


def test_a_feature_with_no_recorded_source_column_is_skipped_not_cleared():
    """Absence of evidence is not evidence of safety (detect/CLAUDE.md rule 5)."""
    graph = FakeGraph(
        aspects={  # type: ignore[arg-type]
            (MODEL_URN, MLModelPropertiesClass): _model(LEAK_FEATURE_URN),
            (LEAK_FEATURE_URN, MLFeaturePropertiesClass): MLFeaturePropertiesClass(
                sources=[FEATURE_TABLE_URN], customProperties={}
            ),
        }
    )
    client = FakeClient(lineage_results=[])

    assert leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG) == ()


def test_a_model_with_no_features_flags_nothing():
    graph = FakeGraph(
        aspects={(MODEL_URN, MLModelPropertiesClass): _model()}  # type: ignore[arg-type]
    )

    assert leakage_findings(make_connection(graph, FakeClient()), MODEL_URN, CONFIG) == ()


def test_an_unknown_model_flags_nothing():
    """A model with no properties aspect at all must not crash the scan."""
    assert leakage_findings(make_connection(FakeGraph(), FakeClient()), MODEL_URN, CONFIG) == ()


# ---------------------------------------------------------------------------
# The trap. These are the tests that would have caught the bug.
# ---------------------------------------------------------------------------


def test_the_detector_reads_paths_and_not_the_result_urn():
    """A live GMS puts the DATASET in urn, never the label column.

    If the detector compared ``LineageResult.urn`` against the label column URN it
    would find nothing here, and would report this leaking graph as clean. This
    fixture reproduces the real shape exactly, so that bug cannot pass.
    """
    graph, client = _leaking_graph()

    # Precisely the situation a live GMS produces: the label column is NOT the urn
    # of any result, and appears only inside paths.
    results = client.lineage.by_column["prior_default_flag"]
    assert all(result.urn != LABEL_COLUMN_URN for result in results)
    assert any(step.urn == LABEL_COLUMN_URN for result in results for step in result.paths or [])

    assert len(leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG)) == 1


def test_results_beyond_the_hop_cap_are_ignored():
    """DataHub returns entities past max_hops once it exceeds 2 (D-020)."""
    graph, _ = _leaking_graph()
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=CONFIG.leakage_max_hops + 1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ]
        }
    )

    assert leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG) == ()


def test_the_upstream_traversal_starts_from_the_features_own_column():
    """The query must be keyed on the source column, not on the whole table."""
    graph, client = _leaking_graph()

    leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG)

    call = client.lineage.lineage_calls[0]
    assert call["source_urn"] == FEATURE_TABLE_URN
    assert call["source_column"] == "prior_default_flag"
    assert call["direction"] == "upstream"


@pytest.mark.parametrize("term", ["urn:li:glossaryTerm:something.else", "urn:li:glossaryTerm:pii"])
def test_a_column_carrying_some_other_term_is_not_a_label(term: str):
    """Only the configured label term declares a label. Any term must not do."""
    graph, client = _leaking_graph()
    graph.set_aspect(LABEL_COLUMN_URN, _terms(term))

    assert leakage_findings(make_connection(graph, client), MODEL_URN, CONFIG) == ()
