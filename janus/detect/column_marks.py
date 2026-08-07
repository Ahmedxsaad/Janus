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
and that same path is what an incident quotes as its proof.

How a column gets marked
------------------------
By a glossary term or a tag, read from two places and unioned, because both are
legitimate ways to say something about a column:

* ``glossaryTerms`` / ``globalTags`` on the ``schemaField`` itself, which is what
  Janus, the seeder, and most ingestion sources write, and
* ``editableSchemaMetadata`` on the parent dataset, which is what the DataHub UI
  writes when a human marks a column by hand.

Reading both means a data scientist marks a column in the UI, touching no
Janus configuration, and detection starts working on their models.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from datahub.metadata.schema_classes import (
    EditableSchemaMetadataClass,
    GlobalTagsClass,
    GlossaryTermsClass,
)
from datahub.metadata.urns import SchemaFieldUrn
from datahub.sdk.lineage_client import LineagePath

from janus.client import DataHubConnection
from janus.config import ScanConfig

#: One marked ancestor the walk reached: its URN, the term or tag that marks it,
#: and the chain of column names walked to get there.
Match = tuple[str, str, tuple[str, ...]]


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
    unnoticed leak (docs/08-evaluation.md).

    Every match is carried, not only the winner. The shortest is still the one
    quoted as proof, and :attr:`hit` is still how a caller asks for it, so no
    output moves. What the rest are for is the counterfactual: a finding reached
    by two derivations is not cleared by cutting one of them, and a remedy that
    only knew about the winner would confidently tell somebody to do half a fix.
    """

    matches: tuple[Match, ...]
    """Every marked ancestor reached, in the order the server listed them."""
    truncated: bool
    """True when the walk saw exactly the cap's worth of results, so a mark
    beyond it may exist and was never checked. Equality, not >=: the cap is a
    hard limit, so exactly-the-cap is the only observable signature that more
    might exist. One result short is a complete answer."""
    hop_capped: bool
    """True when GMS handed back a result the walk chose not to look at because
    it lay beyond ``config.leakage_max_hops``. Distinct from ``truncated``: that
    flag means the walk may not have *seen* everything (raise
    ``JANUS_LINEAGE_RESULT_CAP``); this one means it *saw* an ancestor and
    declined it on distance alone (raise ``JANUS_LEAKAGE_MAX_HOPS``), which
    is a different knob and a different sentence for a coverage gap to print.
    """

    @property
    def hit(self) -> Match | None:
        """The match a finding quotes as its proof, or None when nothing matched.

        The shortest chain, ties broken on the ancestor URN and then the chain
        itself, rather than whichever the server listed first: above two hops
        DataHub answers from a full-graph search in network order, so a
        first-match answer can quote a different derivation chain on two walks
        of an unchanged graph. That chain is the auditable proof a human reads
        in the incident, and proof that changes when nothing changed is not
        proof.
        """
        if not self.matches:
            return None
        return min(self.matches, key=lambda match: (len(match[2]), match[0], match[2]))

    @property
    def others(self) -> tuple[tuple[str, ...], ...]:
        """The chains of every match except the quoted one, in the same order.

        Compared by identity of the whole match rather than of the chain, so two
        different ancestors reached by identically named columns both survive.
        """
        best = self.hit
        return tuple(match[2] for match in self.matches if match is not best)


class ColumnMarkIndex:
    """Which columns carry one of a given set of terms or tags.

    A traversal revisits the same dataset for every column it walks, and the
    UI's declarations live in one aspect on that dataset, so the per-dataset read
    is cached to keep the walk from turning into an N+1 (docs/04-detectors.md).

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


def split_paths(steps: Sequence[LineagePath], source_column_urn: str) -> list[list[LineagePath]]:
    """Cut the SDK's flattened step list back into the paths it was built from.

    ``LineageResult.paths`` is not one path. GMS answers a column-level lineage
    query with a *list* of paths per upstream entity, and the SDK's
    ``_create_lineage_result`` appends every step of every one of them to a
    single list `[verified]`, so two derivations of the same column through the
    same upstream table arrive concatenated and indistinguishable.

    That matters twice. A walk that stopped at the first mark it met would report
    one derivation and never see the second, which is how a counterfactual ends
    up telling somebody to cut one edge of two. And a chain truncated by
    index into the flattened list would carry the tail of the *previous*
    derivation into the quoted proof.

    The boundary is recoverable: every path starts at the column that was
    queried, so each occurrence of it opens a new path. A list that does not
    start with it is returned whole, because a fixture, or a GMS that one day
    omits the start entity, would otherwise lose its only path to a rule about a
    sentinel that was never there.
    """
    if not steps or steps[0].urn != source_column_urn:
        return [list(steps)]

    paths: list[list[LineagePath]] = []
    for step in steps:
        if step.urn == source_column_urn:
            paths.append([])
        paths[-1].append(step)
    return paths


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
    match is collected and every one is returned; which of them a finding quotes
    as its proof, and why that choice has to be stable, is
    :attr:`WalkResult.hit`.

    Args:
        conn: An open connection.
        source_column_urn: The column to walk upstream from.
        index: Which marks make an ancestor interesting.
        config: Supplies the hop cap and the lineage result cap.

    Returns:
        A :class:`WalkResult`. Its ``matches`` holds every marked ancestor
        reached, and its ``hit`` is the one a finding quotes, or None when the
        cone (as far as the walk could see) reaches nothing marked.
        ``truncated`` says whether "as far as the walk could see" might be short
        of the column's whole upstream cone. ``hop_capped`` says whether GMS
        offered an ancestor further away than ``config.leakage_max_hops`` that
        the walk declined on distance alone.
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
        return WalkResult(
            matches=((source_column_urn, direct, (field.field_path,)),),
            truncated=False,
            hop_capped=False,
        )

    results = conn.client.lineage.get_lineage(
        source_urn=field.parent,
        source_column=field.field_path,
        direction="upstream",
        max_hops=config.leakage_max_hops,
        count=config.lineage_result_cap,
    )
    # Equality, not >=: the cap is a hard limit, so exactly-the-cap is the only
    # observable signature that a result beyond it may exist (docs/08-evaluation.md).
    truncated = len(results) == config.lineage_result_cap

    matches: list[Match] = []
    hop_capped = False
    for result in results:
        # Above two hops DataHub switches to a full-graph search and returns
        # entities beyond the cap, so the cap is enforced here. GMS
        # handed this one back; the walk saw it and declined it on distance
        # alone, which is worth telling a reader and is not the
        # same fact as `truncated` above.
        if result.hops > config.leakage_max_hops:
            hop_capped = True
            continue

        for path in split_paths(result.paths or [], source_column_urn):
            for position, step in enumerate(path):
                if step.urn == source_column_urn:
                    continue
                marker = index.marker(step.urn)
                if marker is not None:
                    # Truncated at the match: a path that continues past the
                    # marked column to a more distant ancestor must not be quoted
                    # as part of the derivation chain that proves this finding.
                    columns = tuple(
                        hop.column_name for hop in path[: position + 1] if hop.column_name
                    )
                    matches.append((step.urn, marker, columns))
                    break

    return WalkResult(matches=tuple(matches), truncated=truncated, hop_capped=hop_capped)


def derivation_chains(
    conn: DataHubConnection,
    source_column_urn: str,
    config: ScanConfig,
    *,
    max_hops: int,
) -> tuple[tuple[LineagePath, ...], ...]:
    """Every ordered chain from a column back to a source, shortest first.

    The third question over the same walk. :func:`marked_ancestor` asks whether a
    chain reaches something interesting and returns only the chains that do;
    :func:`related_columns` asks what a column touches and throws the ordering
    away. A provenance card needs neither: it needs the chains themselves,
    including the ones ending in a column nobody has marked anything, because
    "where does this feature come from" is answered by the whole derivation and
    not by the interesting part of it.

    Reads ``LineageResult.paths`` and never ``LineageResult.urn``, and cuts the
    SDK's flattened list apart with :func:`split_paths`, for the reasons the
    module docstring gives. Without the split, two derivations through one
    upstream table would render as one impossible chain on the card.

    Args:
        conn: An open connection.
        source_column_urn: The column to walk upstream from.
        config: Supplies the lineage result cap.
        max_hops: How far back to walk.

    Returns:
        The distinct chains, each starting at ``source_column_urn``, ordered
        shortest first and then by their column names so a card renders the same
        way twice. Empty when the column has no upstream lineage, which is a
        column the warehouse holds no derivation for and not an error.
    """
    field = SchemaFieldUrn.from_string(source_column_urn)
    results = conn.client.lineage.get_lineage(
        source_urn=field.parent,
        source_column=field.field_path,
        direction="upstream",
        max_hops=max_hops,
        count=config.lineage_result_cap,
    )

    # Keyed by the chain's URNs so the same derivation arriving through two
    # results is one chain. A card listing a derivation twice reads as two
    # different paths to the same place, which is a claim about the warehouse
    # that nothing measured.
    chains: dict[tuple[str, ...], tuple[LineagePath, ...]] = {}
    for result in results:
        # Enforced here rather than trusted: above two hops DataHub answers from
        # a full-graph search and returns entities past the cap.
        if result.hops > max_hops:
            continue
        for path in split_paths(result.paths or [], source_column_urn):
            if len(path) < 2:
                continue
            chains.setdefault(tuple(step.urn for step in path), tuple(path))

    return tuple(
        sorted(
            chains.values(),
            key=lambda chain: (len(chain), tuple(step.column_name or "" for step in chain)),
        )
    )


def related_columns(
    conn: DataHubConnection,
    source_column_urn: str,
    config: ScanConfig,
    *,
    direction: Literal["upstream", "downstream"],
    max_hops: int,
) -> dict[str, int]:
    """Every column reachable from one column, mapped to its shortest hop distance.

    The unmarked sibling of :func:`marked_ancestor`: that one answers "does this
    column descend from something interesting", and this one answers "what does
    this column touch at all". Proxy detection needs the second question
    in both directions, because the shape it looks for is not a chain, it is a
    fork: two columns descending from one ancestor, neither descending from the
    other.

    Reads ``LineageResult.paths`` and never ``LineageResult.urn``, for the same
    reason the whole module does (the module docstring): ``urn`` is the upstream
    *dataset*, and a proxy walk comparing datasets would call two unrelated
    columns of one wide table related to each other, which is every column in
    the warehouse.

    Args:
        conn: An open connection.
        source_column_urn: The column to walk from.
        config: Supplies the lineage result cap.
        direction: ``upstream`` or ``downstream``.
        max_hops: How far to walk. Separate from the leakage cap: a proxy
            relationship gets less meaningful with distance, where a leak does
            not (config.proxy_max_hops).

    Returns:
        Column URN to the fewest hops it was reached in, the queried column
        excluded. Empty when the column has no lineage in that direction.
    """
    field = SchemaFieldUrn.from_string(source_column_urn)
    results = conn.client.lineage.get_lineage(
        source_urn=field.parent,
        source_column=field.field_path,
        direction=direction,
        max_hops=max_hops,
        count=config.lineage_result_cap,
    )

    reached: dict[str, int] = {}
    for result in results:
        # DataHub answers past the cap above two hops, so the cap is
        # enforced here rather than trusted from the server.
        if result.hops > max_hops:
            continue
        for path in split_paths(result.paths or [], source_column_urn):
            for position, step in enumerate(path):
                if step.urn == source_column_urn:
                    continue
                # Position in the path is the hop count: the queried column sits
                # at index 0, so the step after it is one hop away.
                previous = reached.get(step.urn)
                if previous is None or position < previous:
                    reached[step.urn] = position
    return reached
