"""Guard coverage over time, recorded on Janus's own entity (T-15).

:mod:`janus.detect.guard_coverage` computes what fraction of a catalog's
models each check can run against. On its own that figure has the same problem a
trust score has: 34% is neither good nor bad until you know it was 11% in March.
The direction is the signal, and here the direction is the only thing anybody can
act on, because the number is a measure of how much declaring somebody has done
rather than of how healthy the catalog is.

So the sweep appends one capped entry, exactly the way
:mod:`janus.writeback.trust_history` appends a score. Same storage decision
(a MULTIPLE structured property, because a new timeseries aspect is a change to
DataHub's own metadata model and belongs in the RFC lane), same reason for the
cap, same pipe-separated line that is readable straight off the property in the
UI without a renderer.

Where it hangs
--------------
On Janus's own ``dataFlow``, the entity T-04 already creates and hangs every
scan run under. A catalog-level figure is a fact about the whole graph, so there
is no dataset or model it belongs to, and inventing a synthetic entity to hold it
would put a made-up asset in somebody's catalog. The agent is already in the
graph it guards; this is a property of the agent's work, recorded on the agent.

That a ``dataFlow`` can carry a structured property is `[verified]` against a live
GMS 1.5.0.6, not assumed: the assignment was written and read back before this
module was written.

Format
------
``<ISO-8601 UTC>|<run_id>|<models>|<covered>/<total>|<check>=<covered>/<total>,...``

The per-check field carries both halves rather than a percentage, for the reason
the history stores a score and not a grade: a rounded rate cannot be re-added
into a catalog figure, and a reader comparing two entries needs to see whether a
rise came from covering more models or from scanning fewer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from janus.client import DataHubConnection
from janus.detect.guard_coverage import CatalogCoverage, CheckCoverage
from janus.writeback.process_instance import agent_flow_urn
from janus.writeback.properties import (
    COVERAGE_HISTORY,
    StructuredPropertyValue,
    assign_properties,
    define_properties,
    read_properties,
)

#: How many sweeps the history keeps. Matches
#: :data:`~janus.writeback.trust_history.HISTORY_LIMIT` on purpose: the two
#: trends are read side by side, and a coverage line that stretched twice as far
#: back as the score beside it would invite a comparison across different spans.
HISTORY_LIMIT = 20

#: Field separator, as in the trust history. The per-check field is itself a
#: comma-separated list, so the outer separator has to be something else.
SEPARATOR = "|"


@dataclass(frozen=True)
class CoverageEntry:
    """One catalog sweep, as recorded in the history."""

    recorded_at: str
    """ISO-8601 UTC to the second. A string because that is what was stored."""
    run_id: str
    models: int
    covered: int
    """(model, check) pairs that could be evaluated."""
    total: int
    """(model, check) pairs the sweep asked about."""
    per_check: tuple[CheckCoverage, ...]

    @property
    def rate(self) -> float:
        """The headline figure this entry recorded."""
        return self.covered / self.total if self.total else 0.0

    def render(self) -> str:
        """Render the entry back to its stored form."""
        return SEPARATOR.join(
            [
                self.recorded_at,
                self.run_id,
                str(self.models),
                f"{self.covered}/{self.total}",
                ",".join(
                    f"{check.check}={check.covered}/{check.total}" for check in self.per_check
                ),
            ]
        )


def _parse_fraction(raw: str) -> tuple[int, int] | None:
    """Parse ``covered/total``, or None when it is not one."""
    covered, _, total = raw.partition("/")
    try:
        return int(covered), int(total)
    except ValueError:
        return None


def parse_entry(raw: str) -> CoverageEntry | None:
    """Parse one stored entry, or None when it is not one.

    Anything unparseable is dropped rather than raised on, for the reason the
    trust history drops it: the property is editable by anyone with write access,
    and a hand-edited line must cost a missing point in a trend rather than a
    failed sweep. Nothing decides anything from this history.
    """
    parts = raw.split(SEPARATOR)
    if len(parts) != 5:
        return None
    recorded_at, run_id, models, headline, checks = parts

    fraction = _parse_fraction(headline)
    if fraction is None:
        return None
    covered, total = fraction

    try:
        model_count = int(models)
    except ValueError:
        return None

    per_check: list[CheckCoverage] = []
    for item in checks.split(","):
        if not item:
            continue
        name, _, rendered = item.rpartition("=")
        parsed = _parse_fraction(rendered)
        if not name or parsed is None:
            return None
        per_check.append(CheckCoverage(check=name, covered=parsed[0], total=parsed[1]))

    return CoverageEntry(
        recorded_at=recorded_at,
        run_id=run_id,
        models=model_count,
        covered=covered,
        total=total,
        per_check=tuple(per_check),
    )


def read_history(conn: DataHubConnection) -> tuple[CoverageEntry, ...]:
    """Return the recorded coverage history, oldest first.

    Empty when no sweep has ever run against this graph, which is a different
    fact from a catalog that scored zero, and is why an empty tuple is returned
    rather than a zeroed entry.
    """
    stored = read_properties(conn, agent_flow_urn()).get(COVERAGE_HISTORY, [])
    entries = (parse_entry(str(value)) for value in stored)
    return tuple(entry for entry in entries if entry is not None)


def project_history(
    conn: DataHubConnection,
    coverage: CatalogCoverage,
    run_id: str,
    *,
    now: datetime | None = None,
) -> tuple[CoverageEntry, ...]:
    """Return what the history will be once this sweep lands. Reads, never writes.

    Idempotent per run, like every other write in this package: a rerun under the
    same ``run_id`` replaces that run's entry instead of appending a second one.

    Args:
        conn: An open connection.
        coverage: What the sweep measured.
        run_id: The sweep's identifier, which is also the idempotency key.
        now: The instant to record. Injected so a test can fix it.

    Returns:
        The history including this sweep, oldest first, already capped.
    """
    recorded_at = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = CoverageEntry(
        recorded_at=recorded_at,
        run_id=run_id,
        models=coverage.models,
        covered=coverage.covered_checks,
        total=coverage.total_checks,
        per_check=coverage.checks,
    )

    kept = [existing for existing in read_history(conn) if existing.run_id != run_id]
    kept.append(entry)
    return tuple(kept[-HISTORY_LIMIT:])


def write_history(conn: DataHubConnection, history: Sequence[CoverageEntry]) -> None:
    """Persist a history exactly as given, on the agent's own flow.

    Defines the properties first, as ``link`` does: this is the one write path a
    graph can reach without a scan having run, so the definition it assigns
    against may not exist yet. Definition is keyed by the property URN, so
    re-emitting an unchanged declaration is a no-op.
    """
    define_properties(conn)
    values: list[StructuredPropertyValue] = [entry.render() for entry in history]
    assign_properties(conn, agent_flow_urn(), {COVERAGE_HISTORY: values})


def append_entry(
    conn: DataHubConnection,
    coverage: CatalogCoverage,
    run_id: str,
    *,
    now: datetime | None = None,
) -> tuple[CoverageEntry, ...]:
    """Record this sweep's coverage, and return the history after it."""
    history = project_history(conn, coverage, run_id, now=now)
    write_history(conn, history)
    return history
