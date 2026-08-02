"""A model's trust score over time, so the number means something.

``modelguard.trust_score`` is a single structured property, overwritten by every
scan. On its own it is close to useless to the person who has to act on it: 82
out of 100 is neither good nor bad until you know it was 95 last Tuesday. The
direction is the signal, and the direction is what says somebody shipped
something.

So each scan that scores a model also appends one line to
``modelguard.trust_history``, oldest first, and the list is capped. This is a
recent trend a human reads on the model in the DataHub UI, not an audit log:
every entry also exists as the ``modelguard.run_id`` stamped on that run's
writes, which is where a full audit trail lives.

Why a structured property and not a timeseries aspect
-----------------------------------------------------
A timeseries aspect is what this would be in a world where ModelGuard could add
one, and it would give DataHub's UI a real chart. It cannot: a new timeseries
aspect is a change to DataHub's own metadata model, which belongs in the RFC lane
(``mcp_ext/RFC-ml-incidents.md``) rather than in an agent that composes what is
already shipped. A multi-valued structured property is the shipped feature that
holds an ordered list of strings, so that is what is used, and the cap is what
keeps a shipped feature from being abused into an unbounded log.

Format
------
``<ISO-8601 UTC>|<run_id>|<score>|<band>|<deduction,deduction,...>``, chosen so
it is readable straight off the property in the UI without a renderer, sorts
chronologically as a string, and parses on ``split`` without a dependency. The
deductions are the *names* only, never their point values: the weights are
configuration and can change between runs, so recording a number that was
computed under weights nobody kept would be a fact that quietly stops being true.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from modelguard.client import DataHubConnection
from modelguard.models import TrustScore
from modelguard.writeback.properties import (
    TRUST_HISTORY,
    StructuredPropertyValue,
    assign_properties,
    read_properties,
)

#: How many entries a model's history keeps. Twenty is a few weeks of a daily
#: `watch`, which is the span somebody actually reasons about, and it bounds a
#: property that would otherwise grow forever on a model scanned every hour.
HISTORY_LIMIT = 20

#: Field separator. A pipe rather than a comma, because the deductions field is
#: itself a comma-separated list and nesting one separator inside another is how
#: a parser starts needing quoting rules.
SEPARATOR = "|"


@dataclass(frozen=True)
class TrustEntry:
    """One scan's score for one model, as recorded in the history."""

    recorded_at: str
    """ISO-8601 UTC, to the second. A string because that is what was stored, and
    reparsing it into a datetime only to format it again would add a way to be
    wrong about a timezone."""
    run_id: str
    score: int
    band: str
    deductions: tuple[str, ...]

    def render(self) -> str:
        """Render the entry back to its stored form."""
        return SEPARATOR.join(
            [
                self.recorded_at,
                self.run_id,
                str(self.score),
                self.band,
                ",".join(self.deductions),
            ]
        )


def parse_entry(raw: str) -> TrustEntry | None:
    """Parse one stored entry, or None when it is not one.

    Anything unparseable is dropped rather than raised on. This property is
    editable by anyone with write access to the model, and a hand-edited line
    must cost a missing row in a trend, never a failed scan: the history is a
    convenience on top of the score, and nothing decides anything from it.
    """
    parts = raw.split(SEPARATOR)
    if len(parts) != 5:
        return None
    recorded_at, run_id, score, band, deductions = parts
    try:
        value = int(score)
    except ValueError:
        return None
    return TrustEntry(
        recorded_at=recorded_at,
        run_id=run_id,
        score=value,
        band=band,
        deductions=tuple(name for name in deductions.split(",") if name),
    )


def read_history(conn: DataHubConnection, model_urn: str) -> tuple[TrustEntry, ...]:
    """Return a model's recorded trust history, oldest first.

    Args:
        conn: An open connection.
        model_urn: The model to read.

    Returns:
        The parseable entries, in stored order. Empty when the model has never
        been scored, which is not the same as a model that scored well.
    """
    stored = read_properties(conn, model_urn).get(TRUST_HISTORY, [])
    entries = (parse_entry(str(value)) for value in stored)
    return tuple(entry for entry in entries if entry is not None)


def project_history(
    conn: DataHubConnection,
    model_urn: str,
    score: TrustScore,
    run_id: str,
    *,
    now: datetime | None = None,
) -> tuple[TrustEntry, ...]:
    """Return what this model's history will be once this run's score lands.

    Read and compute, no write, so a caller can render the trend before the write
    path reaches it. That is not a convenience: the impact report is published per
    finding, which happens before the trust score is persisted, and a report whose
    trend table stopped one scan short of the scan that produced it is a report a
    reader would reasonably file as a bug.

    Idempotent per run, like every other write in this package: a rerun under the
    same ``run_id`` replaces that run's entry instead of adding a second one, so
    a retried or re-emitted run leaves one row and not two.

    Args:
        conn: An open connection.
        model_urn: The model being scored.
        score: What this scan computed.
        run_id: This scan's identifier, which is also the idempotency key here.
        now: The instant to record. Injected so a test can fix it.

    Returns:
        The history including this run, oldest first, already capped.
    """
    recorded_at = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = TrustEntry(
        recorded_at=recorded_at,
        run_id=run_id,
        score=score.value,
        band=str(score.band),
        deductions=tuple(sorted(score.deductions)),
    )

    kept = [existing for existing in read_history(conn, model_urn) if existing.run_id != run_id]
    kept.append(entry)
    # Oldest dropped first: a trend is about where the score is going, and the
    # far past is what stops mattering as new points arrive.
    return tuple(kept[-HISTORY_LIMIT:])


def rendered(history: Sequence[TrustEntry]) -> list[StructuredPropertyValue]:
    """Render a history to the stored form, without writing it.

    Separated from the write so a caller that is already assigning other
    properties to the same model can carry the history in that one call instead
    of opening a second read-merge-write window on the same aspect (F3).
    """
    values: list[StructuredPropertyValue] = [item.render() for item in history]
    return values


def write_history(conn: DataHubConnection, model_urn: str, history: Sequence[TrustEntry]) -> None:
    """Persist a history exactly as given.

    Takes the whole list rather than one entry, so the value written is the one
    the caller already rendered into the report. Deriving it twice would let a
    report and the graph disagree about the same run.
    """
    assign_properties(conn, model_urn, {TRUST_HISTORY: rendered(history)})


def append_entry(
    conn: DataHubConnection,
    model_urn: str,
    score: TrustScore,
    run_id: str,
    *,
    now: datetime | None = None,
) -> tuple[TrustEntry, ...]:
    """Record this run's score on the model, and return the history after it.

    Project then write, for a caller with no need to see the projection first.
    """
    history = project_history(conn, model_urn, score, run_id, now=now)
    write_history(conn, model_urn, history)
    return history
