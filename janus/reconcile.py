"""Re-applying a link an ingestion run has just dropped (T-20, closes F11).

The failure this exists for, measured on a real stack (D-074): DataHub's mlflow
source upserts the **whole** ``mlModelProperties`` aspect on every ingest, which
drops the ``mlFeatures`` that ``janus link`` attached. From that moment the
model has no join to its columns, so the leakage, sensitive-source and proxy
checks have nothing to walk, and every one of them reports "not evaluated" on a
model that was fully checked the day before. Nothing fails. Nothing is logged.
The tool simply stops working on that model until a human remembers.

``link --all`` is the manual patch and it works, because the link's arguments
live in structured properties that ingestion does not touch. It requires somebody
to remember to run it, which is the whole of F11.

This is the automatic half. It watches the change log for the exact aspect write
that causes the damage and re-applies the recorded arguments, so the join
survives an ingest with no human action.

Why the event and not a schedule
--------------------------------
A CronJob after the nightly ingest is what the ``janus-watch`` chart already
ships, and it is a good answer for a warehouse with one ingestion window. It is
the wrong answer for a catalog where anyone can run an ingestion recipe at any
time, because the gap between the ingest and the next cron run is a window in
which a model is silently unchecked, and nothing says how long it was.

What it deliberately will not do
--------------------------------
It re-applies **only** what a human already confirmed. ``recorded_link`` returns
what somebody typed and approved once; this replays exactly that and never infers
a link, never widens one, and never links a model nobody linked. An automatic
process that guessed a model's training table would write a join that looks
identical to a confirmed one and is wrong, and every detector downstream would
then produce confident findings about the wrong columns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.logs import logfmt
from janus.mcl import MclEvent
from janus.writeback.link import LinkError, link_model, recorded_link

logger = logging.getLogger(__name__)

#: The entity and aspect whose upsert drops a link. Named as constants because
#: this is the whole predicate: an ingestion run rewriting anything else on a
#: model is not the failure this handler exists for.
ML_MODEL = "mlModel"
MODEL_PROPERTIES = "mlModelProperties"

#: The change types that replace an aspect wholesale. A PATCH does not: it is a
#: partial update, so it cannot drop a field it does not mention, and reacting to
#: one would re-link on writes that never broke anything.
_REPLACING = frozenset({"UPSERT", "CREATE", "RESTATE"})


@dataclass(frozen=True)
class Relink:
    """The outcome of considering one change-log event."""

    model_urn: str
    relinked: bool
    """Whether the link was actually replayed."""
    reason: str
    """Why, in a sentence, whichever way it went. Logged and printed, so an
    operator can tell an event that was ignored from one that was acted on
    without turning on debug logging."""
    features: int = 0
    """How many features the replay re-declared."""


def _dropped_the_link(event: MclEvent) -> bool:
    """Whether this event is an ``mlModelProperties`` write with no features left.

    Reads the aspect the event carries rather than the model's current state, so
    the decision is about what this write did. Re-reading the model would race
    the next write and could re-link against a graph that has moved on.
    """
    if event.entity_type != ML_MODEL or event.aspect_name != MODEL_PROPERTIES:
        return False
    if event.change_type not in _REPLACING:
        return False
    # Absent and empty are the same fact here: neither leaves a feature for a
    # detector to walk. `mlFeatures` is the field `link` writes and ingestion
    # overwrites (D-074).
    return not event.aspect.get("mlFeatures")


def consider(
    conn: DataHubConnection,
    config: ScanConfig,
    event: MclEvent,
    *,
    dry_run: bool = False,
) -> Relink | None:
    """Replay a dropped link, if this event dropped one. Returns what happened.

    Args:
        conn: A connection with write credentials.
        config: The same config a ``link`` would run with.
        event: One change-log event.
        dry_run: Decide everything and write nothing.

    Returns:
        What was done, or None when the event is not about a model whose link
        was dropped, which is almost every event on the topic.

    Raises:
        Nothing. A ``LinkError`` is caught and returned as a refusal: this runs
        inside a daemon's event loop, and one model whose feature table has since
        been deleted must not stop the loop from protecting the others (the same
        per-finding isolation F4's other half asks for).
    """
    if not _dropped_the_link(event):
        return None

    previous = recorded_link(conn, event.entity_urn)
    if previous is None:
        # A model that never carried a link, or one somebody deliberately
        # unlinked. Either way there is nothing a human confirmed to replay, and
        # inventing one is the thing this module refuses to do.
        return Relink(
            model_urn=event.entity_urn,
            relinked=False,
            reason="no janus link is recorded on this model, so there is nothing "
            "confirmed to replay",
        )

    try:
        result = link_model(
            conn,
            config,
            model_urn=event.entity_urn,
            feature_dataset_urn=previous.feature_dataset_urn,
            label_column_urn=previous.label_column_urn,
            exclude=previous.exclude,
            dry_run=dry_run,
        )
    except LinkError as exc:
        return Relink(
            model_urn=event.entity_urn,
            relinked=False,
            reason=f"the recorded link could not be replayed: {exc}",
        )

    fields = {
        "model": event.entity_urn,
        "features": len(result.feature_urns),
        "dry_run": str(dry_run).lower(),
    }
    logger.info("relinked after ingest %s", logfmt(fields))
    return Relink(
        model_urn=event.entity_urn,
        relinked=not dry_run,
        reason=(
            "an ingestion run replaced mlModelProperties and dropped the features; "
            "the recorded link was replayed"
        ),
        features=len(result.feature_urns),
    )
