"""Janus-Bench: run the detectors against a live graph and publish the numbers.

Run it with a DataHub Quickstart up and the ML graph seeded::

    janus-seed
    python -m benchmarks.run_bench --out benchmarks/RESULTS.md

Why a live graph
----------------
The detectors could be scored against in-memory fixtures in a fraction of the
time, and the resulting numbers would be worth nothing: they would measure the
fixtures. Every trial here plants a real fact in a real DataHub, waits for the
graph to show it, and asks the shipped detector, through the same read path a
user's scan takes (benchmarks/CLAUDE.md rule 4).

What is measured, and how each is isolated
------------------------------------------
* **Detection** calls the detector functions directly, with no LLM and no writes,
  so precision and recall describe detection alone. Narration and write-back
  latency cannot flatter or spoil them.
* **Detection latency** is the time that one detector call takes on an already
  converged graph. It is not an end-to-end MTTD: the wait for DataHub to index a
  change is reported separately, because that is DataHub's latency, not
  Janus's, and adding them would blame the wrong system.
* **Write-back correctness and idempotency** need real writes, so they run one
  full ``run_scan`` and then a second one, and read the graph back through
  DataHub rather than trusting the report the scan returned.

Numbers land as measured. If a detector misses its target the table says so; this
module has no path that retries a trial until it passes (benchmarks/CLAUDE.md
rule 4).
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from benchmarks import baselines, metrics
from benchmarks.baselines import LEAKAGE_APPROACHES, Approach
from benchmarks.counterfactuals import (
    CounterfactualCheck,
    MultiPathCheck,
    findings_for,
    measure_counterfactuals,
    measure_multi_path,
)
from benchmarks.faithfulness import FaithfulnessReport, check_template_narratives
from benchmarks.ingested import IngestedScore, measure_ingested
from benchmarks.inject import (
    Trial,
    await_precondition,
    build_trials,
    restore_baseline,
)
from benchmarks.mutation_report import END_MARKER as MUTATION_END
from benchmarks.mutation_report import START_MARKER as MUTATION_START
from benchmarks.scale import (
    ScaleMeasurement,
    WritePathCost,
    measure_scale,
    measure_write_path,
)
from janus.agent.pipeline import run_scan
from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import TABLE_LEVEL_PRECISION, ScanConfig
from janus.detect.blast_radius import blast_radius
from janus.detect.leakage import leakage_findings
from janus.discovery import search_model_urns
from janus.lifecycle import TypeLifecycle, mttr_by_type, read_lifecycles
from janus.models import Finding, FindingType
from janus.seed import graph_spec as spec
from janus.seed import scenarios
from janus.seed.scenarios import plant_stale_source
from janus.writeback.incidents import attached_incident_urns
from janus.writeback.properties import TRUST_BAND, TRUST_SCORE, read_properties

#: The lag the write-back and idempotency checks plant. Well past any plausible
#: SLA, because those checks are about what gets written, not about the boundary.
WRITEBACK_LAG_HOURS = 30.0


@dataclass(frozen=True)
class TrialOutcome:
    """What one trial did."""

    trial: Trial
    observed: bool
    """Whether the detector fired."""
    detect_seconds: float
    """How long the detector call itself took."""
    settle_seconds: float | None
    """How long DataHub took to show the planted state, or None if it never did."""

    @property
    def errored(self) -> bool:
        """Whether the precondition failed, making this trial unscoreable."""
        return self.settle_seconds is None

    @property
    def correct(self) -> bool:
        """Whether the detector agreed with ground truth."""
        return self.observed == self.trial.expected


def _observe(conn: DataHubConnection, config: ScanConfig, trial: Trial, now_ms: int) -> bool:
    """Ask the detector under test whether it fires. One call, no retries.

    Whether it fires is exactly whether it returns a finding, so this goes
    through the same dispatch the counterfactual measurement uses rather than a
    second copy of it: two lists of detectors could disagree, and the one that
    silently dropped a detector would report a perfect score for it.
    """
    return bool(findings_for(conn, config, trial.family, now_ms))


def run_trials(
    conn: DataHubConnection,
    config: ScanConfig,
    trials: Sequence[Trial],
    *,
    log: bool = True,
) -> tuple[TrialOutcome, ...]:
    """Plant, wait, detect once, record. In the order the matrix declares."""
    outcomes: list[TrialOutcome] = []
    for index, trial in enumerate(trials, start=1):
        now_ms = int(time.time() * 1000)
        trial.plant(conn, trial, now_ms)
        # The trial's own config, which for most trials is the run's config
        # unchanged. A boundary trial moves a cap or a term instead of the graph,
        # and both the precondition and the detector have to see the same one.
        trial_config = trial.config(config)
        settled = await_precondition(conn, trial, trial_config, now_ms)

        started = time.monotonic()
        observed = _observe(conn, trial_config, trial, now_ms)
        detect_seconds = time.monotonic() - started

        outcome = TrialOutcome(
            trial=trial,
            observed=observed,
            detect_seconds=detect_seconds,
            settle_seconds=settled,
        )
        outcomes.append(outcome)
        if log:
            mark = "ERROR" if outcome.errored else ("ok" if outcome.correct else "WRONG")
            print(
                f"  [{index:>2}/{len(trials)}] {trial.name:<24} "
                f"expected={trial.expected!s:<5} observed={observed!s:<5} "
                f"{detect_seconds:.2f}s  {mark}"
            )
    return tuple(outcomes)


@dataclass(frozen=True)
class BlastRadiusCheck:
    """Did the traversal name the model that the seeded graph puts at risk?"""

    expected_models: int
    found_models: int
    named_the_live_deployment: bool

    @property
    def recall(self) -> float | None:
        """Fraction of truly-affected models the traversal reached."""
        return metrics.fraction(self.found_models, self.expected_models)


def measure_blast_radius(conn: DataHubConnection, config: ScanConfig) -> BlastRadiusCheck | None:
    """Check the traversal reaches the one model the seeded graph puts downstream.

    Ground truth is the seed: ``credit_risk_v3`` consumes features derived from
    ``loans_raw``, and it is deployed live. A traversal that stopped at the
    warehouse boundary would find zero, which is the whole failure mode Janus
    exists to fix, so it is worth stating as a number rather than assuming.
    """
    now_ms = int(time.time() * 1000)
    plant_stale_source(conn, lag_hours=WRITEBACK_LAG_HOURS, now_ms=now_ms)
    radius = blast_radius(conn, str(spec.source_table_urn()), config, now_ms=now_ms)
    if radius is None:
        return None

    model_urn = str(spec.model_urn())
    found = [model for model in radius.models if model.urn == model_urn]
    return BlastRadiusCheck(
        expected_models=1,
        found_models=len(found),
        named_the_live_deployment=bool(found) and found[0].is_live,
    )


@dataclass(frozen=True)
class WriteBackCheck:
    """What a real scan left in the graph, read back from DataHub."""

    incidents_after_first: int
    """Incidents attached to the table after one scan, active or since resolved."""
    incidents_after_second: int
    """The same count after an identical rerun. Equal means the rerun added nothing."""
    trust_score_written: bool
    trust_band_written: bool

    @property
    def idempotent(self) -> bool:
        """Whether the rerun added nothing. The property the whole design rests on."""
        return self.incidents_after_second == self.incidents_after_first

    @property
    def duplicates(self) -> int:
        """Incidents the second run added. The hardening doc's target is zero."""
        return self.incidents_after_second - self.incidents_after_first


def measure_writeback(conn: DataHubConnection, config: ScanConfig) -> WriteBackCheck:
    """Run a real scan twice and read back what landed.

    Reads the graph rather than the ``ScanReport``: a scan that believed it wrote
    an incident and did not would otherwise report itself correct.
    """
    now_ms = int(time.time() * 1000)
    table_urn = str(spec.source_table_urn())
    model_urn = str(spec.model_urn())

    run_scan(conn, config, table_urn=table_urn, run_id="bench-writeback-1", now_ms=now_ms)
    after_first = len(attached_incident_urns(conn, table_urn))

    run_scan(conn, config, table_urn=table_urn, run_id="bench-writeback-2", now_ms=now_ms)
    after_second = len(attached_incident_urns(conn, table_urn))

    properties = read_properties(conn, model_urn)
    return WriteBackCheck(
        incidents_after_first=after_first,
        incidents_after_second=after_second,
        trust_score_written=bool(properties.get(TRUST_SCORE)),
        trust_band_written=bool(properties.get(TRUST_BAND)),
    )


@dataclass(frozen=True)
class ApproachScore:
    """How one approach did at naming exactly the leaking features."""

    approach: Approach
    matrix: metrics.Confusion
    flagged_when_clean: int
    """Features still flagged after the leak was removed. The alert-fatigue number."""


def measure_leakage_approaches(
    conn: DataHubConnection, config: ScanConfig
) -> tuple[ApproachScore, ...]:
    """Score every approach on the same graph, at feature granularity.

    Scored per feature rather than per model, because "does this model leak" is a
    question all three get right on a leaking graph. The question that separates
    them is *which* feature leaks, which is what a data scientist has to act on,
    and it is only answerable at column granularity.

    Both graph states are scored: the seeded one, where exactly one of the model's
    two features derives from the label, and the reverted one, where none does. The
    second is the one that hurts, and it is the one a real team lives in after they
    fix something.
    """
    model_urn = str(spec.model_urn())
    features = {name: str(spec.feature_urn(name)) for name in spec.MODEL_FEATURES}
    leaking_urn = features[spec.LEAKAGE_FEATURE]

    observations: dict[str, list[tuple[bool, bool]]] = {
        approach.key: [] for approach, _ in LEAKAGE_APPROACHES
    }
    clean_flags: dict[str, int] = {approach.key: 0 for approach, _ in LEAKAGE_APPROACHES}

    for leaking in (True, False):
        trial = next(
            t
            for t in build_trials(config)
            if t.family is FindingType.TARGET_LEAKAGE and t.expected is leaking
        )
        now_ms = int(time.time() * 1000)
        trial.plant(conn, trial, now_ms)
        if await_precondition(conn, trial, config, now_ms) is None:
            # A comparison drawn against a graph in the wrong state would be
            # wrong rather than missing, so it is dropped. Returning empty
            # rather than raising: this runs last, after every trial has been
            # scored, and throwing away a complete benchmark because an index
            # was slow would be a poor trade. The report then says the
            # comparison was not measured, which is true and visible.
            print(f"  the graph never reached the {trial.name} state; skipping comparison")
            return ()

        truth = {leaking_urn} if leaking else set()

        for approach, detector in LEAKAGE_APPROACHES:
            if detector is None:
                flagged = {
                    finding.leak.feature_urn
                    for finding in leakage_findings(conn, model_urn, config)
                }
            else:
                flagged = set(detector(conn, model_urn, config))

            for urn in features.values():
                observations[approach.key].append((urn in truth, urn in flagged))
            if not leaking:
                clean_flags[approach.key] += len(flagged)

    return tuple(
        ApproachScore(
            approach=approach,
            matrix=metrics.confusion(observations[approach.key]),
            flagged_when_clean=clean_flags[approach.key],
        )
        for approach, _ in LEAKAGE_APPROACHES
    )


def _by_family(outcomes: Sequence[TrialOutcome]) -> dict[FindingType, list[TrialOutcome]]:
    """Group scoreable outcomes by the detector they exercised."""
    grouped: dict[FindingType, list[TrialOutcome]] = {}
    for outcome in outcomes:
        if outcome.errored:
            continue
        grouped.setdefault(outcome.trial.family, []).append(outcome)
    return grouped


_DETECTOR_LABELS = {
    FindingType.UPSTREAM_FRESHNESS: "Upstream freshness (P2)",
    FindingType.TARGET_LEAKAGE: "Target leakage (P1)",
    FindingType.INPUT_SCHEMA_DRIFT: "Input schema drift (P3)",
    FindingType.SENSITIVE_SOURCE: "Sensitive source (P5)",
    FindingType.DEPRECATED_INPUT: "Deprecated input (P6)",
    FindingType.TABLE_LEVEL_RISK: "Table-level risk (degraded mode, T-07)",
    FindingType.PROXY_CANDIDATE: "Proxy candidate (T-11, for human review)",
}


#: Per detector, the one-line mutation that a boundary trial in that family would
#: catch. Prose, not a number: the renderer may explain, it may never state a
#: measurement the run did not produce (benchmarks/CLAUDE.md rule 4). Whether a
#: row gets to claim it *could* have failed is decided by the trials, below.
_BOUNDARY_MUTATIONS = {
    FindingType.UPSTREAM_FRESHNESS: "`>` to `>=` fails the trial at exactly the SLA",
    FindingType.TARGET_LEAKAGE: (
        "an off-by-one in the hop cap, or matching the label by column name "
        "instead of by declared term"
    ),
    FindingType.TABLE_LEVEL_RISK: (
        "running the degraded mode unconditionally instead of only where no "
        "column link exists, which reports every linked model twice"
    ),
    FindingType.PROXY_CANDIDATE: (
        "dropping the direct-descent exclusion, which reports a proved "
        "derivation as a candidate and duplicates the sensitive-source "
        "finding, or an off-by-one in the shared-ancestor hop cap"
    ),
}


def _falsifiability(family: FindingType, outcomes: Sequence[TrialOutcome]) -> tuple[int, str]:
    """Return a detector's boundary-trial count and whether its row could have failed.

    A perfect precision and recall means one of two very different things, and
    the table cannot tell them apart on its own: either every trial that could
    have gone the wrong way went the right way, or no trial could have gone the
    wrong way at all. The second is a construction proof wearing the shape of a
    measurement, and it is the first thing a sceptical reader should be able to
    check without reading the injector (F6).
    """
    boundary = sum(1 for outcome in outcomes if outcome.trial.boundary)
    if not boundary:
        return 0, "No: presence or absence of one planted fact"
    return boundary, f"Yes: {_BOUNDARY_MUTATIONS[family]}"


def _scale_disclosure(scale: Sequence[ScaleMeasurement]) -> list[str]:
    """Return the "what this does not measure" lines about scale.

    Follows what was actually run rather than carrying a fixed caveat: a report
    that still says "no scale test" beside a scale table is a report nobody
    should trust about anything else either.
    """
    if not scale:
        return [
            "- One seeded graph, one model. These are correctness and boundary",
            "  measurements, not a scale test; throughput and a 10k/100k-entity",
            "  curve are not run here.",
        ]
    return [
        "- One seeded graph for the detection numbers, which are correctness and",
        f"  boundary measurements. The scale table sweeps up to {max(m.models for m in scale)}",
        "  models on one machine; a 10k/100k-entity curve, a contended instance, and",
        "  a catalog whose models do not share one feature table are not measured.",
    ]


def _counterfactual_lines(
    checks: Sequence[CounterfactualCheck], multi: MultiPathCheck | None
) -> list[str]:
    """Return the counterfactual section, or nothing when it was not run."""
    if not checks:
        return []

    lines = [
        "",
        "## Counterfactuals, applied",
        "",
        "Every finding carries a counterfactual: a set of changes, each sufficient on its",
        "own to clear it. A suggested fix nobody performed is not a measurement, so each",
        "one below was applied to the live graph and the same detector asked again.",
        "",
        "| Detector | Remedies applied | Cleared the finding | Not mechanically applicable |",
        "|---|---|---|---|",
    ]
    for check in checks:
        applied = ", ".join(remedy.kind.value for remedy in check.applied) or "-"
        unapplied = ", ".join(kind.value for kind in check.unapplied) or "-"
        if not check.settled:
            verdict = "error: the graph never showed the change"
        elif not check.fired:
            verdict = "not measured: nothing fired to remedy"
        else:
            verdict = "yes" if check.cleared else "**no**"
        lines.append(f"| {_DETECTOR_LABELS[check.family]} | {applied} | {verdict} | {unapplied} |")

    lines += [
        "",
        "The last column is not a gap being hidden. Retraining a model, migrating onto a",
        "successor table, and dropping a feature are real fixes that no metadata write can",
        "carry out, so they are named as unverified rather than counted as passes.",
        "",
    ]

    if multi is None:
        return lines

    lines += [
        "### When one cut is not enough",
        "",
        "A single-path counterfactual is close to a construction proof: the remedy undoes",
        "the plant. The case that can go wrong is a feature reaching a declared label by",
        "two derivations, where cutting the one the incident quoted is a fix a reasonable",
        "person would believe in. That graph is planted here, and the quoted path cut.",
        "",
    ]
    if not multi.settled:
        lines += ["Not measured: DataHub never showed the planted state.", ""]
        return lines
    lines += [
        f"- Paths the counterfactual declared: {multi.paths_reported}",
        f"- First edges its cut remedy named: {multi.edges_named}",
        f"- Still fires after one of the two is cut: {multi.still_fires_after_one_cut} "
        f"({'correct' if multi.still_fires_after_one_cut else '**wrong**'})",
        f"- Clears once both are cut: {multi.cleared_after_both_cuts} "
        f"({'correct' if multi.cleared_after_both_cuts else '**wrong**'})",
        "",
        "The second control matters as much as the first: without it, a detector that",
        "could not be silenced at all would score identically on the line above.",
        "",
    ]
    return lines


def _degraded_precision_lines(approaches: Sequence[ApproachScore]) -> list[str]:
    """Check the number the product quotes for its degraded mode against this run.

    ``janus.config.TABLE_LEVEL_PRECISION`` is printed to a user beside every
    table-level finding, as that mode's measured precision. The measurement is
    the table-level baseline above, made here. Those are two copies of one
    number, so the run compares them and says which it found: a constant that has
    drifted from the measurement is a product overstating (or understating) its
    own accuracy to a user, and it should not take a code review to notice.

    Nothing is hand-edited by this (rule 4): the measured value is what gets
    printed either way, and the constant is quoted as the claim under test.
    """
    measured = next(
        (score for score in approaches if score.approach is baselines.TABLE_LEVEL), None
    )
    if measured is None:
        return []

    precision = measured.matrix.precision
    agrees = precision is not None and abs(precision - TABLE_LEVEL_PRECISION) < 0.005
    lines = [
        "",
        "### The number the degraded mode quotes about itself",
        "",
        "`janus scan` offers this same table-level reading for a model nobody has",
        "linked yet (T-07), and prints its measured precision beside every such finding,",
        "so the answer arrives with the odds it is wrong. That figure is the table-level",
        "row above.",
        "",
        f"- Measured here: **{metrics.format_rate(precision)}**",
        "- Quoted by the product (`config.TABLE_LEVEL_PRECISION`): "
        f"**{TABLE_LEVEL_PRECISION:.2f}**",
    ]
    if agrees:
        lines.append("- The two agree, so the disclosure a user reads is this run's measurement.")
    else:
        lines.append(
            "- **They disagree.** The product is quoting a precision this graph does not "
            "support; update `TABLE_LEVEL_PRECISION` in janus/config.py to the "
            "measured value above."
        )
    return lines


def measure_faithfulness(
    conn: DataHubConnection,
    config: ScanConfig,
    trials: Sequence[Trial],
) -> FaithfulnessReport:
    """Narrate a real finding from every detector, and check the prose (T-10).

    Each family's *positive* trial is planted, waited for, and narrated, rather
    than narrating whatever the matrix happened to leave behind. The first
    version did the latter and measured one narrative out of seven, because by
    the end of the run most detectors are looking at a graph they have nothing
    to say about. A faithfulness rate over one narrative is not a rate.

    Findings come from the live graph rather than a fixture, like every other
    measurement here (benchmarks/CLAUDE.md rule 6): the point is prose about
    facts a real GMS served.

    Only the template narrator is exercised unless an LLM is configured, and
    that is stated in the report rather than papered over. The template is the
    path every offline test and every CI run takes, and it is the stricter half
    to fail: its prose is written in this repo, so a violation is this project
    quoting a figure it never measured.
    """
    findings: list[Finding] = []
    seen: set[FindingType] = set()
    for trial in trials:
        # One positive per family: enough to narrate every detector once, and
        # a second trial of the same family would narrate the same finding.
        if not trial.expected or trial.family in seen:
            continue
        seen.add(trial.family)

        now_ms = int(time.time() * 1000)
        trial.plant(conn, trial, now_ms)
        trial_config = trial.config(config)
        if await_precondition(conn, trial, trial_config, now_ms) is None:
            # The planted state never became visible. An error in the harness,
            # not a fact about the narrator, so this family is dropped from the
            # measurement rather than counted as prose that could not be checked.
            continue
        findings.extend(findings_for(conn, trial_config, trial.family, now_ms))

    return check_template_narratives(findings, resolves=conn.graph.exists)


def _faithfulness_lines(faithfulness: FaithfulnessReport | None) -> list[str]:
    """Report whether the generated prose said only what the facts support (T-10).

    Distinct from quality, which is still not scored and should not be: a rubric
    is soft evidence that varies by provider (09 section 2.4). This is a
    property, checked the same way for every provider and for the template.
    """
    if faithfulness is None or faithfulness.rate is None:
        return []

    per_provider = faithfulness.by_provider()
    lines = [
        "",
        "## Narrative faithfulness (T-10)",
        "",
        "Not narrative *quality*, which stays unscored: a readability rubric is soft",
        "evidence that varies by provider, and it would sit badly beside a project whose",
        "decisions are deterministic. This is the property that is actually checkable.",
        "Every figure in generated prose must appear in the facts the narrator was shown,",
        "and every URN in it must resolve in the graph. A model that divides a 30-hour lag",
        'by a 6-hour SLA and writes "five times" has produced a figure nobody measured,',
        "and a reader cannot tell it from one that was.",
        "",
        f"- Narratives checked: {len(faithfulness.checks)}",
        f"- Figures checked: {faithfulness.numbers_checked}",
        f"- URNs checked: {faithfulness.urns_checked}",
        f"- Faithful: **{metrics.format_rate(faithfulness.rate)}**",
        "",
        "| Wrote the prose | Narratives | Figures | URNs | Faithful |",
        "|---|---|---|---|---|",
    ]
    for provider, checks in per_provider.items():
        rate = sum(1 for check in checks if check.faithful) / len(checks)
        label = "template (no LLM)" if provider == "none" else provider
        lines.append(
            f"| {label} | {len(checks)} | {sum(c.numbers_checked for c in checks)} | "
            f"{sum(c.urns_checked for c in checks)} | {metrics.format_rate(rate)} |"
        )

    if faithfulness.violations:
        lines += ["", "Every unsupported claim this run produced:", ""]
        lines += [f"- {violation.why}" for violation in faithfulness.violations]
    else:
        lines += [
            "",
            "A rate of 1.00 is worth reading only beside the figure count above: prose",
            "quoting no number at all is faithful by this measure and says nothing.",
            "`tests/benchmarks/test_faithfulness.py` holds the check that the checker",
            "itself rejects invented, derived and rounded figures, so a green row here",
            "is a measurement rather than a check that cannot fail.",
        ]
    return lines


def measure_lifecycle(conn: DataHubConnection, config: ScanConfig) -> tuple[TypeLifecycle, ...]:
    """Read back how long every Janus incident on this graph stayed open (T-16).

    Measured, not planted: this reads the incidents the run above just raised and
    then resolved, plus whatever earlier runs left behind. Nothing here writes,
    and there is no trial to construct, because the thing being measured is the
    tool's own history rather than a detector's answer.
    """
    return mttr_by_type(read_lifecycles(conn, config, search_model_urns(conn)))


def _lifecycle_lines(rows: Sequence[TypeLifecycle]) -> list[str]:
    """Return the incident-lifecycle section, with what the number is not.

    The caveat is the load-bearing part and it goes above the table, not below
    it. On a benchmark graph, Janus raises a finding and the very next
    measurement reverts what caused it, so nearly every duration here is the
    few seconds between a plant and its restore. That is a real measurement of
    a real write path and it is *not* a mean time to resolution for a team. A
    table published without saying so would be this project quoting its own
    fixture back as an operational result.
    """
    lines = [
        "",
        "## Incident lifecycle",
        "",
        "**These are not production MTTRs, and the distinction matters.** Every number",
        "below is read straight out of the graph, from `incidentInfo.created` and the",
        "resolution stamp GMS wrote, for incidents carrying Janus's own run footer",
        "(T-16). But the graph they are read from is the benchmark's: a trial plants a",
        "failure, the detector raises, and the next trial reverts the cause, so most of",
        "these durations are the seconds between a plant and its restore. What they do",
        "measure is that the raise-and-resolve loop closes at all, per detector, and",
        "that the timestamps a real deployment would be measured with are actually",
        "written. On a real catalog the same command prints the real figure:",
        "`janus inventory`.",
        "",
        "The median is beside the mean because a single incident left open across a",
        "weekend moves a mean by days and describes none of the others.",
        "",
        "| Detector | Raised | Still open | Resolved and timed | Mean | Median |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        mean = "-" if row.mean_hours is None else f"{row.mean_hours:.2f}h"
        median = "-" if row.median_hours is None else f"{row.median_hours:.2f}h"
        lines.append(
            f"| {row.finding_type.value} | {row.raised} | {row.open_now} "
            f"| {row.resolved} | {mean} | {median} |"
        )
    lines.append("")
    if all(row.raised == 0 for row in rows):
        lines += [
            "Every row is zero, which means no incident on this graph carries Janus's",
            "run footer. That is the expected reading of a graph nothing has ever written",
            "to, and it is reported rather than omitted: an absent section would look like",
            "a measurement that was taken and came out fine.",
            "",
        ]
    return lines


def _ingested_lines(score: IngestedScore | None) -> list[str]:
    """Return the ingested-graph section, or the note saying it was not run.

    Kept separate from every table above it, and never merged into them: the
    seeded numbers and these measure different things (a detector against a graph
    built to be measured, and a detector against a graph built by somebody else's
    ingestion), and averaging the two would describe neither.
    """
    lines = [
        "",
        "## Against a graph this project did not build",
        "",
        "Everything above runs on the graph `janus-seed` wrote, which is the graph",
        "where the links the detectors read already exist. This section removes the",
        "seeder: `examples/real-project/` is a postgres warehouse holding a public",
        "dataset, a dbt project, a scikit-learn training script and an MLflow",
        "registry, ingested by DataHub's **own** postgres, dbt and mlflow sources. The",
        "leak is written into the dbt model rather than planted by a seeding call, and",
        "the column lineage the walk follows is what DataHub's SQL parser produced from",
        "the compiled query (T-14).",
        "",
    ]
    if score is None:
        return [
            *lines,
            "**Not run.** No model from that stack is in this DataHub. Stand it up with the",
            "steps in `examples/real-project/README.md` (warehouse, `dbt run`, training",
            "script, then the three ingestion recipes) and rerun; this section then fills",
            "itself in. Nothing above depends on it.",
            "",
        ]

    lines += [
        "### Before anybody links anything",
        "",
        "The state ingestion leaves behind, which is the state every real model starts",
        "in. The measurement restores it first, so a rerun scores the same graph as the",
        "first run: it clears exactly the two aspects `link` writes and plants nothing.",
        "",
        f"- Findings raised: **{score.unlinked_findings}** "
        f"({'correct: nothing was knowable' if score.unlinked_findings == 0 else '**wrong**'})",
        f"- Checks reported as not evaluated: {', '.join(score.unlinked_not_evaluated) or 'none'}",
        f"- Tables the degraded table-level mode (T-07) could read: "
        f"{score.unlinked_training_tables}",
        "",
        "That last line is the one to read twice. On the seeded graph the degraded mode",
        "has a table to fall back to; here it has none, because DataHub's mlflow source",
        "records no inputs on a training run and emits no lineage from the model to the",
        "table it trained on. The table-level answer is therefore not a safety net on an",
        "ingested graph, and the honest report is the one above: nothing was checked, and",
        "here is what each check was missing.",
        "",
        "### Importing the link from a declaration (T-05, T-06)",
        "",
        "The same join, declared twice in the stack's own files: a Feast feature service",
        "and a dbt semantic model. Each is read by its adapter and checked against the",
        "*ingested* table's schema, which is the check that would have caught F10.",
        "",
        "| Adapter | Declared table | Datasets that name matches | Features | Label | Excluded |",
        "|---|---|---|---|---|---|",
    ]
    for route in score.routes:
        detail = route.error or f"{len(route.source_columns)}"
        lines.append(
            f"| `--from {route.adapter}` | `{route.declared_table}` | {route.candidates} "
            f"| {detail} | {route.label_column or 'none declared'} "
            f"| {', '.join(route.excluded) or 'none'} |"
        )
    lines += [
        "",
        f"- Both routes name the same columns: **{score.routes_agree}**",
        "- Every declared column is a column the ingested table has: "
        f"**{all(route.error is None for route in score.routes)}**",
        "",
        "The middle column is a fact about real graphs rather than a defect: DataHub's",
        "dbt source emits a dbt-platform dataset beside the warehouse table and names",
        "both after the same relation, so a declared relation resolves to two datasets",
        "and `link` stops and prints both for a human to choose. This measurement picks",
        "the warehouse one, which is the table the training script queried.",
        "",
        "### The detector, on that graph",
        "",
        "Scored per feature, not per model: the question worth answering is which column",
        "carries the leak. Ground truth is the dbt model on disk, not the graph and not",
        "the detector: delete the column from `customer_features.sql`, rebuild, re-ingest,",
        "and the truth column below flips with no change to this benchmark.",
        "",
        f"- Features scored: {score.leakage.total}",
        f"- Ground truth (from the dbt SQL): {', '.join(score.truth) or 'none'}",
        f"- Flagged: {', '.join(score.flagged) or 'none'}",
        f"- Precision {metrics.format_rate(score.leakage.precision)}, "
        f"recall {metrics.format_rate(score.leakage.recall)}, "
        f"false-positive rate {metrics.format_rate(score.leakage.false_positive_rate)}",
        f"- Named exactly the leaking feature(s) and nothing else: **{score.exact}**",
        f"- Derivation quoted: `{score.leak_path or '-'}`, reaching `{score.label_reached or '-'}`",
        f"- Still not evaluated once linked: {', '.join(score.linked_not_evaluated) or 'nothing'}",
        "",
        "Seven decisions, not one: six of those features are clean and a detector that",
        'answered "leak" to everything would score 0.14 precision here. The derivation is',
        "quoted from DataHub's own column-level lineage, which is the whole claim: no",
        "part of that path was written by this project.",
        "",
        "What this section does **not** measure: freshness, drift and the governance",
        "checks, which need a lag, a schema change and a classification this stack does",
        "not have; and the post-fix graph, which needs a dbt rebuild and a re-ingestion",
        "that this process does not run for you. The line above says what could not be",
        "evaluated rather than leaving silence to be read as a pass.",
        "",
    ]
    return lines


def render_results(
    outcomes: Sequence[TrialOutcome],
    blast: BlastRadiusCheck | None,
    writeback: WriteBackCheck,
    config: ScanConfig,
    *,
    generated_at: datetime,
    approaches: Sequence[ApproachScore] = (),
    scale: Sequence[ScaleMeasurement] = (),
    write_cost: WritePathCost | None = None,
    counterfactuals: Sequence[CounterfactualCheck] = (),
    multi_path: MultiPathCheck | None = None,
    faithfulness: FaithfulnessReport | None = None,
    ingested: IngestedScore | None = None,
    lifecycle: Sequence[TypeLifecycle] = (),
) -> str:
    """Render RESULTS.md. Pure: every number comes from the arguments."""
    grouped = _by_family(outcomes)
    errors = [outcome for outcome in outcomes if outcome.errored]
    protected = ", ".join(
        config.protected_attribute_tag_urns + config.protected_attribute_term_urns
    )
    lines: list[str] = []

    lines += [
        "# Janus-Bench results",
        "",
        "Generated by `python -m benchmarks.run_bench`. Every number here is measured,",
        "never hand-edited (benchmarks/CLAUDE.md rule 4). Rerunning on the seeded graph",
        "reproduces it.",
        "",
        f"- Run at: {generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Freshness SLA under test: {config.freshness_sla_hours:g} hours",
        f"- Blast-radius hop cap: {config.max_hops}; leakage hop cap: {config.leakage_max_hops}",
        f"- Sensitive classifications under test: "
        f"{', '.join(config.sensitive_tag_urns + config.sensitive_term_urns) or 'none'}",
        # Reported beside the sensitive one rather than folded into it: the two
        # are different taxonomies on purpose (T-11), and a reader checking which
        # detector was actually switched on needs to see both (D-133).
        f"- Protected attributes under test: {protected or 'none'}",
        f"- Trials: {len(outcomes)} ({len(errors)} unscoreable)",
        "",
        "## Detection",
        "",
        "Detectors are called directly, with no LLM and no writes, so these describe",
        "detection alone. A false positive is an alert on a clean graph.",
        "",
        "The last two columns are the ones to read first. A boundary trial is one that",
        "could plausibly have gone the other way: a lag a hair either side of the SLA, a",
        "leak exactly at the hop cap, a column named like a label without carrying the",
        "term. A row with none of those is a construction proof rather than a",
        "measurement, and says so here instead of leaving a perfect score to be read as",
        "evidence it is not.",
        "",
        "| Detector | Trials | Precision | Recall | F1 | False-positive rate "
        "| Boundary trials | Could this row have failed? |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for family, label in _DETECTOR_LABELS.items():
        found = grouped.get(family, [])
        if not found:
            lines.append(f"| {label} | 0 | - | - | - | - | 0 | Not run |")
            continue
        matrix = metrics.confusion([(o.trial.expected, o.observed) for o in found])
        boundary, verdict = _falsifiability(family, found)
        lines.append(
            f"| {label} | {matrix.total} "
            f"| {metrics.format_rate(matrix.precision)} "
            f"| {metrics.format_rate(matrix.recall)} "
            f"| {metrics.format_rate(matrix.f1)} "
            f"| {metrics.format_rate(matrix.false_positive_rate)} "
            f"| {boundary} | {verdict} |"
        )

    overall = metrics.confusion([(o.trial.expected, o.observed) for o in outcomes if not o.errored])
    lines += [
        "",
        "Against the targets in `docs/plan/03-production-hardening.md` section A.2,",
        "taken over every scoreable trial:",
        "",
        f"- Recall {metrics.format_rate(overall.recall)} "
        f"(target 0.95): {metrics.meets(overall.recall, 0.95)}",
        f"- Precision {metrics.format_rate(overall.precision)} "
        f"(target 0.90): {metrics.meets(overall.precision, 0.90)}",
        f"- False-positive rate {metrics.format_rate(overall.false_positive_rate)} "
        f"(target 0.05): "
        f"{metrics.meets(overall.false_positive_rate, 0.05, higher_is_better=False)}",
        "",
        "## The freshness boundary",
        "",
        "The sweep exists because planting a 30-hour lag against a 6-hour SLA and",
        "finding it proves nothing. These are the trials either side of the line.",
        "",
        "A perfect column here is only worth reading if the sweep could have failed.",
        "It can: changing the detector's comparison from `>` to `>=`, a one-character",
        "off-by-one, is caught by the trial at exactly the SLA and shows up as a fallen",
        "precision and a false-positive rate above target. That mutation is recorded in",
        "docs/decision-log.md; the check that a suite goes red for a real fault before",
        "its green is trusted is tests/CLAUDE.md rule 6, applied here to the benchmark",
        "itself.",
        "",
        "| Lag | Should fire | Did fire | |",
        "|---|---|---|---|",
    ]

    for outcome in outcomes:
        if outcome.trial.family is not FindingType.UPSTREAM_FRESHNESS:
            continue
        mark = "error" if outcome.errored else ("ok" if outcome.correct else "**wrong**")
        lines.append(
            f"| {outcome.trial.lag_hours:g}h | {outcome.trial.expected} "
            f"| {outcome.observed} | {mark} |"
        )

    detect = metrics.Latency(tuple(o.detect_seconds for o in outcomes if not o.errored))
    settle = metrics.Latency(
        tuple(o.settle_seconds for o in outcomes if o.settle_seconds is not None)
    )
    if approaches:
        lines += [
            "",
            "## Why column-level lineage, measured",
            "",
            "The same graph, the same ground truth, three ways of reading it. Scored per",
            "**feature**, not per model: every approach can tell that a leaking model leaks.",
            "The question that separates them is *which* of its features leaks, which is the",
            "one a data scientist has to act on.",
            "",
            "Two graph states are scored: the seeded one, where exactly one of the model's",
            "two features derives from the label, and the reverted one, where none does.",
            "",
            "| Approach | Precision | Recall | False-positive rate "
            "| Still alerting after the fix |",
            "|---|---|---|---|---|",
        ]
        for score in approaches:
            lines.append(
                f"| {score.approach.name} "
                f"| {metrics.format_rate(score.matrix.precision)} "
                f"| {metrics.format_rate(score.matrix.recall)} "
                f"| {metrics.format_rate(score.matrix.false_positive_rate)} "
                f"| {score.flagged_when_clean} feature(s) |"
            )
        lines += [
            "",
            "What each one can see:",
            "",
        ]
        lines += [f"- **{s.approach.name}**: {s.approach.note}." for s in approaches]
        lines += [
            "",
            "The last column is the one that decides whether a tool survives contact with a",
            "team. An approach that cannot tell which column carries the label also cannot",
            "tell when somebody has fixed it, so it keeps alerting on a graph that is now",
            "clean, and gets switched off. Recall alone would have called it excellent.",
            "",
            "Read this for what it is. These are implementations of an *approach*, written",
            "here and handed Janus's own label index and source-column resolution so",
            "nothing is won by one side starting better informed. No Great Expectations,",
            "Deequ, Evidently or NannyML process was run, and no claim is made about those",
            "products' own behaviour. The no-lineage row is true by construction rather than",
            "by measurement: leakage is a path, and that approach holds no paths.",
        ]
        lines += _degraded_precision_lines(approaches)

    lines += _faithfulness_lines(faithfulness)

    lines += [
        "",
        "## Latency",
        "",
        "Split deliberately. The first is Janus's; the second is how long DataHub",
        "took to index a change before the detector could see it, and blaming that on",
        "the detector would measure the wrong system.",
        "",
        f"- Detector call: median {metrics.format_seconds(detect.median_s)}, "
        f"slowest {metrics.format_seconds(detect.worst_s)} over {detect.count} trials",
        f"- DataHub index convergence: median {metrics.format_seconds(settle.median_s)}, "
        f"slowest {metrics.format_seconds(settle.worst_s)} over {settle.count} trials",
        "",
        "## Blast radius",
        "",
    ]

    if blast is None:
        lines.append("Not measured: the stale table raised no finding to traverse from.")
    else:
        lines += [
            f"- Models at risk found: {blast.found_models}/{blast.expected_models} "
            f"(recall {metrics.format_rate(blast.recall)})",
            f"- Named the live deployment: {blast.named_the_live_deployment}",
            "",
            "The traversal crosses the warehouse-to-ML boundary, which is the gap a",
            "table-level lineage tool leaves open.",
        ]

    lines += [
        "",
        "## Write-back and idempotency",
        "",
        "Read back from DataHub after a real scan, not taken from the scan's own report.",
        "",
        f"- Incidents attached to the table after one scan: {writeback.incidents_after_first}",
        f"- After a second identical scan: {writeback.incidents_after_second}",
        "  (a running total, counting incidents earlier runs raised and resolved;",
        "  what the rerun must not do is add to it)",
        f"- Duplicates created by the rerun: {writeback.duplicates} (target 0): "
        f"{'meets target' if writeback.duplicates == 0 else 'ABOVE TARGET'}",
        f"- Trust score written to the model: {writeback.trust_score_written}",
        f"- Trust band written to the model: {writeback.trust_band_written}",
    ]

    lines += _counterfactual_lines(counterfactuals, multi_path)
    lines += _lifecycle_lines(lifecycle)
    lines += _ingested_lines(ingested)

    if scale:
        lines += [
            "",
            "## Scale",
            "",
            "`janus scan --all-models` runs one independent scan per model, so the",
            "question is whether that stays linear. Each replica is a real mlModel carrying",
            "the seeded model's features and training run, so every detector does its full",
            "job on it; the replicas share one feature table, because the question is what a",
            "sweep costs and duplicating the warehouse side would measure the seeder. Scans",
            "are dry-run: this is the read path.",
            "",
            "Graph reads are counted at the connection, and they are what the wall clock is",
            "made of: a per-model figure that stays flat as the catalog grows is what says",
            "the cost is the catalog's size and not the traversal's shape.",
            "",
            "| Models | Total | Per model | Graph reads | Reads per model |",
            "|---|---|---|---|---|",
        ]
        for measurement in scale:
            lines.append(
                f"| {measurement.models} "
                f"| {metrics.format_seconds(measurement.seconds)} "
                f"| {metrics.format_seconds(measurement.seconds_per_model)} "
                f"| {measurement.graph_reads} "
                f"| {measurement.reads_per_model:.1f} |"
            )
        if write_cost is not None:
            lines += [
                "",
                "### What the write path costs",
                "",
                "The sweep above is dry-run, so the table is the *read* path. A",
                "`scan --all-models --write` pays more than that, and the difference is not",
                "the writes: reconciliation walks a resource's incidents to decide what to",
                "clear, per finding rather than per sweep. Measured on the seeded model,",
                "after the baseline is restored and after the lifecycle read, so the scan",
                "has a real finding to write and its own fresh incident does not land in",
                "somebody else's numbers.",
                "",
                "This figure is not a constant, and the direction it moves in is worth",
                "knowing. Reconciliation walks the incidents already attached to the",
                "resources a scan touches, so it costs almost nothing on a graph seeded a",
                "moment ago and more on one with history. The number below comes from a",
                "graph a full benchmark run has just written to, which is the realistic end",
                "of that range rather than the flattering one.",
                "",
                "| Phase | Graph reads | Share |",
                "|---|---|---|",
                f"| Detection | {write_cost.detect_reads} "
                f"| {write_cost.detect_reads / write_cost.total_write_reads:.0%} |",
                f"| Write-back | {write_cost.write_reads} "
                f"| {write_cost.write_reads / write_cost.total_write_reads:.0%} |",
                f"| Reconciliation | {write_cost.reconcile_reads} "
                f"| {write_cost.reconcile_share:.0%} |",
                f"| **Total** | **{write_cost.total_write_reads}** | |",
                "",
                f"That is **{write_cost.amplification:.1f}x** the reads of the same scan in",
                f"dry run ({metrics.format_seconds(write_cost.dry_run_seconds)} against "
                f"{metrics.format_seconds(write_cost.write_seconds)} of wall clock), and",
                f"reconciliation is {write_cost.reconcile_share:.0%} of it. A whole-catalog",
                "write sweep therefore does not cost what the table above suggests, and the",
                "gap grows with how many incidents each resource already carries. Nothing is",
                "scored against a target here either; the number is published because a",
                "reader planning a nightly sweep needs it and the read-path table alone",
                "would understate it.",
            ]

        lines += [
            "",
            "Measured against a local Docker Quickstart on one developer machine, which is",
            "the slowest realistic deployment and the only one every reader can reproduce.",
            "No target is scored here: there is no published number for how fast a metadata",
            "sweep should be, and inventing one to pass it would be worse than the plain",
            "measurement.",
        ]

    if errors:
        lines += [
            "",
            "## Unscoreable trials",
            "",
            "DataHub never showed the planted state within the timeout, so the detector",
            "was never asked a fair question. Excluded from the tables above rather than",
            "counted as misses.",
            "",
        ]
        lines += [f"- {outcome.trial.name}: {outcome.trial.detail}" for outcome in errors]

    lines += [
        "",
        "## What this does not measure",
        "",
        "Stated so the numbers are not read as more than they are:",
        "",
        *_scale_disclosure(scale),
        "- No Great Expectations, Deequ, Evidently or NannyML *process* was run. The",
        "  approaches compared above are implementations written here, so the table",
        "  measures what an approach can see, never a named product's behaviour.",
        "- Narrative *quality* is not scored, deliberately (09 section 2.4). Narrative",
        "  *faithfulness* is, in its own section above: whether the prose quotes only",
        "  figures the narrator was actually shown. Detection is LLM-free by design, so",
        "  the detection numbers are unchanged with or without a model configured.",
        "- The faithfulness section measures the providers this run could reach. With no",
        "  API key configured, that is the template narrator alone, which is the path",
        "  every offline test and every CI run takes. A provider row appears only when a",
        "  credential for it was present, and its absence is not a passing grade.",
        "",
    ]
    return "\n".join(lines)


def _carry_mutation_section(out: Path, report: str) -> str:
    """Return ``report`` with any existing mutation section appended back.

    A section this renderer does not own and cannot reproduce. Absent from the
    old file, or absent from the new one for any reason, and the report is
    returned unchanged: this only ever preserves, never invents.
    """
    if not out.exists():
        return report
    previous = out.read_text()
    if MUTATION_START not in previous or MUTATION_END not in previous:
        return report
    start = previous.index(MUTATION_START)
    end = previous.index(MUTATION_END) + len(MUTATION_END)
    return f"{report.rstrip()}\n\n{previous[start:end]}\n"


def main() -> None:
    """Entry point: run every measurement and write RESULTS.md."""
    parser = argparse.ArgumentParser(description="Run Janus-Bench against a live DataHub.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("benchmarks/RESULTS.md"),
        help="Where to write the results (default: benchmarks/RESULTS.md).",
    )
    args = parser.parse_args()

    try:
        conn = connect()
    except DataHubConnectionError as exc:
        raise SystemExit(f"{exc}") from exc

    # Both governance detectors are configuration-gated by design: with no
    # classification named neither runs, and a scan reports each as not
    # evaluated (D-079, D-117). That is the right default for a user and the
    # wrong one for a benchmark, which would score a detector it never let run,
    # so the classifications the scenarios plant are supplied here explicitly.
    # The report says so, and every other value still comes from the environment.
    #
    # The protected-attribute list was missing here until Phase 7 and the proxy
    # row was scoreable only on a machine that happened to export the variable,
    # which is benchmarks/CLAUDE.md rule 1's same-run-same-numbers failing
    # silently: on a clean checkout `proxy-planted` reported WRONG for a
    # detector that had never been switched on (D-133).
    config = replace(
        ScanConfig.from_env(),
        sensitive_tag_urns=(scenarios.SENSITIVE_TAG_URN,),
        protected_attribute_tag_urns=(scenarios.PROTECTED_TAG_URN,),
    )
    trials = build_trials(config)

    print(f"Janus-Bench: {len(trials)} trials against {conn.gms_url}\n")
    outcomes = run_trials(conn, config, trials)

    print("\nBlast radius...")
    blast = measure_blast_radius(conn, config)

    print("Write-back and idempotency (this one writes)...")
    writeback = measure_writeback(conn, config)

    print("Comparing against approaches without column-level lineage...")
    approaches = measure_leakage_approaches(conn, config)

    print("Applying each finding's counterfactual (this one writes)...")
    counterfactuals = measure_counterfactuals(conn, config, trials)

    print("The multi-path case: cutting one derivation of two...")
    multi_path = measure_multi_path(conn, config, trials)

    # Scale goes last, and the reason is a measured one rather than a preference.
    # It creates and then hard-deletes fifty models, and the index churn behind
    # that pushed the counterfactual measurement's wait for a refreshed table past
    # its 45s precondition timeout on the first run of this suite: the remedy had
    # landed and the graph had not caught up. Raising the timeout would have made
    # every genuine error slower to report; running the index-latency-sensitive
    # measurement before the index-churning one costs nothing.
    # Before scale, and after everything that reads the seeded graph: this one
    # scores a different graph entirely (the ingested real project), so it is
    # kept away from the trials rather than interleaved with them.
    print("The ingested real project: scoring a graph this project did not build...")
    ingested = measure_ingested(conn, config)
    if ingested is None:
        print("  not ingested into this DataHub; the section will say so")

    print("Scale: replicating models and sweeping the catalog...")
    scale = measure_scale(conn, config)

    # Last measurement before the restore, and the ordering is load-bearing now:
    # it plants each family's positive state to have something to narrate, so
    # anything running after it would be reading a graph it did not set up.
    print("Narrative faithfulness: does the prose quote only measured figures...")
    faithfulness = measure_faithfulness(conn, config, trials)

    print("Restoring the seeded baseline...")
    restore_baseline(conn)

    # After the restore, and that ordering is the measurement: the restore is
    # what makes the recovery scans resolve the incidents this run raised, so
    # reading before it would report every one of them as still open.
    print("Incident lifecycle: how long this run's own findings stayed open...")
    lifecycle = measure_lifecycle(conn, config)

    # Last, and after both the restore and the lifecycle read, for two separate
    # reasons. After the restore because the seeded leak is what it scans, and
    # the counterfactuals cleared it. After the lifecycle because this one writes
    # a fresh incident, which would otherwise show up there as a finding that
    # never closed.
    print("Write path: what reconciliation costs over a dry run...")
    write_cost = measure_write_path(conn, config)

    report = render_results(
        outcomes,
        blast,
        writeback,
        config,
        generated_at=datetime.now(UTC),
        approaches=approaches,
        scale=scale,
        write_cost=write_cost,
        counterfactuals=counterfactuals,
        multi_path=multi_path,
        faithfulness=faithfulness,
        ingested=ingested,
        lifecycle=lifecycle,
    )
    # The mutation section (T-08) is written by benchmarks/mutation_report.py on
    # its own schedule, and this function rewrites the whole file, so without
    # this the section silently disappeared every time the benchmark ran. Its CI
    # job then re-added it and reported the file as stale, which is a job wearing
    # a permanent red X: exactly what ci.yml's own comments warn teaches people
    # to ignore red. Carried across verbatim rather than regenerated, because
    # regenerating it needs a mutmut run this process does not do.
    args.out.write_text(_carry_mutation_section(args.out, report))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
