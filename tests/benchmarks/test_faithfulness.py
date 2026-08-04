"""T-10's faithfulness check, offline.

The whole module is pure, so every case here is real: prose in, verdict out,
no graph and no provider. The cases that matter are the ones where a figure is
*almost* grounded, because that is what a language model actually produces. It
does not usually invent 47 out of nowhere; it divides 30 by 6 and writes "five
times", or rounds 30.0 to "about 30.5", and both read as measurements to a
human skimming an incident.
"""

from __future__ import annotations

from benchmarks.faithfulness import (
    FaithfulnessCheck,
    check,
    check_template_narratives,
    numbers_in,
    report,
    urns_in,
)
from modelguard.agent.narrate import grounding_facts, narrate
from tests.conftest import (
    MODEL_URN,
    make_deprecated_input_finding,
    make_finding,
    make_leakage_finding,
    make_schema_drift_finding,
    make_sensitive_source_finding,
    make_table_level_finding,
)

ALL_BUILDERS = (
    make_finding,
    make_leakage_finding,
    make_schema_drift_finding,
    make_sensitive_source_finding,
    make_deprecated_input_finding,
    make_table_level_finding,
)


class TestNumbersIn:
    def test_a_version_inside_a_name_is_not_a_figure(self):
        """`credit_risk_v3` is an identifier. Reading the 3 out of it would flag
        every model whose version is in its own name."""
        assert numbers_in("credit_risk_v3 is affected") == ()

    def test_a_trailing_underscore_number_is_not_a_figure(self):
        assert numbers_in("loans_raw_2024 went stale") == ()

    def test_a_unit_suffix_still_reads_as_a_figure(self):
        """A unit is not a name: 30.0h is a measurement, v3 is not."""
        assert numbers_in("stale for 30.0h") == (30.0,)

    def test_a_decimal_and_an_integer_of_equal_value_compare_equal(self):
        """The evidence renders 30.0; prose writing "30" quoted it exactly."""
        assert numbers_in("30")[0] == numbers_in("30.0")[0]

    def test_thousands_separators_are_read_as_one_figure(self):
        assert numbers_in("1,234 rows") == (1234.0,)

    def test_every_figure_is_returned_in_order(self):
        assert numbers_in("30.0 hours against 6.0, 1 model") == (30.0, 6.0, 1.0)


class TestUrnsIn:
    def test_a_urn_is_found_whole_including_its_nested_parentheses(self):
        assert urns_in(f"see {MODEL_URN}") == (MODEL_URN,)

    def test_a_sentence_ending_period_is_not_part_of_the_urn(self):
        assert urns_in(f"see {MODEL_URN}.") == (MODEL_URN,)

    def test_a_parenthetical_closing_bracket_is_not_part_of_the_urn(self):
        """The URN's own tail is `,PROD)`; the sentence's is one bracket more."""
        assert urns_in(f"(see {MODEL_URN})") == (MODEL_URN,)

    def test_prose_naming_no_urn_yields_none(self):
        assert urns_in("the model is at risk") == ()


class TestCheck:
    def test_an_invented_figure_is_caught(self):
        finding = make_finding()

        result = check(finding, "stale for 47 hours", source="template")

        assert not result.faithful
        assert [v.token for v in result.violations] == ["47"]

    def test_a_figure_derived_by_arithmetic_is_caught(self):
        """The one that matters. 30 divided by 6 is five, and five was never
        measured: a reader cannot tell it from a figure that was."""
        finding = make_finding()

        result = check(finding, "the lag is 5 times the 6.0 hour SLA", source="template")

        assert not result.faithful
        assert [v.token for v in result.violations] == ["5"]

    def test_a_rounded_figure_is_caught(self):
        finding = make_finding()

        result = check(finding, "stale for about 30.5 hours", source="template")

        assert not result.faithful

    def test_a_figure_from_the_per_type_detail_is_grounded(self):
        """hops is not in Finding.evidence, but it is in the prompt the model
        saw, so quoting it is faithful. Grounding against the mapping alone
        would report this as a hallucination."""
        finding = make_finding()
        assert "hops" not in finding.evidence
        assert "hops=3" in grounding_facts(finding)

        result = check(finding, "the model is 3 hops downstream", source="template")

        assert result.faithful

    def test_prose_quoting_no_figure_is_faithful_but_counts_nothing(self):
        """Faithful by this measure and says nothing, which is why the rate is
        reported beside the count."""
        result = check(make_finding(), "the table is stale", source="template")

        assert result.faithful
        assert result.numbers_checked == 0

    def test_a_urn_that_does_not_resolve_is_caught(self):
        ghost = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,ghost,PROD)"

        result = check(
            make_finding(), f"see {ghost}", source="template", resolves=lambda urn: False
        )

        assert not result.faithful
        assert [v.kind for v in result.violations] == ["urn"]

    def test_a_urn_that_resolves_is_faithful(self):
        result = check(
            make_finding(), f"see {MODEL_URN}", source="template", resolves=lambda urn: True
        )

        assert result.faithful

    def test_without_a_resolver_no_urn_is_counted_rather_than_passed(self):
        """A run with no graph must not read as a run where every URN resolved."""
        result = check(make_finding(), f"see {MODEL_URN}", source="template", resolves=None)

        assert result.urns_checked == 0
        assert result.faithful


class TestTemplates:
    def test_every_finding_types_template_quotes_only_measured_figures(self):
        """The stricter half of T-10: template prose is written in this repo, so
        a violation here is this project quoting a figure it never measured."""
        findings = [build() for build in ALL_BUILDERS]

        result = check_template_narratives(findings)

        assert result.rate == 1.0, [v.why for v in result.violations]

    def test_the_templates_do_quote_figures_so_the_rate_is_not_vacuous(self):
        """A rate of 1.00 over zero figures would be a construction proof."""
        findings = [build() for build in ALL_BUILDERS]

        result = check_template_narratives(findings)

        assert result.numbers_checked > 0

    def test_a_template_that_invented_a_figure_would_fail_this_suite(self):
        """Rule 6 in miniature: the check above is only worth reading because
        this one shows the same call rejecting prose that is not grounded."""
        finding = make_finding()
        unfaithful = narrate(finding, None).assessment + " That is 99.5 times the budget."

        result = check(finding, unfaithful, source="template")

        assert not result.faithful


class TestReport:
    def _check(self, *, faithful: bool, provider: str = "none") -> FaithfulnessCheck:
        from benchmarks.faithfulness import Unfaithful

        return FaithfulnessCheck(
            finding_type="upstream-freshness",
            source="template",
            provider=provider,
            numbers_checked=1,
            urns_checked=0,
            violations=() if faithful else (Unfaithful("number", "47", "invented"),),
        )

    def test_the_rate_is_the_share_of_faithful_narratives(self):
        result = report(
            [self._check(faithful=True), self._check(faithful=True), self._check(faithful=False)]
        )

        assert result.rate is not None
        assert abs(result.rate - 2 / 3) < 1e-9

    def test_a_run_that_narrated_nothing_reports_no_rate_rather_than_a_perfect_one(self):
        """The most misleading number this file could print."""
        assert report([]).rate is None

    def test_checks_group_by_who_wrote_the_prose(self):
        result = report(
            [self._check(faithful=True, provider="none"), self._check(faithful=True, provider="x")]
        )

        assert sorted(result.by_provider()) == ["none", "x"]

    def test_every_violation_is_carried_up_rather_than_only_counted(self):
        result = report([self._check(faithful=False), self._check(faithful=False)])

        assert len(result.violations) == 2


class TestRendering:
    """The section RESULTS.md carries. Rendered through the public entry point."""

    def _rendered(self, faithfulness: object) -> str:
        from datetime import UTC, datetime

        from benchmarks.run_bench import render_results
        from tests.benchmarks.test_report import BLAST, CONFIG, WRITEBACK

        return render_results(
            [],
            BLAST,
            WRITEBACK,
            CONFIG,
            generated_at=datetime(2026, 8, 4, tzinfo=UTC),
            faithfulness=faithfulness,  # type: ignore[arg-type]
        )

    def test_a_run_with_no_narratives_renders_no_section_at_all(self):
        """Rather than a section reporting a perfect rate over nothing."""
        assert "Narrative faithfulness" not in self._rendered(report([]))

    def test_the_section_reports_the_rate_and_the_figure_count_together(self):
        findings = [build() for build in ALL_BUILDERS]

        rendered = self._rendered(check_template_narratives(findings))

        assert "Narrative faithfulness (T-10)" in rendered
        assert "Figures checked:" in rendered
        assert "template (no LLM)" in rendered

    def test_an_unfaithful_run_prints_every_unsupported_claim(self):
        finding = make_finding()
        bad = check(finding, "stale for 47 hours", source="template")

        rendered = self._rendered(report([bad]))

        assert "47 appears in the assessment" in rendered

    def test_the_section_says_quality_is_still_not_scored(self):
        """The distinction T-10 rests on: this is a property, not a rubric."""
        rendered = self._rendered(check_template_narratives([make_finding()]))

        assert "quality" in rendered.lower()
