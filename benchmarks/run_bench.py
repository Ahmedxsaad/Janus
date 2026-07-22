"""ModelGuard-Bench: run the detectors against a live graph and publish the numbers.

Run it with a DataHub Quickstart up and the ML graph seeded::

    modelguard-seed
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
  ModelGuard's, and adding them would blame the wrong system.
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from benchmarks import metrics
from benchmarks.inject import (
    Trial,
    await_precondition,
    build_trials,
    restore_baseline,
)
from modelguard.agent.pipeline import run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import blast_radius
from modelguard.detect.leakage import leakage_findings
from modelguard.detect.schema_drift import schema_drift_findings
from modelguard.models import FindingType
from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import plant_stale_source
from modelguard.writeback.incidents import attached_incident_urns
from modelguard.writeback.properties import TRUST_BAND, TRUST_SCORE, read_properties

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
    """Ask the detector under test whether it fires. One call, no retries."""
    if trial.family is FindingType.UPSTREAM_FRESHNESS:
        return blast_radius(conn, str(spec.source_table_urn()), config, now_ms=now_ms) is not None
    if trial.family is FindingType.TARGET_LEAKAGE:
        return bool(leakage_findings(conn, str(spec.model_urn()), config))
    if trial.family is FindingType.INPUT_SCHEMA_DRIFT:
        return bool(schema_drift_findings(conn, str(spec.model_urn()), config))
    raise ValueError(f"no detector registered for {trial.family}")


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
        settled = await_precondition(conn, trial, config, now_ms)

        started = time.monotonic()
        observed = _observe(conn, config, trial, now_ms)
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
    warehouse boundary would find zero, which is the whole failure mode ModelGuard
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
}


def render_results(
    outcomes: Sequence[TrialOutcome],
    blast: BlastRadiusCheck | None,
    writeback: WriteBackCheck,
    config: ScanConfig,
    *,
    generated_at: datetime,
) -> str:
    """Render RESULTS.md. Pure: every number comes from the arguments."""
    grouped = _by_family(outcomes)
    errors = [outcome for outcome in outcomes if outcome.errored]
    lines: list[str] = []

    lines += [
        "# ModelGuard-Bench results",
        "",
        "Generated by `python -m benchmarks.run_bench`. Every number here is measured,",
        "never hand-edited (benchmarks/CLAUDE.md rule 4). Rerunning on the seeded graph",
        "reproduces it.",
        "",
        f"- Run at: {generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Freshness SLA under test: {config.freshness_sla_hours:g} hours",
        f"- Blast-radius hop cap: {config.max_hops}; leakage hop cap: {config.leakage_max_hops}",
        f"- Trials: {len(outcomes)} ({len(errors)} unscoreable)",
        "",
        "## Detection",
        "",
        "Detectors are called directly, with no LLM and no writes, so these describe",
        "detection alone. A false positive is an alert on a clean graph.",
        "",
        "| Detector | Trials | Precision | Recall | F1 | False-positive rate |",
        "|---|---|---|---|---|---|",
    ]

    for family, label in _DETECTOR_LABELS.items():
        found = grouped.get(family, [])
        if not found:
            lines.append(f"| {label} | 0 | - | - | - | - |")
            continue
        matrix = metrics.confusion([(o.trial.expected, o.observed) for o in found])
        lines.append(
            f"| {label} | {matrix.total} "
            f"| {metrics.format_rate(matrix.precision)} "
            f"| {metrics.format_rate(matrix.recall)} "
            f"| {metrics.format_rate(matrix.f1)} "
            f"| {metrics.format_rate(matrix.false_positive_rate)} |"
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
    lines += [
        "",
        "## Latency",
        "",
        "Split deliberately. The first is ModelGuard's; the second is how long DataHub",
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
        "- One seeded graph, one model. These are correctness and boundary measurements,",
        "  not a scale test; throughput and a 10k/100k-entity curve are not run here.",
        "- No comparison against Great Expectations, Evidently, or naive table-level",
        "  lineage. The claim that only column-level cross-boundary lineage names the",
        "  model at risk is argued in the docs and is not benchmarked here.",
        "- Narrative quality is not scored. Detection is LLM-free by design, so these",
        "  numbers are unchanged with or without a model configured.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """Entry point: run every measurement and write RESULTS.md."""
    parser = argparse.ArgumentParser(description="Run ModelGuard-Bench against a live DataHub.")
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

    config = ScanConfig.from_env()
    trials = build_trials(config)

    print(f"ModelGuard-Bench: {len(trials)} trials against {conn.gms_url}\n")
    outcomes = run_trials(conn, config, trials)

    print("\nBlast radius...")
    blast = measure_blast_radius(conn, config)

    print("Write-back and idempotency (this one writes)...")
    writeback = measure_writeback(conn, config)

    print("Restoring the seeded baseline...")
    restore_baseline(conn)

    report = render_results(outcomes, blast, writeback, config, generated_at=datetime.now(UTC))
    args.out.write_text(report)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
