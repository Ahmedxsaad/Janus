"""One upstream column walk, asked two different questions.

Target leakage asks: does this feature's source column descend from a column
somebody declared to be the label? The sensitive-source detector asks: does it
descend from a column somebody classified as PII or restricted? Those are the
same traversal over the same column graph, differing only in which mark makes an
ancestor interesting, so the traversal lives here and each detector supplies its
own mark.

That is not a refactor for tidiness. The traversal contains the single subtlest
fact in this codebase, and duplicating it would be duplicating the chance to get
it wrong: ``get_lineage(source_column=...)`` does **not** return the upstream
column. It returns the upstream **dataset**. A detector that compared
``LineageResult.urn`` against a column URN would find nothing and pronounce a
contaminated graph clean, which is the worst way for a detector to be wrong. The
column truth lives in ``LineageResult.paths``, whose entries carry a schemaField
``urn`` and a ``column_name``. This module reads ``paths`` and never ``urn``,
and that same path is what an incident quotes as its proof (D-031).

How a column gets marked
------------------------
By a glossary term or a tag, read from two places and unioned, because both are
legitimate ways to say something about a column:

* ``glossaryTerms`` / ``globalTags`` on the ``schemaField`` itself, which is what
  ModelGuard, the seeder, and most ingestion sources write, and
* ``editableSchemaMetadata`` on the parent dataset, which is what the DataHub UI
  writes when a human marks a column by hand.

Reading both means a data scientist marks a column in the UI, touching no
ModelGuard configuration, and detection starts working on their models.
"""

from __future__ import annotations

from dataclasses import dataclass

from datahub.metadata.schema_classes import (
    EditableSchemaMetadataClass,
    GlobalTagsClass,
    GlossaryTermsClass,
)
from datahub.metadata.urns import SchemaFieldUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig


@dataclass(frozen=True)
class WalkResult:
    """What one column's upstream walk found, and whether it saw everything.

    ``get_lineage`` is capped at ``config.lineage_result_cap`` results with no
    continuation token: a column whose upstream cone exceeds it is silently cut
    off, and a walk that finds no mark in the results it did see cannot tell the
    difference between "nothing upstream is marked" and "the mark was past the
    cap". Reporting the second as the first is a false negative in exactly the
    failure mode this project exists to catch, and it gets worse the wider the
    cone, which is to say worst on the mature warehouses most likely to hold an
    unnoticed leak (F1, docs/plan/07).
    """

    hit: tuple[str, str, tuple[str, ...]] | None
    truncated: bool
    """True when the walk saw exactly the cap's worth of results, so a mark
    beyond it may exist and was never checked. Equality, not >=: the cap is a
    hard limit, so exactly-the-cap is the only observable signature that more
    might exist. One result short is a complete answer."""


class ColumnMarkIndex:
    """Which columns carry one of a given set of terms or tags.

    A traversal revisits the same dataset for every column it walks, and the
    UI's declarations live in one aspect on that dataset, so the per-dataset read
    is cached to keep the walk from turning into an N+1 (detect/CLAUDE.md rule 3).

    An index with no terms and no tags matches nothing, and callers are expected
    to skip the traversal entirely rather than walk a graph for a mark that can
    never be found: see :attr:`configured`.
    """

    def __init__(
        self,
        conn: DataHubConnection,
        *,
        terms: frozenset[str] = frozenset(),
        tags: frozenset[str] = frozenset(),
    ) -> None:
        """Build an index over the given glossary terms and tags.

        Args:
            conn: An open connection. Read from, never written to.
            terms: Glossary term URNs that mark a column.
            tags: Tag URNs that mark a column.
        """
        self._conn = conn
        self._terms = terms
        self._tags = tags
        self._by_dataset: dict[str, dict[str, str]] = {}

    @property
    def configured(self) -> bool:
        """Whether this index can match anything at all."""
        return bool(self._terms or self._tags)

    def _editable_marks(self, dataset_urn: str) -> dict[str, str]:
        """Return field path to marker URN for every column a human marked in the UI.

        The UI does not write to the schemaField entity. It writes
        ``editableSchemaMetadata`` on the parent dataset, keyed by field path, so
        a detector that read only the schemaField would never see a human's
        declaration.
        """
        editable = self._conn.graph.get_aspect(dataset_urn, EditableSchemaMetadataClass)
        if editable is None:
            return {}

        marked: dict[str, str] = {}
        for info in editable.editableSchemaFieldInfo:
            terms = info.glossaryTerms
            if terms:
                match = next((a.urn for a in terms.terms if a.urn in self._terms), None)
                if match is not None:
                    marked[info.fieldPath] = match
                    continue
            tags = info.globalTags
            if tags:
                match = next((a.tag for a in tags.tags if a.tag in self._tags), None)
                if match is not None:
                    marked[info.fieldPath] = match
        return marked

    def _marks_in(self, dataset_urn: str) -> dict[str, str]:
        """Return one dataset's UI-declared marks, reading the aspect at most once."""
        cached = self._by_dataset.get(dataset_urn)
        if cached is None:
            cached = self._editable_marks(dataset_urn)
            self._by_dataset[dataset_urn] = cached
        return cached

    def marker(self, column_urn: str) -> str | None:
        """Return the term or tag URN that marks this column, or None.

        The marker itself is returned, not merely a yes: an incident that says
        "descends from a column tagged PII" is actionable, and one that says
        "descends from a marked column" is not.
        """
        field = SchemaFieldUrn.from_string(column_urn)
        from_ui = self._marks_in(field.parent).get(field.field_path)
        if from_ui is not None:
            return from_ui

        # The schemaField's own aspects. Not cached per dataset because they are
        # keyed by column, and a walk visits only a handful of columns. Each read
        # is skipped when nothing of that kind is configured, so a detector that
        # only cares about terms pays exactly what it did before tags existed.
        if self._terms:
            terms = self._conn.graph.get_aspect(column_urn, GlossaryTermsClass)
            if terms is not None:
                match = next((a.urn for a in terms.terms if a.urn in self._terms), None)
                if match is not None:
                    return match

        if self._tags:
            tags = self._conn.graph.get_aspect(column_urn, GlobalTagsClass)
            if tags is not None:
                match = next((a.tag for a in tags.tags if a.tag in self._tags), None)
                if match is not None:
                    return match

        return None

    def is_marked(self, column_urn: str) -> bool:
        """Whether a column carries any of this index's marks."""
        return self.marker(column_urn) is not None


def marked_ancestor(
    conn: DataHubConnection,
    source_column_urn: str,
    index: ColumnMarkIndex,
    config: ScanConfig,
) -> WalkResult:
    """Walk a column's upstream cone and return the marked column it reaches.

    Reads ``LineageResult.paths``, never ``LineageResult.urn``. See the module
    docstring: ``urn`` is the upstream dataset, and comparing it against a column
    URN makes a contaminated graph look clean.

    A column's cone can reach a marked ancestor by more than one chain. Every
    match is collected and the shortest is returned, ties broken on the ancestor
    URN and then the chain itself, rather than returning whichever the server
    listed first: above two hops DataHub answers from a full-graph search in
    network order, so a first-match return can quote a different derivation chain
    on two walks of an unchanged graph. That chain is the auditable proof a human
    reads in the incident, and proof that changes when nothing changed is not
    proof.

    Args:
        conn: An open connection.
        source_column_urn: The column to walk upstream from.
        index: Which marks make an ancestor interesting.
        config: Supplies the hop cap and the lineage result cap.

    Returns:
        A :class:`WalkResult`. Its ``hit`` is the marked column's URN, the term
        or tag that marks it, and the chain of column names walked to reach it,
        or None when the cone (as far as the walk could see) reaches nothing
        marked. ``truncated`` says whether "as far as the walk could see" might
        be short of the column's whole upstream cone.
    """
    field = SchemaFieldUrn.from_string(source_column_urn)

    # A column that IS itself marked, with no transformation at all, is the
    # most direct form of the problem: someone wired a "feature" straight from
    # the ground truth, or straight from a restricted column. The traversal
    # below would never reveal it, since there is nothing left to derive from
    # once the column already is the thing, so it is checked explicitly first.
    # Nothing was walked, so there is nothing that could have been truncated.
    direct = index.marker(source_column_urn)
    if direct is not None:
        return WalkResult(hit=(source_column_urn, direct, (field.field_path,)), truncated=False)

    results = conn.client.lineage.get_lineage(
        source_urn=field.parent,
        source_column=field.field_path,
        direction="upstream",
        max_hops=config.leakage_max_hops,
        count=config.lineage_result_cap,
    )
    # Equality, not >=: the cap is a hard limit, so exactly-the-cap is the only
    # observable signature that a result beyond it may exist (F1, docs/plan/07).
    truncated = len(results) == config.lineage_result_cap

    matches: list[tuple[str, str, tuple[str, ...]]] = []
    for result in results:
        # Above two hops DataHub switches to a full-graph search and returns
        # entities beyond the cap, so the cap is enforced here (D-020).
        if result.hops > config.leakage_max_hops:
            continue

        path = result.paths or []
        for position, step in enumerate(path):
            if step.urn == source_column_urn:
                continue
            marker = index.marker(step.urn)
            if marker is not None:
                # Truncated at the match: a path that continues past the marked
                # column to a more distant ancestor must not be quoted as part of
                # the derivation chain that proves this finding.
                columns = tuple(hop.column_name for hop in path[: position + 1] if hop.column_name)
                matches.append((step.urn, marker, columns))
                break

    if not matches:
        return WalkResult(hit=None, truncated=truncated)
    best = min(matches, key=lambda match: (len(match[2]), match[0], match[2]))
    return WalkResult(hit=best, truncated=truncated)
