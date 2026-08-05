"""T-10: does the generated prose say only what the facts support?

RESULTS.md states that narrative quality is not scored, and 09 section 2.4
argues it should stay that way: an LLM-as-judge rubric is soft evidence, it
varies by provider, and it sits badly next to a project whose whole posture is
that the decisions are deterministic. **Faithfulness** is a different question,
and it is answerable programmatically:

1. Every URN appearing in the prose resolves in the graph. Zero hallucinated
   entities.
2. Every number appearing in the prose appears in the facts the narrator was
   shown.
3. **No number appears in the prose that is absent from those facts.** The
   interesting one, and a hallucination detector for figures: a model that
   quietly computes "five times the SLA" from a 30-hour lag and a 6-hour
   budget has invented a figure nobody measured, and a reader cannot tell that
   from a figure that was.

The grounding set is :func:`janus.agent.narrate.grounding_facts`, which is
the exact text the narrator's prompt wraps in its untrusted-evidence block, and
which the system prompt tells the model is the only thing it may quote figures
from. Deriving it here instead would measure a copy of the answer rather than
the answer.

This module is pure: it takes prose and a finding, and answers. It reaches no
network and builds no connection, so the whole check runs offline against the
template narrator, which is what CI exercises. A provider run is the same
function over prose an API returned.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from janus.agent.narrate import grounding_facts
from janus.models import Finding

#: A number as it appears in text: optional thousands separators, optional
#: decimal part. The leading ``(?<![A-Za-z0-9_.])`` is what keeps an identifier
#: from being read as a figure: the ``3`` in ``credit_risk_v3``, the ``01`` in
#: ``LLM01``, and the ``2024`` in ``loans_raw_2024`` are all parts of a name, and
#: a checker that counted them would flag every model whose version is in its
#: own name. A *trailing* letter is allowed, because a unit is not a name:
#: ``30.0h`` and ``95%`` are figures, and the same rule reads them on both sides.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?")

#: A DataHub URN as it would appear in prose, up to the first whitespace. URNs
#: nest parentheses (``urn:li:mlModel:(urn:li:dataPlatform:mlflow,x,PROD)``), so
#: the tail is trimmed by balancing rather than by the regex.
_URN = re.compile(r"urn:li:[a-zA-Z]+:\S+")

#: Trailing characters a sentence leaves on a URN it ends with.
_TRAILING_PUNCTUATION = ".,;:!?'\"`"


@dataclass(frozen=True)
class Unfaithful:
    """One thing the prose asserted that the facts do not support."""

    kind: str
    """``number`` or ``urn``."""

    token: str
    """The offending text, verbatim, so a failure names what to go and look at."""

    why: str
    """One sentence a reader can act on."""


@dataclass(frozen=True)
class FaithfulnessCheck:
    """The verdict on one narrative."""

    finding_type: str
    source: str
    """``llm`` or ``template``, from :class:`~janus.agent.narrate.Narrative`."""

    provider: str
    """The provider that wrote it, or ``none`` for the template fallback."""

    numbers_checked: int
    urns_checked: int
    violations: tuple[Unfaithful, ...]

    @property
    def faithful(self) -> bool:
        """Whether the prose asserted nothing the facts do not support.

        Note what this does *not* claim: prose that quotes no figure at all is
        faithful by this measure and says nothing. The rate is worth reading
        beside ``numbers_checked``, which is why both are reported.
        """
        return not self.violations


def numbers_in(text: str) -> tuple[float, ...]:
    """Return every figure in ``text``, in order, as floats.

    Comparison is numeric rather than textual on purpose: the evidence renders
    a lag as ``30.0`` and a model writing "30 hours" has quoted it exactly, not
    approximately. Textual matching would call that a hallucination and would
    also accept ``3`` as a substring of ``30``, which is worse.
    """
    found: list[float] = []
    for match in _NUMBER.finditer(text):
        try:
            found.append(float(match.group().replace(",", "")))
        except ValueError:  # pragma: no cover - the pattern cannot produce this
            continue
    return tuple(found)


def urns_in(text: str) -> tuple[str, ...]:
    """Return every URN in ``text``, in order, with sentence punctuation trimmed.

    Parentheses are balanced rather than matched by the regex: a URN's own tail
    is ``,PROD)``, and a URN ending a parenthetical sentence carries a closing
    bracket that is not its own.
    """
    found: list[str] = []
    for match in _URN.finditer(text):
        urn = match.group()
        while urn and (urn[-1] in _TRAILING_PUNCTUATION or _unbalanced(urn)):
            urn = urn[:-1]
        if urn:
            found.append(urn)
    return tuple(found)


def _unbalanced(urn: str) -> bool:
    """Whether ``urn`` closes more parentheses than it opens."""
    return urn.count(")") > urn.count("(")


def check(
    finding: Finding,
    prose: str,
    *,
    source: str,
    provider: str = "none",
    resolves: Callable[[str], bool] | None = None,
) -> FaithfulnessCheck:
    """Measure one narrative against the facts its narrator was shown.

    Args:
        finding: The finding the prose describes. Supplies the grounding facts.
        prose: The assessment text, from an LLM or from the template.
        source: ``llm`` or ``template``.
        provider: Which provider wrote it, for the per-provider breakdown.
        resolves: Whether a URN exists in the graph. ``conn.graph.exists`` in
            the benchmark. None skips the URN check rather than passing it,
            and the count reported is then zero, so a run without a graph
            cannot be read as a run where every URN resolved.

    Returns:
        The verdict, listing every unsupported figure and unresolvable URN.
    """
    facts = grounding_facts(finding)
    grounded = set(numbers_in(facts))

    violations: list[Unfaithful] = []
    quoted = numbers_in(prose)
    for number in quoted:
        if number not in grounded:
            violations.append(
                Unfaithful(
                    kind="number",
                    token=_render(number),
                    why=(
                        f"{_render(number)} appears in the assessment but in none of the "
                        "facts the narrator was shown, so it was invented, derived, or "
                        "rounded rather than measured"
                    ),
                )
            )

    urns = urns_in(prose) if resolves is not None else ()
    for urn in urns:
        if resolves is not None and not resolves(urn):
            violations.append(
                Unfaithful(
                    kind="urn",
                    token=urn,
                    why=f"{urn} appears in the assessment but resolves to nothing in the graph",
                )
            )

    return FaithfulnessCheck(
        finding_type=str(finding.finding_type),
        source=source,
        provider=provider,
        numbers_checked=len(quoted),
        urns_checked=len(urns),
        violations=tuple(violations),
    )


def _render(number: float) -> str:
    """Print a figure the way it was most likely written."""
    return str(int(number)) if number.is_integer() else str(number)


@dataclass(frozen=True)
class FaithfulnessReport:
    """Every check in one run, and the rate over them."""

    checks: tuple[FaithfulnessCheck, ...]

    @property
    def rate(self) -> float | None:
        """The share of narratives asserting nothing unsupported.

        None when nothing was checked, rather than 1.00: a run that narrated
        nothing has not demonstrated faithfulness, and a perfect score printed
        for it would be the most misleading number in the file.
        """
        if not self.checks:
            return None
        return sum(1 for check_ in self.checks if check_.faithful) / len(self.checks)

    @property
    def numbers_checked(self) -> int:
        """How many figures the run's prose quoted, across every narrative."""
        return sum(check_.numbers_checked for check_ in self.checks)

    @property
    def urns_checked(self) -> int:
        """How many URNs the run's prose named, across every narrative."""
        return sum(check_.urns_checked for check_ in self.checks)

    @property
    def violations(self) -> tuple[Unfaithful, ...]:
        """Every unsupported figure and unresolvable URN the run produced."""
        return tuple(v for check_ in self.checks for v in check_.violations)

    def by_provider(self) -> dict[str, tuple[FaithfulnessCheck, ...]]:
        """Checks grouped by who wrote the prose, template included."""
        grouped: dict[str, list[FaithfulnessCheck]] = {}
        for check_ in self.checks:
            grouped.setdefault(check_.provider, []).append(check_)
        return {provider: tuple(items) for provider, items in sorted(grouped.items())}


def report(checks: Iterable[FaithfulnessCheck]) -> FaithfulnessReport:
    """Collect checks into a report."""
    return FaithfulnessReport(checks=tuple(checks))


def check_template_narratives(
    findings: Sequence[Finding],
    *,
    resolves: Callable[[str], bool] | None = None,
) -> FaithfulnessReport:
    """Check the deterministic template over every finding given.

    The template is the path a scan takes with no API key, which is every CI
    run and every offline test, so this is the half of T-10 that is always
    measured rather than measured when a credential happens to be present. It
    is also the stricter half to fail: template prose is written here, so a
    violation is this project quoting a figure it never measured.
    """
    from janus.agent.narrate import narrate

    return report(
        check(
            finding,
            narrate(finding, None).assessment,
            source="template",
            provider="none",
            resolves=resolves,
        )
        for finding in findings
    )
