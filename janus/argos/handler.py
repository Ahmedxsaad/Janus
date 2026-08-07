"""Driving the sprite from the log stream.

Four of the twelve states in docs/11-argos.md are phases of a scan rather
than results of one: the lineage walk, the narrator drafting, an aspect landing
on the graph, an approval waiting. Nothing returns them, because they are things
happening *inside* a call that has not come back yet.

The producer learns about them the same way an operator does, from the log. A
handler on the root logger picks up the records that carry an ``argos_phase``
field and maps each to a state. That means no rendering concern is threaded
through a detector's signature, and the log lines pay for themselves: a person
tailing `janus watch` wants to know a lineage walk started too.

What this deliberately does not take from the record is its message. Log lines
in this project carry identifiers, counts and durations only, never prose or
aspect content (janus/logs.py), so the bubble's sentence comes from the
finding, and the log channel contributes the state and nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from janus.argos.protocol import Event
from janus.logs import LOG_FIELDS, PHASE_FIELD

#: Titles for the phase states. Fixed strings rather than the log message,
#: because the message is not ours to put on screen and the phases are few.
_PHASE_TITLES = {
    "sniffing": "walking lineage",
    "narrating": "drafting the description",
    "scribbling": "writing to the catalogue",
    "tugging": "waiting for your approval",
}


class ArgosHandler(logging.Handler):
    """Forward phase log records to the window as protocol events."""

    def __init__(self, sink: Callable[[Event], None]) -> None:
        """Build a handler that hands every phase event to ``sink``.

        Args:
            sink: Called with one event per phase record. Must be safe to call
                from whichever thread logged, which for
                :class:`~janus.argos.window.ArgosWindow` it is: a write to
                a pipe under the GIL, with no shared state of its own.
        """
        super().__init__(level=logging.INFO)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        """Send one event if this record names a phase, otherwise ignore it.

        A handler that raises breaks logging for the whole process, so every
        failure here goes through :meth:`handleError`, which is what the logging
        module provides for exactly this.
        """
        try:
            fields = getattr(record, LOG_FIELDS, None)
            if not isinstance(fields, dict):
                return
            state = fields.get(PHASE_FIELD)
            if not isinstance(state, str) or state not in _PHASE_TITLES:
                return
            self._sink(Event(state=state, title=_PHASE_TITLES[state]))
        except Exception:  # noqa: BLE001 - logging must survive its own handlers
            self.handleError(record)
