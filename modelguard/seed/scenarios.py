"""Plant the failures the demo and the benchmark detect.

A detector is only trustworthy if you can turn the problem on and off. Each
scenario here writes a fact into the graph that a deterministic detector will
find, and each one is reversible, so a run can prove both directions: planted ->
finding, reverted -> no finding (seed/CLAUDE.md rule 5).

The stale-source scenario
-------------------------
``loans_raw`` is the raw table carrying the label column. Backdating its last
change is the "silent" data failure ModelGuard exists to catch: nothing errors,
nothing is null, the table simply stops being refreshed while a live model keeps
scoring against features derived from it.

Freshness is expressed through DataHub's ``operation`` aspect, whose
``lastUpdatedTimestamp`` field is the graph's own record of when a dataset last
changed. That aspect is a **timeseries** aspect: emitting it appends an event
rather than replacing one, and readers take the latest. Reverting is therefore
not a delete, it is a newer event saying the table just refreshed. That is also
how a real pipeline would announce a recovery.

Because staleness is measured against wall-clock time, these functions take the
current instant as an argument. Tests pass a fixed one; the CLI passes ``now``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    DeprecationClass,
    FineGrainedLineageClass,
    GlobalTagsClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    TagAssociationClass,
)
from datahub.sdk.dataset import Dataset, parse_cll_mapping
from datahub.specific.dataset import DatasetPatchBuilder

from modelguard.client import DataHubConnection
from modelguard.seed import graph_spec as spec
from modelguard.writeback.labels import ensure_tag
from modelguard.writeback.terms import add_term, ensure_term, remove_term

#: Names the scenario in the operation aspect's custom properties, so a reader of
#: the graph can tell a planted failure from a real one.
SCENARIO_PROPERTY = "modelguard.scenario"

#: The one scenario Phase 1 needs: the source table stops refreshing.
STALE_SOURCE = "stale-source-table"

#: Phase 2's schema-drift scenario: the feature table the model trains on has its
#: live schema changed after training, so it no longer matches the training-time
#: snapshot captured on the training run.
SCHEMA_DRIFT = "input-schema-drift"

#: The leakage scenario: the feature table's leaking column-lineage edge, the one
#: saying ``prior_default_flag`` derives from the label. The seeder always plants
#: it (D-032), so before this scenario existed the flagship detector had no
#: negative control at all: no way to show the graph going clean.
TARGET_LEAKAGE = "target-leakage"

#: The multi-path scenario: the same feature reaches a declared label by two
#: independent derivations instead of one. It exists for the counterfactual
#: (T-03), which is only worth anything if it is right about the case where
#: cutting one edge is not a fix, and that case cannot be asked of a graph that
#: only ever has one edge to cut.
SECOND_LEAK_PATH = "second-leak-path"

#: The confusable-negative scenarios (T-09, 09 section 2.2): precision of 1.00
#: is close to vacuous while the negative trials are absent positives rather
#: than hard negatives. Each of these plants a graph shape that looks like it
#: could leak and must not fire, which is the case an "always no" detector
#: would also pass and only a correct one distinguishes.
COMMON_ANCESTOR = "common-ancestor-label"
LABEL_LOOKALIKE = "label-lookalike"

#: The governance scenarios. A classified column upstream of a feature, and a
#: training input its owners have marked deprecated. Both are facts an
#: organization records about its own data and that nothing today joins back to
#: the models trained on it.
SENSITIVE_SOURCE = "sensitive-source"
DEPRECATED_INPUT = "deprecated-input"

#: The degraded-mode scenario (T-07): the model stops declaring its features, the
#: way it does after every mlflow ingest (D-074). Not a fault in the data at all,
#: which is the point: it is the state a stranger's catalog is in on day one, and
#: the only one where the table-level mode is allowed to speak.
DELINKED_MODEL = "delinked-model"

#: The tag the sensitive-source scenario applies to the label's own source table
#: column. A tag rather than a glossary term on purpose: the leakage detector
#: already exercises the term route on this same column, so using a tag here
#: means the benchmark covers both classification surfaces rather than one twice.
SENSITIVE_TAG_URN = "urn:li:tag:modelguard.sensitive"
SENSITIVE_TAG_NAME = "modelguard.sensitive"
SENSITIVE_TAG_DESCRIPTION = (
    "Demo classification: a column whose contents a model must not learn from. "
    "Planted by modelguard-scenario; stands in for whatever PII or restricted "
    "tag an organization already uses."
)

#: The column the sensitive scenario classifies: the source table's self-reported
#: income. Chosen because it is upstream of an actual model *feature*
#: (``applicant_income``) through the seeded column lineage, which is what makes
#: the walk reach it. Classifying ``applicant_id`` instead would look more
#: obviously sensitive and prove nothing: it feeds the feature table's primary
#: key, not a feature, so no model consumes it and no walk would ever start there.
SENSITIVE_SOURCE_COLUMN = "income"

#: What the deprecation scenario records, quoted back in the finding. It is set
#: on the feature table, because that is the dataset the seeded training run
#: records as its input, and the deprecation detector reads a model's inputs.
DEPRECATION_NOTE = (
    "Planted by modelguard-scenario. Superseded by the v2 feature pipeline; "
    "this table stops being written after the migration."
)

#: The drift the demo plants on the feature table, chosen to leave the columns the
#: leakage traversal depends on (applicant_id, prior_default_flag) untouched, so
#: the two Phase 2 scenarios can run on the same graph without interfering.
_RETYPED_COLUMN = "applicant_income"
_RETYPED_TO = "VARCHAR"
_DROPPED_COLUMN = "updated_at"
_ADDED_COLUMN = spec.Column(
    "debt_to_income",
    "NUMBER",
    "Engineered after the model was trained; absent from the training-time schema.",
)

#: The actor recorded on the operation. The Quickstart's default user.
SCENARIO_ACTOR = "urn:li:corpuser:datahub"

#: How stale the demo makes the table. Comfortably past the 6 hour default SLA,
#: and a plausible "nobody noticed over the weekend" duration.
DEFAULT_LAG_HOURS = 30.0

_MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class ScenarioResult:
    """What a scenario did to the graph, so a caller can report or undo it."""

    name: str
    dataset_urn: str
    last_updated_ms: int
    """The value written to operation.lastUpdatedTimestamp."""
    lag_hours: float
    """How stale the dataset now claims to be. Zero after a revert."""


def _now_ms() -> int:
    """Return the current instant in epoch milliseconds."""
    return int(time.time() * 1000)


def _emit_operation(
    conn: DataHubConnection,
    dataset_urn: str,
    *,
    observed_at_ms: int,
    last_updated_ms: int,
    scenario: str,
) -> None:
    """Record when a dataset last changed, as DataHub's operation aspect.

    ``timestampMillis`` is when the observation was made; ``lastUpdatedTimestamp``
    is when the data itself last changed. Freshness is the gap between them, and
    backdating the second is what makes the table look stale.
    """
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=dataset_urn,
            aspect=OperationClass(
                timestampMillis=observed_at_ms,
                operationType=OperationTypeClass.UPDATE,
                lastUpdatedTimestamp=last_updated_ms,
                actor=SCENARIO_ACTOR,
                customProperties={SCENARIO_PROPERTY: scenario},
            ),
        )
    )


def plant_stale_source(
    conn: DataHubConnection,
    *,
    lag_hours: float = DEFAULT_LAG_HOURS,
    now_ms: int | None = None,
) -> ScenarioResult:
    """Make the label-bearing source table look like it stopped refreshing.

    Args:
        conn: A connection with write credentials.
        lag_hours: How long ago the table last changed. Must exceed the scan's
            freshness SLA for the detector to fire.
        now_ms: The instant to measure staleness from. Defaults to now.

    Returns:
        What was written, including the backdated timestamp.

    Raises:
        ValueError: The lag is not positive, which would plant nothing.
    """
    if lag_hours <= 0:
        raise ValueError(f"lag_hours must be positive to plant staleness, got {lag_hours}")

    observed_at = now_ms if now_ms is not None else _now_ms()
    last_updated = observed_at - int(lag_hours * _MS_PER_HOUR)
    dataset_urn = str(spec.source_table_urn())

    _emit_operation(
        conn,
        dataset_urn,
        observed_at_ms=observed_at,
        last_updated_ms=last_updated,
        scenario=STALE_SOURCE,
    )
    return ScenarioResult(
        name=STALE_SOURCE,
        dataset_urn=dataset_urn,
        last_updated_ms=last_updated,
        lag_hours=lag_hours,
    )


def revert_stale_source(
    conn: DataHubConnection,
    *,
    now_ms: int | None = None,
) -> ScenarioResult:
    """Announce that the source table just refreshed, clearing the staleness.

    Emits a newer operation event rather than deleting the planted one, because
    the aspect is a timeseries and readers take the latest event. This is exactly
    what a recovered pipeline would emit.

    Returns:
        What was written. The lag is zero.
    """
    observed_at = now_ms if now_ms is not None else _now_ms()
    dataset_urn = str(spec.source_table_urn())

    _emit_operation(
        conn,
        dataset_urn,
        observed_at_ms=observed_at,
        last_updated_ms=observed_at,
        scenario=STALE_SOURCE,
    )
    return ScenarioResult(
        name=STALE_SOURCE,
        dataset_urn=dataset_urn,
        last_updated_ms=observed_at,
        lag_hours=0.0,
    )


@dataclass(frozen=True)
class SchemaDriftResult:
    """What the schema-drift scenario did to the graph, so a caller can report it."""

    name: str
    dataset_urn: str
    field_paths: tuple[str, ...]
    """The columns the feature table now carries, after the change."""


def _emit_feature_schema(
    conn: DataHubConnection,
    columns: tuple[spec.Column, ...],
    *,
    scenario: str | None,
) -> None:
    """Replace the feature table's live schema with the given columns.

    ``schemaMetadata`` is a versioned aspect, so re-emitting it replaces the
    schema outright: planting the drift and reverting it are the same operation
    with a different column list, and neither leaves a stale column behind.

    The scenario marker goes in the dataset's custom properties when planting and
    is cleared on revert, so a reader can tell a planted change from a real one
    (seed/CLAUDE.md rule 5).
    """
    dataset = Dataset(
        platform=spec.WAREHOUSE_PLATFORM,
        name=spec.feature_table_dataset_urn().name,
        description="Engineered features for the credit risk model.",
        schema=[(c.name, c.native_type, c.description) for c in columns],
        custom_properties={SCENARIO_PROPERTY: scenario} if scenario else {},
    )
    conn.client.entities.upsert(dataset)


def plant_schema_drift(conn: DataHubConnection) -> SchemaDriftResult:
    """Change the feature table's live schema so it no longer matches training.

    Retypes one feature, drops another, and adds a third, none of them a column
    the leakage traversal depends on. The training run still carries the original
    schema fingerprint, so the schema-drift detector sees the divergence.

    Returns:
        What was written, including the drifted column set.
    """
    columns = _drifted_columns()
    _emit_feature_schema(conn, columns, scenario=SCHEMA_DRIFT)
    return SchemaDriftResult(
        name=SCHEMA_DRIFT,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        field_paths=tuple(c.name for c in columns),
    )


def revert_schema_drift(conn: DataHubConnection) -> SchemaDriftResult:
    """Restore the feature table's schema to the one the model was trained on.

    Re-emits the original :data:`~modelguard.seed.graph_spec.FEATURE_COLUMNS`, so a
    subsequent scan finds no drift.

    Returns:
        What was written. The columns match the training-time snapshot.
    """
    _emit_feature_schema(conn, spec.FEATURE_COLUMNS, scenario=None)
    return SchemaDriftResult(
        name=SCHEMA_DRIFT,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        field_paths=tuple(c.name for c in spec.FEATURE_COLUMNS),
    )


@dataclass(frozen=True)
class LeakageResult:
    """What the leakage scenario did to the feature table's column lineage."""

    name: str
    dataset_urn: str
    feature_column: str
    """The feature whose derivation the scenario turned on or off."""
    upstream_columns: tuple[str, ...]
    """What that feature now declares itself derived from. Empty after a revert."""
    leaking: bool
    """Whether the graph currently reaches the label from a model feature."""


def _set_column_lineage(conn: DataHubConnection, mapping: dict[str, list[str]]) -> None:
    """Replace the feature table's column-level lineage with exactly ``mapping``.

    ``client.lineage.add_lineage`` cannot be used to undo an edge: it builds a
    ``DatasetPatchBuilder`` and calls ``add_fine_grained_upstream_lineage``, which
    is an *additive* patch, so re-adding a reduced mapping leaves the removed edge
    in place. Setting the fine-grained list outright is the only way back, and it
    leaves the table-level upstream edge untouched (verified against a live GMS).

    The mapping is turned into aspect objects by the SDK's own
    ``parse_cll_mapping``, the same helper ``add_lineage`` uses, so what this
    writes is byte-for-byte what the seeder would have written.

    Why there is no scenario marker
    -------------------------------
    seed/CLAUDE.md rule 5 asks every scenario to stamp ``modelguard.scenario``
    somewhere a reader can find it. This one cannot, and the reason is worth
    keeping. ``upstreamLineage`` has no ``customProperties``; the dataset's own
    belong to the schema-drift scenario, which clears them on revert, so sharing
    them would have the two scenarios erase each other. The remaining candidate,
    the edge's ``transformOperation``, is part of what GMS keys a fine-grained
    edge on: a marked edge and the seeder's unmarked one are two *different*
    edges, so the next ``modelguard-seed`` adds its own alongside and the graph
    grows a duplicate. That was caught by the Week 1 gate's byte-for-byte
    idempotency test, not by reasoning, which is why the test exists.

    Nothing is lost. Unlike a stale timestamp or a drifted schema, this state is
    not ambiguous: the leaking edge is either in the graph or it is not, and the
    leak is the *seeded baseline* (D-032) rather than an anomaly planted on top
    of it, so there is no planted-versus-real question to answer.
    """
    edges: list[FineGrainedLineageClass] = parse_cll_mapping(
        upstream=str(spec.source_table_urn()),
        downstream=str(spec.feature_table_dataset_urn()),
        cll_mapping=mapping,
    )
    builder = DatasetPatchBuilder(str(spec.feature_table_dataset_urn()))
    builder.set_fine_grained_lineages(edges)
    conn.client.entities.update(builder)


def plant_leakage(conn: DataHubConnection) -> LeakageResult:
    """Declare the leaking feature to derive from the label column.

    Restores the seeded state: ``prior_default_flag`` derives from
    ``default_status``, so a model scan finds target leakage. Planting is
    idempotent, and it is what the seeder already does, so this is only needed to
    undo a :func:`revert_leakage`.

    Returns:
        What the feature table's lineage now says.
    """
    _set_column_lineage(conn, dict(spec.COLUMN_LINEAGE))
    return LeakageResult(
        name=TARGET_LEAKAGE,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column=spec.LEAKAGE_FEATURE,
        upstream_columns=tuple(spec.COLUMN_LINEAGE[spec.LEAKAGE_FEATURE]),
        leaking=True,
    )


def revert_leakage(conn: DataHubConnection) -> LeakageResult:
    """Cut the leaking feature's derivation from the label, leaving the rest.

    This is the negative control for the flagship detector, and it is deliberately
    the narrowest possible edit: the feature still exists, the model still consumes
    it, and the label is still declared. Only the derivation path is gone. So a
    scan going quiet afterwards isolates the one signal the detector keys on,
    rather than proving the easier thing that a feature nobody consumes is safe.

    Reverting is how a team actually fixes leakage: the feature gets rebuilt from
    data available before the outcome is known, and stops descending from the label.

    Returns:
        What the feature table's lineage now says. ``upstream_columns`` is empty.
    """
    clean = {
        column: upstreams
        for column, upstreams in spec.COLUMN_LINEAGE.items()
        if column != spec.LEAKAGE_FEATURE
    }
    _set_column_lineage(conn, clean)
    return LeakageResult(
        name=TARGET_LEAKAGE,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column=spec.LEAKAGE_FEATURE,
        upstream_columns=(),
        leaking=False,
    )


#: The column the multi-path scenario adds to the raw table: a backfilled copy of
#: the label, declared a label itself. Chosen over a second unrelated marked
#: column because it is what actually happens in a warehouse (somebody
#: backfills the outcome into a second column and nobody removes it), and
#: because it makes the finding genuinely two-pathed rather than two findings.
BACKUP_LABEL_COLUMN = spec.Column(
    "default_status_backup",
    "BOOLEAN",
    "A backfilled copy of default_status. Also the label, and also upstream of "
    "prior_default_flag: the second path.",
)


def _emit_source_schema(
    conn: DataHubConnection,
    columns: tuple[spec.Column, ...],
    *,
    scenario: str | None,
) -> None:
    """Replace the raw table's live schema with the given columns.

    The counterpart of :func:`_emit_feature_schema`, on the other table, and the
    same reasoning applies: ``schemaMetadata`` is versioned, so planting and
    reverting are one operation with two column lists. The description matches
    what :func:`~modelguard.seed.seed_ml_graph.seed_warehouse_tables` writes, so
    a revert restores the seeded dataset exactly rather than a near copy of it.

    The scenario marker goes in this dataset's custom properties, which are free:
    the drift scenario owns the *feature* table's, and the two never write to the
    same aspect.
    """
    dataset = Dataset(
        platform=spec.WAREHOUSE_PLATFORM,
        name=spec.source_table_urn().name,
        description="Raw loan applications. Holds the default_status label column.",
        schema=[(c.name, c.native_type, c.description) for c in columns],
        custom_properties={SCENARIO_PROPERTY: scenario} if scenario else {},
    )
    conn.client.entities.upsert(dataset)


def plant_second_leak_path(conn: DataHubConnection, *, keep_first: bool = True) -> LeakageResult:
    """Give the leaking feature a second, independent derivation from a label.

    Adds :data:`BACKUP_LABEL_COLUMN` to the raw table, declares it a label with
    the same glossary term the seeder uses, and wires ``prior_default_flag`` to
    derive from it as well as from ``default_status``.

    Args:
        conn: A connection with write credentials.
        keep_first: Whether the original derivation survives. False plants the
            state a team lands in when they cut the path the incident quoted and
            stopped: the feature still descends from a declared label, by the
            other path, and the finding must still stand. That state is the whole
            reason this scenario exists, so it is a state the scenario can plant
            rather than something a caller has to assemble.

    Returns:
        What the feature table's lineage now says.
    """
    _emit_source_schema(
        conn, (*spec.SOURCE_COLUMNS, BACKUP_LABEL_COLUMN), scenario=SECOND_LEAK_PATH
    )
    ensure_term(conn, spec.LABEL_TERM_URN, spec.LABEL_TERM_NAME, spec.LABEL_TERM_DEFINITION)
    add_term(conn, str(spec.source_column_urn(BACKUP_LABEL_COLUMN.name)), spec.LABEL_TERM_URN)

    upstreams = (
        [spec.LABEL_SOURCE_COLUMN, BACKUP_LABEL_COLUMN.name]
        if keep_first
        else [BACKUP_LABEL_COLUMN.name]
    )
    _set_column_lineage(conn, {**spec.COLUMN_LINEAGE, spec.LEAKAGE_FEATURE: upstreams})
    return LeakageResult(
        name=SECOND_LEAK_PATH,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column=spec.LEAKAGE_FEATURE,
        upstream_columns=tuple(upstreams),
        leaking=True,
    )


def revert_second_leak_path(conn: DataHubConnection) -> LeakageResult:
    """Remove the second derivation and the column it came from.

    Undeclares the backup column before dropping it from the schema, in that
    order: a glossary term written on a ``schemaField`` outlives the column's
    removal from ``schemaMetadata``, so dropping the column first would leave a
    declared label behind on a column nobody can see. The seeded single-path
    leak is restored, because that is the baseline the demo expects (D-032).
    """
    remove_term(conn, str(spec.source_column_urn(BACKUP_LABEL_COLUMN.name)), spec.LABEL_TERM_URN)
    _emit_source_schema(conn, spec.SOURCE_COLUMNS, scenario=None)
    _set_column_lineage(conn, dict(spec.COLUMN_LINEAGE))
    return LeakageResult(
        name=SECOND_LEAK_PATH,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column=spec.LEAKAGE_FEATURE,
        upstream_columns=(spec.LABEL_SOURCE_COLUMN,),
        leaking=True,
    )


#: The seeded lineage with the flagship leak's own edge removed: the baseline
#: T-09's two confusable-negative scenarios build on. Spreading
#: ``spec.COLUMN_LINEAGE`` directly, as :func:`plant_leakage` does, would
#: silently reintroduce the flagship leak into a graph shape that is supposed
#: to be about a completely different column, because ``_set_column_lineage``
#: replaces the whole mapping rather than merging into it. Built the same way
#: :func:`revert_leakage` builds its own ``clean`` dict.
_LEAK_FREE_COLUMN_LINEAGE: dict[str, list[str]] = {
    column: upstreams
    for column, upstreams in spec.COLUMN_LINEAGE.items()
    if column != spec.LEAKAGE_FEATURE
}

#: The common-ancestor scenario's column: a second declared label on the
#: feature table, deriving from ``income``, the same source column the model's
#: own ``applicant_income`` feature already derives from. Neither this column
#: nor ``applicant_income`` descends from the other; both are the ancestor's
#: children, which an upstream-only walk must never confuse with an ancestor
#: relationship (T-09).
COMMON_ANCESTOR_LABEL = spec.Column(
    "income_verified_label",
    "BOOLEAN",
    "Declared a label for this scenario only. Derives from income, the same "
    "column applicant_income derives from; neither descends from the other.",
)


def plant_common_ancestor_label(conn: DataHubConnection) -> LeakageResult:
    """Give a declared label the same upstream column a clean feature has.

    Proves the walk is upstream-only and never mistakes a shared ancestor for
    a derivation between siblings: on a real catalog, a feature and a label
    both computed from one raw column is common and is not leakage, and
    nothing before this scenario tested that a walk agrees (09 section 2.2).

    The flagship leak is cut in the same write: this scenario is about
    applicant_income, and a model whose prior_default_flag still derives from
    the label would still legitimately be flagged, which is not what this
    scenario means to test.
    """
    _emit_feature_schema(
        conn, (*spec.FEATURE_COLUMNS, COMMON_ANCESTOR_LABEL), scenario=COMMON_ANCESTOR
    )
    ensure_term(conn, spec.LABEL_TERM_URN, spec.LABEL_TERM_NAME, spec.LABEL_TERM_DEFINITION)
    add_term(conn, str(spec.feature_column_urn(COMMON_ANCESTOR_LABEL.name)), spec.LABEL_TERM_URN)
    _set_column_lineage(conn, {**_LEAK_FREE_COLUMN_LINEAGE, COMMON_ANCESTOR_LABEL.name: ["income"]})
    return LeakageResult(
        name=COMMON_ANCESTOR,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column=COMMON_ANCESTOR_LABEL.name,
        upstream_columns=("income",),
        leaking=False,
    )


def revert_common_ancestor_label(conn: DataHubConnection) -> LeakageResult:
    """Undeclare and drop the sibling label, in that order (see revert_second_leak_path)."""
    remove_term(conn, str(spec.feature_column_urn(COMMON_ANCESTOR_LABEL.name)), spec.LABEL_TERM_URN)
    _emit_feature_schema(conn, spec.FEATURE_COLUMNS, scenario=None)
    _set_column_lineage(conn, dict(spec.COLUMN_LINEAGE))
    return LeakageResult(
        name=COMMON_ANCESTOR,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column=COMMON_ANCESTOR_LABEL.name,
        upstream_columns=(),
        leaking=False,
    )


#: The label-lookalike scenario's column: named the way a hasty detector might
#: match on, declared a label nowhere. It feeds ``applicant_income`` in place
#: of ``income``, so the only thing that changed from the clean baseline is
#: the upstream column's name (T-09).
LOOKALIKE_COLUMN = spec.Column(
    "target_indicator",
    "BOOLEAN",
    "Named like a label. Not declared one anywhere: no glossary term, no tag. "
    "Proves detection matches the declared term, never the column's name.",
)


def plant_label_lookalike(conn: DataHubConnection) -> LeakageResult:
    """Feed a feature from a suggestively named column that carries no term.

    Proves detection keys on the declared glossary term, not on a name a
    heuristic might key on instead: a detector doing the latter would flag
    ``applicant_income`` here, and nothing before this scenario tested that it
    does not (09 section 2.2).

    The flagship leak is cut in the same write, for the same reason
    :func:`plant_common_ancestor_label` cuts it: this scenario is about
    applicant_income, not about prior_default_flag still legitimately leaking.
    """
    _emit_source_schema(conn, (*spec.SOURCE_COLUMNS, LOOKALIKE_COLUMN), scenario=LABEL_LOOKALIKE)
    _set_column_lineage(
        conn, {**_LEAK_FREE_COLUMN_LINEAGE, "applicant_income": [LOOKALIKE_COLUMN.name]}
    )
    return LeakageResult(
        name=LABEL_LOOKALIKE,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column="applicant_income",
        upstream_columns=(LOOKALIKE_COLUMN.name,),
        leaking=False,
    )


def revert_label_lookalike(conn: DataHubConnection) -> LeakageResult:
    """Restore applicant_income's derivation from income and drop the lookalike column."""
    _emit_source_schema(conn, spec.SOURCE_COLUMNS, scenario=None)
    _set_column_lineage(conn, dict(spec.COLUMN_LINEAGE))
    return LeakageResult(
        name=LABEL_LOOKALIKE,
        dataset_urn=str(spec.feature_table_dataset_urn()),
        feature_column="applicant_income",
        upstream_columns=("income",),
        leaking=False,
    )


@dataclass(frozen=True)
class GovernanceResult:
    """What a governance scenario declared, so a caller can report or undo it."""

    name: str
    resource_urn: str
    """The column or dataset the declaration was written on."""
    declared: bool
    """Whether the declaration is currently in place. False after a revert."""
    detail: str
    """The classification or note now recorded, empty after a revert."""


def _set_sensitive_tag(conn: DataHubConnection, *, tagged: bool) -> GovernanceResult:
    """Apply or remove the demo classification on the source table's income column.

    Written on the ``schemaField`` entity's own ``globalTags``, which is the route
    an ingestion source or a classifier takes. The detector also reads the parent
    dataset's ``editableSchemaMetadata``, which is the route the UI takes; either
    is enough, and using the schemaField here keeps the scenario from colliding
    with anything a human does in the UI while the demo runs.

    Reverting writes the aspect with an empty tag list rather than deleting it:
    the same shape as the leakage revert, and positive evidence that the
    classification is gone rather than an absence a reader has to interpret.
    """
    column_urn = str(spec.source_column_urn(SENSITIVE_SOURCE_COLUMN))
    tags = [TagAssociationClass(tag=SENSITIVE_TAG_URN)] if tagged else []
    conn.graph.emit(
        MetadataChangeProposalWrapper(entityUrn=column_urn, aspect=GlobalTagsClass(tags=tags))
    )
    return GovernanceResult(
        name=SENSITIVE_SOURCE,
        resource_urn=column_urn,
        declared=tagged,
        detail=SENSITIVE_TAG_URN if tagged else "",
    )


def plant_sensitive_source(conn: DataHubConnection) -> GovernanceResult:
    """Classify the column a model feature derives from as sensitive.

    The demo's governance failure: nothing about the model changes, and nothing
    breaks. Somebody classifies a column three hops upstream, and a feature the
    live model trains on turns out to descend from it.

    The tag itself is created first, so the classification a judge clicks into in
    the UI has a name and a description rather than being a bare URN.
    """
    ensure_tag(conn, SENSITIVE_TAG_NAME, SENSITIVE_TAG_DESCRIPTION)
    return _set_sensitive_tag(conn, tagged=True)


def revert_sensitive_source(conn: DataHubConnection) -> GovernanceResult:
    """Remove the classification, leaving the column, the feature and the model.

    The negative control: the derivation still exists and the model still
    consumes the feature. Only the organization's declaration is gone, so a scan
    going quiet isolates the one signal this detector keys on.
    """
    return _set_sensitive_tag(conn, tagged=False)


def _set_deprecation(conn: DataHubConnection, *, deprecated: bool) -> GovernanceResult:
    """Mark the model's training input deprecated, or lift the deprecation.

    Lifting writes ``deprecated=False`` rather than removing the aspect, because
    that is how DataHub itself records a deprecation being withdrawn, and it is
    the state the detector is written to treat as healthy.
    """
    dataset_urn = str(spec.feature_table_dataset_urn())
    conn.graph.emit(
        MetadataChangeProposalWrapper(
            entityUrn=dataset_urn,
            aspect=DeprecationClass(
                deprecated=deprecated,
                note=DEPRECATION_NOTE if deprecated else "",
                actor=SCENARIO_ACTOR,
            ),
        )
    )
    return GovernanceResult(
        name=DEPRECATED_INPUT,
        resource_urn=dataset_urn,
        declared=deprecated,
        detail=DEPRECATION_NOTE if deprecated else "",
    )


def plant_deprecated_input(conn: DataHubConnection) -> GovernanceResult:
    """Mark the table the model trains on as deprecated by its owners."""
    return _set_deprecation(conn, deprecated=True)


def revert_deprecated_input(conn: DataHubConnection) -> GovernanceResult:
    """Withdraw the deprecation, the way a team that changed its mind would."""
    return _set_deprecation(conn, deprecated=False)


def _set_model_features(conn: DataHubConnection, *, declared: bool) -> GovernanceResult:
    """Drop the model's declared features, or put them back.

    This is not a failure somebody plants; it is what an ordinary ingestion run
    does. DataHub's mlflow source upserts the whole ``mlModelProperties`` aspect
    and the features ``modelguard link`` attached are gone with it (verified live,
    D-074). Reproducing it here is what gives the degraded mode (T-07) a graph to
    be measured on: with the features present the column-level detectors answer,
    and with them absent they cannot, which is the exact boundary the mode is
    gated on.

    Read-merge-emit like every other write in this project: only ``mlFeatures``
    changes, so the training runs and the deployment that decide severity survive.
    """
    model_urn = str(spec.model_urn())
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None:
        raise RuntimeError(
            f"{model_urn} has no mlModelProperties; run modelguard-seed before this scenario."
        )
    properties.mlFeatures = (
        [str(spec.feature_urn(name)) for name in spec.MODEL_FEATURES] if declared else []
    )
    conn.graph.emit(MetadataChangeProposalWrapper(entityUrn=model_urn, aspect=properties))
    return GovernanceResult(
        name=DELINKED_MODEL,
        resource_urn=model_urn,
        declared=not declared,
        detail=(
            "" if declared else "the model declares no features, so only table-level checks can run"
        ),
    )


def plant_delinked_model(conn: DataHubConnection) -> GovernanceResult:
    """Strip the model's features, the way the next mlflow ingest will."""
    return _set_model_features(conn, declared=False)


def revert_delinked_model(conn: DataHubConnection) -> GovernanceResult:
    """Re-declare the model's features, the way ``modelguard link --all`` does."""
    return _set_model_features(conn, declared=True)


def _drifted_columns() -> tuple[spec.Column, ...]:
    """Return the feature table's columns after the planted drift.

    Derived from the seeded :data:`~modelguard.seed.graph_spec.FEATURE_COLUMNS` so
    the scenario tracks the seed: one column retyped, one dropped, one added.
    """
    columns: list[spec.Column] = []
    for column in spec.FEATURE_COLUMNS:
        if column.name == _DROPPED_COLUMN:
            continue
        if column.name == _RETYPED_COLUMN:
            columns.append(spec.Column(column.name, _RETYPED_TO, column.description))
            continue
        columns.append(column)
    columns.append(_ADDED_COLUMN)
    return tuple(columns)


#: Printed after any scenario whose effect reaches a detector through an index
#: rather than synchronously. Schema drift is exempt: it writes ``schemaMetadata``,
#: a versioned aspect GMS serves straight back.
_INDEXING_NOTE = (
    "[dim]DataHub indexes this asynchronously (about 3s locally); a scan run "
    "immediately may still report the previous state.[/dim]"
)


def main() -> None:
    """Entry point for the ``modelguard-scenario`` command."""
    import argparse

    from rich.console import Console

    from modelguard.client import DataHubConnectionError, connect

    parser = argparse.ArgumentParser(description="Plant or revert a failure ModelGuard detects.")
    parser.add_argument(
        "--scenario",
        choices=[STALE_SOURCE, SCHEMA_DRIFT, TARGET_LEAKAGE, SENSITIVE_SOURCE, DEPRECATED_INPUT],
        default=STALE_SOURCE,
        help=f"Which failure to plant (default: {STALE_SOURCE}).",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Undo the scenario instead of planting it.",
    )
    parser.add_argument(
        "--lag-hours",
        type=float,
        default=DEFAULT_LAG_HOURS,
        help=f"For {STALE_SOURCE}: how stale to make the table (default: {DEFAULT_LAG_HOURS}).",
    )
    args = parser.parse_args()

    console = Console(soft_wrap=True)
    try:
        conn = connect()
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if args.scenario == TARGET_LEAKAGE:
        leak = revert_leakage(conn) if args.revert else plant_leakage(conn)
        verb = "Reverted" if args.revert else "Planted"
        color = "green" if args.revert else "yellow"
        console.print(f"[{color}]{verb}[/{color}] {leak.name} on {leak.dataset_urn}")
        if args.revert:
            console.print(
                f"{leak.feature_column} no longer derives from the label; "
                "a model scan finds no leakage."
            )
        else:
            console.print(
                f"{leak.feature_column} derives from "
                f"{', '.join(leak.upstream_columns)}, which is the label."
            )
        console.print(_INDEXING_NOTE)
        return

    if args.scenario in (SENSITIVE_SOURCE, DEPRECATED_INPUT):
        if args.scenario == SENSITIVE_SOURCE:
            declared = (
                revert_sensitive_source(conn) if args.revert else plant_sensitive_source(conn)
            )
        else:
            declared = (
                revert_deprecated_input(conn) if args.revert else plant_deprecated_input(conn)
            )

        verb = "Reverted" if args.revert else "Planted"
        color = "green" if args.revert else "yellow"
        console.print(f"[{color}]{verb}[/{color}] {declared.name} on {declared.resource_urn}")
        console.print(
            f"Now declared: {declared.detail}"
            if declared.declared
            else "The declaration is withdrawn; a scan of the model finds nothing."
        )
        if declared.name == SENSITIVE_SOURCE:
            console.print(
                "[dim]The sensitive-source check only runs once a classification is "
                f"configured. Set MODELGUARD_SENSITIVE_TAG_URNS={SENSITIVE_TAG_URN} "
                "in .env, or a scan reports the check as not evaluated.[/dim]"
            )
            console.print(_INDEXING_NOTE)
        return

    if args.scenario == SCHEMA_DRIFT:
        drift = revert_schema_drift(conn) if args.revert else plant_schema_drift(conn)
        verb = "Reverted" if args.revert else "Planted"
        color = "green" if args.revert else "yellow"
        console.print(f"[{color}]{verb}[/{color}] {drift.name} on {drift.dataset_urn}")
        if args.revert:
            console.print("The feature table matches the training schema again; a scan is clean.")
        else:
            console.print(f"The feature table now carries: {', '.join(drift.field_paths)}.")
        return

    # The freshness scenario writes the operation aspect, which is a timeseries and
    # therefore reaches a reader through Kafka and Elasticsearch rather than
    # synchronously. Measured at roughly three seconds on a local Quickstart, in
    # both directions. Saying so matters: a scan run in that window reports the
    # value from before the change, which reads as a broken tool rather than as a
    # graph still catching up, and this is the demo's own command sequence.
    if args.revert:
        result = revert_stale_source(conn)
        console.print(f"[green]Reverted[/green] {result.name} on {result.dataset_urn}")
        console.print("The table now reports a fresh update; a scan will find nothing.")
        console.print(_INDEXING_NOTE)
        return

    result = plant_stale_source(conn, lag_hours=args.lag_hours)
    console.print(f"[yellow]Planted[/yellow] {result.name} on {result.dataset_urn}")
    console.print(f"The table now claims it last changed {result.lag_hours:.1f} hours ago.")
    console.print(_INDEXING_NOTE)
