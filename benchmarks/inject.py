"""The labelled trials ModelGuard-Bench scores the detectors against.

A trial is one graph state plus the findings that state should produce. Ground
truth is not a judgement call: it is whatever the injector planted, and every
trial is built from the same reversible scenarios the demo uses
(``modelguard.seed.scenarios``), so the benchmark measures the shipped detectors
rather than a reimplementation of them (benchmarks/CLAUDE.md rule 1).

Why the freshness sweep is the interesting part
-----------------------------------------------
Planting a 30-hour lag against a 6-hour SLA and finding it proves almost nothing:
the answer is never in doubt. What a benchmark owes is the boundary. The sweep
walks the lag from well inside the SLA to well past it, including values a hair
either side, so recall and the false-positive rate are measured where a detector
actually goes wrong rather than where it cannot. A detector that fired on
everything, or that missed anything under a day, would score perfectly on the
demo scenario and badly here, which is the point.

Determinism
-----------
Every trial is a fixed constant: fixed lags, fixed order, fixed expectations. No
sampling and no seeded randomness, because there is nothing here worth
randomising, and a run that cannot be re-derived by reading this file is a run
whose numbers nobody can check (benchmarks/CLAUDE.md rules 1 and 4).

The precondition, and why it is not the answer
----------------------------------------------
DataHub indexes lineage asynchronously: a scan run immediately after a write can
read the pre-write graph. So each trial waits for the graph to *show the state it
planted* before the detector is asked anything. That is a precondition on the
experiment, not a peek at the outcome. The benchmark never waits for a detector
to produce the expected finding, which would manufacture perfect recall; it waits
for the world to be in the intended state, then asks once and records the answer.
A trial whose precondition never lands is reported as an error and excluded from
the detection metrics rather than silently scored as a miss.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

from datahub.metadata.schema_classes import DeprecationClass, SchemaMetadataClass

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import freshness_signal
from modelguard.detect.governance import sensitive_index
from modelguard.models import FindingType
from modelguard.seed import graph_spec as spec
from modelguard.seed.scenarios import (
    BACKUP_LABEL_COLUMN,
    SENSITIVE_SOURCE_COLUMN,
    plant_deprecated_input,
    plant_leakage,
    plant_schema_drift,
    plant_second_leak_path,
    plant_sensitive_source,
    plant_stale_source,
    revert_deprecated_input,
    revert_leakage,
    revert_schema_drift,
    revert_second_leak_path,
    revert_sensitive_source,
    revert_stale_source,
)

#: Every column any scenario can declare to be a label. The leakage precondition
#: reads the lineage for these and nothing else, so an unrelated upstream column
#: appearing in the graph cannot move it.
_LABEL_COLUMNS: tuple[str, ...] = (spec.LABEL_SOURCE_COLUMN, BACKUP_LABEL_COLUMN.name)

#: How long a planted fact may take to become visible through the read path the
#: detector uses. Lineage converged in about three seconds on a local Quickstart;
#: this leaves an order of magnitude of headroom before a trial is called an error.
PRECONDITION_TIMEOUT_S = 45.0

#: Gap between precondition polls. Short enough not to dominate a fast trial.
POLL_INTERVAL_S = 1.0


class Target(StrEnum):
    """Which entity a trial scans. The two answer different questions."""

    TABLE = "table"
    MODEL = "model"


@dataclass(frozen=True)
class Trial:
    """One graph state and the findings it is supposed to produce."""

    name: str
    family: FindingType
    """The detector under test. Findings of other types are ignored when scoring:
    a leakage trial says nothing about whether the table is also stale."""
    target: Target
    expected: bool
    """Whether the detector should fire. This is the ground-truth label."""
    detail: str
    """What makes this trial what it is, quoted verbatim into RESULTS.md."""
    plant: Callable[[DataHubConnection, Trial, int], None] = field(repr=False)
    """Puts the graph into this trial's state."""
    lag_hours: float | None = None
    """For freshness trials: how stale the table is planted to be."""
    overrides: tuple[tuple[str, object], ...] = ()
    """Config fields this trial changes, as (field, value) pairs.

    A boundary is not always a graph shape. A hop cap and the label term are
    configuration, and moving them is how the *same* graph asks the detector a
    question whose answer could go either way, without seeding a second warehouse
    to ask it with (F6). Applied with ``dataclasses.replace``, so a typo in a
    field name fails the run rather than being silently ignored."""
    boundary: bool = False
    """Whether this trial sits somewhere the detector could plausibly go wrong.

    A 30-hour lag against a 6-hour SLA cannot fail: the answer is never in doubt,
    and a trial like that documents the detector rather than testing it. A lag at
    exactly the SLA, a leak exactly at the hop cap, or a column named like a label
    without carrying the term can each go the wrong way, and a mutation of one
    line flips them. RESULTS.md counts these per detector and says plainly, per
    row, whether that row could have failed."""
    leak_upstreams: tuple[str, ...] | None = None
    """Which declared label columns the leaking feature must be seen deriving from.

    Set only by the multi-path trials, where "is the label reachable" is no longer
    the question: with two derivations planted, the interesting states differ in
    *which* of them survive, and a precondition that only asked whether some label
    was reachable would pass on the wrong graph. None means the usual question."""
    planted: bool | None = None
    """What is planted in the graph, when that differs from what should be found.

    Defaults to ``expected``, which is the usual case. It differs whenever the
    trial changes the *question* rather than the graph: the leak is planted and
    present, and the detector must still stay quiet because the configured label
    term is one nothing carries. The precondition waits on this, never on
    ``expected``, because waiting for the expected answer would manufacture it
    (rule 7)."""

    @property
    def graph_state(self) -> bool:
        """Whether this trial's anomaly is planted in the graph."""
        return self.expected if self.planted is None else self.planted

    def config(self, base: ScanConfig) -> ScanConfig:
        """Return the config this trial's detector runs under."""
        if not self.overrides:
            return base
        # dataclasses.replace's stub checks each keyword against ScanConfig's
        # exact field type, which a dict[str, object] built from an arbitrary
        # (name, value) tuple can never satisfy statically: mypy has no way to
        # know overrides.append(("leakage_max_hops", 1)) is an int and not,
        # say, a str. A field name that does not exist on ScanConfig still
        # fails at runtime, loudly, the moment this trial runs, which is the
        # actual safety net rule 7's "a typo fails the run" describes.
        return replace(base, **dict(self.overrides))  # type: ignore[arg-type]


def _plant_freshness(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Backdate the source table by the trial's lag."""
    assert trial.lag_hours is not None
    plant_stale_source(conn, lag_hours=trial.lag_hours, now_ms=now_ms)


def _plant_leakage(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Restore or cut the leaking column-lineage edge."""
    plant_leakage(conn) if trial.graph_state else revert_leakage(conn)


def _plant_two_leak_paths(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Plant both derivations, or only the second, per the trial's expectation.

    The trial's own ``leak_upstreams`` decides which: it is the state the
    precondition then waits for, so the plant and the wait cannot disagree about
    what this trial is.
    """
    assert trial.leak_upstreams is not None
    plant_second_leak_path(conn, keep_first=spec.LABEL_SOURCE_COLUMN in trial.leak_upstreams)


def _plant_drift(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Drift the feature table's schema away from training, or restore it."""
    plant_schema_drift(conn) if trial.expected else revert_schema_drift(conn)


def _plant_sensitive(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Classify the upstream column a model feature derives from, or unclassify it."""
    plant_sensitive_source(conn) if trial.expected else revert_sensitive_source(conn)


def _plant_deprecation(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Mark the model's training input deprecated, or withdraw the deprecation."""
    plant_deprecated_input(conn) if trial.expected else revert_deprecated_input(conn)


#: The lags the sweep walks, in hours, against the default 6 hour SLA. Chosen to
#: sit either side of the boundary rather than only at the comfortable extremes:
#: 5.5 and 6.5 are the pair that separates a detector with a correct comparison
#: from one that is off by an hour, and 6.0 pins the exact boundary, where "at the
#: SLA" must count as within it.
SWEEP_LAG_HOURS: tuple[float, ...] = (0.5, 2.0, 4.0, 5.5, 6.0, 6.5, 8.0, 12.0, 30.0, 72.0)


def _freshness_trials(sla_hours: float) -> tuple[Trial, ...]:
    """Walk the freshness lag across the SLA boundary.

    A lag at exactly the SLA is expected *not* to fire: the SLA is the budget, and
    spending all of it is not yet an overrun.
    """
    trials: list[Trial] = []
    for lag in SWEEP_LAG_HOURS:
        stale = lag > sla_hours
        trials.append(
            Trial(
                name=f"freshness-lag-{lag:g}h",
                family=FindingType.UPSTREAM_FRESHNESS,
                target=Target.TABLE,
                expected=stale,
                detail=f"{lag:g}h lag against a {sla_hours:g}h SLA",
                lag_hours=lag,
                plant=_plant_freshness,
                # Within an hour of the SLA either way is where an off-by-one
                # lives. Further out, the answer is never in doubt.
                boundary=abs(lag - sla_hours) <= 1.0,
            )
        )
    return tuple(trials)


#: A glossary term no column in the seeded graph carries. Used to ask the leakage
#: detector the same question about the same graph with the declaration removed
#: from under it: the label column is still *named* ``default_status`` and the
#: feature still derives from it, and the only thing that changed is which term
#: counts as a label. A detector that matched on the name rather than on the
#: declaration passes every other trial in this file and fails this one.
UNUSED_LABEL_TERM = "urn:li:glossaryTerm:modelguard.bench_unused_label"


def _leakage_trials() -> tuple[Trial, ...]:
    """The flagship detector: both directions, plus the boundaries it can fail at.

    The negative is the narrow one: the feature and the label declaration both
    survive, only the derivation path is cut, so a clean result isolates the
    lineage signal instead of proving that a feature nobody derives is safe.

    The two after it exist because presence-and-absence of one edge is a
    construction proof, not a measurement: neither of those trials can go the
    wrong way without the detector being broken outright (F6). The boundary
    trials can. Both hold the graph fixed and move the *question*, which is what
    makes them cheap enough to be honest: the seeded leak sits exactly one column
    hop from the label, so a cap of one must find it and a cap of zero must not,
    and an off-by-one in the hop filter flips one of them.
    """
    return (
        Trial(
            name="leakage-planted",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=True,
            detail="the model's feature derives from the declared label column",
            plant=_plant_leakage,
        ),
        Trial(
            name="leakage-reverted",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=False,
            detail="same feature and same declared label, derivation cut",
            plant=_plant_leakage,
        ),
        Trial(
            name="leakage-at-hop-cap",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=True,
            detail="the leak is exactly 1 column hop away, with the hop cap at 1",
            plant=_plant_leakage,
            overrides=(("leakage_max_hops", 1),),
            boundary=True,
        ),
        Trial(
            name="leakage-past-hop-cap",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=False,
            planted=True,
            detail="the same 1-hop leak with the hop cap at 0: out of reach, not absent",
            plant=_plant_leakage,
            overrides=(("leakage_max_hops", 0),),
            boundary=True,
        ),
        Trial(
            name="leakage-two-paths",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=True,
            detail="the feature derives from two separately declared label columns",
            plant=_plant_two_leak_paths,
            leak_upstreams=(spec.LABEL_SOURCE_COLUMN, BACKUP_LABEL_COLUMN.name),
            boundary=True,
        ),
        Trial(
            name="leakage-one-of-two-cut",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=True,
            detail=(
                "the derivation the incident quoted is cut and the second is left: "
                "half a fix, and the finding must stand"
            ),
            plant=_plant_two_leak_paths,
            leak_upstreams=(BACKUP_LABEL_COLUMN.name,),
            boundary=True,
        ),
        Trial(
            name="leakage-named-not-declared",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=False,
            planted=True,
            detail=(
                "the leak is planted and the column is still named default_status, "
                "but the configured label term is one nothing carries"
            ),
            plant=_plant_leakage,
            overrides=(("label_term_urn", UNUSED_LABEL_TERM),),
            boundary=True,
        ),
    )


def _drift_trials() -> tuple[Trial, ...]:
    """Training-serving schema drift, both directions."""
    return (
        Trial(
            name="drift-planted",
            family=FindingType.INPUT_SCHEMA_DRIFT,
            target=Target.MODEL,
            expected=True,
            detail="a column retyped, one dropped and one added since training",
            plant=_plant_drift,
        ),
        Trial(
            name="drift-reverted",
            family=FindingType.INPUT_SCHEMA_DRIFT,
            target=Target.MODEL,
            expected=False,
            detail="the feature table matches the training-time snapshot",
            plant=_plant_drift,
        ),
    )


def _sensitive_trials() -> tuple[Trial, ...]:
    """A model feature descending from a classified column, both directions.

    The negative is the narrow one, like leakage's: the column, the derivation
    and the model all survive, and only the organization's classification is
    withdrawn. A clean result therefore isolates the classification signal rather
    than proving that a column nothing descends from is safe.
    """
    return (
        Trial(
            name="sensitive-planted",
            family=FindingType.SENSITIVE_SOURCE,
            target=Target.MODEL,
            expected=True,
            detail="a model feature derives from a column tagged sensitive upstream",
            plant=_plant_sensitive,
        ),
        Trial(
            name="sensitive-reverted",
            family=FindingType.SENSITIVE_SOURCE,
            target=Target.MODEL,
            expected=False,
            detail="same column and same derivation, classification withdrawn",
            plant=_plant_sensitive,
        ),
    )


def _deprecation_trials() -> tuple[Trial, ...]:
    """A deprecated training input, both directions.

    The negative writes ``deprecated=False`` rather than removing the aspect,
    because that is how DataHub records a withdrawn deprecation, and a detector
    that treated the aspect's mere presence as the signal would fail exactly
    here.
    """
    return (
        Trial(
            name="deprecation-planted",
            family=FindingType.DEPRECATED_INPUT,
            target=Target.MODEL,
            expected=True,
            detail="the model's training input is marked deprecated by its owners",
            plant=_plant_deprecation,
        ),
        Trial(
            name="deprecation-reverted",
            family=FindingType.DEPRECATED_INPUT,
            target=Target.MODEL,
            expected=False,
            detail="the same aspect present with deprecated=false, a withdrawn deprecation",
            plant=_plant_deprecation,
        ),
    )


def build_trials(config: ScanConfig) -> tuple[Trial, ...]:
    """Return the whole matrix, in a fixed order.

    Args:
        config: Supplies the freshness SLA the sweep is built around, so the
            benchmark measures the boundary the scan actually enforces rather
            than a number hardcoded here.
    """
    return (
        _freshness_trials(config.freshness_sla_hours)
        + _leakage_trials()
        + _drift_trials()
        + _sensitive_trials()
        + _deprecation_trials()
    )


def _freshness_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the graph reports the lag this trial planted.

    Reads the same ``operation`` aspect the detector reads, and compares against
    the planted lag rather than against the SLA, so this cannot accidentally
    become a test of the verdict.
    """
    assert trial.lag_hours is not None
    signal = freshness_signal(conn, str(spec.source_table_urn()), config, now_ms=now_ms)
    if signal is None:
        return False
    return abs(signal.lag_hours - trial.lag_hours) < 0.05


def _leakage_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the lineage index reflects the edge this trial planted or cut.

    Asks the same column-lineage query the detector asks, but inspects the
    *lineage*, not whether a finding was raised.

    A multi-path trial names the exact set of declared label columns the feature
    must be seen deriving from, because with two derivations in play "a label is
    reachable" is true of three different graphs and only one of them is the
    trial.
    """
    results = conn.client.lineage.get_lineage(
        source_urn=str(spec.feature_table_dataset_urn()),
        source_column=spec.LEAKAGE_FEATURE,
        direction="upstream",
        max_hops=1,
    )
    reached = {step.column_name for result in results for step in (result.paths or [])} & set(
        _LABEL_COLUMNS
    )
    if trial.leak_upstreams is not None:
        return reached == set(trial.leak_upstreams)
    return (spec.LABEL_SOURCE_COLUMN in reached) == trial.graph_state


def _drift_visible(conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int) -> bool:
    """Whether the feature table's live schema is the one this trial wrote.

    ``schemaMetadata`` is versioned, so GMS serves it synchronously; this is a
    guard against a lost write rather than against an index lag.
    """
    schema = conn.graph.get_aspect(str(spec.feature_table_dataset_urn()), SchemaMetadataClass)
    if schema is None:
        return False
    live = {schema_field.fieldPath for schema_field in schema.fields}
    # Compared against the seeded column set rather than against any column the
    # scenario happens to add, so this keeps working if the scenario changes which
    # columns it moves. The planted drift drops one and adds one, so the sets differ.
    drifted = live != {column.name for column in spec.FEATURE_COLUMNS}
    return drifted == trial.expected


def _sensitive_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the classification this trial wrote is readable on the column.

    Asks the same index the detector asks, about the column the scenario writes,
    and compares against what was planted rather than against a finding.
    """
    column_urn = str(spec.source_column_urn(SENSITIVE_SOURCE_COLUMN))
    return sensitive_index(conn, config).is_marked(column_urn) == trial.expected


def _deprecation_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the deprecation this trial wrote is readable on the input dataset."""
    aspect = conn.graph.get_aspect(str(spec.feature_table_dataset_urn()), DeprecationClass)
    deprecated = bool(aspect and aspect.deprecated)
    return deprecated == trial.expected


_VISIBILITY: dict[FindingType, Callable[[DataHubConnection, Trial, ScanConfig, int], bool]] = {
    FindingType.UPSTREAM_FRESHNESS: _freshness_visible,
    FindingType.TARGET_LEAKAGE: _leakage_visible,
    FindingType.INPUT_SCHEMA_DRIFT: _drift_visible,
    FindingType.SENSITIVE_SOURCE: _sensitive_visible,
    FindingType.DEPRECATED_INPUT: _deprecation_visible,
}


def await_precondition(
    conn: DataHubConnection,
    trial: Trial,
    config: ScanConfig,
    now_ms: int,
    *,
    timeout_s: float = PRECONDITION_TIMEOUT_S,
) -> float | None:
    """Block until the graph shows the state ``trial`` planted.

    Returns:
        Seconds waited, or None if the state never became visible. A None is an
        error in the harness, not a miss by the detector, and the caller reports
        it separately so it cannot quietly depress recall.
    """
    is_visible = _VISIBILITY[trial.family]
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if is_visible(conn, trial, config, now_ms):
            return time.monotonic() - started
        time.sleep(POLL_INTERVAL_S)
    return None


def restore_baseline(conn: DataHubConnection, *, now_ms: int | None = None) -> None:
    """Return the graph to the seeded state: fresh table, leaking edge, no drift.

    Run after the suite so a benchmark leaves the graph the way the demo expects
    to find it, and between families so one detector's trial cannot set up
    another's.
    """
    revert_stale_source(conn, now_ms=now_ms)
    # Before the leak is replanted, because reverting the second path restores
    # the seeded single-path lineage itself and would otherwise be the last word.
    revert_second_leak_path(conn)
    plant_leakage(conn)
    revert_schema_drift(conn)
    # The governance declarations are anomalies planted on top of the seed, not
    # part of it, so the baseline is the withdrawn state for both. The leak above
    # is the exception because it *is* the seeded baseline (D-032).
    revert_sensitive_source(conn)
    revert_deprecated_input(conn)
