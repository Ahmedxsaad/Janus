"""Renderings of a scan for readers that are not a terminal.

``cli.py`` prints a scan for a human sitting at a console, with colour and
soft-wrapped URNs. Two other readers exist and neither is served by that:

* **A program.** A team that wants to route findings somewhere Janus does
  not know about (a ticket, a dashboard, a policy engine of their own) should
  not have to parse coloured console text. ``--format json`` gives them the
  report as data.
* **A pull request.** ``gate`` answers in an exit code, which in CI means a red
  cross and a click into a log. GitHub Actions already exposes a better place:
  anything appended to the file named by ``GITHUB_STEP_SUMMARY`` renders as
  markdown on the run's own page. No token, no API call, no permission block.

Both renderings are pure functions of a :class:`~janus.agent.pipeline.ScanReport`
(and, for the gate, a :class:`~janus.gate.GateVerdict`), for the same reason
:mod:`janus.gate` is pure: they can be exercised offline against a
hand-built report, and they hold no judgement of their own. Nothing here decides
anything; it only restates what a detector already measured.

The JSON shape is a public interface
------------------------------------
Somebody will write a script against it, so the keys are stable and every value
is a plain JSON type. Numbers a reader might act on (severities, trust scores,
hop counts) come from a finding's own evidence mapping, never from prose: the
narrator's assessment is carried under its own key, alongside the source that
produced it, so a consumer can tell a measured fact from a drafted sentence.

A third reader: a governance function
-------------------------------------
:func:`crosswalk_markdown` renders something that is not a scan at all: the map
from each detector to the NIST AI RMF subcategory it produces evidence for. It
lives here because it is the same kind of thing, a rendering with no judgement in
it, and because it is derived from the detector registry rather than typed by
hand, so a detector cannot be added without appearing in it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from janus.agent.pipeline import FindingWrites, ScanReport
from janus.config import SCORE_PROVENANCE, SCORING_VERSION
from janus.env import optional_value
from janus.gate import GateVerdict
from janus.models import FindingType, ModelRef


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
#: then rendered on the run's summary page. Read through :mod:`janus.env`
#: like every other environment value (docs/02-architecture.md, env.py boundary).
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
        # An object and not a list of sentences: a consumer routing findings into
        # a ticket wants the remedy's kind and its targets, and the sentence is
        # the part it is least likely to want. `paths` is here because a consumer
        # that shows only the first remedy would otherwise be showing half a fix
        # with nothing to warn it.
        "counterfactual": {
            "paths": finding.counterfactual.paths,
            "remedies": [
                {
                    "kind": remedy.kind.value,
                    "summary": remedy.summary,
                    "targets": list(remedy.targets),
                }
                for remedy in finding.counterfactual.remedies
            ],
        },
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
                # the terminal. A consumer that wants a lookup can build one;
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
            return "### Janus: no blocking policy set"
        return "### Janus: BLOCKED" if verdict.blocked else "### Janus: passed"
    if report.clean and not report.not_evaluated:
        return "### Janus: no finding"
    if report.clean:
        return "### Janus: no finding, some checks did not run"
    return f"### Janus: {len(report.writes)} finding(s)"


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
        # What is wrong before what it scored. The band is a judgement about
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


# --------------------------------------------------------------------------
# The NIST AI RMF crosswalk
# --------------------------------------------------------------------------

#: The subcategories this crosswalk cites, quoted verbatim from the NIST AI RMF
#: 1.0 Playbook (https://airc.nist.gov/AI_RMF_Knowledge_Base/Playbook), retrieved
#: 2026-08-04. Held in one place and cited by id, so several detectors can point
#: at the same subcategory without the text being retyped, and so a reader can
#: check the quotation against the source in one pass.
AI_RMF_SUBCATEGORIES: dict[str, str] = {
    "MAP 2.3": (
        "Scientific integrity and TEVV considerations are identified and documented, "
        "including those related to experimental design, data collection and selection."
    ),
    "MAP 4.1": (
        "Approaches for mapping AI technology and legal risks of its components, "
        "including the use of third-party data or software, are in place, followed, "
        "and documented."
    ),
    "MAP 4.2": (
        "Internal risk controls for components of the AI system including third-party "
        "AI technologies are identified and documented."
    ),
    "MEASURE 2.3": (
        "AI system performance or assurance criteria are measured qualitatively or "
        "quantitatively and demonstrated for conditions similar to deployment setting(s)."
    ),
    "MEASURE 2.4": (
        "The functionality and behavior of the AI system and its components are "
        "monitored when in production."
    ),
    "MEASURE 2.5": (
        "The AI system to be deployed is demonstrated to be valid and reliable. "
        "Limitations of the generalizability beyond the conditions under which the "
        "technology was developed are documented."
    ),
    "MEASURE 2.10": "Privacy risk of the AI system is examined and documented.",
    "MEASURE 2.11": (
        "Fairness and bias, as identified in the MAP function, are evaluated and "
        "results are documented."
    ),
    "MANAGE 2.2": (
        "Mechanisms are in place and applied to sustain the value of deployed AI systems."
    ),
    "MANAGE 2.3": (
        "Procedures are followed to respond to and recover from a previously unknown "
        "risk when it is identified."
    ),
    "MANAGE 4.1": (
        "Post-deployment AI system monitoring plans are implemented, including "
        "mechanisms for capturing and evaluating input from users and other relevant "
        "AI actors, appeal and override, decommissioning, incident response, recovery, "
        "and change management."
    ),
}


@dataclass(frozen=True)
class CrosswalkRow:
    """One detector, and the AI RMF subcategories its output is evidence for."""

    detector: str
    """The check as a reader of the docs knows it, not the enum value."""
    evidence: str
    """What this detector actually puts in the graph. The claim is only ever that
    this artifact exists and is machine-checkable, never that the subcategory is
    thereby satisfied."""
    map_id: str
    measure_id: str
    manage_id: str

    @property
    def subcategory_ids(self) -> tuple[str, str, str]:
        """The three cited subcategories, in function order."""
        return (self.map_id, self.measure_id, self.manage_id)


#: Every detector, keyed by the finding type it raises. Keyed by the enum rather
#: than listed, so adding a detector without a row here is a failing test and not
#: a quiet gap in a compliance artifact.
CROSSWALK: dict[FindingType, CrosswalkRow] = {
    FindingType.UPSTREAM_FRESHNESS: CrosswalkRow(
        detector="Freshness and blast radius",
        evidence=(
            "A freshness incident on the failing table, a guarding assertion with its "
            "measured run, and the named live models downstream of it"
        ),
        map_id="MAP 4.1",
        measure_id="MEASURE 2.4",
        manage_id="MANAGE 4.1",
    ),
    FindingType.TARGET_LEAKAGE: CrosswalkRow(
        detector="Target leakage",
        evidence=(
            "A field incident on the offending column, quoting the derivation chain "
            "from the feature to the label column, hop by hop"
        ),
        map_id="MAP 2.3",
        measure_id="MEASURE 2.5",
        manage_id="MANAGE 2.3",
    ),
    FindingType.INPUT_SCHEMA_DRIFT: CrosswalkRow(
        detector="Input schema drift",
        evidence=(
            "A schema incident naming every column added, removed or retyped since the "
            "training-time snapshot recorded on the training run"
        ),
        map_id="MAP 2.3",
        measure_id="MEASURE 2.3",
        manage_id="MANAGE 4.1",
    ),
    FindingType.SENSITIVE_SOURCE: CrosswalkRow(
        detector="Sensitive source",
        evidence=(
            "A field incident naming the classification the organization applied and "
            "the path from the classified column to the feature that carries it"
        ),
        map_id="MAP 4.1",
        measure_id="MEASURE 2.10",
        manage_id="MANAGE 2.3",
    ),
    FindingType.PROXY_CANDIDATE: CrosswalkRow(
        detector="Proxy candidate (for human review)",
        evidence=(
            "A field incident naming the column a feature and a classified protected "
            "attribute both descend from, raised as a question for a human rather "
            "than as a determination that the feature proxies for the attribute"
        ),
        map_id="MAP 2.3",
        measure_id="MEASURE 2.11",
        manage_id="MANAGE 2.3",
    ),
    FindingType.DEPRECATED_INPUT: CrosswalkRow(
        detector="Deprecated input",
        evidence=(
            "An incident on the deprecated dataset, quoting its owners' own note and "
            "decommission date, against the models still training on it"
        ),
        map_id="MAP 4.2",
        measure_id="MEASURE 2.4",
        manage_id="MANAGE 2.2",
    ),
    FindingType.TABLE_LEVEL_RISK: CrosswalkRow(
        detector="Table-level risk (degraded mode, no column link)",
        evidence=(
            "An incident on the training table naming the table-level fact, the mode "
            "that produced it, and that mode's measured precision, so the record "
            "states how far it can be trusted"
        ),
        map_id="MAP 4.1",
        # MEASURE 2.5 for the same reason the column-level leakage row cites it,
        # read the other way round: that row documents a limitation of the model,
        # this one documents a limitation of the measurement.
        measure_id="MEASURE 2.5",
        manage_id="MANAGE 4.1",
    ),
}

#: Printed above the table, and it is the load-bearing part of the artifact.
#:
#: A generated table that read as a conformity claim would be worse than no table:
#: a crosswalk says which subcategory an artifact is evidence *for*, and evidence
#: is not conformity. The distinction is the same one the sensitive-source
#: detector already makes between "not evaluated" and "clean".
CROSSWALK_DISCLAIMER = (
    "This is a mapping, not a conformity claim. Each row says which NIST AI RMF "
    "subcategory a detector's output is evidence for; whether that subcategory is "
    "satisfied is a judgement about your organization's whole process, which no "
    "tool reading a metadata graph can make. Subcategory text is quoted from the "
    "NIST AI RMF 1.0 Playbook."
)


def crosswalk_markdown() -> str:
    """Render the detector-to-AI-RMF crosswalk as markdown.

    Generated from :data:`CROSSWALK`, which is keyed by
    :class:`~janus.models.FindingType`, so the table cannot fall behind the
    detectors: a new finding type with no row fails a test rather than quietly
    leaving a hole in a document somebody files.

    Returns:
        The markdown document: the disclaimer, the table, and the verbatim text
        of every subcategory the table cites.
    """
    lines = [
        "# Janus and the NIST AI RMF",
        "",
        CROSSWALK_DISCLAIMER,
        "",
        "| Detector | Evidence it writes into the graph | MAP | MEASURE | MANAGE |",
        "|---|---|---|---|---|",
    ]
    # Ordered by the enum's declaration order, which is the order the detectors
    # were built and the order the docs introduce them in.
    rows = [(finding_type, CROSSWALK[finding_type]) for finding_type in FindingType]
    lines += [
        f"| {row.detector} | {row.evidence} | {row.map_id} | {row.measure_id} | {row.manage_id} |"
        for _, row in rows
    ]

    cited = sorted(
        {sub_id for _, row in rows for sub_id in row.subcategory_ids},
        key=lambda sub_id: list(AI_RMF_SUBCATEGORIES).index(sub_id),
    )
    lines += ["", "## The subcategories cited above", ""]
    lines += [f"- **{sub_id}**: {AI_RMF_SUBCATEGORIES[sub_id]}" for sub_id in cited]
    lines.append("")
    return "\n".join(lines)
