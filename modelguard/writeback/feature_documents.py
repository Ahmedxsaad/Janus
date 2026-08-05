"""A Data Card for one feature, assembled from measured graph facts (T-19).

``model_documents.py`` documents a model. This documents a *feature*, and it
answers the question every data scientist asks about one and nothing today
answers:

    Where does this feature actually come from?

Pushkarna, Zaldivar and Kjartansson, *Data Cards: Purposeful and Transparent
Dataset Documentation for Responsible AI* (FAccT 2022), argue that the
documentation which gets maintained is the documentation a system produces
rather than the documentation a person promises to write. Every field here is
read from lineage, classification and operation aspects DataHub already holds, so
the card is current by construction and empty exactly where the catalog is.

What one card carries
---------------------
* The **derivation chain** back to a source column, hop by hop, and every other
  chain the walk found. Two derivations of one feature is a fact about the
  warehouse worth seeing, and the leakage detector already had to learn to keep
  both of them (T-03).
* The **transitive tables** the chain crosses, each with its freshness *now*.
  Not at training time: DataHub records no snapshot of that, and this card says
  so rather than substituting a number that answers a different question.
* **Classification exposure**: whether the chain reaches a column somebody
  classified as restricted, or as a protected attribute. Unconfigured means the
  card reports the check as not evaluated, never as clean, which is
  ``coverage.py``'s discipline applied to a document.
* Its **drift**: whether the column's type today differs from the training-time
  snapshot the run recorded.
* Its **counterfactual**, when a finding names this feature: the changes that
  would clear it, each sufficient on its own (T-03).

What a card is not
------------------
Not an approval, and not a statement about the feature's values. ModelGuard has
never read a row. A card with no exposure and no finding records that the checks
which ran found nothing, and the section listing what could not run is rendered
whether or not it is empty, so its absence can never be mistaken for its
non-existence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from datahub.metadata.schema_classes import MLModelPropertiesClass, SchemaMetadataClass
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn
from datahub.sdk.document import Document

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import freshness_signal
from modelguard.detect.column_marks import ColumnMarkIndex, derivation_chains, marked_ancestor
from modelguard.detect.governance import protected_attribute_index, sensitive_index
from modelguard.detect.leakage import feature_source_column
from modelguard.detect.schema_drift import training_snapshot
from modelguard.models import Finding, FreshnessSignal
from modelguard.writeback.documents import RUN_ID_PROPERTY
from modelguard.writeback.model_documents import NOT_RECORDED

#: The document subtype, distinct from the model card's and the impact report's
#: so a reader can filter the catalog for one kind without the others.
FEATURE_CARD_SUBTYPE = "Feature Provenance Card"


@dataclass(frozen=True)
class Exposure:
    """One classified ancestor a feature's derivation reaches."""

    kind: str
    """``restricted`` or ``protected attribute``, as a reader knows it."""
    column_urn: str
    column_name: str
    dataset_name: str
    marker_urn: str
    chain: tuple[str, ...]
    """The column names walked to reach it, the feature's own first."""


@dataclass(frozen=True)
class TableInTheChain:
    """One table a derivation crosses, and how current it is right now."""

    dataset_urn: str
    dataset_name: str
    signal: FreshnessSignal | None
    """None when the table has never reported an ``operation`` aspect. Absence of
    evidence, not staleness: a table nobody instrumented is not a stale one."""


@dataclass(frozen=True)
class FeatureFacts:
    """Everything one card describes, read once.

    Assembled by :func:`gather_feature`, the only function here that touches the
    graph. :func:`render_feature_card` is a pure function of this, so the card is
    testable offline and cannot disagree with the model card about the same
    feature.
    """

    feature_urn: str
    feature_name: str
    model_urn: str
    model_name: str
    source_column_urn: str | None
    source_column_name: str | None
    source_dataset_name: str | None
    chains: tuple[tuple[str, ...], ...] = ()
    """Derivation chains as column names, shortest first. The first is the one a
    finding would quote as proof."""
    tables: tuple[TableInTheChain, ...] = ()
    exposures: tuple[Exposure, ...] = ()
    unevaluated: tuple[str, ...] = ()
    """Checks this card could not run, named. Rendered whether or not empty."""
    training_type: str | None = None
    current_type: str | None = None
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    """Findings from this scan that name this feature's source column."""

    @property
    def traced(self) -> bool:
        """Whether column-level provenance exists for this feature at all."""
        return self.source_column_urn is not None

    @property
    def drifted(self) -> bool:
        """Whether the column's type today differs from the training snapshot.

        False when either type is unknown. A card must not report drift it could
        not measure, and the unevaluated list says which half was missing.
        """
        return (
            self.training_type is not None
            and self.current_type is not None
            and self.training_type != self.current_type
        )


def _chain_names(chain: Sequence[object]) -> tuple[str, ...]:
    """Column names along one lineage chain, skipping steps that carry none."""
    return tuple(name for step in chain if (name := getattr(step, "column_name", None)) is not None)


def _exposures(
    conn: DataHubConnection,
    config: ScanConfig,
    source_column_urn: str,
) -> tuple[tuple[Exposure, ...], tuple[str, ...]]:
    """Classified ancestors the derivation reaches, and the checks that could not run.

    Both classifications have no default and no fallback (root rule 6a), so an
    unconfigured one is reported as not evaluated. Reporting it as clean would be
    the exact failure ``coverage.py`` exists to prevent, moved into a document
    somebody files.
    """
    kinds: list[tuple[str, ColumnMarkIndex, str]] = [
        (
            "restricted",
            sensitive_index(conn, config),
            "MODELGUARD_SENSITIVE_TERM_URNS or MODELGUARD_SENSITIVE_TAG_URNS",
        ),
        (
            "protected attribute",
            protected_attribute_index(conn, config),
            "MODELGUARD_PROTECTED_ATTRIBUTE_TERM_URNS or MODELGUARD_PROTECTED_ATTRIBUTE_TAG_URNS",
        ),
    ]

    found: list[Exposure] = []
    unevaluated: list[str] = []
    for kind, index, variables in kinds:
        if not index.configured:
            unevaluated.append(
                f"**{kind} exposure**: no {kind} classification is configured, so no "
                f"column in this catalog counts as one. Set {variables}."
            )
            continue
        walk = marked_ancestor(conn, source_column_urn, index, config)
        for column_urn, marker_urn, chain in walk.matches:
            field_urn = SchemaFieldUrn.from_string(column_urn)
            found.append(
                Exposure(
                    kind=kind,
                    column_urn=column_urn,
                    column_name=field_urn.field_path,
                    dataset_name=DatasetUrn.from_string(field_urn.parent).name,
                    marker_urn=marker_urn,
                    chain=chain,
                )
            )
        if walk.truncated or walk.hop_capped:
            unevaluated.append(
                f"**{kind} exposure, completely**: the upstream walk hit a cap "
                f"({'result cap' if walk.truncated else 'hop cap'}), so a classified "
                "ancestor beyond it would not appear above."
            )
    return tuple(found), tuple(unevaluated)


def _types(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
    source_column_urn: str,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """The column's training-time and current native types, plus what was missing.

    The training type comes from the snapshot ``link`` captured on the training
    run, keyed by field path. Absent means nobody captured one, which is the
    normal state of a model DataHub's own ingestion produced, and it is named
    rather than passed over.
    """
    field_urn = SchemaFieldUrn.from_string(source_column_urn)
    unevaluated: list[str] = []

    # The snapshot is keyed by input dataset URN and then by field path, so this
    # reads the entry for *this column's own table*. Flattening the datasets
    # together would let a column of the same name in another input answer for
    # this one, which is exactly the collision D-070 already cost this project
    # once at model granularity.
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    training_type: str | None = None
    for run_urn in (properties.trainingJobs or []) if properties else []:
        captured = training_snapshot(conn, run_urn, config.training_schema_property)
        for_dataset = (captured or {}).get(str(field_urn.parent), {})
        training_type = for_dataset.get(field_urn.field_path, training_type)
    if training_type is None:
        unevaluated.append(
            "**drift**: no training run of this feature's model carries a "
            f"{config.training_schema_property!r} snapshot, so there is no "
            "training-time type to compare today's against."
        )

    schema = conn.graph.get_aspect(str(field_urn.parent), SchemaMetadataClass)
    current_type = next(
        (
            column.nativeDataType
            for column in (schema.fields if schema else [])
            if column.fieldPath == field_urn.field_path
        ),
        None,
    )
    if current_type is None:
        unevaluated.append(
            "**drift**: the source dataset records no schema for this column today, "
            "so there is nothing to compare a snapshot against."
        )

    return training_type, current_type, tuple(unevaluated)


def gather_feature(
    conn: DataHubConnection,
    feature_urn: str,
    model_urn: str,
    model_name: str,
    config: ScanConfig,
    *,
    findings: Sequence[Finding] = (),
) -> FeatureFacts:
    """Read every fact one feature's card describes. Read-only.

    Args:
        conn: An open connection.
        feature_urn: The ``mlFeature`` to document.
        model_urn: The model that declares it, whose training run carries the
            schema snapshot the drift section needs.
        model_name: That model's readable name, already resolved by the caller.
        config: Hop caps, classification lists and the snapshot property name.
        findings: This scan's findings. Filtered here to the ones naming this
            feature's source column, so a card carries the counterfactual for its
            own feature and never for the model's other ones.

    Returns:
        The facts. Anything the graph does not hold arrives as None or empty and
        is rendered as unrecorded, never inferred.
    """
    feature_name = MlFeatureUrn.from_string(feature_urn).name
    source = feature_source_column(conn, feature_urn)

    if source is None:
        return FeatureFacts(
            feature_urn=feature_urn,
            feature_name=feature_name,
            model_urn=model_urn,
            model_name=model_name,
            source_column_urn=None,
            source_column_name=None,
            source_dataset_name=None,
            unevaluated=(
                "**everything below**: this feature records no source column "
                "(`modelguard.source_column`), so there is no column to walk back "
                "from. `modelguard link` is what writes it.",
            ),
        )

    field_urn = SchemaFieldUrn.from_string(source)
    walked = derivation_chains(conn, source, config, max_hops=config.leakage_max_hops)
    chains = tuple(_chain_names(chain) for chain in walked)

    # Every dataset the chains cross, the feature's own source table included:
    # it is the first table in the provenance and a reader looking for "which of
    # these is stale" would be surprised to find it missing.
    dataset_urns = {str(field_urn.parent)}
    for chain in walked:
        dataset_urns.update(str(SchemaFieldUrn.from_string(step.urn).parent) for step in chain)

    tables = tuple(
        TableInTheChain(
            dataset_urn=dataset_urn,
            dataset_name=DatasetUrn.from_string(dataset_urn).name,
            signal=freshness_signal(conn, dataset_urn, config),
        )
        for dataset_urn in sorted(dataset_urns)
    )

    exposures, exposure_gaps = _exposures(conn, config, source)
    training_type, current_type, type_gaps = _types(conn, config, model_urn, source)

    return FeatureFacts(
        feature_urn=feature_urn,
        feature_name=feature_name,
        model_urn=model_urn,
        model_name=model_name,
        source_column_urn=source,
        source_column_name=field_urn.field_path,
        source_dataset_name=DatasetUrn.from_string(field_urn.parent).name,
        chains=chains,
        tables=tables,
        exposures=exposures,
        unevaluated=exposure_gaps + type_gaps,
        training_type=training_type,
        current_type=current_type,
        findings=tuple(finding for finding in findings if finding.resource_urn == source),
    )


def _chain_lines(facts: FeatureFacts) -> list[str]:
    """The derivation, hop by hop, with the other chains named rather than hidden."""
    if not facts.chains:
        return [
            "This column has no upstream column lineage in the catalog, so the "
            f"derivation is {NOT_RECORDED}. dbt, Spark and the warehouse connectors "
            "emit column-level lineage; the SDK's `add_lineage` accepts an explicit "
            "column mapping."
        ]

    lines = [f"`{'` <- `'.join(facts.chains[0])}`"]
    if len(facts.chains) > 1:
        lines += [
            "",
            f"This feature is derived by **{len(facts.chains)} distinct chains**, not "
            "one. Anything that changes how it is computed has to account for all of "
            "them, and a fix applied to the chain above alone would leave the rest in "
            "place:",
            "",
            *[f"- `{'` <- `'.join(chain)}`" for chain in facts.chains[1:]],
        ]
    return lines


def _freshness_lines(facts: FeatureFacts) -> list[str]:
    """One row per table crossed, with its lag now and never at training time."""
    if not facts.tables:
        return [f"No table is recorded in this feature's derivation ({NOT_RECORDED})."]

    lines = ["| Table | Last changed | Within SLA |", "|---|---|---|"]
    for table in facts.tables:
        if table.signal is None:
            lines.append(f"| `{table.dataset_name}` | {NOT_RECORDED} | not evaluated |")
        else:
            verdict = "no" if table.signal.is_stale else "yes"
            lines.append(
                f"| `{table.dataset_name}` | {table.signal.lag_hours:.1f}h ago | {verdict} |"
            )
    lines += [
        "",
        "Freshness is measured **now**, not as of the training run: DataHub records "
        "no snapshot of it at training time, and quoting today's number as though it "
        "were then would answer a different question.",
    ]
    return lines


def _exposure_lines(facts: FeatureFacts) -> list[str]:
    """Classified ancestors, or an explicit statement that none was reached."""
    if not facts.exposures:
        return [
            "No classified column was reached by this feature's derivation, among "
            "the classifications configured. The section below says which of them "
            "were configured at all."
        ]
    return [
        f"- **{exposure.kind}**: `{exposure.column_name}` in "
        f"`{exposure.dataset_name}`, marked `{exposure.marker_urn}`, reached by "
        f"`{'` <- `'.join(exposure.chain)}`"
        for exposure in facts.exposures
    ]


def _drift_lines(facts: FeatureFacts) -> list[str]:
    """Whether the column's type moved since training, or why that is unknown."""
    if facts.training_type is None or facts.current_type is None:
        return [
            "Not established. The section on what could not be checked says which half was missing."
        ]
    if facts.drifted:
        return [
            f"**Changed.** At training time this column was `{facts.training_type}`; "
            f"today it is `{facts.current_type}`. A model fitted against the first "
            "is being served the second."
        ]
    return [f"Unchanged: `{facts.current_type}` at training time and today."]


def _counterfactual_lines(facts: FeatureFacts) -> list[str]:
    """The remedies for any finding naming this feature, each sufficient alone."""
    if not facts.findings:
        return [
            "No finding from this scan names this feature. That records what the "
            "checks which ran found, which is a smaller claim than it looks."
        ]

    lines: list[str] = []
    for finding in facts.findings:
        counterfactual = finding.counterfactual
        lines += [f"**{finding.title}** ({finding.severity})", ""]
        if counterfactual.paths > 1:
            lines += [
                f"Reached by {counterfactual.paths} derivations, so every one has to "
                "be cut. Each remedy below is sufficient on its own only where it "
                "says so.",
                "",
            ]
        lines += [f"- {remedy.summary}" for remedy in counterfactual.remedies]
        lines.append("")
    return lines


def render_feature_card(facts: FeatureFacts) -> str:
    """Render one feature's provenance card. Pure: every fact comes from ``facts``."""
    lines = [
        f"# Feature provenance: {facts.feature_name}",
        "",
        f"A Data Card for one feature of `{facts.model_name}`, generated by ModelGuard "
        "from lineage and classification metadata in DataHub. Every fact below is read "
        f"from the catalog; anything it does not record is marked *{NOT_RECORDED}* "
        "rather than omitted or guessed. Pushkarna, Zaldivar and Kjartansson, *Data "
        "Cards* (FAccT 2022), for the form.",
        "",
        "## What this feature is computed from",
        "",
    ]

    if not facts.traced:
        lines += [
            f"**{NOT_RECORDED}.** No source column is declared for this feature, so "
            "nothing below could be established.",
            "",
            "## What could not be checked",
            "",
            *[f"- {gap}" for gap in facts.unevaluated],
        ]
        return "\n".join(lines)

    lines += [
        f"- Source column: `{facts.source_column_name}`",
        f"- Source dataset: `{facts.source_dataset_name}`",
        "",
        "### Derivation",
        "",
        *_chain_lines(facts),
        "",
        "## Tables this feature depends on",
        "",
        *_freshness_lines(facts),
        "",
        "## Classification exposure",
        "",
        *_exposure_lines(facts),
        "",
        "## Schema drift",
        "",
        *_drift_lines(facts),
        "",
        "## Open findings and how to clear them",
        "",
        *_counterfactual_lines(facts),
        "",
        "## What could not be checked",
        "",
    ]
    # Rendered whether or not it is empty, deliberately: an omitted section reads
    # as an absent problem, and this is the section a reader is most likely to
    # mistake for a clean bill.
    lines += (
        [f"- {gap}" for gap in facts.unevaluated]
        if facts.unevaluated
        else [
            "Every check this card asked for had the metadata it needed. That is not "
            "a claim that every check worth running was asked for."
        ]
    )
    lines += [
        "",
        "## Limits of this card",
        "",
        "- ModelGuard has never read this feature's values. Nothing here is a "
        "statement about the data's contents, its distribution, or its quality.",
        "- It is generated from metadata, so it is exactly as complete as the catalog "
        "is. A gap above is a fact about the catalog, not about the feature.",
        "- It is not an approval.",
    ]
    return "\n".join(lines)


def publish_feature_card(
    conn: DataHubConnection,
    facts: FeatureFacts,
    *,
    run_id: str,
) -> str:
    """Write the card as a document linked to the feature. Returns its URN.

    Keyed on the feature alone, like the model card is keyed on the model: there
    is one card per feature and it is meant to be current, so a rerun replaces it
    rather than adding a second.

    Related to the feature *and* the model, because a reader arrives from either:
    from the model wanting to know what it trains on, or from the feature wanting
    to know where it comes from.
    """
    document = Document.create_document(
        id=f"modelguard-feature-card-{facts.feature_name}",
        title=f"Feature provenance: {facts.feature_name}",
        text=render_feature_card(facts),
        subtype=FEATURE_CARD_SUBTYPE,
        related_assets=[facts.feature_urn, facts.model_urn],
        custom_properties={RUN_ID_PROPERTY: run_id},
    )
    conn.client.entities.upsert(document)
    return str(document.urn)
