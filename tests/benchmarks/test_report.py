"""How the report treats a trial that could not be scored.

DataHub indexes asynchronously, so a trial can fail because the harness never saw
the planted state, which says nothing about the detector. Counting that as a miss
would let a slow Elasticsearch quietly depress recall and publish it as a
Janus number. These pin the alternative: excluded from the tables, disclosed
in the report, and never silently dropped.

``render_results`` is pure, so all of this runs offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from benchmarks.inject import Target, Trial
from benchmarks.run_bench import BlastRadiusCheck, TrialOutcome, WriteBackCheck, render_results
from benchmarks.scale import ScaleMeasurement
from janus.config import ScanConfig
from janus.lifecycle import TypeLifecycle
from janus.models import FindingType

CONFIG = ScanConfig(freshness_sla_hours=6.0)
WHEN = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _trial(name: str, family: FindingType, *, expected: bool, boundary: bool = False) -> Trial:
    return Trial(
        name=name,
        family=family,
        target=Target.MODEL,
        expected=expected,
        detail=f"{name} detail",
        plant=lambda conn, trial, now_ms: None,
        boundary=boundary,
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


def _lifecycle_report(*, lifecycle: tuple[TypeLifecycle, ...]) -> str:
    """A report carrying only the incident-lifecycle section's inputs (T-16)."""
    return render_results([], BLAST, WRITEBACK, CONFIG, generated_at=WHEN, lifecycle=lifecycle)


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
    assert row == "| Target leakage (P1) | 0 | - | - | - | - | 0 | Not run |"


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


def test_a_measured_sweep_replaces_the_no_scale_test_caveat():
    """A report still claiming "no scale test" beside a scale table is untrustworthy.

    The point of the disclosure section is that it tracks what was actually run.
    A caveat that outlives the gap it described teaches a reader to skip the
    section, which is where the caveats that are still true live.
    """
    outcomes = [_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)]
    report = render_results(
        outcomes,
        BLAST,
        WRITEBACK,
        CONFIG,
        generated_at=WHEN,
        scale=(
            ScaleMeasurement(models=1, seconds=0.4, graph_reads=12),
            ScaleMeasurement(models=10, seconds=4.2, graph_reads=120),
        ),
    )

    assert "## Scale" in report
    assert "| 10 |" in report
    assert "scale test" not in report
    # The remaining limits are still stated, including the new ceiling.
    assert "sweeps up to 10" in report
    assert "10k/100k-entity curve" in report


def test_the_scale_section_is_absent_when_nothing_was_swept():
    """An empty measurement renders no table rather than an empty one."""
    report = _report([_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)])

    assert "## Scale" not in report
    assert "scale test" in report


def test_the_per_model_cost_is_reported_not_only_the_total():
    """The total says how long to wait; the per-model figure says whether it scales."""
    outcomes = [_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)]
    report = render_results(
        outcomes,
        BLAST,
        WRITEBACK,
        CONFIG,
        generated_at=WHEN,
        scale=(ScaleMeasurement(models=50, seconds=25.0, graph_reads=600),),
    )

    row = next(line for line in report.splitlines() if line.startswith("| 50 |"))
    # 25s over 50 models is 0.5s each, and 600 reads is 12.0 each.
    assert "0.50s" in row
    assert "12.0" in row


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


def test_a_missing_comparison_is_omitted_rather_than_half_rendered():
    """A slow index must not cost a complete benchmark run.

    measure_leakage_approaches returns empty when the graph never reaches the
    state it planted, because a comparison drawn against the wrong state would be
    wrong rather than merely missing. The report then simply has no comparison
    section, which is honest, instead of a table nobody can trust.
    """
    report = _report([_outcome(_trial("g", FindingType.TARGET_LEAKAGE, expected=True), True)])

    assert "## Why column-level lineage, measured" not in report
    # The rest of the report still renders.
    assert "## Detection" in report
    assert "## Write-back and idempotency" in report


def test_a_row_with_no_boundary_trial_says_it_could_not_have_failed():
    """F6: a perfect score over trials that cannot fail is a construction proof.

    The reader should not have to open the injector to find that out, so the row
    itself says which of the two it is.
    """
    obvious = _trial("obvious", FindingType.INPUT_SCHEMA_DRIFT, expected=True)

    report = _report([_outcome(obvious, True)])

    row = next(line for line in report.splitlines() if line.startswith("| Input schema drift"))
    assert "| 0 | No: presence or absence of one planted fact |" in row


def test_a_row_with_a_boundary_trial_counts_it_and_names_the_mutation():
    at_the_line = _trial("at-the-line", FindingType.TARGET_LEAKAGE, expected=True, boundary=True)
    obvious = _trial("obvious", FindingType.TARGET_LEAKAGE, expected=False)

    report = _report([_outcome(at_the_line, True), _outcome(obvious, False)])

    row = next(line for line in report.splitlines() if line.startswith("| Target leakage"))
    assert "| 1 | Yes: an off-by-one in the hop cap" in row


def test_every_detector_has_a_row_label_so_none_is_silently_dropped():
    """A family with no label renders no row, silently.

    The report would then be short one detector with nothing saying so.

    Exactly what happened when the proxy detector first ran: 31 trials
    executed, four of them proxy trials, and the table printed six rows
    (D-117).
    """
    from benchmarks.run_bench import _DETECTOR_LABELS

    assert set(_DETECTOR_LABELS) == set(FindingType)


def _row(
    family: FindingType,
    *,
    raised: int = 0,
    open_now: int = 0,
    resolved: int = 0,
    mean_ms: float | None = None,
    median_ms: float | None = None,
) -> TypeLifecycle:
    return TypeLifecycle(
        finding_type=family,
        raised=raised,
        open_now=open_now,
        resolved=resolved,
        mean_ms=mean_ms,
        median_ms=median_ms,
    )


def test_the_lifecycle_section_says_these_are_not_production_mttrs():
    """The caveat is the section (T-16), and it goes above the table.

    Almost every duration on a benchmark graph is the gap between a planted
    failure and the next trial's restore. Publishing the table without saying so
    would be this project quoting its own fixture back as an operational result,
    which is exactly the misreading the rest of the report is built to prevent.
    """
    markdown = _lifecycle_report(
        lifecycle=(
            _row(
                FindingType.TARGET_LEAKAGE,
                raised=3,
                resolved=3,
                mean_ms=3_600_000,
                median_ms=3_600_000,
            ),
        )
    )

    section = markdown.split("## Incident lifecycle")[1]
    table_start = section.index("| Detector |")
    assert "not production MTTRs" in section[:table_start]


def test_the_lifecycle_table_reports_the_median_beside_the_mean():
    markdown = _lifecycle_report(
        lifecycle=(
            _row(
                FindingType.TARGET_LEAKAGE,
                raised=2,
                resolved=2,
                mean_ms=7_200_000,
                median_ms=3_600_000,
            ),
        )
    )

    assert "| target-leakage | 2 | 0 | 2 | 2.00h | 1.00h |" in markdown


def test_a_detector_that_never_fired_is_a_dash_not_a_zero_duration():
    """Zero hours would claim findings of that type close instantly."""
    markdown = _lifecycle_report(lifecycle=(_row(FindingType.PROXY_CANDIDATE),))

    assert "| proxy-candidate | 0 | 0 | 0 | - | - |" in markdown


def test_an_all_zero_lifecycle_table_explains_itself_rather_than_reading_as_clean():
    """A graph nothing has written to must not look like a graph with no problems."""
    markdown = _lifecycle_report(lifecycle=(_row(FindingType.TARGET_LEAKAGE),))

    assert "no incident on this graph carries Janus's" in markdown
