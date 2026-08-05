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
    DataProcessInstancePropertiesClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLModelPropertiesClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
)
from datahub.metadata.urns import SchemaFieldUrn

from janus.adapters import DeclaredFeature, DeclaredLink
from janus.config import ScanConfig
from janus.writeback.link import LinkError
from janus.writeback.link_infer import InferenceError, declared_proposal, infer_link
from tests.conftest import (
    FEATURE_TABLE_URN,
    LABEL_COLUMN_URN,
    LEAK_COLUMN_URN,
    MODEL_URN,
    TABLE_URN,
    TRAINING_RUN_URN,
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
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

    def test_several_inputs_become_the_shortlist_rather_than_a_coin_toss(self):
        """Which table holds the features is not something the graph says.

        It is, however, exactly the question the user can answer in one word, so
        the tables that were found are offered instead of being discarded.
        """
        graph = _graph(
            inputs={TRAINING_RUN_URN: [FEATURE_TABLE_URN, TABLE_URN]},
            schema=_default_schema(),
        )

        proposal = infer_link(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)

        assert proposal.feature_dataset_urn is None
        assert not proposal.complete
        assert set(proposal.candidates) == {FEATURE_TABLE_URN, TABLE_URN}
        assert "--features" in proposal.reasons[0]

    def test_a_run_parameter_naming_a_table_is_resolved_and_labelled_a_convention(self):
        """Route 2: what a plain mlflow ingest does carry, when the team logged it."""
        graph = _graph(runs=[TRAINING_RUN_URN], inputs={TRAINING_RUN_URN: []})
        graph.set_aspect(
            TRAINING_RUN_URN,
            DataProcessInstancePropertiesClass(
                name="credit_risk_v3_run",
                created=None,  # type: ignore[arg-type]
                customProperties={"dataset": "customer_features"},
            ),
        )
        graph.set_aspect(FEATURE_TABLE_URN, _default_schema())
        conn = make_connection(graph, FakeClient(search_urns=[FEATURE_TABLE_URN, TABLE_URN]))

        proposal = infer_link(conn, CONFIG, MODEL_URN)

        assert proposal.feature_dataset_urn == FEATURE_TABLE_URN
        assert "'dataset' parameter" in proposal.reasons[0]
        assert "convention, not a declaration" in proposal.reasons[0]

    def test_a_recorded_input_beats_a_run_parameter(self):
        """A declaration outranks a convention, and the reason says which it was."""
        graph = _graph(schema=_default_schema())
        graph.set_aspect(
            TRAINING_RUN_URN,
            DataProcessInstancePropertiesClass(
                name="credit_risk_v3_run",
                created=None,  # type: ignore[arg-type]
                customProperties={"dataset": "loans_raw"},
            ),
        )
        conn = make_connection(graph, FakeClient(search_urns=[TABLE_URN]))

        proposal = infer_link(conn, CONFIG, MODEL_URN)

        assert proposal.feature_dataset_urn == FEATURE_TABLE_URN
        assert "dataProcessInstanceInput" in proposal.reasons[0]

    def test_dataset_to_model_lineage_is_read_when_the_run_says_nothing(self):
        """Route 3: Spark and some sources declare this edge; reading it beats guessing."""
        graph = _graph(runs=[], inputs={}, schema=_default_schema())
        client = FakeClient(lineage_results=[lineage_result(FEATURE_TABLE_URN, 1)])

        proposal = infer_link(make_connection(graph, client), CONFIG, MODEL_URN)

        assert proposal.feature_dataset_urn == FEATURE_TABLE_URN
        assert "declares upstream of this model" in proposal.reasons[0]

    def test_a_run_with_no_inputs_explains_itself_instead_of_refusing(self):
        """The usual state after an mlflow ingest (D-074), and the case F10 is about.

        Raising here is what made --infer decline on precisely the stack this
        project validated against. It now returns the incomplete proposal, names
        the parameter that would fix it next time, and offers only tables that
        actually share a word with the model: nothing here shortlists at random.
        """
        unrelated = "urn:li:dataset:(urn:li:dataPlatform:snowflake,marketing.public.sessions,PROD)"
        graph = _graph(inputs={TRAINING_RUN_URN: []}, schema=_default_schema())
        client = FakeClient(search_urns=[FEATURE_TABLE_URN, unrelated])

        proposal = infer_link(make_connection(graph, client), CONFIG, MODEL_URN)

        assert proposal.feature_dataset_urn is None
        assert "records no inputs" in proposal.reasons[0]
        assert "log the training table as an MLflow run parameter" in proposal.reasons[0]
        # Neither table shares a word with credit_risk_v3, and the shortlist is
        # filtered by shared word, so nothing irrelevant is offered.
        assert proposal.candidates == ()

    def test_the_shortlist_offers_tables_sharing_a_word_with_the_model(self):
        graph = _graph(runs=[], inputs={}, schema=_default_schema())
        risky = (
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.credit_features,PROD)"
        )
        client = FakeClient(search_urns=[risky, TABLE_URN])

        proposal = infer_link(make_connection(graph, client), CONFIG, MODEL_URN)

        assert proposal.candidates == (risky,)

    def test_an_uningested_model_is_still_a_hard_error(self):
        """The CLI handles inference and link failures through one path."""
        graph = FakeGraph()

        with pytest.raises(LinkError, match="no mlModelProperties"):
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

    def test_a_label_declared_upstream_is_found_and_named_with_its_own_table(self):
        """The shape a real warehouse has: the label sits in its own mart.

        The feature table holds no label and never will; the declaration is on
        the raw column its feature descends from. Reading only this table's
        schema left the proposal permanently incomplete on the graph this
        project's own demo seeds, which is a refusal dressed as caution.
        """
        graph = _graph(schema=_schema(_field("applicant_income"), _field("prior_default_flag")))
        graph.set_aspect(
            LABEL_COLUMN_URN,
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
            ),
        )
        client = FakeClient(
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

        proposal = infer_link(make_connection(graph, client), CONFIG, MODEL_URN)

        assert proposal.label_column_urn == LABEL_COLUMN_URN
        assert proposal.complete
        assert "in ecommerce.public.loans_raw carries" in proposal.reasons[1]
        assert "reached from prior_default_flag" in proposal.reasons[1]
        # The label is in another table, so the command has to say which.
        assert "--label-table ecommerce.public.loans_raw" in proposal.command()

    def test_a_declaration_in_this_table_beats_one_found_upstream(self):
        """The nearer declaration is the one somebody made about this data."""
        graph = _graph(schema=_schema(_field("prior_default_flag"), _field("outcome")))
        for urn in (LABEL_COLUMN_URN, str(SchemaFieldUrn(FEATURE_TABLE_URN, "outcome"))):
            graph.set_aspect(
                urn,
                GlossaryTermsClass(
                    terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
                ),
            )
        client = FakeClient(
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

        proposal = infer_link(make_connection(graph, client), CONFIG, MODEL_URN)

        assert proposal.label_column_urn is not None
        assert proposal.label_column_urn.endswith("outcome)")

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

        assert "janus link" in command
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


class TestDeclaredProposal:
    """The other route to a proposal: a declaration an adapter read off disk.

    The graph half is the interesting part. A declaration names the features
    positively and `link` takes the complement, so this is where a column that
    exists in the table but in no declaration has to become an exclusion, and
    where a declaration that has drifted from the catalog has to stop everything.
    """

    @staticmethod
    def _declaration(*columns: str, label: str | None = None) -> DeclaredLink:
        return DeclaredLink(
            adapter="feast",
            name="churn_model_v1",
            source_table="customer_features",
            features=tuple(
                DeclaredFeature(name=column, source_column=column, declared_in="feature view 'v'")
                for column in columns
            ),
            label_column=label,
            label_table=None,
            reasons=("read 'churn_model_v1' from the Feast repo at .",),
        )

    def test_a_column_the_declaration_does_not_name_becomes_an_exclusion(self):
        graph = _graph(schema=_default_schema())
        conn = make_connection(graph, FakeClient())

        proposal = declared_proposal(
            conn,
            MODEL_URN,
            self._declaration("applicant_income", "prior_default_flag"),
            feature_dataset_urn=FEATURE_TABLE_URN,
            label_dataset_urn=None,
        )

        # applicant_id is the join key and default_status is the label's column:
        # neither is declared as a feature, so neither is one.
        assert proposal.exclude == {"applicant_id", "default_status"}
        assert proposal.feature_dataset_urn == FEATURE_TABLE_URN

    def test_the_adapter_reasons_are_kept_and_the_exclusion_is_explained(self):
        conn = make_connection(_graph(schema=_default_schema()), FakeClient())

        proposal = declared_proposal(
            conn,
            MODEL_URN,
            self._declaration("applicant_income"),
            feature_dataset_urn=FEATURE_TABLE_URN,
            label_dataset_urn=None,
        )

        assert proposal.reasons[0] == "read 'churn_model_v1' from the Feast repo at ."
        assert "excluded columns" in proposal.reasons[-1]
        assert "prior_default_flag" in proposal.reasons[-1]

    def test_a_declared_label_is_placed_on_the_table_the_caller_resolved(self):
        conn = make_connection(_graph(schema=_default_schema()), FakeClient())

        proposal = declared_proposal(
            conn,
            MODEL_URN,
            self._declaration("applicant_income", label="default_status"),
            feature_dataset_urn=FEATURE_TABLE_URN,
            label_dataset_urn=TABLE_URN,
        )

        assert proposal.label_column_urn == str(SchemaFieldUrn(TABLE_URN, "default_status"))
        assert proposal.complete

    def test_a_declaration_the_table_disagrees_with_stops_everything(self):
        """The failure this exists to prevent is the silent one.

        Linking the intersection would declare the features that still match and
        leave the renamed one undeclared, so the next scan would report a model
        it cannot fully see as clean.
        """
        conn = make_connection(_graph(schema=_default_schema()), FakeClient())

        with pytest.raises(LinkError) as caught:
            declared_proposal(
                conn,
                MODEL_URN,
                self._declaration("applicant_income", "tenure_months"),
                feature_dataset_urn=FEATURE_TABLE_URN,
                label_dataset_urn=None,
            )

        assert "tenure_months" in str(caught.value)
