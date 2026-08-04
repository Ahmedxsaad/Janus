"""The trial matrix, checked offline.

These guard the ground truth itself. Every other number the benchmark publishes
is derived from these labels, so a label that is quietly wrong does not produce a
visible failure, it produces a confident, wrong RESULTS.md.
"""

from __future__ import annotations

from benchmarks.inject import SWEEP_LAG_HOURS, Target, Trial, build_trials
from modelguard.config import ScanConfig
from modelguard.models import FindingType
from modelguard.seed import graph_spec as spec

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


def test_the_leakage_boundary_trials_move_the_question_not_the_graph():
    """F6: the leak stays planted; the cap and the label term are what change.

    That is what makes them cheap enough to have: no second warehouse to seed,
    and each one can still go the wrong way. The precondition therefore has to
    wait on ``graph_state`` (the leak is there) rather than on ``expected`` (the
    detector must stay quiet), or the trial is unscoreable by construction.
    """
    boundary = {
        t.name: t for t in _trials(FindingType.TARGET_LEAKAGE) if t.boundary and t.overrides
    }

    assert set(boundary) == {
        "leakage-at-hop-cap",
        "leakage-past-hop-cap",
        "leakage-named-not-declared",
    }
    assert all(trial.graph_state is True for trial in boundary.values())
    assert boundary["leakage-named-not-declared"].expected is False


def test_the_multi_path_trials_name_the_label_columns_they_wait_for():
    """T-03: with two derivations planted, "a label is reachable" is not the question.

    Both trials leave the feature descending from a declared label, so the plain
    precondition would pass on either graph and on the wrong one. Each therefore
    names the exact set of label columns it expects to be reachable, and that set
    is what tells the two apart.
    """
    trials = {t.name: t for t in _trials(FindingType.TARGET_LEAKAGE) if t.leak_upstreams}

    assert set(trials) == {"leakage-two-paths", "leakage-one-of-two-cut"}
    assert len(trials["leakage-two-paths"].leak_upstreams or ()) == 2
    assert len(trials["leakage-one-of-two-cut"].leak_upstreams or ()) == 1
    # Both expect the detector to fire: the second is the half-fix, and a finding
    # that went quiet there would be the tool endorsing an incomplete remedy.
    assert all(trial.expected for trial in trials.values())
    assert set(trials["leakage-one-of-two-cut"].leak_upstreams or ()).isdisjoint(
        {spec.LABEL_SOURCE_COLUMN}
    ), "the quoted path is the one that gets cut"


def test_a_trial_config_carries_only_that_trials_overrides():
    trials = {trial.name: trial for trial in build_trials(CONFIG)}

    capped = trials["leakage-past-hop-cap"].config(CONFIG)
    plain = trials["leakage-planted"].config(CONFIG)

    assert capped.leakage_max_hops == 0
    assert capped.freshness_sla_hours == CONFIG.freshness_sla_hours
    assert plain is CONFIG, "a trial with no override must not copy the config"


def test_the_freshness_trials_near_the_sla_are_the_ones_marked_boundary():
    """A 30h lag against a 6h SLA cannot fail; 5.5 and 6.5 can."""
    marked = {t.lag_hours for t in _trials(FindingType.UPSTREAM_FRESHNESS) if t.boundary}

    assert marked == {5.5, 6.0, 6.5}
