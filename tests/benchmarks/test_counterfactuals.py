"""The counterfactual harness, checked offline.

The measurement itself needs a live graph. What can be checked here is the part
that would fail silently: an applier keyed on a remedy kind no finding of that
family actually offers would never run, and the table would say "0 remedies
applied" in a column nobody reads as a failure.
"""

from __future__ import annotations

from benchmarks.counterfactuals import APPLIERS, CounterfactualCheck, MultiPathCheck, _cleared_state
from benchmarks.inject import build_trials
from benchmarks.run_bench import _counterfactual_lines
from modelguard.config import ScanConfig
from modelguard.models import Finding, FindingType, RemedyKind
from tests.conftest import (
    make_deprecated_input_finding,
    make_finding,
    make_leakage_finding,
    make_proxy_candidate_finding,
    make_schema_drift_finding,
    make_sensitive_source_finding,
    make_table_level_finding,
)

CONFIG = ScanConfig()

#: One finding per detector, so a remedy kind can be checked against the finding
#: that is supposed to offer it.
_FINDINGS: dict[FindingType, Finding] = {
    FindingType.UPSTREAM_FRESHNESS: make_finding(),
    FindingType.TARGET_LEAKAGE: make_leakage_finding(),
    FindingType.INPUT_SCHEMA_DRIFT: make_schema_drift_finding(),
    FindingType.SENSITIVE_SOURCE: make_sensitive_source_finding(),
    FindingType.DEPRECATED_INPUT: make_deprecated_input_finding(),
    FindingType.PROXY_CANDIDATE: make_proxy_candidate_finding(),
    FindingType.TABLE_LEVEL_RISK: make_table_level_finding(),
}


def test_every_detector_has_a_counterfactual_the_benchmark_can_actually_apply():
    """Otherwise a new detector ships with a remedy nobody ever performed.

    The whole claim of this measurement is that the suggested fixes were tried.
    A family with no applier quietly opts out of it.
    """
    families = {family for family, _ in APPLIERS}

    assert families == set(FindingType)


def test_every_applier_is_keyed_on_a_remedy_its_finding_really_offers():
    """A renamed kind would leave an applier that can never match anything.

    Nothing else would notice: the harness would report zero remedies applied,
    which reads as a limitation rather than as the bug it is.
    """
    for (family, kind), applier in APPLIERS.items():
        offered = {remedy.kind for remedy in _FINDINGS[family].counterfactual.remedies}
        assert kind in offered, f"{family}: {kind} is applied but never offered"
        assert callable(applier)


def test_the_remedied_state_of_a_freshness_trial_expects_a_fresh_table():
    """The freshness precondition compares the observed lag against the trial's own.

    Leaving the planted lag on the remedied trial would make the harness wait for
    the table to be stale again, which is the opposite of what a refresh means,
    and time it out into an error rather than a measurement.
    """
    trials = {trial.name: trial for trial in build_trials(CONFIG)}
    stale = trials["freshness-lag-30h"]
    leaking = trials["leakage-planted"]

    assert _cleared_state(stale).lag_hours == 0.0
    assert _cleared_state(stale).expected is False
    assert _cleared_state(leaking).lag_hours is None
    assert _cleared_state(leaking).graph_state is False


def _check(**kwargs: object) -> CounterfactualCheck:
    base: dict[str, object] = {"family": FindingType.TARGET_LEAKAGE, "fired": True}
    return CounterfactualCheck(**{**base, **kwargs})  # type: ignore[arg-type]


def test_a_finding_that_did_not_clear_is_rendered_as_a_failure():
    leak = make_leakage_finding()
    applied = tuple(r for r in leak.counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE)

    rendered = "\n".join(_counterfactual_lines((_check(applied=applied, cleared=False),), None))

    assert "**no**" in rendered
    assert RemedyKind.CUT_LINEAGE.value in rendered


def test_remedies_nobody_could_apply_are_named_rather_than_counted_as_passes():
    rendered = "\n".join(
        _counterfactual_lines(
            (_check(cleared=False, unapplied=(RemedyKind.RETRAIN,)),),
            None,
        )
    )

    assert RemedyKind.RETRAIN.value in rendered
    assert "not measured: nothing fired to remedy" not in rendered


def test_the_multi_path_row_marks_a_finding_that_went_quiet_after_half_a_fix():
    """The one row in this section that can genuinely go wrong."""
    rendered = "\n".join(
        _counterfactual_lines(
            (_check(cleared=True),),
            MultiPathCheck(
                paths_reported=2,
                edges_named=2,
                still_fires_after_one_cut=False,
                cleared_after_both_cuts=True,
            ),
        )
    )

    assert "Still fires after one of the two is cut: False (**wrong**)" in rendered


def test_a_counterfactual_section_is_absent_when_it_was_not_run():
    """Rule 4: the renderer never states a measurement the run did not produce."""
    assert _counterfactual_lines((), None) == []
