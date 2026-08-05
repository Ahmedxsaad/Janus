"""Tests for the scale measurement's arithmetic.

Offline: the sweep itself needs a live graph and fifty replicas, but what it
reports is arithmetic over two integers and is the part that can be wrong
silently. A per-model figure that divided by the wrong denominator would still
render a plausible table.
"""

from __future__ import annotations

import pytest

from benchmarks.scale import ScaleMeasurement, WritePathCost


def _cost(detect: int = 29, write: int = 67, reconcile: int = 158) -> WritePathCost:
    """A cost with the shape a live run produced, so the numbers mean something."""
    return WritePathCost(
        detect_reads=detect,
        write_reads=write,
        reconcile_reads=reconcile,
        dry_run_seconds=0.75,
        write_seconds=8.92,
    )


def test_per_model_figures_divide_by_the_catalog_size():
    measurement = ScaleMeasurement(models=50, seconds=100.0, graph_reads=1350)

    assert measurement.seconds_per_model == 2.0
    assert measurement.reads_per_model == 27.0


def test_an_empty_sweep_does_not_divide_by_zero():
    """A sweep of nothing reports nothing, rather than raising in the renderer."""
    measurement = ScaleMeasurement(models=0, seconds=0.0, graph_reads=0)

    assert measurement.seconds_per_model == 0.0
    assert measurement.reads_per_model == 0.0


def test_the_write_path_total_is_every_phase():
    assert _cost().total_write_reads == 254


def test_amplification_is_the_whole_write_path_over_detection_alone():
    """The claim the section makes: a write scan is not detection plus a write."""
    cost = _cost()

    assert cost.amplification == pytest.approx(254 / 29)
    # Not write+reconcile over detect, and not total over the two phases: both
    # render a plausible multiple of a different question.
    assert cost.amplification != pytest.approx((67 + 158) / 29)


def test_reconcile_share_is_of_the_total_not_of_the_writes():
    cost = _cost()

    assert cost.reconcile_share == pytest.approx(158 / 254)
    assert cost.reconcile_share != pytest.approx(158 / (158 + 67))


def test_a_run_that_read_nothing_reports_zero_rather_than_raising():
    cost = WritePathCost(
        detect_reads=0,
        write_reads=0,
        reconcile_reads=0,
        dry_run_seconds=0.0,
        write_seconds=0.0,
    )

    assert cost.amplification == 0.0
    assert cost.reconcile_share == 0.0


def test_reconciliation_dominating_is_what_the_section_reports():
    """The finding worth publishing: the expensive phase is not the writes."""
    cost = _cost()

    assert cost.reconcile_reads > cost.write_reads + cost.detect_reads
    assert cost.reconcile_share > 0.5
