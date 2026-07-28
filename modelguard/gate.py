"""The policy that decides whether a build may proceed.

ModelGuard's tagline is "the missing CI for your ML supply chain", and until this
module existed that was aspirational: everything it did was after the fact, on a
graph that already held the mistake. A gate is the preventive half. It answers one
question, in an exit code, at the moment somebody proposes a change: *would this
model be safe to ship?*

Kept separate from the CLI, and pure, for the same reason
``benchmarks/metrics.py`` is: this is where a silent mistake is invisible. A gate
that wrongly passes is worse than no gate, because a team stops looking. Every
decision here is a function of a :class:`~modelguard.agent.pipeline.ScanReport`
and a :class:`GatePolicy`, so it can be exercised offline against hand-built
reports, and the CLI is left holding no judgement of its own.

Why a gate reads and does not write
-----------------------------------
Every other ModelGuard entry point writes back: that is the point of it. This one
does not, by default, and the reason is the context rather than a change of heart.
A gate runs on every push to every branch of every pull request, most of which are
never merged, many of which are the same commit retried. Raising an incident on
each would fill the governance graph with findings about code that does not exist,
keyed to a run nobody can trace, and the write-back layer's idempotency does not
help: these are genuinely different runs, not repeats of one.

So the gate reads the graph, judges, and reports. The write-back belongs on the
branch that merged, which is what ``scan`` on main is for. ``--write`` exists for
teams that decide otherwise, and says so.

Exit codes, and why the distinction is load-bearing
---------------------------------------------------
* ``0`` the policy is satisfied.
* ``1`` the policy is violated: this change should not ship.
* ``2`` the gate could not reach a verdict: DataHub was unreachable, the config
  was unusable, the target did not resolve.

Collapsing 2 into 1 would be the mistake that gets a gate switched off. A tool
that cannot tell "your model is leaking" from "I could not connect" trains the
team to read every red build as infrastructure flakiness, and the first real
finding is waved through with the rest.
"""

from __future__ import annotations

from dataclasses import dataclass

from modelguard.agent.pipeline import ScanReport
from modelguard.models import Finding, Severity, severity_rank

#: Exit code when the policy is satisfied.
EXIT_PASS = 0

#: Exit code when the policy is violated. The build should stop here.
EXIT_BLOCKED = 1

#: Exit code when no verdict could be reached. Never used for a finding.
EXIT_ERROR = 2


@dataclass(frozen=True)
class GatePolicy:
    """What a team considers unshippable.

    Both rules are opt-in and independent. A policy with neither set blocks
    nothing, which is deliberate: a gate that fails on installation, before anyone
    has said what they care about, is a gate that gets removed the same afternoon.
    """

    block_at_or_above: Severity | None = None
    """Block when any finding is at least this severe. None disables the rule."""

    min_trust_score: float | None = None
    """Block when any scored model falls below this. None disables the rule."""

    @property
    def blocks_anything(self) -> bool:
        """Whether this policy can fail a build at all."""
        return self.block_at_or_above is not None or self.min_trust_score is not None


@dataclass(frozen=True)
class Violation:
    """One reason the build was stopped, in terms the author can act on."""

    headline: str
    """What is wrong, as one line. Quoted into the CI annotation."""
    detail: str
    """What to do about it, or the evidence behind it."""
    severity: Severity | None = None
    """The finding's severity, when the violation came from one."""


@dataclass(frozen=True)
class GateVerdict:
    """The gate's answer, and everything needed to explain it."""

    violations: tuple[Violation, ...]
    findings_seen: int
    """Every finding the scan produced, including those the policy tolerates."""
    policy: GatePolicy

    @property
    def blocked(self) -> bool:
        """Whether the build should stop."""
        return bool(self.violations)

    @property
    def exit_code(self) -> int:
        """The code the CLI returns. Never 2: a verdict was reached."""
        return EXIT_BLOCKED if self.blocked else EXIT_PASS


def _blocks(finding: Finding, threshold: Severity) -> bool:
    """Whether a finding is at least as severe as the threshold.

    Compares through ``severity_rank`` rather than the enum's own order, because
    the enum is declared worst-first and a naive ``>=`` on it would silently mean
    the opposite of what it reads like.
    """
    return severity_rank(finding.severity) <= severity_rank(threshold)


def evaluate(report: ScanReport, policy: GatePolicy) -> GateVerdict:
    """Judge a scan against a policy.

    Pure: it reads the report it is handed and touches nothing else, so the same
    report and policy always produce the same verdict.

    Args:
        report: What the scan found. A dry-run report is expected and normal here.
        policy: What the team considers unshippable.

    Returns:
        The verdict, listing every violation rather than the first, so one run
        tells the author everything they have to fix.
    """
    violations: list[Violation] = []

    if policy.block_at_or_above is not None:
        for write in report.writes:
            finding = write.finding
            if _blocks(finding, policy.block_at_or_above):
                violations.append(
                    Violation(
                        headline=finding.title,
                        detail=(
                            f"severity {finding.severity.value}, at or above the "
                            f"{policy.block_at_or_above.value} the policy blocks on"
                        ),
                        severity=finding.severity,
                    )
                )

    if policy.min_trust_score is not None:
        for trust in report.trust:
            if trust.score.value < policy.min_trust_score:
                risks = ", ".join(sorted(trust.score.deductions)) or "no recorded risk"
                violations.append(
                    Violation(
                        headline=(
                            f"{trust.model_name} scores {trust.score.value}/100, "
                            f"below the required {policy.min_trust_score:g}"
                        ),
                        detail=f"deductions: {risks}",
                    )
                )

    return GateVerdict(
        violations=tuple(violations),
        findings_seen=len(report.writes),
        policy=policy,
    )


def github_annotations(verdict: GateVerdict) -> tuple[str, ...]:
    """Render the verdict as GitHub Actions workflow commands.

    A gate that only sets an exit code makes the author open the log and read it.
    These put each violation on the pull request itself, where the change is. The
    format is GitHub's; other CI systems ignore the lines as ordinary output,
    which is why they are emitted unconditionally rather than behind a flag that
    somebody has to remember.

    Newlines are escaped as ``%0A`` because a raw one ends the command, and the
    rest of the message would be printed as unrelated log noise.
    """
    rendered: list[str] = []
    for violation in verdict.violations:
        title = violation.headline.replace("\n", " ")
        body = violation.detail.replace("\n", "%0A")
        rendered.append(f"::error title=ModelGuard: {title}::{body}")
    return tuple(rendered)


def summary(verdict: GateVerdict) -> str:
    """One line stating the outcome, for a human reading the log tail."""
    if not verdict.policy.blocks_anything:
        return (
            f"{verdict.findings_seen} finding(s); no blocking policy set, so nothing "
            "was enforced. Pass --block-at-or-above or --min-trust to enforce one."
        )
    if verdict.blocked:
        return (
            f"BLOCKED: {len(verdict.violations)} violation(s) "
            f"across {verdict.findings_seen} finding(s)."
        )
    return f"PASSED: {verdict.findings_seen} finding(s), none violating the policy."
