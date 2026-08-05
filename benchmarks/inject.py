"""The labelled trials Janus-Bench scores the detectors against.

A trial is one graph state plus the findings that state should produce. Ground
truth is not a judgement call: it is whatever the injector planted, and every
trial is built from the same reversible scenarios the demo uses
(``janus.seed.scenarios``), so the benchmark measures the shipped detectors
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

from datahub.metadata.schema_classes import (
    DeprecationClass,
    GlobalTagsClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)

from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.detect.blast_radius import freshness_signal
from janus.detect.governance import sensitive_index
from janus.models import FindingType
from janus.seed import graph_spec as spec
from janus.seed.scenarios import (
    BACKUP_LABEL_COLUMN,
    LOOKALIKE_COLUMN,
    PROTECTED_TAG_URN,
    PROXY_PROTECTED_COLUMN,
    SENSITIVE_SOURCE_COLUMN,
    plant_common_ancestor_label,
    plant_delinked_model,
    plant_deprecated_input,
    plant_label_lookalike,
    plant_leakage,
    plant_proxy_attribute,
    plant_schema_drift,
    plant_second_leak_path,
    plant_sensitive_source,
    plant_stale_source,
    revert_common_ancestor_label,
    revert_delinked_model,
    revert_deprecated_input,
    revert_label_lookalike,
    revert_leakage,
    revert_proxy_attribute,
    revert_schema_drift,
    revert_second_leak_path,
    revert_sensitive_source,
    revert_stale_source,
)

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
    """Which columns the queried feature must be seen deriving from, exactly.

    Set by the multi-path trials, where "is the label reachable" is no longer
    the question: with two derivations planted, the interesting states differ in
    *which* of them survive, and a precondition that only asked whether some label
    was reachable would pass on the wrong graph. Set by the T-09 confusable
    negatives too, whose upstream is not a label at all. None means the usual
    question: whether the declared label specifically is reachable."""
    leak_feature_column: str = spec.LEAKAGE_FEATURE
    """Which feature-table column's upstream lineage a leakage trial's
    precondition inspects. Every trial about the flagship leak asks about
    ``prior_default_flag``, the default; T-09's confusable-negative scenarios
    never touch that column at all, so they name their own."""
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


def _plant_common_ancestor(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Plant or revert the common-ancestor scenario (T-09).

    Planting also cuts the flagship leak, in the same write the scenario
    itself makes (scenarios.py): prior_default_flag still deriving from
    default_status would otherwise be a second, unrelated leakage finding on
    this model, and ``_observe`` asks about the model as a whole, not about
    applicant_income specifically.
    """
    plant_common_ancestor_label(conn) if trial.graph_state else revert_common_ancestor_label(conn)


def _plant_label_lookalike(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Plant or revert the label-lookalike scenario (T-09). See _plant_common_ancestor."""
    plant_label_lookalike(conn) if trial.graph_state else revert_label_lookalike(conn)


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
UNUSED_LABEL_TERM = "urn:li:glossaryTerm:janus.bench_unused_label"


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
        # T-09 (09 section 2.2): precision of 1.00 is close to vacuous while the
        # negative trials are absent positives rather than hard negatives. Both
        # below plant a graph shape that could plausibly fool a weaker detector
        # and must not fire. leakage-label-lookalike runs before
        # leakage-common-ancestor, never after: nothing in this matrix reverts
        # a trial's plant before the next one runs (restore_baseline is only
        # called once, at the very end), and common-ancestor's own plant
        # happens to restore applicant_income's baseline derivation from
        # income as a side effect of the one _set_column_lineage call it
        # makes. Label-lookalike left last would leave applicant_income
        # deriving from target_indicator for every trial after it, which is
        # exactly what broke the sensitive-source trial's own precondition
        # the first time this ran live (D-116).
        Trial(
            name="leakage-label-lookalike",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=False,
            planted=True,
            detail=(
                "applicant_income derives from a column named like a label, "
                "target_indicator, which carries no label term"
            ),
            plant=_plant_label_lookalike,
            leak_feature_column="applicant_income",
            leak_upstreams=(LOOKALIKE_COLUMN.name,),
            boundary=True,
        ),
        Trial(
            name="leakage-common-ancestor",
            family=FindingType.TARGET_LEAKAGE,
            target=Target.MODEL,
            expected=False,
            planted=True,
            detail=(
                "applicant_income and a declared label both derive from income; "
                "neither descends from the other"
            ),
            plant=_plant_common_ancestor,
            leak_feature_column="applicant_income",
            leak_upstreams=("income",),
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


def _plant_proxy(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Plant or revert the proxy fork (T-11)."""
    plant_proxy_attribute(conn) if trial.graph_state else revert_proxy_attribute(conn)


def _proxy_trials() -> tuple[Trial, ...]:
    """The fork, and the two ways it is not one (T-11, 09 section 5.1).

    The negatives are the whole point of this family. A detector that reported
    every feature sharing any ancestor with any classified column would pass a
    positive-only suite and be useless on a real catalog, because in a
    warehouse everything shares an ancestor with everything eventually.
    """
    return (
        Trial(
            name="proxy-planted",
            family=FindingType.PROXY_CANDIDATE,
            target=Target.MODEL,
            expected=True,
            detail=(
                "a model feature and a column classified a protected attribute both "
                "derive from income; neither descends from the other"
            ),
            plant=_plant_proxy,
            boundary=True,
        ),
        Trial(
            name="proxy-reverted",
            family=FindingType.PROXY_CANDIDATE,
            target=Target.MODEL,
            expected=False,
            detail="the same graph with the protected attribute withdrawn",
            plant=_plant_proxy,
        ),
        Trial(
            name="proxy-direct-descent-is-not-a-candidate",
            family=FindingType.PROXY_CANDIDATE,
            target=Target.MODEL,
            expected=False,
            planted=True,
            detail=(
                "the classified column is the feature's own ancestor rather than its "
                "sibling: proved descent, which the sensitive-source detector reports "
                "and this one must not report a second time"
            ),
            plant=_plant_proxy,
            overrides=(
                ("protected_attribute_tag_urns", ()),
                ("sensitive_tag_urns", (PROTECTED_TAG_URN,)),
            ),
            boundary=True,
        ),
        Trial(
            name="proxy-beyond-the-hop-cap",
            family=FindingType.PROXY_CANDIDATE,
            target=Target.MODEL,
            expected=False,
            planted=True,
            detail=(
                "the same fork with the proxy hop cap at 0: the shared ancestor is out "
                "of reach, not absent, and everything shares one eventually"
            ),
            plant=_plant_proxy,
            overrides=(("proxy_max_hops", 0),),
            boundary=True,
        ),
    )


def _plant_degraded(conn: DataHubConnection, trial: Trial, now_ms: int) -> None:
    """Deprecate the training input, then take the model's features away or give them back.

    Both trials keep the deprecation in place, so the only thing that changes
    between them is whether the model can be answered at column level. That is
    the mode's whole gate, and it is what these two measure: with no features the
    table-level finding is the only thing anybody can be told; with features it
    must go quiet and let the column-level detector speak.
    """
    plant_deprecated_input(conn)
    plant_delinked_model(conn) if trial.expected else revert_delinked_model(conn)


def _degraded_trials() -> tuple[Trial, ...]:
    """The degraded mode, both directions (T-07).

    The negative is the one that matters, and it is a boundary rather than a
    comfortable miss: the graph is *identical* apart from the link, and the table
    really is deprecated, so a detector that ran this mode unconditionally would
    report every properly linked model twice, once with proof and once with a
    maybe.
    """
    return (
        Trial(
            name="degraded-unlinked",
            family=FindingType.TABLE_LEVEL_RISK,
            target=Target.MODEL,
            expected=True,
            detail=(
                "the model declares no features, as it does after every mlflow "
                "ingest, and the table it trains on is deprecated"
            ),
            plant=_plant_degraded,
            boundary=True,
        ),
        Trial(
            name="degraded-linked",
            family=FindingType.TABLE_LEVEL_RISK,
            target=Target.MODEL,
            expected=False,
            detail=(
                "the same deprecated table, with the model's features declared "
                "again: the column-level detector answers, so this mode must not"
            ),
            plant=_plant_degraded,
            boundary=True,
        ),
    )


def build_trials(config: ScanConfig) -> tuple[Trial, ...]:
    """Return the whole matrix, in a fixed order.

    Args:
        config: Supplies the freshness SLA the sweep is built around, so the
            benchmark measures the boundary the scan actually enforces rather
            than a number hardcoded here.
    """
    # The degraded pair sits in the middle rather than at the end, and the reason
    # is measured rather than aesthetic. It is the only family that rewrites
    # mlModelProperties.mlFeatures, which is the last edge of the blast-radius
    # traversal run_bench measures straight after the matrix. Left last, that
    # walk read a relationship index still catching up and reported 0 of 1 models
    # on a graph that plainly held one. Waiting for the model to reappear would
    # have been waiting for the answer, which rule 7 forbids precisely because it
    # would manufacture that recall, so the churn moves instead and the families
    # after it give the index their own preconditions' worth of time.
    return (
        _freshness_trials(config.freshness_sla_hours)
        + _leakage_trials()
        + _degraded_trials()
        + _drift_trials()
        + _sensitive_trials()
        + _proxy_trials()
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


#: Every upstream column any leakage-family scenario can put in play. A trial
#: that names an exact ``leak_upstreams`` set is filtered to this before the
#: comparison, so a query answered mid-reindex, still carrying an edge a
#: *previous* trial wrote, does not fail an exact-set match on noise neither
#: trial cares about.
_RELEVANT_UPSTREAM_COLUMNS: tuple[str, ...] = (
    spec.LABEL_SOURCE_COLUMN,
    BACKUP_LABEL_COLUMN.name,
    "income",
    LOOKALIKE_COLUMN.name,
)


def _leakage_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the lineage index reflects the edge this trial planted or cut.

    Asks the same column-lineage query the detector asks, but inspects the
    *lineage*, not whether a finding was raised.

    A multi-path trial, and T-09's confusable-negative trials, name the exact
    set of columns the queried feature must be seen deriving from, because
    "a label is reachable" (or "the seeded default answers") is true of more
    than one graph and only one of them is the trial. Both ask about
    ``trial.leak_feature_column``, which is ``prior_default_flag`` for every
    trial about the flagship leak and a different column for the ones that
    never touch it.

    T-09's trials also clear the flagship leak while their own scenario is
    planted (and restore it on revert): ``_observe`` asks whether the *model*
    has any leakage finding, so the flagship leak still being present would
    read as a false positive on the scenario's own column rather than what it
    actually is, a precondition that has not finished catching up.
    """
    results = conn.client.lineage.get_lineage(
        source_urn=str(spec.feature_table_dataset_urn()),
        source_column=trial.leak_feature_column,
        direction="upstream",
        max_hops=1,
    )
    reached = {step.column_name for result in results for step in (result.paths or [])}
    if trial.leak_upstreams is not None:
        matched = (reached & set(_RELEVANT_UPSTREAM_COLUMNS)) == set(trial.leak_upstreams)
    else:
        matched = (spec.LABEL_SOURCE_COLUMN in reached) == trial.graph_state
    if not matched or trial.leak_feature_column == spec.LEAKAGE_FEATURE:
        return matched

    flagship = conn.client.lineage.get_lineage(
        source_urn=str(spec.feature_table_dataset_urn()),
        source_column=spec.LEAKAGE_FEATURE,
        direction="upstream",
        max_hops=1,
    )
    flagship_reached = {step.column_name for result in flagship for step in (result.paths or [])}
    # Planted (graph_state True) means the scenario's own column is what must
    # answer, so the flagship leak must be *absent*; reverted restores it.
    return (spec.LABEL_SOURCE_COLUMN in flagship_reached) != trial.graph_state


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
    """Whether the classification this trial wrote is readable *and reachable*.

    The tag's own presence on the column resolves through a synchronously
    served aspect read and says nothing about whether the lineage edge from
    ``applicant_income`` up to it has been indexed yet. The detector needs
    both: it walks that edge (``marked_ancestor``, the traversal leakage
    shares), served from the async-indexed lineage read `_leakage_visible`
    already accounts for. Checking only the tag let this precondition pass
    while the walk still found nothing, live.
    """
    column_urn = str(spec.source_column_urn(SENSITIVE_SOURCE_COLUMN))
    marked = sensitive_index(conn, config).is_marked(column_urn)

    results = conn.client.lineage.get_lineage(
        source_urn=str(spec.feature_table_dataset_urn()),
        source_column="applicant_income",
        direction="upstream",
        max_hops=1,
    )
    reached = {step.column_name for result in results for step in (result.paths or [])}
    return (marked and SENSITIVE_SOURCE_COLUMN in reached) == trial.expected


def _deprecation_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the deprecation this trial wrote is readable on the input dataset."""
    aspect = conn.graph.get_aspect(str(spec.feature_table_dataset_urn()), DeprecationClass)
    deprecated = bool(aspect and aspect.deprecated)
    return deprecated == trial.expected


def _proxy_visible(conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int) -> bool:
    """Whether the fork this trial planted is readable, tag and lineage both.

    Both halves are checked because both are planted, and they land through
    different paths: the tag on the schemaField is served synchronously, the
    derivation from ``income`` is indexed asynchronously. Waiting on the tag
    alone would let a trial ask the detector its question before the ancestry
    the answer depends on had arrived, which is the bug that broke the
    sensitive-source precondition (D-116).
    """
    column_urn = str(spec.feature_column_urn(PROXY_PROTECTED_COLUMN.name))
    tags = conn.graph.get_aspect(column_urn, GlobalTagsClass)
    tagged = bool(tags and any(tag.tag == PROTECTED_TAG_URN for tag in tags.tags))

    results = conn.client.lineage.get_lineage(
        source_urn=str(spec.feature_table_dataset_urn()),
        source_column=PROXY_PROTECTED_COLUMN.name,
        direction="upstream",
        max_hops=1,
    )
    reached = {step.column_name for result in results for step in (result.paths or [])}
    return (tagged and "income" in reached) == trial.graph_state


def _degraded_visible(
    conn: DataHubConnection, trial: Trial, config: ScanConfig, now_ms: int
) -> bool:
    """Whether the model's link is in the state this trial planted, deprecation and all.

    Both halves are checked, because both are planted: a trial that waited only
    on the features could ask the detector its question before the deprecation
    the answer depends on had landed.
    """
    properties = conn.graph.get_aspect(str(spec.model_urn()), MLModelPropertiesClass)
    unlinked = properties is None or not properties.mlFeatures
    aspect = conn.graph.get_aspect(str(spec.feature_table_dataset_urn()), DeprecationClass)
    return unlinked == trial.expected and bool(aspect and aspect.deprecated)


_VISIBILITY: dict[FindingType, Callable[[DataHubConnection, Trial, ScanConfig, int], bool]] = {
    FindingType.UPSTREAM_FRESHNESS: _freshness_visible,
    FindingType.TARGET_LEAKAGE: _leakage_visible,
    FindingType.INPUT_SCHEMA_DRIFT: _drift_visible,
    FindingType.SENSITIVE_SOURCE: _sensitive_visible,
    FindingType.DEPRECATED_INPUT: _deprecation_visible,
    FindingType.PROXY_CANDIDATE: _proxy_visible,
    FindingType.TABLE_LEVEL_RISK: _degraded_visible,
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
    # Both T-09 scenarios move applicant_income's own lineage, which nothing
    # above touches, so they revert independently of the leak above.
    revert_common_ancestor_label(conn)
    revert_label_lookalike(conn)
    revert_proxy_attribute(conn)
    revert_schema_drift(conn)
    # The governance declarations are anomalies planted on top of the seed, not
    # part of it, so the baseline is the withdrawn state for both. The leak above
    # is the exception because it *is* the seeded baseline (D-032).
    revert_sensitive_source(conn)
    revert_deprecated_input(conn)
    # The link is part of the seeded baseline, not an anomaly: a benchmark that
    # left the model unlinked would leave every column-level detector with
    # nothing to read, and the next run would score them all as silent.
    revert_delinked_model(conn)
