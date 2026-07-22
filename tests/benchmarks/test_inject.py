"""The trial matrix, checked offline.

These guard the ground truth itself. Every other number the benchmark publishes
is derived from these labels, so a label that is quietly wrong does not produce a
visible failure, it produces a confident, wrong RESULTS.md.
"""

from __future__ import annotations

from benchmarks.inject import SWEEP_LAG_HOURS, Target, Trial, build_trials
from modelguard.config import ScanConfig
from modelguard.models import FindingType

SLA = 6.0
CONFIG = ScanConfig(freshness_sla_hours=SLA)


def _trials(family: FindingType) -> list[Trial]:
    return [trial for trial in build_trials(CONFIG) if trial.family is family]


def test_a_lag_is_labelled_stale_only_when_it_exceeds_the_sla():
    for trial in _trials(FindingType.UPSTREAM_FRESHNESS):
        assert trial.lag_hours is not None
        assert trial.expected == (trial.lag_hours > SLA), trial.name


def test_a_lag_exactly_at_the_sla_is_not_stale():
    """Spending the whole budget is not yet an overrun, and the sweep pins it."""
    at_sla = [t for t in _trials(FindingType.UPSTREAM_FRESHNESS) if t.lag_hours == SLA]

    assert len(at_sla) == 1, "the sweep must probe the exact boundary"
    assert at_sla[0].expected is False


def test_the_sweep_probes_both_sides_of_the_boundary_closely():
    """A sweep only at the extremes would pass an off-by-an-hour comparison."""
    lags = set(SWEEP_LAG_HOURS)
    just_under = {lag for lag in lags if SLA - 1 < lag < SLA}
    just_over = {lag for lag in lags if SLA < lag < SLA + 1}

    assert just_under, "no lag sits just inside the SLA"
    assert just_over, "no lag sits just outside the SLA"


def test_the_sweep_carries_both_labels():
    """A sweep that was all positives could not measure a false-positive rate."""
    expectations = {trial.expected for trial in _trials(FindingType.UPSTREAM_FRESHNESS)}
    assert expectations == {True, False}


def test_the_sla_comes_from_the_config_not_from_a_constant():
    """The benchmark must measure the boundary the scan enforces."""
    trials = [
        trial
        for trial in build_trials(ScanConfig(freshness_sla_hours=12.0))
        if trial.family is FindingType.UPSTREAM_FRESHNESS
    ]

    at_eight = next(t for t in trials if t.lag_hours == 8.0)
    assert at_eight.expected is False  # inside a 12h SLA, though outside a 6h one


def test_every_detector_has_a_positive_and_a_negative_trial():
    """Without both, precision and the false-positive rate are unmeasurable."""
    for family in FindingType:
        expectations = {trial.expected for trial in _trials(family)}
        assert expectations == {True, False}, f"{family} lacks both labels"


def test_leakage_and_drift_are_scanned_as_models_and_freshness_as_a_table():
    """The two targets answer different questions and run different detectors."""
    for trial in build_trials(CONFIG):
        expected_target = (
            Target.TABLE if trial.family is FindingType.UPSTREAM_FRESHNESS else Target.MODEL
        )
        assert trial.target is expected_target, trial.name


def test_trial_names_are_unique():
    """Names key the results table; a duplicate would silently overwrite a row."""
    names = [trial.name for trial in build_trials(CONFIG)]
    assert len(names) == len(set(names))


def test_the_matrix_is_deterministic():
    """Rule 1: same run, same numbers. Two builds must not differ."""
    first = build_trials(CONFIG)
    second = build_trials(CONFIG)

    assert [(t.name, t.expected, t.lag_hours) for t in first] == [
        (t.name, t.expected, t.lag_hours) for t in second
    ]


def test_every_trial_can_plant_itself():
    assert all(callable(trial.plant) for trial in build_trials(CONFIG))
