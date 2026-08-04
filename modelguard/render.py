"""Renderings of a scan for readers that are not a terminal.

``cli.py`` prints a scan for a human sitting at a console, with colour and
soft-wrapped URNs. Two other readers exist and neither is served by that:

* **A program.** A team that wants to route findings somewhere ModelGuard does
  not know about (a ticket, a dashboard, a policy engine of their own) should
  not have to parse coloured console text. ``--format json`` gives them the
  report as data.
* **A pull request.** ``gate`` answers in an exit code, which in CI means a red
  cross and a click into a log. GitHub Actions already exposes a better place:
  anything appended to the file named by ``GITHUB_STEP_SUMMARY`` renders as
  markdown on the run's own page. No token, no API call, no permission block.

Both renderings are pure functions of a :class:`~modelguard.agent.pipeline.ScanReport`
(and, for the gate, a :class:`~modelguard.gate.GateVerdict`), for the same reason
:mod:`modelguard.gate` is pure: they can be exercised offline against a
hand-built report, and they hold no judgement of their own. Nothing here decides
anything; it only restates what a detector already measured.

The JSON shape is a public interface
------------------------------------
Somebody will write a script against it, so the keys are stable and every value
is a plain JSON type. Numbers a reader might act on (severities, trust scores,
hop counts) come from a finding's own evidence mapping, never from prose: the
narrator's assessment is carried under its own key, alongside the source that
produced it, so a consumer can tell a measured fact from a drafted sentence.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from modelguard.agent.pipeline import FindingWrites, ScanReport
from modelguard.config import SCORE_PROVENANCE, SCORING_VERSION
from modelguard.env import optional_value
from modelguard.gate import GateVerdict
from modelguard.models import ModelRef


class OutputFormat(StrEnum):
    """What a command puts on stdout.

    A StrEnum rather than a bare string so Typer validates the value itself and
    lists the choices in ``--help``: a typo becomes a usage error, never a silent
    fall back to the other format.
    """

    TEXT = "text"
    """A report for a human at a terminal: the default, and the only one with colour."""

    JSON = "json"
    """One parseable document, for a program. Progress lines move to stderr."""


#: GitHub Actions sets this to a file every step may append markdown to, which is
#: then rendered on the run's summary page. Read through :mod:`modelguard.env`
#: like every other environment value (modelguard/CLAUDE.md, env.py boundary).
ENV_STEP_SUMMARY = "GITHUB_STEP_SUMMARY"

#: How many findings the job summary lists before it stops and says how many are
#: left. A gate on a badly broken model can produce dozens, and a summary page
#: that has to be scrolled past is one nobody reads.
_SUMMARY_FINDING_LIMIT = 20


def _model_dict(model: ModelRef) -> dict[str, Any]:
    """One model a finding endangers, as data."""
    return {
        "urn": model.urn,
        "name": model.name,
        "is_live": model.is_live,
        "has_owner": model.has_owner,
    }


def _finding_dict(write: FindingWrites) -> dict[str, Any]:
    """One finding, its evidence, and the prose drafted about it."""
    finding = write.finding
    return {
        "type": finding.finding_type.value,
        "title": finding.title,
        "severity": finding.severity.value,
        "resource_urn": finding.resource_urn,
        # Already a str -> str mapping by the Finding contract, so it needs no
        # coercion and cannot carry a type json cannot represent.
        "evidence": dict(finding.evidence),
        "models_at_risk": [_model_dict(model) for model in finding.models_at_risk],
        "assessment": write.narrative.assessment,
        "assessment_source": write.narrative.source.value,
    }


def report_dict(report: ScanReport, verdict: GateVerdict | None = None) -> dict[str, Any]:
    """Render a scan, and optionally a gate's verdict on it, as JSON-ready data.

    Args:
        report: What the scan found.
        verdict: The gate's judgement, when this rendering is for ``gate``. A
            plain ``scan`` passes None and the ``gate`` key is absent, rather
            than present and null: a consumer checking for the key then cannot
            mistake "no policy was applied" for "the policy passed".

    Returns:
        A dictionary of plain JSON types, safe to hand to :func:`json.dumps`.
    """
    payload: dict[str, Any] = {
        "run_id": report.run_id,
        "dry_run": report.dry_run,
        "table_urn": report.table_urn,
        "model_urn": report.model_urn,
        "clean": report.clean,
        "severity": report.severity.value if report.severity else None,
        "findings": [_finding_dict(write) for write in report.writes],
        "trust": [
            {
                "model_urn": trust.model_urn,
                "model_name": trust.model_name,
                "score": trust.score.value,
                "band": trust.score.band.value,
                "scoring_version": SCORING_VERSION,
                # Null, not the current score, when this model has never been
                # scored before: "unchanged" and "never measured" are different
                # facts and only one of them is reassuring.
                "previous_score": trust.previous_score,
                # An array and not an object, because the order is information:
                # worst deduction first, which is the waterfall a human reads in
                # the terminal (F7). A consumer that wants a lookup can build one;
                # one that gets an object cannot recover the ordering.
                "deductions": [
                    {
                        "name": deduction.name,
                        "points": deduction.points,
                        "cause": deduction.cause,
                    }
                    for deduction in trust.score.deductions
                ],
            }
            for trust in report.trust
        ],
        "not_evaluated": [
            {
                "check": gap.check,
                "target_urn": gap.target_urn,
                "reason": gap.reason,
                "remedy": gap.remedy,
            }
            for gap in report.not_evaluated
        ],
        "warnings": list(report.warnings),
    }

    if verdict is not None:
        payload["gate"] = {
            "blocked": verdict.blocked,
            "exit_code": verdict.exit_code,
            "findings_seen": verdict.findings_seen,
            "enforced": verdict.policy.blocks_anything,
            "violations": [
                {
                    "headline": violation.headline,
                    "detail": violation.detail,
                    "severity": violation.severity.value if violation.severity else None,
                }
                for violation in verdict.violations
            ],
        }

    return payload


def report_json(report: ScanReport, verdict: GateVerdict | None = None) -> str:
    """Render a scan as a JSON document, indented for a human who pipes it to a file."""
    return json.dumps(report_dict(report, verdict), indent=2, sort_keys=False)


def _summary_headline(report: ScanReport, verdict: GateVerdict | None) -> str:
    """The one line at the top of the summary, stating the outcome."""
    if verdict is not None:
        if not verdict.policy.blocks_anything:
            return "### ModelGuard: no blocking policy set"
        return "### ModelGuard: BLOCKED" if verdict.blocked else "### ModelGuard: passed"
    if report.clean and not report.not_evaluated:
        return "### ModelGuard: no finding"
    if report.clean:
        return "### ModelGuard: no finding, some checks did not run"
    return f"### ModelGuard: {len(report.writes)} finding(s)"


def job_summary_markdown(report: ScanReport, verdict: GateVerdict | None = None) -> str:
    """Render the scan as the markdown GitHub shows on a workflow run's page.

    Deliberately a summary and not the whole report: the findings table, the
    trust scores, and the checks that could not run. The evidence behind each
    finding stays in the log and in the JSON, because a summary long enough to
    hold it is one a reviewer scrolls past.
    """
    lines = [_summary_headline(report, verdict), ""]

    if verdict is not None and verdict.violations:
        lines.append("| Violation | Why |")
        lines.append("|---|---|")
        for violation in verdict.violations:
            lines.append(f"| {violation.headline} | {violation.detail} |")
        lines.append("")

    if report.writes:
        lines.append("| Finding | Severity | Models at risk |")
        lines.append("|---|---|---|")
        for write in report.writes[:_SUMMARY_FINDING_LIMIT]:
            finding = write.finding
            models = ", ".join(model.name for model in finding.models_at_risk) or "-"
            lines.append(f"| {finding.title} | {finding.severity.value} | {models} |")
        hidden = len(report.writes) - _SUMMARY_FINDING_LIMIT
        if hidden > 0:
            lines.append(f"| ...and {hidden} more | | |")
        lines.append("")

    if report.trust:
        # What is wrong before what it scored (F7). The band is a judgement about
        # the model; the integer is a weighted sum whose units nobody defined, so
        # it goes last and the reasons that a reader can act on go first.
        lines.append("| Model | Band | What cost it | Score |")
        lines.append("|---|---|---|---|")
        for trust in report.trust:
            reasons = (
                ", ".join(
                    f"{deduction.name} (-{deduction.points:g})"
                    for deduction in trust.score.deductions
                )
                or "nothing"
            )
            lines.append(
                f"| {trust.model_name} | {trust.score.band.value} "
                f"| {reasons} | {trust.score.value}/100 |"
            )
        lines.append("")
        lines.append(
            f"<sub>Scored under scoring version {SCORING_VERSION}. {SCORE_PROVENANCE}</sub>"
        )
        lines.append("")

    # Printed whatever the outcome. A check that never ran is the thing a reader
    # is most likely to mistake for a check that passed, and a green summary is
    # exactly where that mistake is most expensive.
    if report.not_evaluated:
        lines.append("**Not evaluated**")
        lines.append("")
        for gap in report.not_evaluated:
            lines.append(f"- {gap.describe()}")
        lines.append("")

    lines.append(f"<sub>run id: {report.run_id}</sub>")
    return "\n".join(lines) + "\n"


def write_job_summary(markdown: str) -> Path | None:
    """Append a rendering to the CI job summary, when there is one to append to.

    Returns:
        The file written, or None when ``GITHUB_STEP_SUMMARY`` is unset, which is
        every run outside GitHub Actions. Absence is the normal case and never an
        error: this is an extra place to put the answer, not the answer itself.
    """
    target = optional_value(ENV_STEP_SUMMARY)
    if target is None:
        return None

    path = Path(target)
    # Append, never truncate: several steps in one job share this file, and a
    # gate that overwrote it would delete another step's output.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(markdown)
    return path
