"""The benchmark's arithmetic, checked offline.

A wrong precision still looks like a precision, so these assert the numbers
against hand-worked cases rather than against the implementation's own output.
"""

from __future__ import annotations

import pytest

from benchmarks.metrics import (
    Confusion,
    Latency,
    confusion,
    format_rate,
    format_seconds,
    fraction,
    meets,
)


def test_counting_sorts_each_pair_into_the_right_cell():
    # 2 caught, 1 missed, 1 false alarm, 3 correctly quiet.
    observations = [
        (True, True),
        (True, True),
        (True, False),
        (False, True),
        (False, False),
        (False, False),
        (False, False),
    ]
    matrix = confusion(observations)

    assert matrix.true_positives == 2
    assert matrix.false_negatives == 1
    assert matrix.false_positives == 1
    assert matrix.true_negatives == 3
    assert matrix.total == 7


def test_the_rates_match_the_hand_worked_values():
    matrix = Confusion(true_positives=2, false_positives=1, true_negatives=3, false_negatives=1)

    assert matrix.precision == pytest.approx(2 / 3)
    assert matrix.recall == pytest.approx(2 / 3)
    assert matrix.f1 == pytest.approx(2 / 3)
    assert matrix.false_positive_rate == pytest.approx(1 / 4)


def test_f1_is_the_harmonic_mean_not_the_average():
    """The case that separates the two: they disagree whenever P and R differ."""
    matrix = Confusion(true_positives=1, false_positives=3, true_negatives=0, false_negatives=0)

    assert matrix.precision == pytest.approx(0.25)
    assert matrix.recall == pytest.approx(1.0)
    assert matrix.f1 == pytest.approx(0.4)  # harmonic; the arithmetic mean is 0.625


def test_a_perfect_detector_scores_one_across_the_board():
    matrix = confusion([(True, True), (True, True), (False, False), (False, False)])

    assert matrix.precision == 1.0
    assert matrix.recall == 1.0
    assert matrix.f1 == 1.0
    assert matrix.false_positive_rate == 0.0


def test_a_detector_that_fires_on_everything_is_caught_by_the_false_positive_rate():
    """Recall alone would call this perfect. That is why the FP rate is reported."""
    matrix = confusion([(True, True), (False, True), (False, True)])

    assert matrix.recall == 1.0
    assert matrix.false_positive_rate == 1.0
    assert matrix.precision == pytest.approx(1 / 3)


def test_precision_is_undefined_rather_than_zero_when_nothing_fired():
    """Zero would read as a detector that is always wrong. It raised no alerts."""
    matrix = confusion([(False, False), (False, False)])

    assert matrix.precision is None
    assert matrix.recall is None
    assert matrix.f1 is None
    assert matrix.false_positive_rate == 0.0


def test_recall_is_undefined_when_there_was_nothing_to_catch():
    assert confusion([(False, False)]).recall is None


def test_f1_is_undefined_when_precision_and_recall_are_both_zero():
    matrix = Confusion(true_positives=0, false_positives=1, true_negatives=0, false_negatives=1)

    assert matrix.precision == 0.0
    assert matrix.recall == 0.0
    assert matrix.f1 is None  # the harmonic mean would divide by zero


def test_counting_nothing_yields_every_rate_undefined():
    matrix = confusion([])

    assert matrix.total == 0
    assert matrix.precision is None
    assert matrix.recall is None
    assert matrix.false_positive_rate is None


def test_latency_reports_the_median_and_the_worst_case():
    latency = Latency((0.1, 0.5, 0.2, 0.4, 0.3))

    assert latency.count == 5
    assert latency.median_s == pytest.approx(0.3)
    assert latency.worst_s == pytest.approx(0.5)


def test_latency_with_no_samples_reports_nothing_rather_than_zero():
    latency = Latency(())

    assert latency.count == 0
    assert latency.median_s is None
    assert latency.worst_s is None


def test_a_fraction_of_nothing_is_undefined():
    assert fraction(1, 2) == pytest.approx(0.5)
    assert fraction(0, 0) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "-"), (0.0, "0.00"), (1.0, "1.00"), (2 / 3, "0.67")],
)
def test_an_unmeasured_rate_renders_as_a_dash_never_as_zero(value, expected):
    assert format_rate(value) == expected


def test_seconds_render_with_a_unit_and_an_unmeasured_one_as_a_dash():
    assert format_seconds(1.239) == "1.24s"
    assert format_seconds(None) == "-"


def test_a_target_is_met_at_the_boundary_not_only_past_it():
    assert meets(0.95, 0.95) == "meets target"
    assert meets(0.94, 0.95) == "BELOW TARGET"


def test_a_lower_is_better_target_inverts_the_comparison():
    assert meets(0.05, 0.05, higher_is_better=False) == "meets target"
    assert meets(0.06, 0.05, higher_is_better=False) == "ABOVE TARGET"


def test_an_unmeasured_value_neither_passes_nor_fails():
    assert meets(None, 0.95) == "not measured"
    assert meets(None, 0.05, higher_is_better=False) == "not measured"
