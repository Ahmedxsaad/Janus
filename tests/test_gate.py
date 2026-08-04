"""The CI gate's policy, checked offline.

A gate that wrongly passes is worse than no gate, because a team stops looking, so
the assertions here are mostly about the failure directions: that a violation is
never silently tolerated, and that "I could not tell" is never reported as "your
model is fine".

``evaluate`` is pure, so all of this runs against hand-built reports with no
DataHub anywhere.
"""

from __future__ import annotations

import pytest

from modelguard.agent.pipeline import FindingWrites, ScanReport, TrustWrite
from modelguard.config import SCORE_PROVENANCE
from modelguard.gate import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_PASS,
    GatePolicy,
    GateVerdict,
    Violation,
    evaluate,
    github_annotations,
    summary,
)
from modelguard.models import Finding, Severity, TrustBand
from tests.conftest import MODEL_URN, make_trust_score
from tests.conftest import make_finding as _finding
from tests.conftest import make_leakage_finding as _leakage_finding


def _narrative(finding):  # noqa: ANN202 - a stand-in, never asserted on
    from modelguard.agent.narrate import narrate

    return narrate(finding, None)


def _report(*findings: Finding, trust: tuple[TrustWrite, ...] = ()) -> ScanReport:
    """A dry-run report holding the given findings, as the gate receives one."""
    return ScanReport(
        run_id="gate-test",
        table_urn=None,
        model_urn=MODEL_URN,
        dry_run=True,
        writes=tuple(
            FindingWrites(finding=finding, narrative=_narrative(finding)) for finding in findings
        ),
        trust=trust,
        assertion_yaml="",
        warnings=(),
    )


def _trust(value: int, *, name: str = "Credit Risk v3") -> TrustWrite:
    band = TrustBand.HEALTHY if value >= 70 else TrustBand.AT_RISK
    return TrustWrite(
        model_urn=MODEL_URN,
        model_name=name,
        score=make_trust_score(value, band=band),
    )


# --------------------------------------------------------------------------
# The severity rule
# --------------------------------------------------------------------------


def test_a_finding_at_the_threshold_blocks():
    """The boundary must be included, or the named level never fires."""
    verdict = evaluate(_report(_leakage_finding()), GatePolicy(block_at_or_above=Severity.CRITICAL))

    assert verdict.blocked
    assert verdict.exit_code == EXIT_BLOCKED


def test_a_finding_below_the_threshold_does_not_block():
    """A leakage finding is critical; a policy blocking on nothing worse passes it."""
    verdict = evaluate(_report(_leakage_finding()), GatePolicy(block_at_or_above=None))

    assert not verdict.blocked
    assert verdict.exit_code == EXIT_PASS


def test_severity_is_compared_by_rank_not_by_enum_order():
    """The enum is declared worst-first, so a naive >= would inverit the meaning.

    A medium-severity policy must block a critical finding. If the comparison ran
    on the enum's own ordering the critical one would slip through, which is the
    exact direction a gate must never fail in.
    """
    freshness = _finding(live=True)  # critical: a live model on stale data
    assert freshness.severity is Severity.CRITICAL

    verdict = evaluate(_report(freshness), GatePolicy(block_at_or_above=Severity.MEDIUM))

    assert verdict.blocked, "a critical finding escaped a medium-severity policy"


def test_every_violation_is_reported_not_only_the_first():
    """One run must tell the author everything to fix, not send them round again."""
    verdict = evaluate(
        _report(_finding(), _leakage_finding()),
        GatePolicy(block_at_or_above=Severity.LOW),
    )

    assert len(verdict.violations) == 2


# --------------------------------------------------------------------------
# The trust rule
# --------------------------------------------------------------------------


def test_a_model_below_the_trust_floor_blocks():
    verdict = evaluate(_report(trust=(_trust(35),)), GatePolicy(min_trust_score=70))

    assert verdict.blocked
    assert "35/100" in verdict.violations[0].headline


def test_a_model_exactly_at_the_floor_passes():
    """The floor is a minimum, so meeting it is meeting it."""
    verdict = evaluate(_report(trust=(_trust(70),)), GatePolicy(min_trust_score=70))

    assert not verdict.blocked


def test_the_two_rules_are_independent():
    """A healthy score must not excuse a critical finding."""
    verdict = evaluate(
        _report(_leakage_finding(), trust=(_trust(95),)),
        GatePolicy(block_at_or_above=Severity.HIGH, min_trust_score=70),
    )

    assert verdict.blocked
    assert len(verdict.violations) == 1


# --------------------------------------------------------------------------
# The default, and the shape of the answer
# --------------------------------------------------------------------------


def test_a_policy_with_no_rule_blocks_nothing():
    """A gate that fails on installation gets removed the same afternoon."""
    verdict = evaluate(_report(_leakage_finding()), GatePolicy())

    assert not verdict.blocked
    assert verdict.exit_code == EXIT_PASS
    assert not verdict.policy.blocks_anything


def test_an_unenforced_run_says_so_rather_than_claiming_a_pass():
    """Silence would read as approval. It was not asked to approve anything."""
    verdict = evaluate(_report(_leakage_finding()), GatePolicy())

    assert "no blocking policy set" in summary(verdict)


def test_a_clean_scan_passes_and_says_what_it_saw():
    verdict = evaluate(_report(), GatePolicy(block_at_or_above=Severity.LOW))

    assert not verdict.blocked
    assert "PASSED" in summary(verdict)


def test_findings_seen_counts_tolerated_findings_too():
    """The count is what was found, not what was blocked on."""
    verdict = evaluate(_report(_leakage_finding()), GatePolicy(min_trust_score=70))

    assert verdict.findings_seen == 1
    assert not verdict.blocked


def test_the_error_code_is_never_a_verdict():
    """A verdict is only ever 0 or 1.

    2 means the gate could not tell, and only the CLI raises it: no report can
    produce one, or a broken DataHub would read as a policy violation.
    """
    for report, policy in (
        (_report(), GatePolicy()),
        (_report(_leakage_finding()), GatePolicy(block_at_or_above=Severity.LOW)),
    ):
        assert evaluate(report, policy).exit_code != EXIT_ERROR


# --------------------------------------------------------------------------
# CI annotations
# --------------------------------------------------------------------------


def test_each_violation_becomes_one_github_annotation():
    verdict = evaluate(
        _report(_finding(), _leakage_finding()), GatePolicy(block_at_or_above=Severity.LOW)
    )
    lines = github_annotations(verdict)

    assert len(lines) == 2
    assert all(line.startswith("::error title=ModelGuard: ") for line in lines)


def test_a_passing_verdict_emits_no_annotations():
    assert github_annotations(evaluate(_report(), GatePolicy(min_trust_score=70))) == ()


def test_a_newline_in_a_message_cannot_truncate_the_annotation():
    """A raw newline ends the workflow command; the rest becomes stray log noise."""
    verdict = GateVerdict(
        violations=(Violation(headline="line one\nline two", detail="detail one\ndetail two"),),
        findings_seen=1,
        policy=GatePolicy(min_trust_score=70),
    )

    line = github_annotations(verdict)[0]
    assert "\n" not in line
    assert "%0A" in line


@pytest.mark.parametrize("value", [0, 1, 69, 100])
def test_the_trust_floor_is_a_strict_comparison_at_every_value(value: int):
    """Guards the boundary at more than one point, so an off-by-one is visible."""
    verdict = evaluate(_report(trust=(_trust(value),)), GatePolicy(min_trust_score=70))

    assert verdict.blocked is (value < 70)


class TestAdvisory:
    """--min-trust is a blunt control and the policy says so (F7 step 3, T-01)."""

    def test_a_trust_floor_alone_cautions_that_the_scale_is_undefined(self):
        advisory = GatePolicy(min_trust_score=80).advisory

        assert "--block-at-or-above" in advisory
        assert SCORE_PROVENANCE in advisory

    def test_a_trust_floor_beside_a_severity_policy_is_not_cautioned(self):
        assert GatePolicy(min_trust_score=80, block_at_or_above=Severity.HIGH).advisory == ""

    def test_a_severity_policy_alone_is_not_cautioned(self):
        assert GatePolicy(block_at_or_above=Severity.HIGH).advisory == ""

    def test_an_empty_policy_is_not_cautioned(self):
        """Nothing is being gated on, so there is nothing to caution about."""
        assert GatePolicy().advisory == ""

    def test_the_advisory_never_blocks_by_itself(self):
        """It is advice about how the policy is written, not a violation."""
        policy = GatePolicy(min_trust_score=80)
        verdict = evaluate(_report(trust=(_trust(90),)), policy)

        assert policy.advisory
        assert verdict.blocked is False
