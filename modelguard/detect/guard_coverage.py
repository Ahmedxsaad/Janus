"""How much of a catalog ModelGuard can actually check, as one number (T-15).

:mod:`modelguard.detect.coverage` answers the question per model: which checks
could not run against *this* model, and what is missing. That answer is the right
one when somebody is looking at one model, and the wrong shape entirely for the
person who has to decide whether adopting this tool was worth it. They ask a
different question:

    Guard coverage: 34% of models have a checkable leakage path (up from 11%),
    8% have a training schema snapshot.

Nothing in the ecosystem produces that figure, and it reframes the adoption cliff
(F10, F11) from an embarrassment into a roadmap: ModelGuard measures how
observable an ML estate is, then names the single next declaration that would
raise the number most.

Why this is a separate module from ``coverage.py``
--------------------------------------------------
``coverage.py`` reads the graph. This one reads nothing: it is a pure fold over
gaps somebody else already collected, which is what lets a caller feed it a
sweep it ran however it liked (``inventory``'s dry-run scans, a benchmark's
fixture, a replay of stored gaps) without this module knowing about scans at all.
Keeping the read out of here is also what keeps ``detect/`` from having to import
``agent/pipeline``, which would invert the layering.

The denominator
---------------
Models, and only models it was actually handed. A rate over a set the caller
chose is a rate about that set, so :attr:`CatalogCoverage.models` is reported
beside every percentage rather than left implicit: 100% of two models is not the
same claim as 100% of two hundred.

Freshness is deliberately not in the figure. It is a check asked of a *table*,
and folding a table check and five model checks into one percentage would divide
by two different denominators and call the result one number. 09 section 3.2's
illustrative sentence included it; that is corrected in place per docs/CLAUDE.md
rule 1.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from modelguard.detect.coverage import MODEL_CHECKS, Unevaluated


@dataclass(frozen=True)
class ModelCoverage:
    """One model in the sweep, and the checks that could not run against it."""

    model_urn: str
    gaps: tuple[Unevaluated, ...]

    @property
    def blocked_checks(self) -> frozenset[str]:
        """The check names this model could not be evaluated for."""
        return frozenset(gap.check for gap in self.gaps)


@dataclass(frozen=True)
class CheckCoverage:
    """One check, and how much of the catalog it can currently be run against."""

    check: str
    covered: int
    """Models where this check had the metadata it needed."""
    total: int
    """Models in the sweep. Held per check rather than once, so a row is readable
    on its own when it is quoted out of the table."""

    @property
    def rate(self) -> float:
        """Fraction of models this check can run against, 0.0 to 1.0.

        Zero models is 0.0 and not 1.0. An empty catalog has not passed every
        check; it has not been asked any, and the reassuring reading of that
        arithmetic is exactly the one this whole module exists to prevent.
        """
        return self.covered / self.total if self.total else 0.0

    def describe(self) -> str:
        """One line for a console: the percentage with its denominator attached."""
        return f"{self.check}: {self.rate:.0%} ({self.covered}/{self.total} models)"


@dataclass(frozen=True)
class NextJoin:
    """The one declaration that would unblock the most checks.

    Keyed on the *remedy*, not on the check or the model, because the remedy is
    the unit of work somebody actually does. One `modelguard link` on a model
    unblocks leakage, sensitive source and proxy at once, and a ranking by check
    would recommend it three times instead of once.
    """

    remedy: str
    """The action, quoted verbatim from the gap that asked for it. Never
    rephrased: a gap already states its remedy precisely enough to go and do, and
    a summary written here would be a second wording that could drift from it."""

    unblocks: int
    """How many (model, check) pairs this one action would make evaluable."""

    models: tuple[str, ...]
    """The models it would unblock, sorted. The caller decides how many to show."""

    def describe(self) -> str:
        """One line naming the action and what it buys."""
        return (
            f"{self.unblocks} check(s) across {len(self.models)} model(s) are blocked "
            f"on one action: {self.remedy}"
        )


@dataclass(frozen=True)
class CatalogCoverage:
    """What fraction of a catalog's models each check can be run against."""

    models: int
    """How many models the sweep covered. The denominator, stated once."""

    checks: tuple[CheckCoverage, ...]
    """One row per check in :data:`~modelguard.detect.coverage.MODEL_CHECKS`
    order, so two runs of the same catalog render in the same order."""

    next_join: NextJoin | None
    """The highest-leverage remedy, or None when nothing is blocked."""

    @property
    def covered_checks(self) -> int:
        """(model, check) pairs that could be evaluated."""
        return sum(check.covered for check in self.checks)

    @property
    def total_checks(self) -> int:
        """(model, check) pairs the sweep asked about."""
        return sum(check.total for check in self.checks)

    @property
    def rate(self) -> float:
        """The single catalog figure: evaluable pairs over asked pairs."""
        return self.covered_checks / self.total_checks if self.total_checks else 0.0

    @property
    def by_check(self) -> Mapping[str, CheckCoverage]:
        """The rows keyed by check name, for a caller that wants one of them."""
        return {check.check: check for check in self.checks}


def _next_join(sweep: Sequence[ModelCoverage]) -> NextJoin | None:
    """Return the remedy blocking the most (model, check) pairs, or None.

    Ties are broken by the number of distinct models and then by the remedy text,
    so a catalog where two actions are worth the same amount names the same one
    on every run. A recommendation that reshuffled between two identical runs
    would read as the tool changing its mind about a graph that did not change.
    """
    unblocks: dict[str, int] = {}
    models: dict[str, set[str]] = {}
    for model in sweep:
        for gap in model.gaps:
            unblocks[gap.remedy] = unblocks.get(gap.remedy, 0) + 1
            models.setdefault(gap.remedy, set()).add(model.model_urn)

    if not unblocks:
        return None

    remedy = max(
        unblocks,
        key=lambda text: (unblocks[text], len(models[text]), text),
    )
    return NextJoin(
        remedy=remedy,
        unblocks=unblocks[remedy],
        models=tuple(sorted(models[remedy])),
    )


def aggregate(sweep: Iterable[ModelCoverage]) -> CatalogCoverage:
    """Fold a sweep's per-model gaps into one catalog figure.

    Args:
        sweep: One entry per model examined. A model with no gaps is a model
            every check ran against, and it must still be passed in: leaving the
            clean ones out would shrink the denominator and inflate every rate.

    Returns:
        The per-check rates, in
        :data:`~modelguard.detect.coverage.MODEL_CHECKS` order, and the single
        remedy that would unblock the most.
    """
    entries = list(sweep)
    blocked_per_check = dict.fromkeys(MODEL_CHECKS, 0)
    for model in entries:
        for check in model.blocked_checks & set(MODEL_CHECKS):
            blocked_per_check[check] += 1

    return CatalogCoverage(
        models=len(entries),
        checks=tuple(
            CheckCoverage(
                check=check,
                covered=len(entries) - blocked_per_check[check],
                total=len(entries),
            )
            for check in MODEL_CHECKS
        ),
        next_join=_next_join(entries),
    )
