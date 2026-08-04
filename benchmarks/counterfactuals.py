"""Apply each finding's counterfactual to the graph and check the finding clears.

A remedy is a claim: *change this one thing and the finding goes away*. Printed
in an incident it looks like advice, and advice is not measurable, which is
exactly how a tool ends up shipping remedies nobody ever performed. So this
module performs them. It plants a failure, reads the counterfactual the shipped
detector attached to the finding, applies one of its remedies against the live
graph, and asks the same detector again. A remedy that does not clear its own
finding is a bug (T-03).

What is applied, and what is not
--------------------------------
Every remedy carries a :class:`~modelguard.models.RemedyKind`, and this module
holds an applier for each kind that a metadata write can perform. Several
remedies are real fixes that no harness can carry out: retraining a model,
migrating onto a successor table, dropping a feature from a model somebody else
owns. Those are reported by name as *not mechanically applicable* rather than
counted as passes, because a remedy that was never applied has not been verified
and a table that implied otherwise would be the same fabrication this file
exists to prevent (benchmarks/CLAUDE.md rules 4 and 8).

The multi-path case is the one worth running
--------------------------------------------
A single-path counterfactual is close to a construction proof: the remedy undoes
the plant, so of course the finding clears. The case that can genuinely go wrong
is a finding reached by two derivations, where cutting one is a fix a reasonable
person would believe in. :func:`measure_multi_path` plants exactly that, cuts the
path the incident quoted, and asserts the detector still fires. If it does not,
the counterfactual is telling people to do half a fix.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from benchmarks.inject import Trial, await_precondition, restore_baseline
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import blast_radius, finding_for
from modelguard.detect.degraded import table_level_findings
from modelguard.detect.governance import deprecated_input_findings, sensitive_source_findings
from modelguard.detect.leakage import leakage_findings
from modelguard.detect.schema_drift import schema_drift_findings
from modelguard.models import Finding, FindingType, Remedy, RemedyKind
from modelguard.seed import graph_spec as spec
from modelguard.seed import scenarios

#: How a remedy is performed against the seeded graph, keyed by the detector it
#: belongs to and the kind of change it asks for. Keyed by both because one kind
#: means different work in different families: cutting the derivation of a leak
#: is a different edit from cutting the derivation of a classified column, and
#: the seeder has a reversal for one of them.
#:
#: These are the *scenario* reversals, which is the point: the remedy text tells
#: a human to do something, and the applier does the graph-level equivalent. If
#: they ever stop meaning the same thing, the trial that used to pass fails.
APPLIERS: dict[tuple[FindingType, RemedyKind], Callable[[DataHubConnection], object]] = {
    (FindingType.UPSTREAM_FRESHNESS, RemedyKind.REFRESH_SOURCE): scenarios.revert_stale_source,
    (FindingType.TARGET_LEAKAGE, RemedyKind.CUT_LINEAGE): scenarios.revert_leakage,
    (FindingType.INPUT_SCHEMA_DRIFT, RemedyKind.RESTORE_SCHEMA): scenarios.revert_schema_drift,
    (FindingType.SENSITIVE_SOURCE, RemedyKind.CORRECT_MARK): scenarios.revert_sensitive_source,
    (
        FindingType.DEPRECATED_INPUT,
        RemedyKind.WITHDRAW_DEPRECATION,
    ): scenarios.revert_deprecated_input,
    # The degraded mode's first remedy is not a fix to the data at all: declaring
    # the link is what replaces a table-level maybe with a column-level answer,
    # and re-declaring the model's features is that write. So this applier is the
    # one place the benchmark checks that the mode really does stand down once
    # somebody links the model, rather than adding a second opinion beside the
    # detector that can prove things.
    (FindingType.TABLE_LEVEL_RISK, RemedyKind.DECLARE_LINK): scenarios.revert_delinked_model,
}


def findings_for(
    conn: DataHubConnection,
    config: ScanConfig,
    family: FindingType,
    now_ms: int,
) -> tuple[Finding, ...]:
    """Return what one detector finds on the graph as it currently stands.

    Raises rather than returning nothing for an unregistered family, for the
    reason ``run_bench._observe`` does: a detector nobody wired up here would
    otherwise be scored as one that never fires, which is a perfect
    false-negative rate reported as a measurement.
    """
    if family is FindingType.UPSTREAM_FRESHNESS:
        radius = blast_radius(conn, str(spec.source_table_urn()), config, now_ms=now_ms)
        return () if radius is None else (finding_for(radius),)
    if family is FindingType.TARGET_LEAKAGE:
        return leakage_findings(conn, str(spec.model_urn()), config)
    if family is FindingType.INPUT_SCHEMA_DRIFT:
        return schema_drift_findings(conn, str(spec.model_urn()), config)
    if family is FindingType.SENSITIVE_SOURCE:
        return sensitive_source_findings(conn, str(spec.model_urn()), config)
    if family is FindingType.DEPRECATED_INPUT:
        return deprecated_input_findings(conn, str(spec.model_urn()), config)
    if family is FindingType.TABLE_LEVEL_RISK:
        return table_level_findings(conn, str(spec.model_urn()), config, now_ms=now_ms)
    raise ValueError(f"no detector registered for {family}")


@dataclass(frozen=True)
class CounterfactualCheck:
    """One family's counterfactual, and what happened when it was performed."""

    family: FindingType
    fired: bool
    """Whether the finding was there to be remedied in the first place. False
    makes everything below meaningless, and the report says so rather than
    reading a vacuous pass as a success."""
    applied: tuple[Remedy, ...] = ()
    """The remedies this harness performed against the graph."""
    cleared: bool = False
    """Whether the detector went quiet after they were applied."""
    unapplied: tuple[RemedyKind, ...] = ()
    """Remedies with no mechanical applier: real fixes, unverified here."""
    settled: bool = True
    """Whether DataHub showed the remedied state before the detector was asked.
    False means the trial is an error, not a failure."""

    @property
    def verified(self) -> bool:
        """Whether this row is a measurement rather than an accident."""
        return self.fired and self.settled and bool(self.applied)


def _cleared_state(trial: Trial) -> Trial:
    """Return the same trial describing the graph *after* its remedy.

    The precondition helpers all compare the graph against the trial, so the way
    to wait for a remedy to land is to hand them a trial that expects the
    remedied graph. The lag goes to zero for a freshness trial because that is
    what a refresh means, and the freshness precondition compares the observed
    lag against the trial's own rather than against the SLA.
    """
    return replace(
        trial,
        expected=False,
        planted=None,
        lag_hours=None if trial.lag_hours is None else 0.0,
    )


def measure_counterfactual(
    conn: DataHubConnection,
    config: ScanConfig,
    trial: Trial,
) -> CounterfactualCheck:
    """Plant one family's failure, apply its counterfactual, and re-detect.

    Args:
        conn: A connection with write credentials. This measurement mutates the
            graph, like the write-back one, and restores nothing itself: the
            caller restores the baseline once at the end.
        config: The scan configuration the detector runs under.
        trial: A trial whose plant produces the failure under test. Reused rather
            than replanted here so this measures the same graph state the
            detection table scored.

    Returns:
        What was applied and whether the finding cleared.
    """
    now_ms = int(time.time() * 1000)
    trial.plant(conn, trial, now_ms)
    if await_precondition(conn, trial, config, now_ms) is None:
        return CounterfactualCheck(family=trial.family, fired=False, settled=False)

    findings = findings_for(conn, config, trial.family, now_ms)
    if not findings:
        return CounterfactualCheck(family=trial.family, fired=False)

    counterfactual = findings[0].counterfactual
    applied = tuple(
        remedy for remedy in counterfactual.remedies if (trial.family, remedy.kind) in APPLIERS
    )
    unapplied = tuple(remedy.kind for remedy in counterfactual.remedies if remedy not in applied)
    if not applied:
        return CounterfactualCheck(family=trial.family, fired=True, unapplied=unapplied)

    for remedy in applied:
        APPLIERS[(trial.family, remedy.kind)](conn)

    remedied = _cleared_state(trial)
    now_ms = int(time.time() * 1000)
    if await_precondition(conn, remedied, config, now_ms) is None:
        return CounterfactualCheck(
            family=trial.family, fired=True, applied=applied, unapplied=unapplied, settled=False
        )

    return CounterfactualCheck(
        family=trial.family,
        fired=True,
        applied=applied,
        cleared=not findings_for(conn, config, trial.family, now_ms),
        unapplied=unapplied,
    )


@dataclass(frozen=True)
class MultiPathCheck:
    """What a two-path finding said, and what half a fix did to it."""

    paths_reported: int
    """How many derivations the counterfactual declared. Two, or it is wrong."""
    edges_named: int
    """How many first edges its cut remedy listed. One would be half a fix."""
    still_fires_after_one_cut: bool
    """The measurement this whole scenario exists for."""
    cleared_after_both_cuts: bool
    """The control: cutting every path really does clear it, so the row above is
    about the number of paths and not about a detector that cannot be silenced."""
    settled: bool = True


def measure_multi_path(
    conn: DataHubConnection,
    config: ScanConfig,
    trials: Sequence[Trial],
) -> MultiPathCheck | None:
    """Plant two derivations, cut one, and check the finding stands.

    Args:
        conn: A connection with write credentials.
        config: The scan configuration the detector runs under.
        trials: The matrix, from which the two multi-path leakage trials are
            taken by name so this measurement and the detection table agree
            about what "two paths" means.

    Returns:
        The measurement, or None when the matrix carries no multi-path trials.
    """
    both, one = (
        next((t for t in trials if t.name == name), None)
        for name in ("leakage-two-paths", "leakage-one-of-two-cut")
    )
    if both is None or one is None:
        return None

    now_ms = int(time.time() * 1000)
    both.plant(conn, both, now_ms)
    if await_precondition(conn, both, config, now_ms) is None:
        return MultiPathCheck(0, 0, False, False, settled=False)

    findings = findings_for(conn, config, FindingType.TARGET_LEAKAGE, now_ms)
    if not findings:
        return MultiPathCheck(0, 0, False, False)

    counterfactual = findings[0].counterfactual
    cut = next(
        (r for r in counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE),
        None,
    )

    # Half a fix: the derivation the incident quoted goes, the other stays.
    now_ms = int(time.time() * 1000)
    one.plant(conn, one, now_ms)
    if await_precondition(conn, one, config, now_ms) is None:
        return MultiPathCheck(
            counterfactual.paths, len(cut.targets) if cut else 0, False, False, settled=False
        )
    still_fires = bool(findings_for(conn, config, FindingType.TARGET_LEAKAGE, now_ms))

    # The whole fix, which is what the counterfactual actually asked for.
    scenarios.revert_second_leak_path(conn)
    scenarios.revert_leakage(conn)
    cleared_trial = _cleared_state(replace(one, leak_upstreams=None, planted=False))
    now_ms = int(time.time() * 1000)
    if await_precondition(conn, cleared_trial, config, now_ms) is None:
        return MultiPathCheck(
            counterfactual.paths, len(cut.targets) if cut else 0, still_fires, False, settled=False
        )
    cleared = not findings_for(conn, config, FindingType.TARGET_LEAKAGE, now_ms)

    return MultiPathCheck(
        paths_reported=counterfactual.paths,
        edges_named=len(cut.targets) if cut else 0,
        still_fires_after_one_cut=still_fires,
        cleared_after_both_cuts=cleared,
    )


def measure_counterfactuals(
    conn: DataHubConnection,
    config: ScanConfig,
    trials: Sequence[Trial],
    *,
    log: bool = True,
) -> tuple[CounterfactualCheck, ...]:
    """Run one counterfactual check per detector, in the matrix's own order.

    The trial used per family is that family's first positive trial with no
    configuration override, so the graph state under test is the same one the
    detection table scored rather than a second state invented here.
    """
    checks: list[CounterfactualCheck] = []
    for family in FindingType:
        trial = next(
            (t for t in trials if t.family is family and t.expected and not t.overrides),
            None,
        )
        if trial is None:
            continue
        check = measure_counterfactual(conn, config, trial)
        checks.append(check)
        if log:
            state = (
                "cleared"
                if check.cleared
                else (
                    "ERROR" if not check.settled else ("no finding" if not check.fired else "STOOD")
                )
            )
            print(f"  {family.value:<20} {len(check.applied)} remedy(ies) applied  {state}")
    restore_baseline(conn)
    return tuple(checks)
