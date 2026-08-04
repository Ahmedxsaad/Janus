"""T-12 and T-13: the two per-model artifacts. Offline: renderers are pure.

Most of these are about what the documents refuse to say. Both land in a
catalog under an authoritative-looking title, and the evidence pack's title
names a regulation, so the failure mode that matters is not a missing section:
it is a document that reads as an approval. The tests below pin the disclaimer,
its position, and the rule that anything the graph does not record is named as
unrecorded rather than quietly omitted.
"""

from __future__ import annotations

from modelguard.detect.coverage import Unevaluated
from modelguard.models import ModelRef
from modelguard.writeback.model_documents import (
    NOT_RECORDED,
    FeatureProvenance,
    ModelFacts,
    render_evidence_pack,
    render_model_card,
)
from tests.conftest import (
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    MODEL_URN,
    make_finding,
    make_trust_score,
)

TRACED = FeatureProvenance(
    feature_urn="urn:li:mlFeature:(credit_risk,applicant_income)",
    feature_name="applicant_income",
    source_column_urn="urn:li:schemaField:(x,applicant_income)",
    source_column_name="applicant_income",
    source_dataset_name="ecommerce.public.customer_features",
)
UNTRACED = FeatureProvenance(
    feature_urn="urn:li:mlFeature:(credit_risk,mystery)",
    feature_name="mystery",
    source_column_urn=None,
    source_column_name=None,
    source_dataset_name=None,
)


def _facts(**overrides: object) -> ModelFacts:
    defaults: dict = {
        "model": ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,),
            has_owner=False,
        ),
        "description": "Predicts applicant default.",
        "version": "3",
        "features": (TRACED,),
        "training_runs": ("urn:li:dataProcessInstance:credit_risk_v3_run",),
        "input_datasets": (FEATURE_TABLE_URN,),
        "training_schemas": {FEATURE_TABLE_URN: {"applicant_income": "NUMBER"}},
    }
    defaults.update(overrides)
    return ModelFacts(**defaults)  # type: ignore[arg-type]


class TestEvidencePackIsNotACertification:
    def test_it_says_so_before_it_says_anything_else(self):
        """09 section 5.2: a generated document implying conformity would be the.

        single most damaging thing this project could ship. So the denial is not
        a footnote, it is the first heading a reader meets.
        """
        rendered = render_evidence_pack(_facts())

        headings = [line for line in rendered.splitlines() if line.startswith("## ")]
        assert headings[0] == "## This is not a compliance certification"

    def test_it_refuses_all_three_readings_a_filer_might_take(self):
        rendered = render_evidence_pack(_facts())

        assert "not a conformity assessment" in rendered
        assert "not a certification" in rendered
        assert "not legal advice" in rendered
        assert "must not be filed or cited" in rendered

    def test_what_it_could_not_establish_comes_before_what_it_could(self):
        """A gap at the end of a long document is a gap nobody reads."""
        rendered = render_evidence_pack(_facts())

        assert rendered.index("could NOT establish") < rendered.index("Training data provenance")

    def test_it_cites_the_articles_by_number_so_the_mapping_is_checkable(self):
        rendered = render_evidence_pack(_facts())

        assert "Article 10" in rendered
        assert "Article 12" in rendered
        assert "2024/1689" in rendered

    def test_it_names_the_mapping_as_this_project_s_reading_rather_than_fact(self):
        rendered = render_evidence_pack(_facts())

        assert "this project's reading" in rendered


class TestNothingIsQuietlyOmitted:
    def test_a_feature_with_no_provenance_is_named_not_dropped(self):
        """Dropping it would make the provenance table read as complete."""
        rendered = render_evidence_pack(_facts(features=(TRACED, UNTRACED)))

        assert "mystery" in rendered
        assert "1 of 2 declared feature(s)" in rendered

    def test_a_missing_training_schema_is_reported_as_not_established(self):
        rendered = render_evidence_pack(_facts(training_schemas={}))

        assert "input schema at training time" in rendered.lower()
        assert "cannot state what the data" in rendered

    def test_freshness_at_training_time_is_always_named_as_unavailable(self):
        """The one Article 10 fact this project measures a near-miss of: it knows.

        freshness now, and nothing records it as of the run. Saying "fresh" here
        would be answering a different question than the one asked.
        """
        rendered = render_evidence_pack(_facts())

        assert "Freshness of each input at training time" in rendered
        assert "is not a substitute" in rendered

    def test_the_coverage_gaps_from_the_scan_reach_both_artifacts(self):
        gap = Unevaluated(
            check="sensitive source",
            target_urn=MODEL_URN,
            reason="no classification is configured",
            remedy="Set MODELGUARD_SENSITIVE_TAG_URNS.",
        )
        facts = _facts(gaps=(gap,))

        assert "no classification is configured" in render_evidence_pack(facts)
        assert "no classification is configured" in render_model_card(facts)


class TestModelCard:
    def test_an_undeclared_intended_use_says_so_rather_than_inventing_one(self):
        """The one section a graph cannot derive: it is a statement of purpose."""
        rendered = render_model_card(_facts(description=None))

        assert NOT_RECORDED in rendered
        assert "statement of purpose" in rendered

    def test_a_declared_intended_use_is_quoted(self):
        rendered = render_model_card(_facts(description="Predicts applicant default."))

        assert "Predicts applicant default." in rendered

    def test_an_unowned_model_is_marked_unowned(self):
        rendered = render_model_card(_facts())

        assert "Unowned" in rendered

    def test_the_trust_waterfall_is_rendered_once_not_twice(self):
        """Render_trust_waterfall carries its own heading; a second would read.

        as a rendering bug and cost the artifact credibility it needs.
        """
        score = make_trust_score(70, deductions={"leakage": 20.0})

        rendered = render_model_card(_facts(score=score))

        assert rendered.count("## Trust score") == 1

    def test_a_model_with_no_findings_says_that_is_a_smaller_claim_than_it_looks(self):
        rendered = render_model_card(_facts(findings=()))

        assert "not a statement that the model is sound" in rendered

    def test_open_findings_are_listed_worst_first_as_the_scan_ordered_them(self):
        rendered = render_model_card(_facts(findings=(make_finding(),)))

        assert "Stale upstream data" in rendered
        assert "critical" in rendered

    def test_it_states_that_the_data_itself_was_never_read(self):
        rendered = render_model_card(_facts())

        assert "never read this model's training data" in rendered

    def test_a_card_with_no_findings_is_not_an_approval(self):
        rendered = render_model_card(_facts())

        assert "not an approval" in rendered


class TestPurity:
    def test_both_renderers_are_pure_functions_of_the_facts(self):
        """No connection, no graph, no clock. Two calls, identical output."""
        facts = _facts()

        assert render_model_card(facts) == render_model_card(facts)
        assert render_evidence_pack(facts) == render_evidence_pack(facts)

    def test_an_empty_model_still_renders_both_artifacts(self):
        """A model DataHub ingested and nobody linked: the common real case."""
        bare = ModelFacts(
            model=ModelRef(
                urn=MODEL_URN,
                name="bare",
                deployments=(),
                live_deployments=(),
                has_owner=False,
            ),
            description=None,
            version=None,
        )

        card = render_model_card(bare)
        pack = render_evidence_pack(bare)

        assert NOT_RECORDED in card
        assert "No features are declared" in card
        assert "not a certification" in pack
