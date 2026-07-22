"""How the report treats a trial that could not be scored.

DataHub indexes asynchronously, so a trial can fail because the harness never saw
the planted state, which says nothing about the detector. Counting that as a miss
would let a slow Elasticsearch quietly depress recall and publish it as a
ModelGuard number. These pin the alternative: excluded from the tables, disclosed
in the report, and never silently dropped.

``render_results`` is pure, so all of this runs offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from benchmarks.inject import Target, Trial
from benchmarks.run_bench import BlastRadiusCheck, TrialOutcome, WriteBackCheck, render_results
from modelguard.config import ScanConfig
from modelguard.models import FindingType

CONFIG = ScanConfig(freshness_sla_hours=6.0)
WHEN = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _trial(name: str, family: FindingType, *, expected: bool) -> Trial:
    return Trial(
        name=name,
        family=family,
        target=Target.MODEL,
        expected=expected,
        detail=f"{name} detail",
        plant=lambda conn, trial, now_ms: None,
    )


def _outcome(trial: Trial, observed: bool, *, settled: float | None = 1.0) -> TrialOutcome:
    return TrialOutcome(trial=trial, observed=observed, detect_seconds=0.1, settle_seconds=settled)


WRITEBACK = WriteBackCheck(
    incidents_after_first=1,
    incidents_after_second=1,
    trust_score_written=True,
    trust_band_written=True,
)
BLAST = BlastRadiusCheck(expected_models=1, found_models=1, named_the_live_deployment=True)


def _report(outcomes: list[TrialOutcome]) -> str:
    return render_results(outcomes, BLAST, WRITEBACK, CONFIG, generated_at=WHEN)


def test_an_unscoreable_trial_is_kept_out_of_the_detection_table():
    """The errored trial here would otherwise read as a false positive."""
    good = _trial("good", FindingType.TARGET_LEAKAGE, expected=True)
    broken = _trial("broken", FindingType.TARGET_LEAKAGE, expected=False)

    report = _report([_outcome(good, True), _outcome(broken, True, settled=None)])

    row = next(line for line in report.splitlines() if line.startswith("| Target leakage"))
    assert "| 1 |" in row, f"the errored trial was counted: {row}"
    # Scored on the one good trial alone, so precision stays 1.00 rather than 0.50.
    assert row.count("1.00") >= 2


def test_an_unscoreable_trial_is_disclosed_by_name():
    """Excluding it quietly would be worse than counting it."""
    broken = _trial("broken", FindingType.INPUT_SCHEMA_DRIFT, expected=True)

    report = _report([_outcome(broken, False, settled=None)])

    assert "## Unscoreable trials" in report
    assert "broken" in report
    assert "broken detail" in report


def test_a_clean_run_has_no_unscoreable_section():
    good = _trial("good", FindingType.TARGET_LEAKAGE, expected=True)
    report = _report([_outcome(good, True)])

    assert "## Unscoreable trials" not in report


def test_the_header_states_how_many_trials_could_not_be_scored():
    good = _trial("good", FindingType.TARGET_LEAKAGE, expected=True)
    broken = _trial("broken", FindingType.TARGET_LEAKAGE, expected=True)

    report = _report([_outcome(good, True), _outcome(broken, False, settled=None)])

    assert "- Trials: 2 (1 unscoreable)" in report


def test_a_detector_with_no_scoreable_trials_reports_dashes_not_zeroes():
    broken = _trial("broken", FindingType.TARGET_LEAKAGE, expected=True)
    report = _report([_outcome(broken, False, settled=None)])

    row = next(line for line in report.splitlines() if line.startswith("| Target leakage"))
    assert row == "| Target leakage (P1) | 0 | - | - | - | - |"


def test_an_errored_trial_does_not_contribute_a_latency_sample():
    """Its detector call happened against a graph in the wrong state."""
    good = _trial("good", FindingType.TARGET_LEAKAGE, expected=True)
    broken = _trial("broken", FindingType.TARGET_LEAKAGE, expected=True)

    report = _report([_outcome(good, True), _outcome(broken, True, settled=None)])

    assert "over 1 trials" in report


def test_a_wrong_answer_is_marked_in_the_freshness_table():
    """A miss must be visible as a row, not only folded into an aggregate."""
    missed = Trial(
        name="freshness-lag-30h",
        family=FindingType.UPSTREAM_FRESHNESS,
        target=Target.TABLE,
        expected=True,
        detail="30h lag",
        plant=lambda conn, trial, now_ms: None,
        lag_hours=30.0,
    )

    report = _report([_outcome(missed, False)])

    row = next(line for line in report.splitlines() if line.startswith("| 30h"))
    assert "**wrong**" in row


def test_the_report_states_what_it_does_not_measure():
    """The limits are part of the result, not a footnote to be dropped."""
    report = _report([_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)])

    assert "## What this does not measure" in report
    for absent in ("scale test", "Great Expectations", "Evidently"):
        assert absent in report


def test_a_missing_blast_radius_says_so_rather_than_printing_a_score():
    outcomes = [_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)]
    report = render_results(outcomes, None, WRITEBACK, CONFIG, generated_at=WHEN)

    assert "Not measured" in report


@pytest.mark.parametrize(
    ("second", "expected_marker"),
    [(1, "meets target"), (2, "ABOVE TARGET")],
)
def test_a_duplicate_incident_on_rerun_is_reported_against_its_target(second, expected_marker):
    writeback = WriteBackCheck(
        incidents_after_first=1,
        incidents_after_second=second,
        trust_score_written=True,
        trust_band_written=True,
    )
    outcomes = [_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)]
    report = render_results(outcomes, BLAST, writeback, CONFIG, generated_at=WHEN)

    line = next(line for line in report.splitlines() if "Duplicates created" in line)
    assert expected_marker in line
