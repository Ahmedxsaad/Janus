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
from dataclasses import dataclass
from functools import singledispatch

from datahub.metadata.urns import MlModelUrn
from datahub.sdk.document import Document

from modelguard.client import DataHubConnection
from modelguard.models import (
    Finding,
    FindingType,
    FreshnessFinding,
    LeakageFinding,
    ModelAtRisk,
    SchemaDriftFinding,
)

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

## Caveats

ModelGuard compares the training-time schema snapshot against the current schema, \
both from metadata DataHub holds. It did not query the warehouse.
"""


def render_impact_report(finding: Finding, narrative: str, run_id: str) -> str:
    """Render the Model Impact Report as markdown.

    The skeleton is shared; the sections that depend on what actually went wrong
    are dispatched per finding type. The narrative is substituted last, so prose
    an LLM wrote can never collide with a brace in the template.

    Args:
        finding: The deterministic finding. Every number below comes from here.
        narrative: Prose explaining the finding. May be LLM-written or templated;
            it is quoted as narrative and never used as a source of fact.
        run_id: The scan that produced the report.

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
    return header + _report_body(finding).replace("{narrative}", narrative)


def publish_impact_report(
    conn: DataHubConnection,
    *,
    model_urn: str,
    finding: Finding,
    narrative: str,
    run_id: str,
) -> DocumentWrite:
    """Write the impact report as a knowledge document linked to the model.

    Args:
        conn: A connection with write credentials.
        model_urn: The model the report concerns. Becomes a related asset, which
            is what makes the report reachable from the model's page.
        finding: The deterministic finding the report describes.
        narrative: Prose for the assessment section.
        run_id: Stamped on the document as provenance.

    Returns:
        The document URN and the markdown that was published.
    """
    markdown = render_impact_report(finding, narrative, run_id)
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
