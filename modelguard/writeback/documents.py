"""Publish the Model Impact Report as a knowledge document on the model.

The incident says a table broke. The report says what that means for the model:
which features it reaches, whether the endpoint is live, and what to do. It is
the artifact a human actually reads, and attaching it to the model is what turns
ModelGuard from a linter into something that leaves institutional memory behind.

Document entities are OSS
-------------------------
The plan assumed the report could only be written through the MCP server's
``save_document`` write tool. The installed SDK ships a first-class
:class:`~datahub.sdk.document.Document` entity, and a local OSS Quickstart (GMS
1.5.0.6) accepts it, so the report is a real, searchable graph entity linked to
the model by ``related_assets``. Verified before this module was written; see
docs/decision-log.md.

Idempotency
-----------
The document id is derived from the model URN, the finding's type, and the
finding's own resource URN, so a rerun of the *same* finding updates one
document in place rather than piling up a report per scan. The other two halves
matter: a model can be named in more than one finding in a single scan
(downstream of a stale table and independently leaking, or leaking through two
separate features), and two detectors can name the *same* resource (a table
that is both stale and drifted from the model's training schema). Keying on all
three gives two distinct findings on one model two distinct, individually
convergent reports instead of the second silently overwriting the first.

The prose in the body may come from an LLM. It is only ever prose: every number
in the report is passed in from the deterministic finding, and the LLM's text is
rendered as a quoted narrative section, never as a fact table.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import singledispatch

from datahub.metadata.urns import MlModelUrn
from datahub.sdk.document import Document

from modelguard.client import DataHubConnection
from modelguard.config import SCORE_PROVENANCE, SCORING_VERSION
from modelguard.models import (
    DeprecatedInputFinding,
    Finding,
    FindingType,
    FreshnessFinding,
    LeakageFinding,
    ModelAtRisk,
    ProxyCandidateFinding,
    SchemaDriftFinding,
    SensitiveSourceFinding,
    TableLevelRiskFinding,
    TrustScore,
)
from modelguard.writeback.trust_history import TrustEntry

#: Renders under the document's title in the UI, and groups ModelGuard's reports.
REPORT_SUBTYPE = "Model Impact Report"

#: Provenance stamped on the document so a reader can trace it to a scan.
RUN_ID_PROPERTY = "modelguard.run_id"


@dataclass(frozen=True)
class DocumentWrite:
    """The outcome of publishing one impact report."""

    urn: str
    model_urn: str
    markdown: str


def _document_id(model_urn: str, finding_type: FindingType, resource_urn: str) -> str:
    """Derive a stable document id from the model and the finding it reports on.

    The model's name, not its full URN: URNs contain characters that make an
    unreadable document id. The finding type and the resource URN are folded in
    as a short hash so that two different findings on the same model (a leaking
    column and a stale upstream table, or two separate leaking columns) land on
    two different documents rather than colliding on one: without this, the
    second ``publish_impact_report`` call in a scan would silently overwrite the
    first finding's report. Hashed rather than embedded verbatim because a
    schemaField URN contains parentheses and colons that do not belong in an id.

    The finding type is part of the hash because the resource URN alone does not
    separate the detectors: a table that is both stale and drifted from a
    model's training schema is the ``resource_urn`` of a freshness finding and
    of a schema-drift finding at once, and keying on the resource alone let the
    second report overwrite the first. It mirrors the incident dedup key
    ``(resource_urn, incident_type, title)``, which separates the same case.
    """
    model_name = MlModelUrn.from_string(model_urn).name
    key = f"{finding_type.value}:{resource_urn}"
    resource_hash = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"modelguard-impact-{model_name}-{resource_hash}"


def _model_section(model: ModelAtRisk) -> str:
    """Render one at-risk model as a markdown block."""
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to fix this."
    features = "\n".join(f"  - `{urn}`" for urn in model.features_at_risk) or "  - (none)"
    return (
        f"### {model.name}\n\n"
        f"- URN: `{model.urn}`\n"
        f"- Severity: **{model.severity}**\n"
        f"- Distance from the failing table: {model.hops} lineage hops\n"
        f"- Serving status: {serving}\n"
        f"- Ownership: {owner}\n"
        f"- Features fed by the failing table:\n{features}\n"
    )


@singledispatch
def report_subject(finding: Finding) -> str:
    """Return the thing the report is about, used in its title and heading."""
    raise NotImplementedError(f"no report subject for {type(finding).__name__}")


@report_subject.register
def _freshness_subject(finding: FreshnessFinding) -> str:
    return finding.blast_radius.failing_table_name


@report_subject.register
def _leakage_subject(finding: LeakageFinding) -> str:
    return finding.model.name


@report_subject.register
def _drift_subject(finding: SchemaDriftFinding) -> str:
    return finding.model.name


@report_subject.register
def _sensitive_subject(finding: SensitiveSourceFinding) -> str:
    return finding.model.name


@report_subject.register
def _deprecated_subject(finding: DeprecatedInputFinding) -> str:
    return finding.model.name


@report_subject.register
def _proxy_subject(finding: ProxyCandidateFinding) -> str:
    return finding.model.name


@report_subject.register
def _table_level_subject(finding: TableLevelRiskFinding) -> str:
    return finding.model.name


@singledispatch
def _report_body(finding: Finding) -> str:
    """Return the sections that only this kind of finding can fill in."""
    raise NotImplementedError(f"no report body for {type(finding).__name__}")


@_report_body.register
def _freshness_body(finding: FreshnessFinding) -> str:
    radius = finding.blast_radius
    signal = radius.signal
    models = "\n".join(_model_section(model) for model in radius.models) or (
        "No model consumes this table within the configured hop cap.\n"
    )

    return f"""## What happened

`{radius.failing_table_name}` last changed {signal.lag_hours:.1f} hours ago, \
against a freshness SLA of {signal.sla_hours:.1f} hours. Freshness was measured \
from the dataset's `operation` aspect, which is DataHub's own record of when the \
table last changed.

## Assessment

{{narrative}}

## Blast radius

Traversed downstream from the failing table across column-level warehouse lineage \
and into the ML graph.

- Downstream datasets: {len(radius.downstream_datasets)}
- Downstream features: {len(radius.downstream_features)}
- Models reached: {len(radius.models)}

{models}
## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the failing table.
2. Tagged every at-risk model so it surfaces in search.
3. Recorded the risk flags as structured properties on each model.
4. Left a guarding freshness assertion on the failing table, with the result of \
this evaluation attached, so the next stale load is caught rather than discovered.

{{counterfactual}}
## Caveats

Freshness here is derived from metadata DataHub already holds. ModelGuard did not \
query the warehouse. Scheduled evaluation of assertions and anomaly detection are \
DataHub Cloud features; the check logic above is ModelGuard's own.
"""


@_report_body.register
def _leakage_body(finding: LeakageFinding) -> str:
    leak = finding.leak
    model = finding.model
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to fix this."

    return f"""## What happened

The model **{model.name}** consumes the feature `{leak.feature_name}`. Column-level \
lineage shows it derives from `{leak.label_dataset_name}.{leak.label_column_name}`, \
the label the model is trained to predict.

The model is learning from the answer.

## The leak path

```
{leak.path_text.replace("`", "'")}
```

Every edge above is a declared column-level lineage edge in DataHub. ModelGuard \
read the lineage, not the data.

## Assessment

{{narrative}}

## Why this matters

Target leakage does not fail loudly. The model's offline metrics are inflated by \
construction, because the feature encodes the outcome, and a held-out split \
validates the contamination rather than catching it. In production, \
`{leak.label_column_name}` is not known at the moment a prediction is made, so the \
accuracy reported at review time was never real.

Kaufman, Rosset and Perlich (KDD 2011) formalize this and prescribe exactly this \
remedy: inspect how each feature was constructed rather than trusting a held-out \
score.

## Model at risk

### {model.name}

- URN: `{model.urn}`
- Severity: **{finding.severity}**
- Serving status: {serving}
- Ownership: {owner}
- Leaking feature: `{leak.feature_urn}`

## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the leaking column, \
`{leak.source_column_name}`.
2. Marked the feature with the leakage-risk glossary term.
3. Tagged the model and recorded the risk flag as a structured property on it.

## What to do

Drop `{leak.feature_name}`, or rebuild it from data observable *before* the \
outcome is known, then retrain and revalidate. Until then, treat this model's \
reported performance as unverified.

{{counterfactual}}
## Caveats

ModelGuard proves the derivation from lineage, not from the data. If the lineage \
is wrong, this finding is wrong: correct the lineage and rescan.
"""


@_report_body.register
def _drift_body(finding: SchemaDriftFinding) -> str:
    model = finding.model
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to fix this."
    changes = "\n".join(f"- {change.describe()}" for change in finding.changes)

    return f"""## What happened

The model **{model.name}** was trained on `{finding.dataset_name}`. Its schema has \
since drifted from the one captured when the model was trained:

{changes}

The training-time schema was snapshotted on the training run \
(`{finding.training_run_urn}`). ModelGuard compared it against the dataset's \
current `schemaMetadata`; it read the schemas, not the data.

## Assessment

{{narrative}}

## Why this matters

Training-serving skew does not fail loudly. A retyped column is parsed differently \
at serving time than at training; a removed column leaves the serving code reading \
something that is gone; an added column is noise the model never learned to weigh. \
The model keeps scoring, on inputs that no longer mean what they meant at training.

Breck et al. (MLSys 2019) prescribe exactly this guard: a schema fixed at training \
time, against which serving data is continuously validated.

## Model at risk

### {model.name}

- URN: `{model.urn}`
- Severity: **{finding.severity}**
- Serving status: {serving}
- Ownership: {owner}
- Drifted input: `{finding.dataset_urn}`

## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the drifted input dataset.
2. Tagged the model and recorded the risk flag as a structured property on it.

## What to do

Reconcile `{finding.dataset_name}`'s schema with the model's training schema, or \
retrain the model against the current schema, then revalidate. Until then, treat \
this model's predictions as scored on inputs it was not trained for.

{{counterfactual}}
## Caveats

ModelGuard compares the training-time schema snapshot against the current schema, \
both from metadata DataHub holds. It did not query the warehouse.
"""


@_report_body.register
def _sensitive_body(finding: SensitiveSourceFinding) -> str:
    exposure = finding.exposure
    model = finding.model
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to fix this."

    return f"""## What happened

The model **{model.name}** consumes the feature `{exposure.feature_name}`. Column-level \
lineage shows it derives from `{exposure.sensitive_dataset_name}.\
{exposure.sensitive_column_name}`, a column this organization classified as \
`{exposure.marker_name}`.

Nothing is broken. The model works, and what is wrong is what it was allowed to see.

## The derivation path

```
{exposure.path_text.replace("`", "'")}
```

Every edge above is a declared column-level lineage edge in DataHub. ModelGuard \
read the lineage and the classification, not the data.

## Assessment

{{narrative}}

## Why this matters

A classification is a commitment somebody made about how a column may be handled. \
A model trained on data derived from it has absorbed that column's information into \
its weights, several joins away from anyone who would recognise it, and no test \
suite fails because of it. This is a standing exposure rather than an outage, which \
is exactly why it survives so long unnoticed.

## Model at risk

### {model.name}

- URN: `{model.urn}`
- Severity: **{finding.severity}**
- Serving status: {serving}
- Ownership: {owner}
- Exposed feature: `{exposure.feature_urn}`
- Classification: `{exposure.marker_urn}`

## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the feature's own source column, \
`{exposure.source_column_name}`.
2. Tagged the model and recorded the risk flag as a structured property on it.

## What to do

Confirm with whoever owns the classification whether this model may learn from \
`{exposure.sensitive_column_name}`. If it may not, rebuild `{exposure.feature_name}` \
from a column that carries no such constraint, retrain, and revalidate.

{{counterfactual}}
## Caveats

ModelGuard reports the derivation and the classification as DataHub holds them. It \
does not interpret what the classification permits: that is a policy question, and \
this report is the input to it rather than the answer.
"""


@_report_body.register
def _deprecated_body(finding: DeprecatedInputFinding) -> str:
    model = finding.model
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to fix this."
    note = f"\n\nThe owners left this note:\n\n> {finding.note}" if finding.note else ""
    deadline = (
        f"\n- Decommission time recorded by its owners: `{finding.decommission_time_ms}` (epoch ms)"
        if finding.decommission_time_ms is not None
        else ""
    )

    return f"""## What happened

The model **{model.name}** trains on `{finding.dataset_name}`, and that table's own \
owners have marked it deprecated in DataHub.{note}

Nothing has failed. This is the warning that arrives while there is still time to \
act on it.

## Assessment

{{narrative}}

## Why this matters

A deprecation is a decision somebody already made and communicated. The team that \
made it has no way of knowing a model depends on the table, and the team that owns \
the model has no way of hearing about it, so the usual outcome is that the table \
disappears and a training run fails weeks later with no obvious cause.

## Model at risk

### {model.name}

- URN: `{model.urn}`
- Severity: **{finding.severity}**
- Serving status: {serving}
- Ownership: {owner}
- Deprecated input: `{finding.dataset_urn}`{deadline}

## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the deprecated dataset, where the \
decision was made.
2. Tagged the model and recorded the risk flag as a structured property on it.

## What to do

Talk to the table's owners about their timeline, then migrate this model onto \
whatever replaces `{finding.dataset_name}` and retrain. If the deprecation was a \
mistake, withdrawing it in DataHub closes this finding on the next scan.

{{counterfactual}}
## Caveats

ModelGuard reads the `deprecation` aspect and the model's recorded inputs. It has no \
opinion on whether the deprecation is justified, and it cannot tell whether a \
replacement table already exists.
"""


@_report_body.register
def _proxy_body(finding: ProxyCandidateFinding) -> str:
    candidate = finding.candidate
    model = finding.model
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to look at this."

    return f"""## This is a question, not a finding

The model **{model.name}** consumes the feature `{candidate.feature_name}`, computed \
from `{candidate.source_column_name}`. That column and \
`{candidate.protected_dataset_name}.{candidate.protected_column_name}`, which this \
organization classified as `{candidate.marker_name}`, are **both computed from** \
`{candidate.ancestor_dataset_name}.{candidate.ancestor_name}`.

Neither derives from the other. That is the whole of what has been established, and \
it is not a determination that `{candidate.feature_name}` proxies for \
`{candidate.protected_column_name}`, nor that this model discriminates. Whether the \
feature actually carries information about the attribute depends on the data, which \
ModelGuard has never read.

## The shared ancestry

```
{candidate.shared_text.replace("`", "'")}
```

`{candidate.ancestor_name}` is {candidate.feature_hops} hop(s) from the feature's \
source column and {candidate.protected_hops} from the classified column. Every edge \
above is a declared column-level lineage edge in DataHub.

## Assessment

{{narrative}}

## Why this is worth someone's time

A proxy variable is how unintentional discrimination usually arises: nobody puts a \
protected attribute into a model, they put in something computed from the same source, \
and it carries the attribute with it. Barocas and Selbst (2016) identify this as the \
dominant mechanism. Finding the candidates needs lineage rather than data, so this can \
be asked before the model is trained, which is the only time the answer is cheap to act \
on.

## Model under review

### {model.name}

- URN: `{model.urn}`
- Severity: **{finding.severity}** (capped: a prompt to look, not a defect)
- Serving status: {serving}
- Ownership: {owner}
- Feature under review: `{candidate.feature_urn}`
- Classification on the other column: `{candidate.marker_urn}`

## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the feature's own source column, \
`{candidate.source_column_name}`.
2. Nothing else. This finding does not move the model's trust score, because a \
question nobody has answered should not change how much a model is trusted.

## What to do

Have whoever owns `{candidate.feature_name}` check it against the data: does it carry \
information about `{candidate.protected_column_name}`? If it does not, record that and \
this stops being a question. If it does, the decision about what to do next is theirs \
and their compliance function's, not this tool's.
"""


@_report_body.register
def _table_level_body(finding: TableLevelRiskFinding) -> str:
    model = finding.model
    serving = (
        f"**Live**, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    owner = "Owned." if model.has_owner else "**Unowned**: nobody is on the hook to fix this."
    measured = "\n".join(
        f"- {key.replace('_', ' ')}: `{value}`"
        for key, value in sorted(finding.measurement.items())
    )

    return f"""## What happened

`{finding.dataset_name}`, one of the tables **{model.name}** is recorded as training \
on, is **{finding.risk}**.

{measured}

## How confident this is

{finding.mode_note}

## Assessment

{{narrative}}

## Why this matters

A model whose columns nobody has declared is a model no column-level check can \
reach, so the alternative to this weaker answer is silence. It is published as the \
weaker answer on purpose: read as a column-level finding it would be a false alarm \
about a feature nobody has shown to exist.

## Model at risk

### {model.name}

- URN: `{model.urn}`
- Severity: **{finding.severity}** (capped below the column-level detectors, which \
can prove what this one can only suggest)
- Serving status: {serving}
- Ownership: {owner}
- Training input: `{finding.dataset_urn}`

## What ModelGuard did

1. Raised a `{finding.incident_type}` incident on the training table, which is the \
only asset this mode can name precisely.
2. Tagged the model and recorded the risk flag as a structured property on it.

{{counterfactual}}
## Caveats

This finding was produced without column-level lineage. It cannot say which \
feature carries the problem into the model, or whether any of them does. Declaring \
the link (`modelguard link`) replaces it with a finding that can.
"""


def render_counterfactual(finding: Finding) -> str:
    """Render the finding's counterfactual as a markdown section.

    Sits inside the body rather than being appended after it, because it belongs
    beside the proof: the section above says what the graph shows, and this one
    says what the graph would have to show instead. Rendered as a fenced block so
    the remedies read as a list of alternatives rather than as steps to perform
    in order, which is the one way this could be misread (T-03).
    """
    return "\n".join(
        [
            "## How to clear this",
            "",
            "Each of these is sufficient on its own. They are alternatives, not steps.",
            "",
            "```",
            *finding.counterfactual.lines(),
            "```",
            "",
        ]
    )


def render_trust_waterfall(score: TrustScore) -> str:
    """Render a model's score as its deductions, or ``""`` when there are none.

    The contrastive rendering F7 asks for: what went wrong, what each thing cost,
    and only then the total. A reader who meets the integer first has to go
    looking for what to do about it, and the integer is the least defensible part
    of the object (see :class:`~modelguard.models.TrustScore`). Empty for a model
    with no deductions, because a waterfall with no steps is just the number
    again with extra ceremony.
    """
    if not score.deductions:
        return ""

    return "\n".join(
        [
            "## Trust score",
            "",
            "What this model lost trust for, worst first.",
            "",
            "```",
            *score.waterfall(),
            "```",
            "",
            f"Scored under scoring version {SCORING_VERSION}. {SCORE_PROVENANCE}",
            "",
        ]
    )


def render_trust_trend(history: Sequence[TrustEntry]) -> str:
    """Render a model's recent trust scores as a markdown section, or ``""``.

    Empty for a model with fewer than two entries, deliberately: one point is not
    a trend, and a table of one row invites a reader to draw a line through it.
    The section is what turns "82/100" into "82, down from 95 on Tuesday, and
    here is the deduction that appeared in between".
    """
    if len(history) < 2:
        return ""

    lines = [
        "## Trust over time",
        "",
        "Every scan that scored this model, most recent last. The score alone is not",
        "the signal; the direction is, and the deductions column says what moved it.",
        "",
        "| When (UTC) | Score | Band | Deductions | Scoring |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {entry.recorded_at} | {entry.score}/100 | {entry.band} "
        f"| {', '.join(entry.deductions) or 'none'} "
        f"| {('v' + entry.scoring_version) if entry.scoring_version else 'unknown'} |"
        for entry in history
    ]

    change = history[-1].score - history[0].score
    direction = "unchanged" if change == 0 else ("up" if change > 0 else "down")
    lines += [
        "",
        f"Across these {len(history)} scans the score is {direction}"
        + (f" by {abs(change)} points." if change else "."),
        "",
    ]

    # A version change is a discontinuity in the function, not a change in the
    # model. Saying so is the whole reason the version is recorded: without this
    # line a release that added a detector reads exactly like a regression (F7).
    versions = {entry.scoring_version for entry in history}
    if len(versions) > 1:
        lines += [
            "The scoring function changed inside this window "
            f"({', '.join('v' + v if v else 'unknown' for v in sorted(versions))}). "
            "Scores either side of that change are not comparable: the step is a "
            "release, not a regression.",
            "",
        ]
    return "\n".join(lines)


def render_impact_report(
    finding: Finding,
    narrative: str,
    run_id: str,
    history: Sequence[TrustEntry] = (),
    score: TrustScore | None = None,
) -> str:
    """Render the Model Impact Report as markdown.

    The skeleton is shared; the sections that depend on what actually went wrong
    are dispatched per finding type. The narrative is substituted last, so prose
    an LLM wrote can never collide with a brace in the template, and the
    counterfactual, which is deterministic, is substituted just before it.

    Args:
        finding: The deterministic finding. Every number below comes from here.
        narrative: Prose explaining the finding. May be LLM-written or templated;
            it is quoted as narrative and never used as a source of fact.
        run_id: The scan that produced the report.
        history: This model's recorded trust scores, oldest first. Omitted, or
            shorter than two entries, and the trend section is left out entirely
            rather than rendered empty.
        score: This run's score for the model the report is about, for the
            waterfall. Omitted and the section is left out: a report about a
            finding is still a complete report without it.

    Returns:
        The markdown body.
    """
    models = finding.models_at_risk
    live = sum(1 for model in models if model.is_live)

    header = (
        f"# Model Impact Report: {report_subject(finding)}\n\n"
        f"**Severity: {finding.severity}** | Models at risk: {len(models)} "
        f"(live: {live}) | Run: `{run_id}`\n\n"
    )
    # The counterfactual first and the narrative last: both are substituted into
    # the same template, and only one of them may have been written by an LLM.
    # Doing the deterministic one first means no brace an LLM emitted can ever
    # reach a substitution, which is the property the narrative-last ordering
    # already had and must keep.
    body = (
        _report_body(finding)
        .replace("{counterfactual}", render_counterfactual(finding))
        .replace("{narrative}", narrative)
    )
    sections = [render_trust_waterfall(score) if score else "", render_trust_trend(history)]
    return header + body + "".join(f"\n\n{section}" for section in sections if section)


def publish_impact_report(
    conn: DataHubConnection,
    *,
    model_urn: str,
    finding: Finding,
    narrative: str,
    run_id: str,
    history: Sequence[TrustEntry] = (),
    score: TrustScore | None = None,
) -> DocumentWrite:
    """Write the impact report as a knowledge document linked to the model.

    Args:
        conn: A connection with write credentials.
        model_urn: The model the report concerns. Becomes a related asset, which
            is what makes the report reachable from the model's page.
        finding: The deterministic finding the report describes.
        narrative: Prose for the assessment section.
        run_id: Stamped on the document as provenance.
        history: The model's recorded trust scores, oldest first, for the trend
            section. Empty and the section is omitted.
        score: This run's score for this model, for the waterfall section.

    Returns:
        The document URN and the markdown that was published.
    """
    markdown = render_impact_report(finding, narrative, run_id, history, score)
    title = f"ModelGuard impact report: {report_subject(finding)}"

    document = Document.create_document(
        id=_document_id(model_urn, finding.finding_type, finding.resource_urn),
        title=title,
        text=markdown,
        subtype=REPORT_SUBTYPE,
        related_assets=[model_urn],
        custom_properties={RUN_ID_PROPERTY: run_id},
    )
    conn.client.entities.upsert(document)

    return DocumentWrite(urn=str(document.urn), model_urn=model_urn, markdown=markdown)
