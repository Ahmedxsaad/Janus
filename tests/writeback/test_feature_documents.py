"""The per-feature Data Card. Offline: the renderer is pure.

Two obligations, and they are the ones every generated document in this project
carries. The card must never report a check it could not run as a check that
passed, and it must never report the freshness it measured today as the
freshness at training time, because that answers a different question.

The gather half is exercised against fixture graphs, because the interesting
parts of it are reads that can each be absent: a feature with no source column,
a snapshot keyed by a different dataset, a classification nobody configured.
"""

from __future__ import annotations

from dataclasses import replace

from datahub.metadata.schema_classes import (
    DataProcessInstancePropertiesClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    TagAssociationClass,
)
from datahub.metadata.schema_classes import (
    GlobalTagsClass as Tags,
)

from janus.config import ScanConfig
from janus.detect.leakage import SOURCE_COLUMN_PROPERTY
from janus.models import FreshnessSignal
from janus.writeback.feature_documents import (
    FeatureFacts,
    TableInTheChain,
    gather_feature,
    render_feature_card,
)
from janus.writeback.model_documents import NOT_RECORDED
from tests.conftest import (
    CLEAN_COLUMN_URN,
    FEATURE_TABLE_URN,
    LABEL_COLUMN_URN,
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
    make_leakage_finding,
    schema_metadata,
)

CONFIG = ScanConfig()
RUN_URN = "urn:li:dataProcessInstance:credit_risk_v3_run"
SENSITIVE_TAG = "urn:li:tag:janus.sensitive"


def facts(**overrides: object) -> FeatureFacts:
    """A fully traced card's facts, so a test varies one thing at a time."""
    defaults: dict = {
        "feature_urn": LEAK_FEATURE_URN,
        "feature_name": "prior_default_flag",
        "model_urn": MODEL_URN,
        "model_name": "credit_risk_v3",
        "source_column_urn": LEAK_COLUMN_URN,
        "source_column_name": "prior_default_flag",
        "source_dataset_name": "ecommerce.public.customer_features",
        "chains": (("prior_default_flag", "default_status"),),
        "tables": (
            TableInTheChain(
                dataset_urn=TABLE_URN,
                dataset_name="ecommerce.public.loans_raw",
                signal=FreshnessSignal(
                    dataset_urn=TABLE_URN,
                    last_updated_ms=NOW_MS - 3_600_000,
                    observed_at_ms=NOW_MS,
                    sla_hours=6.0,
                ),
            ),
        ),
        "training_type": "BOOLEAN",
        "current_type": "BOOLEAN",
    }
    defaults.update(overrides)
    return FeatureFacts(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The renderer
# --------------------------------------------------------------------------


def test_the_freshness_table_says_the_number_is_now_and_not_at_training_time():
    """Substituting today's lag for training-time lag answers a different question.

    The evidence pack already refuses to make that substitution. A card
    that quietly made it would contradict the pack about the same model.
    """
    card = render_feature_card(facts())

    assert "measured **now**, not as of the training run" in card


def test_a_table_with_no_operation_aspect_is_not_evaluated_rather_than_fresh():
    """Absence of evidence is not freshness (detect rule 5), in a document."""
    card = render_feature_card(
        facts(
            tables=(
                TableInTheChain(
                    dataset_urn=TABLE_URN,
                    dataset_name="ecommerce.public.loans_raw",
                    signal=None,
                ),
            )
        )
    )

    assert f"| `ecommerce.public.loans_raw` | {NOT_RECORDED} | not evaluated |" in card


def test_a_second_derivation_is_named_and_the_reader_is_told_one_fix_is_not_enough():
    """A feature reached two ways is not fixed by cutting one of them."""
    card = render_feature_card(
        facts(
            chains=(
                ("prior_default_flag", "default_status"),
                ("prior_default_flag", "default_backfill", "default_status"),
            )
        )
    )

    assert "**2 distinct chains**" in card
    assert "`prior_default_flag` <- `default_backfill` <- `default_status`" in card


def test_an_unconfigured_classification_is_reported_as_unevaluated_not_clean():
    """The whole posture, in the one place it is most tempting to skip."""
    card = render_feature_card(
        facts(unevaluated=("**restricted exposure**: no restricted classification",))
    )

    assert "## What could not be checked" in card
    assert "**restricted exposure**" in card


def test_the_could_not_check_section_is_rendered_even_when_it_is_empty():
    """An omitted section reads as an absent problem."""
    card = render_feature_card(facts(unevaluated=()))

    assert "## What could not be checked" in card
    assert "Every check this card asked for had the metadata it needed" in card


def test_drift_is_not_established_rather_than_absent_when_a_type_is_unknown():
    """No snapshot means unknown, and unknown must not render as unchanged."""
    card = render_feature_card(facts(training_type=None))

    assert "Not established." in card
    assert "Unchanged" not in card


def test_a_moved_type_is_reported_with_both_ends():
    card = render_feature_card(facts(training_type="BOOLEAN", current_type="VARCHAR"))

    assert "**Changed.**" in card
    assert "`BOOLEAN`" in card
    assert "`VARCHAR`" in card


def test_a_finding_naming_the_feature_brings_its_counterfactual_onto_the_card():
    card = render_feature_card(facts(findings=(make_leakage_finding(),)))

    assert "## Open findings and how to clear them" in card
    for remedy in make_leakage_finding().counterfactual.remedies:
        assert remedy.summary in card


def test_no_finding_is_stated_as_a_smaller_claim_than_it_looks():
    """A clean card must not read as an approval, and says so itself."""
    card = render_feature_card(facts(findings=()))

    assert "smaller claim than it looks" in card
    assert "It is not an approval." in card


def test_a_feature_with_no_source_column_produces_a_card_that_says_only_that():
    """Nothing downstream of an absent source column could have been established."""
    card = render_feature_card(
        facts(
            source_column_urn=None,
            source_column_name=None,
            source_dataset_name=None,
            unevaluated=("**everything below**: this feature records no source column",),
        )
    )

    assert NOT_RECORDED in card
    # No section may claim a fact it could not have.
    assert "## Tables this feature depends on" not in card
    assert "## Schema drift" not in card


# --------------------------------------------------------------------------
# The gather
# --------------------------------------------------------------------------


def traced_graph() -> FakeGraph:
    """A feature whose source column exists, with a training snapshot on the run."""
    graph = FakeGraph()
    graph.set_aspect(
        LEAK_FEATURE_URN,
        MLFeaturePropertiesClass(customProperties={SOURCE_COLUMN_PROPERTY: LEAK_COLUMN_URN}),
    )
    graph.set_aspect(MODEL_URN, MLModelPropertiesClass(trainingJobs=[RUN_URN]))
    graph.set_aspect(FEATURE_TABLE_URN, schema_metadata({"prior_default_flag": "BOOLEAN"}))
    graph.set_aspect(
        TABLE_URN,
        schema_metadata({"default_status": "BOOLEAN"}),
    )
    return graph


def snapshot_on_run(graph: FakeGraph, snapshot: dict) -> None:
    """Put a training-time schema snapshot on the run, as `link` writes it."""
    import json

    graph.set_aspect(
        RUN_URN,
        DataProcessInstancePropertiesClass(
            name="run",
            created=None,
            customProperties={CONFIG.training_schema_property: json.dumps(snapshot)},
        ),
    )


def leaking_client() -> FakeClient:
    """A lineage client answering the leak column's upstream walk."""
    return FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ]
        }
    )


def test_gather_reports_a_feature_with_no_source_column_as_untraced():
    graph = FakeGraph()
    graph.set_aspect(LEAK_FEATURE_URN, MLFeaturePropertiesClass(customProperties={}))

    result = gather_feature(
        make_connection(graph, FakeClient()), LEAK_FEATURE_URN, MODEL_URN, "m", CONFIG
    )

    assert not result.traced
    assert result.unevaluated
    assert "janus.source_column" in result.unevaluated[0]


def test_gather_reads_the_snapshot_for_this_columns_own_table_only():
    """A same-named column of another input must not answer for this one.

    The snapshot is keyed by dataset URN and then by field path. Flattening the
    datasets together is the collision that already cost this project once at
    model granularity, arriving one level down.
    """
    graph = traced_graph()
    snapshot_on_run(
        graph,
        # The feature's own table first and the decoy second, so a read that
        # flattened the datasets together would end up with the decoy's type.
        {
            FEATURE_TABLE_URN: {"prior_default_flag": "BOOLEAN"},
            TABLE_URN: {"prior_default_flag": "VARCHAR"},
        },
    )

    result = gather_feature(
        make_connection(graph, leaking_client()), LEAK_FEATURE_URN, MODEL_URN, "m", CONFIG
    )

    assert result.training_type == "BOOLEAN"
    assert result.current_type == "BOOLEAN"
    assert not result.drifted


def test_gather_reports_both_classifications_as_unevaluated_when_neither_is_set():
    graph = traced_graph()

    result = gather_feature(
        make_connection(graph, leaking_client()), LEAK_FEATURE_URN, MODEL_URN, "m", CONFIG
    )

    gaps = " ".join(result.unevaluated)
    assert "restricted exposure" in gaps
    assert "protected attribute exposure" in gaps


def test_gather_names_a_classified_ancestor_when_one_is_configured():
    graph = traced_graph()
    graph.set_aspect(LABEL_COLUMN_URN, Tags(tags=[TagAssociationClass(tag=SENSITIVE_TAG)]))
    config = replace(CONFIG, sensitive_tag_urns=(SENSITIVE_TAG,))

    result = gather_feature(
        make_connection(graph, leaking_client()), LEAK_FEATURE_URN, MODEL_URN, "m", config
    )

    assert [exposure.kind for exposure in result.exposures] == ["restricted"]
    assert result.exposures[0].column_name == "default_status"
    assert result.exposures[0].marker_urn == SENSITIVE_TAG
    # And the configured one is no longer listed as unevaluated.
    assert "restricted exposure" not in " ".join(result.unevaluated)


def test_gather_includes_the_features_own_table_among_the_tables_it_depends_on():
    """A reader asking which of these is stale would be surprised to miss it."""
    graph = traced_graph()
    graph._timeseries[(TABLE_URN, OperationClass)] = OperationClass(0, "UPDATE", NOW_MS)

    result = gather_feature(
        make_connection(graph, leaking_client()), LEAK_FEATURE_URN, MODEL_URN, "m", CONFIG
    )

    assert {table.dataset_urn for table in result.tables} == {FEATURE_TABLE_URN, TABLE_URN}


def test_gather_keeps_only_the_findings_that_name_this_feature():
    """A card carries its own feature's counterfactual, never the model's others."""
    graph = traced_graph()
    mine = make_leakage_finding()
    someone_elses = replace(
        make_leakage_finding(),
        leak=replace(mine.leak, source_column_urn=CLEAN_COLUMN_URN),
    )

    result = gather_feature(
        make_connection(graph, leaking_client()),
        LEAK_FEATURE_URN,
        MODEL_URN,
        "m",
        CONFIG,
        findings=(mine, someone_elses),
    )

    assert [finding.resource_urn for finding in result.findings] == [LEAK_COLUMN_URN]


def test_a_column_with_no_upstream_lineage_still_lists_its_own_table():
    """The seed of the depends-on set, which the chains would otherwise supply.

    A column the warehouse holds no derivation for is the common case on a
    freshly-ingested catalog, and a card that then listed no table at all would
    drop the one table a reader could actually go and look at.
    """
    graph = traced_graph()

    result = gather_feature(
        make_connection(graph, FakeClient()), LEAK_FEATURE_URN, MODEL_URN, "m", CONFIG
    )

    assert result.chains == ()
    assert [table.dataset_urn for table in result.tables] == [FEATURE_TABLE_URN]
