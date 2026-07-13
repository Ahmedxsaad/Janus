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
from datahub.metadata.schema_classes import OperationClass, OperationTypeClass

from modelguard.client import DataHubConnection
from modelguard.seed import graph_spec as spec

#: Names the scenario in the operation aspect's custom properties, so a reader of
#: the graph can tell a planted failure from a real one.
SCENARIO_PROPERTY = "modelguard.scenario"

#: The one scenario Phase 1 needs: the source table stops refreshing.
STALE_SOURCE = "stale-source-table"

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


def main() -> None:
    """Entry point for the ``modelguard-scenario`` command."""
    import argparse

    from rich.console import Console

    from modelguard.client import DataHubConnectionError, connect

    parser = argparse.ArgumentParser(
        description="Plant or revert the stale-source failure ModelGuard detects."
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Announce a refresh instead of planting staleness.",
    )
    parser.add_argument(
        "--lag-hours",
        type=float,
        default=DEFAULT_LAG_HOURS,
        help=f"How stale to make the table (default: {DEFAULT_LAG_HOURS}).",
    )
    args = parser.parse_args()

    console = Console(soft_wrap=True)
    try:
        conn = connect()
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if args.revert:
        result = revert_stale_source(conn)
        console.print(f"[green]Reverted[/green] {result.name} on {result.dataset_urn}")
        console.print("The table now reports a fresh update; a scan will find nothing.")
        return

    result = plant_stale_source(conn, lag_hours=args.lag_hours)
    console.print(f"[yellow]Planted[/yellow] {result.name} on {result.dataset_urn}")
    console.print(f"The table now claims it last changed {result.lag_hours:.1f} hours ago.")
