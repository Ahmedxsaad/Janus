"""The JSON and job-summary renderings, checked offline.

Both are pure functions of a report, so none of this needs DataHub. The
assertions that matter are the ones about a consumer being misled: that JSON is
actually parseable, that a check which never ran is never rendered as a pass,
and that the job summary is appended rather than overwriting a sibling step's
output.
"""

from __future__ import annotations

import json

import pytest

from janus.agent.pipeline import FindingWrites, ScanReport, TrustWrite
from janus.detect.coverage import Unevaluated
from janus.gate import GatePolicy, evaluate
from janus.models import Finding, FindingType, Severity
from janus.render import (
    AI_RMF_SUBCATEGORIES,
    CROSSWALK,
    CROSSWALK_DISCLAIMER,
    ENV_STEP_SUMMARY,
    crosswalk_markdown,
    job_summary_markdown,
    report_dict,
    report_json,
    write_job_summary,
)
from tests.conftest import MODEL_URN, make_trust_score
from tests.conftest import make_finding as _finding
from tests.conftest import make_leakage_finding as _leakage_finding


def _narrative(finding):  # noqa: ANN202 - a stand-in, never asserted on
    from janus.agent.narrate import narrate

    return narrate(finding, None)


def _report(
    *findings: Finding,
    trust: tuple[TrustWrite, ...] = (),
    not_evaluated: tuple[Unevaluated, ...] = (),
) -> ScanReport:
    return ScanReport(
        run_id="render-test",
        table_urn=None,
        model_urn=MODEL_URN,
        dry_run=True,
        writes=tuple(
            FindingWrites(finding=finding, narrative=_narrative(finding)) for finding in findings
        ),
        trust=trust,
        assertion_yaml="",
        warnings=(),
        not_evaluated=not_evaluated,
    )


def _trust(value: int) -> TrustWrite:
    return TrustWrite(
        model_urn=MODEL_URN,
        model_name="Credit Risk v3",
        score=make_trust_score(value),
    )


def _gap() -> Unevaluated:
    return Unevaluated(
        check="target leakage",
        target_urn=MODEL_URN,
        reason="the model declares no features",
        remedy="run janus link",
    )


class TestJson:
    def test_a_report_round_trips_through_json(self):
        """The whole point of --format json: a program can actually parse it."""
        rendered = report_json(_report(_leakage_finding(), trust=(_trust(40),)))
        parsed = json.loads(rendered)

        assert parsed["run_id"] == "render-test"
        assert parsed["clean"] is False
        assert parsed["findings"][0]["severity"] in {level.value for level in Severity}
        assert parsed["trust"][0]["score"] == 40

    def test_evidence_survives_as_data_not_prose(self):
        """A consumer acts on the measured facts, so they are carried separately."""
        finding = _leakage_finding()
        parsed = json.loads(report_json(_report(finding)))

        assert parsed["findings"][0]["evidence"] == dict(finding.evidence)
        assert parsed["findings"][0]["assessment_source"] in {"llm", "template"}

    def test_a_clean_scan_still_reports_what_never_ran(self):
        """The failure this guards: "clean" read as "checked and healthy"."""
        parsed = json.loads(report_json(_report(not_evaluated=(_gap(),))))

        assert parsed["clean"] is True
        assert parsed["not_evaluated"][0]["check"] == "target leakage"
        assert parsed["not_evaluated"][0]["remedy"] == "run janus link"

    def test_a_plain_scan_omits_the_gate_key_rather_than_nulling_it(self):
        """A present-but-null gate reads as "the policy passed" to a naive check."""
        assert "gate" not in report_dict(_report(_finding()))

    def test_a_gated_report_carries_the_verdict(self):
        report = _report(_leakage_finding())
        verdict = evaluate(report, GatePolicy(block_at_or_above=Severity.HIGH))
        parsed = json.loads(report_json(report, verdict))

        assert parsed["gate"]["blocked"] is True
        assert parsed["gate"]["exit_code"] == 1
        assert parsed["gate"]["enforced"] is True
        assert parsed["gate"]["violations"]

    def test_every_value_is_json_native(self):
        """No enum, URN object, or dataclass leaks through to break json.dumps."""
        report = _report(_finding(), _leakage_finding(), trust=(_trust(10),))
        verdict = evaluate(report, GatePolicy(min_trust_score=80))

        # Round-tripping proves it: json.dumps would already have raised, and
        # comparing against a reparse proves nothing was coerced to a repr.
        payload = report_dict(report, verdict)
        assert json.loads(json.dumps(payload)) == payload


class TestJobSummary:
    def test_findings_are_listed_with_their_severity(self):
        markdown = job_summary_markdown(_report(_leakage_finding()))

        assert "| Finding | Severity | Models at risk |" in markdown
        assert "render-test" in markdown

    def test_a_blocked_gate_says_so_in_the_headline(self):
        report = _report(_leakage_finding())
        verdict = evaluate(report, GatePolicy(block_at_or_above=Severity.HIGH))

        assert job_summary_markdown(report, verdict).startswith("### Janus: BLOCKED")

    def test_a_passing_gate_still_names_the_checks_that_did_not_run(self):
        """A green summary is exactly where a skipped check is most expensive."""
        report = _report(not_evaluated=(_gap(),))
        verdict = evaluate(report, GatePolicy(block_at_or_above=Severity.HIGH))
        markdown = job_summary_markdown(report, verdict)

        assert "passed" in markdown
        assert "Not evaluated" in markdown
        assert "the model declares no features" in markdown

    def test_a_long_findings_list_is_truncated_rather_than_endless(self):
        report = _report(*[_leakage_finding() for _ in range(25)])
        markdown = job_summary_markdown(report)

        assert "...and 5 more" in markdown

    def test_nothing_is_written_when_the_variable_is_unset(self, monkeypatch):
        """Every run outside GitHub Actions. Absence is normal, never an error."""
        monkeypatch.delenv(ENV_STEP_SUMMARY, raising=False)

        assert write_job_summary("anything") is None

    def test_the_summary_is_appended_so_a_sibling_step_is_not_erased(self, monkeypatch, tmp_path):
        """Several steps share one file; truncating would delete another's output."""
        target = tmp_path / "summary.md"
        target.write_text("### an earlier step\n", encoding="utf-8")
        monkeypatch.setenv(ENV_STEP_SUMMARY, str(target))

        written = write_job_summary("### Janus: no finding\n")

        assert written == target
        assert target.read_text(encoding="utf-8") == (
            "### an earlier step\n### Janus: no finding\n"
        )


@pytest.mark.parametrize("clean", [True, False])
def test_the_headline_never_claims_health_without_a_check(clean: bool):
    """Guards the one phrasing that would mislead: "no finding" with nothing run."""
    report = _report(*(() if clean else (_finding(),)), not_evaluated=(_gap(),))
    headline = job_summary_markdown(report).splitlines()[0]

    if clean:
        assert "some checks did not run" in headline
    else:
        assert "finding(s)" in headline


class TestCrosswalk:
    """The NIST AI RMF crosswalk is generated, so it cannot fall behind (T-02)."""

    def test_every_detector_has_a_row(self):
        """Adding a detector without a crosswalk row leaves a hole in a filed document."""
        assert set(CROSSWALK) == set(FindingType)

    def test_every_cited_subcategory_is_quoted(self):
        """A row citing an id with no verbatim text is a citation a reader cannot check."""
        cited = {sub_id for row in CROSSWALK.values() for sub_id in row.subcategory_ids}
        assert cited <= set(AI_RMF_SUBCATEGORIES)

    def test_every_quoted_subcategory_is_cited(self):
        """Text nobody cites is dead weight in an artifact somebody has to read."""
        cited = {sub_id for row in CROSSWALK.values() for sub_id in row.subcategory_ids}
        assert set(AI_RMF_SUBCATEGORIES) <= cited

    def test_each_row_cites_one_subcategory_per_function(self):
        for finding_type, row in CROSSWALK.items():
            assert row.map_id.startswith("MAP "), finding_type
            assert row.measure_id.startswith("MEASURE "), finding_type
            assert row.manage_id.startswith("MANAGE "), finding_type

    def test_the_markdown_carries_one_row_per_detector_and_the_disclaimer(self):
        markdown = crosswalk_markdown()

        assert CROSSWALK_DISCLAIMER in markdown
        for row in CROSSWALK.values():
            assert f"| {row.detector} |" in markdown
        for sub_id, text in AI_RMF_SUBCATEGORIES.items():
            assert f"**{sub_id}**: {text}" in markdown

    def test_the_markdown_is_not_a_conformity_claim(self):
        """The whole artifact turns on this distinction; assert it rather than trust it."""
        assert "not a conformity claim" in crosswalk_markdown()


def test_the_json_carries_each_finding_counterfactual_with_its_kinds_and_targets():
    """T-03: a program routing findings into a ticket needs the fix, not only the fault.

    The kind and the targets, not just the sentence: a consumer that wanted prose
    already has the assessment, and one that wants to act needs the machine-
    readable half.
    """
    finding = _leakage_finding()
    payload = report_dict(_report(finding))

    counterfactual = payload["findings"][0]["counterfactual"]
    assert counterfactual["paths"] == 1
    assert [r["kind"] for r in counterfactual["remedies"]] == [
        remedy.kind.value for remedy in finding.counterfactual.remedies
    ]
    assert counterfactual["remedies"][0]["targets"] == ["prior_default_flag <- default_status"]
    # It has to survive the trip: the JSON is the public interface.
    assert json.loads(report_json(_report(finding)))["findings"][0]["counterfactual"]
