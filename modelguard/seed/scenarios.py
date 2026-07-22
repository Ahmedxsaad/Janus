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
    FineGrainedLineageClass,
    OperationClass,
    OperationTypeClass,
)
from datahub.sdk.dataset import Dataset, parse_cll_mapping
from datahub.specific.dataset import DatasetPatchBuilder

from modelguard.client import DataHubConnection
from modelguard.seed import graph_spec as spec

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
        choices=[STALE_SOURCE, SCHEMA_DRIFT, TARGET_LEAKAGE],
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
