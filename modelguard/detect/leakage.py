"""P1: does a model consume a feature derived from its own label?

Target leakage is the failure that does not announce itself. Nothing breaks, no
job fails, no table goes stale. The model simply reports an accuracy it does not
have, because one of its features encodes the answer. It passes review, it ships,
and it collapses in production, where the label does not exist yet at the moment
of scoring. Offline metrics cannot catch it: they are computed on the same
contaminated data, so a held-out split validates the contamination.

Only the lineage can catch it, and that is why this detector belongs on DataHub.
The evidence is a path in the column graph, from a feature's source column back to
the column holding the ground truth. ModelGuard reads that path, so a finding is
auditable rather than asserted, and it needs no training run and no access to the
underlying data.

How a label is declared, and how the walk works
-----------------------------------------------
A label is a glossary term on the column, read from the ``schemaField`` itself
and from the parent dataset's ``editableSchemaMetadata`` (what the UI writes) and
unioned, so a data scientist declaring their label in the UI needs no ModelGuard
configuration at all. The upstream walk, the two aspects it reads, and the
``paths``-not-``urn`` trap that walk turns on all live in
:mod:`modelguard.detect.column_marks`, because the sensitive-source detector asks
the same question of the same graph with a different mark. This module supplies
the mark (the configured label term) and turns a hit into a finding.

Literature
----------
Kaufman, Rosset and Perlich, "Leakage in Data Mining: Formulation, Detection, and
Avoidance" (KDD 2011, TKDD 2012), formalizes leakage as the illegitimate presence
in the training data of information about the target. Their prescription is to
inspect how each feature was constructed rather than to trust a held-out score.
Column-level lineage is that inspection, made mechanical.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
)
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn
from datahub.utilities.urns.error import InvalidUrnError

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.column_marks import ColumnMarkIndex, marked_ancestor
from modelguard.detect.graph_reads import model_ref
from modelguard.models import LeakageFinding, LeakingFeature, ModelRef

#: Custom property on an MLFeature naming the exact column it derives from.
#: MLFeatureProperties.sources declares entityTypes [dataset], so a feature can
#: say which table it came from but not which column. This bridges that (D-012).
SOURCE_COLUMN_PROPERTY = "modelguard.source_column"


def label_index(conn: DataHubConnection, config: ScanConfig) -> ColumnMarkIndex:
    """Return the index of columns declared to be labels.

    A :class:`~modelguard.detect.column_marks.ColumnMarkIndex` over exactly one
    glossary term, the configured label term. Labels are never declared by a tag:
    a label is a statement about what a column *means* to a model, which is what
    a glossary term is for, and accepting tags too would let an unrelated tag
    silently start producing leakage incidents.
    """
    return ColumnMarkIndex(conn, terms=frozenset({config.label_term_urn}))


def feature_source_column(conn: DataHubConnection, feature_urn: str) -> str | None:
    """Return the exact column a feature is computed from, or None if unrecorded.

    A feature with no recorded source column is not evidence of safety, it is the
    absence of evidence, and this detector fires only on positive evidence. Such a
    feature is skipped, never cleared (detect/CLAUDE.md rule 5).

    The property is free text that anything may have written: another ingestion
    job, a human editing the feature by hand, an older URN format. A value that
    is not a parseable schemaField URN is treated exactly like an absent one,
    because it says nothing about the feature either way. Returning it raw would
    let one malformed property raise out of the traversal and abort the whole
    model's leakage scan, turning a single unreadable feature into no detection
    at all.
    """
    properties = conn.graph.get_aspect(feature_urn, MLFeaturePropertiesClass)
    if properties is None or not properties.customProperties:
        return None

    recorded = properties.customProperties.get(SOURCE_COLUMN_PROPERTY)
    if recorded is None:
        return None
    try:
        SchemaFieldUrn.from_string(recorded)
    except InvalidUrnError:
        return None
    return recorded


def leak_path(
    conn: DataHubConnection,
    source_column_urn: str,
    labels: ColumnMarkIndex,
    config: ScanConfig,
) -> tuple[str, tuple[str, ...]] | None:
    """Walk a column's upstream cone and return the label it reaches, if any.

    A thin wrapper over :func:`~modelguard.detect.column_marks.marked_ancestor`,
    which holds the traversal and the ``paths``-not-``urn`` rule it turns on. The
    marker it returns is dropped here: for a label index there is only one
    possible marker, the configured label term, so naming it in every finding
    would add a column of identical values.

    Returns:
        The label column's URN and the chain of column names walked to reach it,
        or None when the cone reaches no declared label. Silent about
        truncation: a caller that needs it (coverage.py, deciding whether a
        clean answer here is trustworthy) calls marked_ancestor directly rather
        than through this label-specific wrapper.
    """
    walk = marked_ancestor(conn, source_column_urn, labels, config)
    if walk.hit is None:
        return None
    label_urn, _marker, column_path = walk.hit
    return label_urn, column_path


def leakage_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[LeakageFinding, ...]:
    """Return every feature of a model that derives from a declared label column.

    Deterministic and read-only. The LLM is never asked whether a feature leaks;
    it is told that one does, and writes the prose around it.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Supplies the label glossary term and the hop cap.

    Returns:
        One finding per leaking feature, ordered by the leaking column's name so a
        report reads identically on every run. Empty when no feature's lineage
        reaches a label, which is the answer a clean model must produce.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.mlFeatures:
        return ()

    model = model_ref(conn, model_urn, properties=properties)
    labels = label_index(conn, config)

    findings: list[LeakageFinding] = []
    for feature_urn in properties.mlFeatures:
        source_column = feature_source_column(conn, feature_urn)
        if source_column is None:
            continue

        hit = leak_path(conn, source_column, labels, config)
        if hit is None:
            continue

        label_urn, column_path = hit
        findings.append(_finding(model, feature_urn, source_column, label_urn, column_path))

    return tuple(sorted(findings, key=lambda finding: finding.leak.source_column_name))


def _finding(
    model: ModelRef,
    feature_urn: str,
    source_column_urn: str,
    label_column_urn: str,
    column_path: tuple[str, ...],
) -> LeakageFinding:
    """Assemble one finding from the columns the traversal actually proved."""
    source_field = SchemaFieldUrn.from_string(source_column_urn)
    label_field = SchemaFieldUrn.from_string(label_column_urn)

    return LeakageFinding(
        model=model,
        leak=LeakingFeature(
            feature_urn=feature_urn,
            feature_name=MlFeatureUrn.from_string(feature_urn).name,
            source_column_urn=source_column_urn,
            source_column_name=source_field.field_path,
            label_column_urn=label_column_urn,
            label_column_name=label_field.field_path,
            label_dataset_name=DatasetUrn.from_string(label_field.parent).name,
            column_path=column_path,
        ),
    )
