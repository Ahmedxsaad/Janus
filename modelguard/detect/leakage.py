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

How a label is declared
-----------------------
By a glossary term on the column, read from two places and unioned, because both
are legitimate ways to say "this column is the answer":

* ``glossaryTerms`` on the ``schemaField`` itself, which is what ModelGuard and
  the seeder write, and
* ``editableSchemaMetadata`` on the parent dataset, which is what the DataHub UI
  writes when a human tags a column by hand.

Reading both means a data scientist declares their label in the UI, touching no
ModelGuard configuration, and leakage detection starts working on their models.
Both routes were emitted and read back against a live GMS before this module was
written.

The trap in column-level lineage
--------------------------------
``get_lineage(source_column=...)`` does **not** return the upstream column. It
returns the upstream **dataset**: for the seeded graph ``LineageResult.urn`` is
``loans_raw``, never ``loans_raw.default_status``. A detector that compared result
URNs against the label column URN would find nothing and pronounce a leaking graph
clean, which is the worst way for a detector to be wrong. The column truth is
carried in ``LineageResult.paths``, whose entries are ``LineagePath`` objects with
a schemaField ``urn`` and a ``column_name``. This module reads ``paths`` and never
``urn``, and that same path is what the incident quotes.

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
    EditableSchemaMetadataClass,
    GlossaryTermsClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
)
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.graph_reads import model_ref
from modelguard.models import LeakageFinding, LeakingFeature, ModelRef

#: Custom property on an MLFeature naming the exact column it derives from.
#: MLFeatureProperties.sources declares entityTypes [dataset], so a feature can
#: say which table it came from but not which column. This bridges that (D-012).
SOURCE_COLUMN_PROPERTY = "modelguard.source_column"


class _LabelIndex:
    """Which columns are declared labels, read once per dataset.

    A leak path revisits the same dataset for every column it walks, and the
    declaration lives in an aspect on that dataset. Caching the per-dataset read
    keeps the traversal from turning into an N+1 (detect/CLAUDE.md rule 3).
    """

    def __init__(self, conn: DataHubConnection, config: ScanConfig) -> None:
        self._conn = conn
        self._term = config.label_term_urn
        self._by_dataset: dict[str, frozenset[str]] = {}

    def _editable_terms(self, dataset_urn: str) -> frozenset[str]:
        """Return the field paths a human tagged as labels through the UI.

        The UI does not write to the schemaField entity. It writes
        ``editableSchemaMetadata`` on the parent dataset, keyed by field path, so
        a detector that read only the schemaField would never see a human's
        declaration.
        """
        editable = self._conn.graph.get_aspect(dataset_urn, EditableSchemaMetadataClass)
        if editable is None:
            return frozenset()

        return frozenset(
            info.fieldPath
            for info in editable.editableSchemaFieldInfo
            if info.glossaryTerms
            and any(association.urn == self._term for association in info.glossaryTerms.terms)
        )

    def _labels_in(self, dataset_urn: str) -> frozenset[str]:
        """Return the label column URNs of one dataset, reading it at most once."""
        cached = self._by_dataset.get(dataset_urn)
        if cached is not None:
            return cached

        dataset = DatasetUrn.from_string(dataset_urn)
        labels = {
            str(SchemaFieldUrn(parent=dataset, field_path=path))
            for path in self._editable_terms(dataset_urn)
        }
        self._by_dataset[dataset_urn] = frozenset(labels)
        return self._by_dataset[dataset_urn]

    def is_label(self, column_urn: str) -> bool:
        """Whether a column is declared a label, by either route."""
        field = SchemaFieldUrn.from_string(column_urn)
        if column_urn in self._labels_in(field.parent):
            return True

        # The schemaField's own aspect. Not cached per dataset because it is keyed
        # by column, and a leak path walks only a handful of columns.
        terms = self._conn.graph.get_aspect(column_urn, GlossaryTermsClass)
        if terms is None or not terms.terms:
            return False
        return any(association.urn == self._term for association in terms.terms)


def feature_source_column(conn: DataHubConnection, feature_urn: str) -> str | None:
    """Return the exact column a feature is computed from, or None if unrecorded.

    A feature with no recorded source column is not evidence of safety, it is the
    absence of evidence, and this detector fires only on positive evidence. Such a
    feature is skipped, never cleared (detect/CLAUDE.md rule 5).
    """
    properties = conn.graph.get_aspect(feature_urn, MLFeaturePropertiesClass)
    if properties is None or not properties.customProperties:
        return None
    return properties.customProperties.get(SOURCE_COLUMN_PROPERTY)


def leak_path(
    conn: DataHubConnection,
    source_column_urn: str,
    labels: _LabelIndex,
    config: ScanConfig,
) -> tuple[str, tuple[str, ...]] | None:
    """Walk a column's upstream cone and return the label it reaches, if any.

    Reads ``LineageResult.paths``, never ``LineageResult.urn``. See the module
    docstring: ``urn`` is the upstream dataset, and comparing it against a label
    column makes a leaking graph look clean.

    Returns:
        The label column's URN and the chain of column names walked to reach it,
        or None when the cone reaches no declared label.
    """
    field = SchemaFieldUrn.from_string(source_column_urn)

    results = conn.client.lineage.get_lineage(
        source_urn=field.parent,
        source_column=field.field_path,
        direction="upstream",
        max_hops=config.leakage_max_hops,
        count=config.lineage_result_cap,
    )

    for result in results:
        # Above two hops DataHub switches to a full-graph search and returns
        # entities beyond the cap, so the cap is enforced here (D-020).
        if result.hops > config.leakage_max_hops:
            continue

        path = result.paths or []
        for step in path:
            if step.urn == source_column_urn:
                continue
            if labels.is_label(step.urn):
                columns = tuple(hop.column_name for hop in path if hop.column_name)
                return step.urn, columns

    return None


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

    model = model_ref(conn, model_urn)
    labels = _LabelIndex(conn, config)

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
