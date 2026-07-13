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
The document id is derived from the model URN, so a rerun updates one document in
place rather than piling up a report per scan. The body changes between runs
(the lag moves), which is an update, not a duplicate.

The prose in the body may come from an LLM. It is only ever prose: every number
in the report is passed in from the deterministic finding, and the LLM's text is
rendered as a quoted narrative section, never as a fact table.
"""

from __future__ import annotations

from dataclasses import dataclass

from datahub.metadata.urns import MlModelUrn
from datahub.sdk.document import Document

from modelguard.client import DataHubConnection
from modelguard.models import Finding, ModelAtRisk

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


def _document_id(model_urn: str) -> str:
    """Derive a stable document id from the model it reports on.

    The model's name, not its full URN: URNs contain characters that make an
    unreadable document id, and one report per model is exactly the cardinality
    we want.
    """
    return f"modelguard-impact-{MlModelUrn.from_string(model_urn).name}"


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


def render_impact_report(finding: Finding, narrative: str, run_id: str) -> str:
    """Render the Model Impact Report as markdown.

    Args:
        finding: The deterministic finding. Every number below comes from here.
        narrative: Prose explaining the finding. May be LLM-written or templated;
            it is quoted as narrative and never used as a source of fact.
        run_id: The scan that produced the report.

    Returns:
        The markdown body.
    """
    radius = finding.blast_radius
    signal = radius.signal

    models = "\n".join(_model_section(model) for model in radius.models) or (
        "No model consumes this table within the configured hop cap.\n"
    )

    return f"""# Model Impact Report: {radius.failing_table_name}

**Severity: {finding.severity}** | Models at risk: {len(radius.models)} \
(live: {len(radius.live_models)}) | Run: `{run_id}`

## What happened

`{radius.failing_table_name}` last changed {signal.lag_hours:.1f} hours ago, \
against a freshness SLA of {signal.sla_hours:.1f} hours. Freshness was measured \
from the dataset's `operation` aspect, which is DataHub's own record of when the \
table last changed.

## Assessment

{narrative}

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
    title = f"ModelGuard impact report: {finding.blast_radius.failing_table_name}"

    document = Document.create_document(
        id=_document_id(model_urn),
        title=title,
        text=markdown,
        subtype=REPORT_SUBTYPE,
        related_assets=[model_urn],
        custom_properties={RUN_ID_PROPERTY: run_id},
    )
    conn.client.entities.upsert(document)

    return DocumentWrite(urn=str(document.urn), model_urn=model_urn, markdown=markdown)
