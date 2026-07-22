"""The scoring arithmetic, kept pure so it can be tested without a DataHub.

Nothing here reads a graph, times anything, or knows what a detector is. It turns
(expected, observed) pairs into the numbers RESULTS.md publishes, which is the one
part of the benchmark where a silent mistake would be invisible: a wrong
precision still looks like a precision. Separating it means the arithmetic is
checked by fast offline tests, and the live harness is left with no arithmetic of
its own to get wrong.

Conventions
-----------
A "positive" is a trial where the detector was supposed to fire. So a false
positive is an alert on a clean graph, which is the failure that makes a
reliability tool get switched off, and it is reported as its own rate rather than
being folded into precision alone.

Undefined rates are ``None``, never zero. Precision with no alerts at all is not
zero precision, it is a precision nobody measured, and printing 0.00 there would
read as a detector that is always wrong.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Confusion:
    """One detector's trials, counted."""

    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int

    @property
    def positives(self) -> int:
        """Trials where the detector was supposed to fire."""
        return self.true_positives + self.false_negatives

    @property
    def negatives(self) -> int:
        """Trials where a clean graph was supposed to stay quiet."""
        return self.true_negatives + self.false_positives

    @property
    def total(self) -> int:
        """Every scored trial."""
        return self.positives + self.negatives

    @property
    def precision(self) -> float | None:
        """Of the alerts raised, the fraction that were real. None if none were."""
        alerts = self.true_positives + self.false_positives
        return self.true_positives / alerts if alerts else None

    @property
    def recall(self) -> float | None:
        """Of the real problems, the fraction caught. None if there were none."""
        return self.true_positives / self.positives if self.positives else None

    @property
    def f1(self) -> float | None:
        """Harmonic mean of precision and recall, when both are defined."""
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)

    @property
    def false_positive_rate(self) -> float | None:
        """Of the clean trials, the fraction that raised an alert anyway."""
        return self.false_positives / self.negatives if self.negatives else None


def confusion(observations: Sequence[tuple[bool, bool]]) -> Confusion:
    """Count (expected, observed) pairs into a confusion matrix.

    Args:
        observations: One pair per scored trial: what should have happened, and
            what the detector actually did.
    """
    true_positives = sum(1 for expected, observed in observations if expected and observed)
    false_positives = sum(1 for expected, observed in observations if not expected and observed)
    true_negatives = sum(1 for expected, observed in observations if not expected and not observed)
    false_negatives = sum(1 for expected, observed in observations if expected and not observed)
    return Confusion(
        true_positives=true_positives,
        false_positives=false_positives,
        true_negatives=true_negatives,
        false_negatives=false_negatives,
    )


@dataclass(frozen=True)
class Latency:
    """A set of timings, summarised the way an SLO is written."""

    samples: tuple[float, ...]

    @property
    def count(self) -> int:
        """How many timings were taken."""
        return len(self.samples)

    @property
    def median_s(self) -> float | None:
        """The typical case."""
        return statistics.median(self.samples) if self.samples else None

    @property
    def worst_s(self) -> float | None:
        """The slowest observed run.

        Reported instead of a p95, because a run of this size has too few samples
        for a percentile to mean anything, and quoting one would dress up a
        maximum as a distribution.
        """
        return max(self.samples) if self.samples else None


def fraction(found: int, total: int) -> float | None:
    """Return found/total, or None when there was nothing to find."""
    return found / total if total else None


def format_rate(value: float | None) -> str:
    """Render a rate for the results table, marking the undefined case.

    A dash says "not measured". It never reads as zero, which is a different and
    much worse claim.
    """
    return "-" if value is None else f"{value:.2f}"


def format_seconds(value: float | None) -> str:
    """Render a duration for the results table."""
    return "-" if value is None else f"{value:.2f}s"


def meets(value: float | None, target: float, *, higher_is_better: bool = True) -> str:
    """Say whether a measurement cleared the hardening doc's target.

    Returns a bare marker rather than a judgement, so RESULTS.md states the
    comparison and leaves the reader to draw the conclusion. An unmeasured value
    cannot pass or fail, so it reports neither.
    """
    if value is None:
        return "not measured"
    if higher_is_better:
        return "meets target" if value >= target else "BELOW TARGET"
    return "meets target" if value <= target else "ABOVE TARGET"
