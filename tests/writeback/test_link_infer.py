"""Inferring a link from the graph. Offline: no DataHub, no network.

Two things matter here and they pull in opposite directions. The inference has to
be useful enough that a user stops typing four arguments, and honest enough that
they can tell a fact from a guess before they accept it. So every test asserts on
the *reasons* alongside the values: a proposal that is right for a reason nobody
can check is not better than no proposal.

The failure that would be worst is a confidently wrong label. A leakage verdict
computed against the wrong label column is wrong in both directions at once, so
the case where nothing names a label returns an incomplete proposal rather than
a guessed one.
"""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLModelPropertiesClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
)
from datahub.metadata.urns import SchemaFieldUrn

from modelguard.config import ScanConfig
from modelguard.writeback.link import LinkError
from modelguard.writeback.link_infer import InferenceError, infer_link
from tests.conftest import (
    FEATURE_TABLE_URN,
    MODEL_URN,
    TABLE_URN,
    TRAINING_RUN_URN,
    FakeClient,
    FakeGraph,
    make_connection,
)

CONFIG = ScanConfig()
LABEL_TERM_URN = CONFIG.label_term_urn


def _field(path: str, *, part_of_key: bool = False, partitioning: bool = False) -> SchemaFieldClass:
    return SchemaFieldClass(
        fieldPath=path,
        type=SchemaFieldDataTypeClass(type=StringTypeClass()),
        nativeDataType="VARCHAR",
        isPartOfKey=part_of_key,
        isPartitioningKey=partitioning,
    )


def _schema(
    *fields: SchemaFieldClass, primary_keys: list[str] | None = None
) -> SchemaMetadataClass:
    return SchemaMetadataClass(
        schemaName="customer_features",
        platform="urn:li:dataPlatform:postgres",
        version=0,
        hash="",
        platformSchema=None,  # type: ignore[arg-type]
        fields=list(fields),
        primaryKeys=primary_keys,
    )


def _graph(
    *,
    runs: list[str] | None = None,
    inputs: dict[str, list[str]] | None = None,
    schema: SchemaMetadataClass | None = None,
) -> FakeGraph:
    """A model, its training run, and the schema of the table that run read."""
    aspects: dict[tuple[str, type], object] = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            name="Credit Risk v3",
            trainingJobs=runs if runs is not None else [TRAINING_RUN_URN],
        ),
    }
    for run_urn, dataset_urns in (inputs or {TRAINING_RUN_URN: [FEATURE_TABLE_URN]}).items():
        aspects[(run_urn, DataProcessInstanceInputClass)] = DataProcessInstanceInputClass(
            inputs=dataset_urns
        )
    if schema is not None:
        aspects[(FEATURE_TABLE_URN, SchemaMetadataClass)] = schema
    return FakeGraph(aspects=aspects)  # type: ignore[arg-type]


def _default_schema() -> SchemaMetadataClass:
    return _schema(
        _field("applicant_id", part_of_key=True),
        _field("applicant_income"),
        _field("prior_default_flag"),
        _field("default_status"),
    )


class TestFeatureTable:
    def test_the_single_recorded_input_is_proposed(self):
        conn = make_connection(_graph(schema=_default_schema()), FakeClient())

        proposal = infer_link(conn, CONFIG, MODEL_URN)

        assert proposal.feature_dataset_urn == FEATURE_TABLE_URN
        assert "dataProcessInstanceInput" in proposal.reasons[0]

    def test_several_inputs_are_a_question_not_a_coin_toss(self):
        """Which table holds the features is not something the graph says."""
        graph = _graph(
            inputs={TRAINING_RUN_URN: [FEATURE_TABLE_URN, TABLE_URN]},
            schema=_default_schema(),
        )

        with pytest.raises(InferenceError) as exc:
            infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert "--features" in str(exc.value)
        assert "loans_raw" in str(exc.value)

    def test_no_training_run_names_the_arguments_that_would_work_instead(self):
        graph = _graph(runs=[], inputs={}, schema=_default_schema())

        with pytest.raises(InferenceError) as exc:
            infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert "records no training run" in str(exc.value)
        assert "--label-column" in str(exc.value)

    def test_a_run_with_no_inputs_says_so_rather_than_searching_the_catalog(self):
        """The usual state after an mlflow ingest, and guessing here would be wild."""
        graph = _graph(inputs={TRAINING_RUN_URN: []}, schema=_default_schema())

        with pytest.raises(InferenceError) as exc:
            infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert "records no input datasets" in str(exc.value)

    def test_an_inference_failure_is_catchable_as_a_link_failure(self):
        """The CLI handles both through one path; a new subclass must not escape it."""
        graph = _graph(runs=[], inputs={}, schema=_default_schema())

        with pytest.raises(LinkError):
            infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

    def test_a_table_with_no_schema_cannot_be_proposed_from(self):
        with pytest.raises(InferenceError) as exc:
            infer_link(make_connection(_graph(), FakeClient()), CONFIG, MODEL_URN)

        assert "no schemaMetadata" in str(exc.value)


class TestLabelColumn:
    def test_a_declared_label_is_used_and_reported_as_declared(self):
        """Somebody applied the term in the UI or an earlier link. Not a guess."""
        graph = _graph(schema=_schema(_field("applicant_income"), _field("outcome_flag")))
        graph.set_aspect(
            str(SchemaFieldUrn(FEATURE_TABLE_URN, "outcome_flag")),
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
            ),
        )

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert proposal.label_column_urn is not None
        assert proposal.label_column_urn.endswith("outcome_flag)")
        assert "declared rather than guessed" in proposal.reasons[1]

    def test_a_name_match_is_used_but_flagged_as_a_guess(self):
        conn = make_connection(_graph(schema=_default_schema()), FakeClient())

        proposal = infer_link(conn, CONFIG, MODEL_URN)

        assert proposal.label_column_urn is not None
        assert proposal.label_column_urn.endswith("default_status)")
        assert "guess" in proposal.reasons[1]

    def test_a_declared_label_beats_a_name_match(self):
        """Both present: the declaration wins, because somebody meant it."""
        graph = _graph(schema=_schema(_field("target"), _field("actual_outcome")))
        graph.set_aspect(
            str(SchemaFieldUrn(FEATURE_TABLE_URN, "actual_outcome")),
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
            ),
        )

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert proposal.label_column_urn is not None
        assert proposal.label_column_urn.endswith("actual_outcome)")

    def test_no_label_leaves_the_proposal_incomplete_rather_than_guessing_one(self):
        """A wrong label makes every leakage verdict wrong in both directions."""
        graph = _graph(schema=_schema(_field("applicant_income"), _field("tenure_months")))

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert proposal.label_column_urn is None
        assert not proposal.complete
        assert "NOT FOUND" in proposal.reasons[1]
        assert "--label-column" in proposal.reasons[1]

    def test_the_configured_names_are_what_is_matched(self):
        """Override the list and a column that used to match stops matching."""
        graph = _graph(schema=_default_schema())
        config = ScanConfig(label_column_names=("churned",))

        proposal = infer_link(make_connection(graph, FakeClient()), config, MODEL_URN)

        assert proposal.label_column_urn is None


class TestExcludedColumns:
    def test_declared_keys_are_excluded(self):
        graph = _graph(
            schema=_schema(
                _field("applicant_id", part_of_key=True),
                _field("load_date", partitioning=True),
                _field("region", part_of_key=False),
                _field("target"),
                primary_keys=["region"],
            )
        )

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        # applicant_id from isPartOfKey, load_date from isPartitioningKey, region
        # from primaryKeys, target because a label is not a feature of itself.
        assert proposal.exclude == frozenset({"applicant_id", "load_date", "region", "target"})

    def test_nothing_is_excluded_on_a_name_that_merely_looks_like_a_key(self):
        """customer_id is usually a join key and score_id is usually a feature.

        No rule over names separates them, so nothing here tries: only the
        warehouse's own key declarations are read.
        """
        graph = _graph(schema=_schema(_field("customer_id"), _field("target")))

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert "customer_id" not in proposal.exclude
        assert "--exclude" in proposal.reasons[2]

    def test_a_label_in_another_table_is_not_excluded_from_this_one(self):
        graph = _graph(schema=_schema(_field("applicant_income"), _field("tenure_months")))
        graph.set_aspect(
            str(SchemaFieldUrn(TABLE_URN, "default_status")),
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
            ),
        )

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        # The label lives in loans_raw, not in the feature table, so it was never
        # a candidate column here and nothing about it is excluded.
        assert proposal.exclude == frozenset()


class TestRenderedCommand:
    def test_the_command_is_the_one_a_person_would_have_typed(self):
        conn = make_connection(_graph(schema=_default_schema()), FakeClient())

        command = infer_link(conn, CONFIG, MODEL_URN).command()

        assert "modelguard link" in command
        assert "--model credit_risk_v3" in command
        assert "--features ecommerce.public.customer_features" in command
        assert "--label-column default_status" in command
        assert "--exclude applicant_id" in command

    def test_a_label_in_another_table_gets_its_own_flag(self):
        graph = _graph(schema=_schema(_field("applicant_income")))
        graph.set_aspect(
            str(SchemaFieldUrn(TABLE_URN, "default_status")),
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
            ),
        )
        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        # The label is not in the feature table, so nothing proposes it; the
        # rendered command is therefore the incomplete one, without a label.
        assert proposal.label_column_urn is None
        assert "--label-column" not in proposal.command()

    def test_an_incomplete_proposal_still_renders_what_it_does_know(self):
        graph = _graph(schema=_schema(_field("applicant_income")))

        command = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN).command()

        assert "--features ecommerce.public.customer_features" in command
        assert "--label-column" not in command
