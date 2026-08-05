"""Two per-model artifacts assembled from facts the graph already holds.

``documents.py`` writes one report per *finding*: what went wrong, and what to
do. These two are per *model*, and neither reports a problem:

* **The model card** (T-12, 09 section 5.3). Mitchell et al. 2019 proposed
  model cards as documentation that travels with a model; ``trust_score.py``
  has cited that paper since Phase 2 without producing the artifact. This
  generates it from graph facts: intended use where somebody declared it,
  where the training data came from, the trust score with its waterfall, the
  findings open against it, and the checks that could not run.

* **The EU AI Act Article 10 evidence pack** (T-13, 09 section 5.2). Article 10
  requires documented training-data provenance, governance and examination for
  bias for high-risk systems; Article 12 requires record-keeping. ModelGuard
  already computes most of that evidence as a side effect of detection, so
  assembling it is a renderer rather than a new capability.

What both of these are not
--------------------------
Neither is a certification, and the evidence pack says so in its own first
paragraph rather than in a footnote. A generated document that implied
conformity would be the single most damaging thing this project could ship
(09 section 5.2's wording, and it is not an exaggeration: these land in a
catalog, under a compliance-sounding title, where somebody may cite them).

So both artifacts inherit ``coverage.py``'s discipline and push it further:
what could **not** be established is a section of its own, not a caveat at the
end, and it is rendered even when it is empty so its absence is never mistaken
for its non-existence. Anything the graph does not record is named as
unrecorded, never inferred and never quietly omitted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from datahub.metadata.schema_classes import MLModelPropertiesClass
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn
from datahub.sdk.document import Document

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.coverage import Unevaluated
from modelguard.detect.governance import model_input_datasets
from modelguard.detect.graph_reads import model_ref
from modelguard.detect.leakage import feature_source_column
from modelguard.detect.schema_drift import training_snapshot
from modelguard.models import Finding, ModelRef, TrustScore
from modelguard.writeback.documents import (
    RUN_ID_PROPERTY,
    render_trust_waterfall,
    urn_hash,
)

#: DataHub document subtypes. Distinct from the impact report's, so a reader
#: filtering the catalog can ask for one kind without the others.
MODEL_CARD_SUBTYPE = "Model Card"
EVIDENCE_PACK_SUBTYPE = "AI Act Evidence Pack"

#: Printed wherever a fact the artifact wanted is simply not in the graph.
#: One phrase, used everywhere, because a reader skimming for gaps should be
#: able to search for a single string and find all of them.
NOT_RECORDED = "not recorded in the catalog"


@dataclass(frozen=True)
class FeatureProvenance:
    """One feature and the column it was computed from, if the graph says."""

    feature_urn: str
    feature_name: str
    source_column_urn: str | None
    source_column_name: str | None
    source_dataset_name: str | None

    @property
    def traced(self) -> bool:
        """Whether column-level provenance exists for this feature."""
        return self.source_column_urn is not None


@dataclass(frozen=True)
class ModelFacts:
    """Everything both artifacts need, read once.

    Assembled by :func:`gather`, which is the only function here that touches
    the graph. The renderers are pure functions of this, so they are testable
    offline and two artifacts cannot disagree about the same model.
    """

    model: ModelRef
    description: str | None
    version: str | None
    training_metrics: dict[str, str] = field(default_factory=dict)
    hyper_parameters: dict[str, str] = field(default_factory=dict)
    training_runs: tuple[str, ...] = ()
    input_datasets: tuple[str, ...] = ()
    features: tuple[FeatureProvenance, ...] = ()
    training_schemas: dict[str, dict[str, str]] = field(default_factory=dict)
    """Training-time schema per input dataset URN, from the run's own snapshot."""
    findings: tuple[Finding, ...] = ()
    gaps: tuple[Unevaluated, ...] = ()
    score: TrustScore | None = None

    @property
    def untraced_features(self) -> tuple[FeatureProvenance, ...]:
        """Features with no column-level provenance. Named, never dropped."""
        return tuple(feature for feature in self.features if not feature.traced)


def gather(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
    *,
    findings: Sequence[Finding] = (),
    gaps: Sequence[Unevaluated] = (),
    score: TrustScore | None = None,
) -> ModelFacts:
    """Read every fact both artifacts describe. Read-only.

    Args:
        conn: An open connection.
        model_urn: The model to document.
        config: Supplies the training-schema property name.
        findings: This scan's findings for this model, already decided.
        gaps: What this scan could not check, from ``coverage.py``.
        score: This model's trust score, for the card's waterfall.

    Returns:
        The facts. Absent metadata arrives as None or an empty collection and is
        rendered as :data:`NOT_RECORDED`, never inferred.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    model = model_ref(conn, model_urn, properties=properties)

    features: list[FeatureProvenance] = []
    for feature_urn in (properties.mlFeatures or []) if properties else []:
        source = feature_source_column(conn, feature_urn)
        field_urn = SchemaFieldUrn.from_string(source) if source else None
        features.append(
            FeatureProvenance(
                feature_urn=feature_urn,
                feature_name=MlFeatureUrn.from_string(feature_urn).name,
                source_column_urn=source,
                source_column_name=field_urn.field_path if field_urn else None,
                source_dataset_name=(
                    DatasetUrn.from_string(field_urn.parent).name if field_urn else None
                ),
            )
        )

    runs = tuple(properties.trainingJobs or []) if properties else ()
    schemas: dict[str, dict[str, str]] = {}
    for run_urn in runs:
        snapshot = training_snapshot(conn, run_urn, config.training_schema_property)
        if snapshot:
            schemas.update(snapshot)

    return ModelFacts(
        model=model,
        description=(properties.description if properties else None) or None,
        version=(properties.version.versionTag if properties and properties.version else None),
        training_metrics=_metrics(properties),
        hyper_parameters=_hyper_parameters(properties),
        training_runs=runs,
        input_datasets=model_input_datasets(conn, model_urn),
        features=tuple(features),
        training_schemas=schemas,
        findings=tuple(findings),
        gaps=tuple(gaps),
        score=score,
    )


def _metrics(properties: MLModelPropertiesClass | None) -> dict[str, str]:
    """Training metrics as a plain mapping.

    ``trainingMetrics`` is a list of ``MLMetric`` objects rather than a mapping,
    which is worth flattening here: every renderer below wants name-to-value,
    and a metric with no name is not one a reader can do anything with.
    """
    if properties is None or not properties.trainingMetrics:
        return {}
    return {
        metric.name: str(metric.value)
        for metric in properties.trainingMetrics
        if metric.name and metric.value is not None
    }


def _hyper_parameters(properties: MLModelPropertiesClass | None) -> dict[str, str]:
    """Hyper-parameters as a plain mapping. Same shape as :func:`_metrics`."""
    if properties is None or not properties.hyperParams:
        return {}
    return {
        param.name: str(param.value)
        for param in properties.hyperParams
        if param.name and param.value is not None
    }


def _dataset_name(urn: str) -> str:
    """A dataset's readable name, falling back to the URN if it will not parse."""
    try:
        return DatasetUrn.from_string(urn).name
    except Exception:  # noqa: BLE001 - a malformed URN is still worth printing
        return urn


def _provenance_table(facts: ModelFacts) -> list[str]:
    """One row per feature: what it is, and the column it came from."""
    if not facts.features:
        return [f"No features are declared on this model ({NOT_RECORDED})."]

    lines = [
        "| Feature | Source column | Source dataset |",
        "|---|---|---|",
    ]
    for feature in facts.features:
        if feature.traced:
            lines.append(
                f"| `{feature.feature_name}` | `{feature.source_column_name}` | "
                f"`{feature.source_dataset_name}` |"
            )
        else:
            lines.append(f"| `{feature.feature_name}` | {NOT_RECORDED} | {NOT_RECORDED} |")
    return lines


def _findings_section(facts: ModelFacts) -> list[str]:
    """Open findings, worst first, or an explicit statement that there are none."""
    if not facts.findings:
        return [
            "No finding is open against this model in the scan that produced this "
            "document. That is a statement about the checks that ran, which the "
            "next section lists, and not a statement that the model is sound."
        ]
    lines = ["| Finding | Severity | Resource |", "|---|---|---|"]
    lines += [
        f"| {finding.title} | **{finding.severity}** | `{finding.resource_urn}` |"
        for finding in facts.findings
    ]
    return lines


def _gaps_section(facts: ModelFacts) -> list[str]:
    """What could not be checked. Rendered even when empty, deliberately.

    An omitted section reads as an absent problem. This one says either what was
    not established or that everything asked for was, and a reader never has to
    guess which of the two a missing heading meant.
    """
    if not facts.gaps:
        return [
            "Every check this scan asked for had the metadata it needed to run. "
            "That is not a claim that every check worth running was asked for."
        ]
    return [f"- **{gap.check}**: {gap.reason}. {gap.remedy}" for gap in facts.gaps]


def render_model_card(facts: ModelFacts) -> str:
    """Render the model card (T-12). Pure: every fact comes from ``facts``.

    Follows Mitchell et al. 2019's structure where the graph can fill it in, and
    says so where it cannot. Intended use is the section most often empty: it is
    a human's statement of purpose, and no catalog derives it.
    """
    model = facts.model
    serving = (
        f"Live, serving through {len(model.live_deployments)} deployment(s)."
        if model.is_live
        else "Not currently serving."
    )
    intended_use = facts.description or (
        f"**{NOT_RECORDED}.** Nothing in the catalog states what this model is for. "
        "That is the one section here a graph cannot derive: it is a statement of "
        "purpose somebody has to make. Set the model's description in DataHub."
    )

    lines = [
        f"# Model card: {model.name}",
        "",
        "Generated by ModelGuard from metadata in DataHub. Every fact below is read "
        "from the catalog; nothing is inferred, and anything the catalog does not "
        f"record is marked *{NOT_RECORDED}* rather than omitted or guessed.",
        "",
        "## Intended use",
        "",
        intended_use,
        "",
        "## Model details",
        "",
        f"- URN: `{model.urn}`",
        f"- Version: {facts.version or NOT_RECORDED}",
        f"- Serving status: {serving}",
        f"- Ownership: {'Owned.' if model.has_owner else f'**Unowned** ({NOT_RECORDED}).'}",
        "",
        "## Training data provenance",
        "",
        "Which columns this model was actually computed from, read from column-level "
        "lineage rather than from documentation somebody maintained by hand.",
        "",
        *_provenance_table(facts),
        "",
    ]

    if facts.input_datasets:
        lines += [
            "Input datasets recorded on the training run(s):",
            "",
            *[f"- `{_dataset_name(urn)}`" for urn in facts.input_datasets],
            "",
        ]

    if facts.training_metrics:
        lines += [
            "## Reported training metrics",
            "",
            "As recorded on the model by whatever trained it. ModelGuard did not "
            "measure these and cannot vouch for them; where a leakage finding is "
            "open below, they are the numbers that finding calls into question.",
            "",
            *[f"- {name}: {value}" for name, value in sorted(facts.training_metrics.items())],
            "",
        ]

    if facts.score is not None:
        # render_trust_waterfall emits its own "## Trust score" heading, so this
        # must not add a second one: two headings in a generated document read as
        # a rendering bug and cost the artifact credibility it needs.
        waterfall = render_trust_waterfall(facts.score)
        if waterfall:
            lines += [waterfall, ""]

    lines += [
        "## Known findings",
        "",
        *_findings_section(facts),
        "",
        "## What could not be checked",
        "",
        *_gaps_section(facts),
        "",
        "## Limits of this document",
        "",
        "- It is generated from metadata, so it is exactly as complete as the "
        "catalog is. A model with no declared features produces a card with no "
        "provenance, and that is a fact about the catalog, not about the model.",
        "- ModelGuard has never read this model's training data or its predictions. "
        "Nothing here is a statement about the data's contents, its quality, or "
        "the model's behaviour.",
        "- It is not an approval. A card with no open findings records that the "
        "checks which ran found nothing, which is a smaller claim than it looks.",
    ]
    return "\n".join(lines)


def render_evidence_pack(facts: ModelFacts) -> str:
    """Render the EU AI Act Article 10 evidence pack (T-13).

    The first paragraph is the load-bearing part and is deliberately the first
    thing a reader meets: this assembles measured facts, it does not certify
    conformity, and nobody should be able to skim it and come away thinking
    otherwise.
    """
    model = facts.model
    traced = [feature for feature in facts.features if feature.traced]

    lines = [
        f"# Article 10 evidence pack: {model.name}",
        "",
        "## This is not a compliance certification",
        "",
        "This document assembles facts measured from metadata in DataHub. **It is "
        "not a conformity assessment, not a certification, and not legal advice, "
        "and it must not be filed or cited as any of those.** Whether an AI system "
        "meets Article 10 is a judgement about an organization's whole data "
        "governance practice, including things no metadata catalog records: how the "
        "data was collected, what it represents, whose it is, and what was done to "
        "examine it for bias. What follows is evidence that may support such an "
        "assessment, produced automatically so that it is at least current and "
        "consistent. The assessment itself remains a human one.",
        "",
        "Regulation (EU) 2024/1689, **Article 10** (data and data governance) requires "
        "documented training-data provenance, governance practices, and examination "
        "for possible biases. **Article 12** (record-keeping) requires automatic "
        "recording of events over the system's lifetime. The sections below map to "
        "those obligations; the mapping is this project's reading of them, offered so "
        "a reader can check it rather than trust it.",
        "",
        "## What this pack could NOT establish",
        "",
        "Deliberately first, and not a footnote. A reader who stops here has the "
        "most important part, and a gap listed at the end of a long document is a "
        "gap nobody reads.",
        "",
    ]

    unestablished: list[str] = [
        "- **Freshness of each input at training time.** ModelGuard measures "
        "freshness *now*, and DataHub records no snapshot of it as of the training "
        "run, so this pack cannot state how current the data was when the model was "
        "fitted. Current freshness is a different claim and is not a substitute.",
        "- **Whether the data was examined for bias.** Article 10(2)(f) and (g) ask "
        "about examination for possible biases and measures to address them. Those "
        "are activities, and no catalog records whether they happened. A proxy "
        "candidate raised below is a prompt to examine, not evidence that examination "
        "occurred.",
        "- **Data collection, its purpose, and the assumptions behind it.** "
        "Article 10(2)(b) and (c). Statements a human makes, absent from the graph.",
        "- **Anything about the data's contents.** ModelGuard reads lineage and "
        "metadata. It has never read a row of this model's training data.",
    ]
    if facts.untraced_features:
        untraced = ", ".join(f"`{f.feature_name}`" for f in facts.untraced_features)
        unestablished.append(
            f"- **Column-level provenance for {len(facts.untraced_features)} feature(s)**: "
            f"{untraced}. No source column is recorded, so their derivation is unknown "
            "to this document."
        )
    if not facts.training_schemas:
        unestablished.append(
            "- **The input schema at training time.** No training run of this model "
            "carries a schema snapshot, so this pack cannot state what the data "
            "looked like when the model was fitted."
        )
    unestablished += [f"- **{gap.check}**: {gap.reason}." for gap in facts.gaps]
    lines += unestablished
    lines += [
        "",
        "## Training data provenance (Article 10(2)(b), 10(3))",
        "",
        f"Column-level provenance for {len(traced)} of {len(facts.features)} declared "
        "feature(s), read from lineage rather than from hand-maintained documentation:",
        "",
        *_provenance_table(facts),
        "",
    ]

    if facts.input_datasets:
        lines += [
            "Datasets recorded as inputs to the training run(s):",
            "",
            *[f"- `{_dataset_name(urn)}`" for urn in facts.input_datasets],
            "",
        ]

    if facts.training_schemas:
        lines += [
            "## Input schema at training time (Article 10(3))",
            "",
            "Captured on the training run when the model was fitted, so this is the "
            "shape of the data the model was actually built against rather than the "
            "shape it has now.",
            "",
        ]
        for dataset_urn, schema in sorted(facts.training_schemas.items()):
            lines += [
                f"**`{_dataset_name(dataset_urn)}`**",
                "",
                *[f"- `{name}`: {native}" for name, native in sorted(schema.items())],
                "",
            ]

    lines += [
        "## Governance findings (Article 10(2)(f), 10(5))",
        "",
        *_findings_section(facts),
        "",
        "## Record-keeping (Article 12)",
        "",
        f"- Training run(s) recorded: {len(facts.training_runs) or NOT_RECORDED}",
        *[f"  - `{run}`" for run in facts.training_runs],
        "- Every ModelGuard scan emits a `dataProcessInstance` carrying its own "
        "`run_id`, so each write in this catalog is traceable to the run that made "
        "it (T-04). That covers ModelGuard's own activity, and nothing else's.",
        "",
        "## Ownership",
        "",
        f"- {_ownership_sentence(model)}",
    ]
    return "\n".join(lines)


def _ownership_sentence(model: ModelRef) -> str:
    """State whether anybody is on the hook, and why the question is asked."""
    if model.has_owner:
        return "An owner is recorded on this model."
    return (
        f"**No owner is recorded** ({NOT_RECORDED}). Article 10 obligations fall on "
        "the provider; a model nobody owns in the catalog has nobody in it to ask."
    )


def publish_model_card(
    conn: DataHubConnection,
    facts: ModelFacts,
    *,
    run_id: str,
) -> str:
    """Write the model card as a document linked to the model. Returns its URN.

    Keyed on the model's full URN, not its bare name: MlModelUrn.name drops the
    platform and env, so two models named the same on different platforms (or
    in PROD vs STAGING) would otherwise collide on one document. A rerun of the
    same model still replaces its own card rather than adding a second one:
    unlike an impact report there is exactly one card per model, and it is
    meant to be current rather than historical.
    """
    return _publish(
        conn,
        facts,
        run_id=run_id,
        document_id=f"modelguard-model-card-{facts.model.name}-{urn_hash(facts.model.urn)}",
        title=f"Model card: {facts.model.name}",
        subtype=MODEL_CARD_SUBTYPE,
        markdown=render_model_card(facts),
    )


def publish_evidence_pack(
    conn: DataHubConnection,
    facts: ModelFacts,
    *,
    run_id: str,
) -> str:
    """Write the Article 10 evidence pack as a document. Returns its URN.

    Keyed on the model's full URN for the same reason as ``publish_model_card``:
    the bare name collides across platforms and environments, and silently
    overwriting another model's Article 10 pack is the most damaging thing this
    module could ship.
    """
    return _publish(
        conn,
        facts,
        run_id=run_id,
        document_id=f"modelguard-evidence-pack-{facts.model.name}-{urn_hash(facts.model.urn)}",
        title=f"AI Act Article 10 evidence pack: {facts.model.name}",
        subtype=EVIDENCE_PACK_SUBTYPE,
        markdown=render_evidence_pack(facts),
    )


def _publish(
    conn: DataHubConnection,
    facts: ModelFacts,
    *,
    run_id: str,
    document_id: str,
    title: str,
    subtype: str,
    markdown: str,
) -> str:
    """Upsert one per-model document, stamped with the run that produced it."""
    document = Document.create_document(
        id=document_id,
        title=title,
        text=markdown,
        subtype=subtype,
        related_assets=[facts.model.urn],
        custom_properties={RUN_ID_PROPERTY: run_id},
    )
    conn.client.entities.upsert(document)
    return str(document.urn)
