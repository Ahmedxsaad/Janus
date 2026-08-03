"""The wire between a producer and the Argos window.

One versioned event shape going out, one command shape coming back, both
newline-delimited JSON. This module is the whole contract: any process that can
print these events on a pipe drives the dog, which is what makes Argos a general
DataHub companion rather than a ModelGuard pet (docs/plan/08 section 6).

Two asymmetries are deliberate.

* Events are trusted and commands are not. An event is built in this process
  from a finding we detected; a command arrives from a window a user was
  clicking on, so :meth:`Command.parse` validates it against a closed set of
  names and a closed set of argument keys, and returns None for anything else.
* Events are forgiving on the way in. :meth:`Event.from_dict` accepts a state it
  does not know, because the window is the thing that renders an unknown state
  as patrolling; a newer producer must never break an older reader, in either
  direction.

Nothing here talks to DataHub, imports the SDK, or reads the environment.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Bumped when a field changes meaning, never when one is added. A reader that
#: sees a version it does not know should ignore the event rather than guess.
PROTOCOL_VERSION = 1

#: Every state the sprite can be in (docs/plan/08 section 3). A producer may not
#: invent one: an unknown state renders as patrolling, which would silently look
#: like health.
STATES: frozenset[str] = frozenset(
    {
        "patrolling",
        "sniffing",
        "narrating",
        "barking",
        "scribbling",
        "tugging",
        "asleep",
        "sick",
        "ghost",
    }
)

#: The commands a window may send. A closed set, matched exactly: this is the
#: one channel that flows *into* the process and it can trigger writes to the
#: catalogue, so there is no dynamic dispatch off the name and no pattern match.
COMMANDS: frozenset[str] = frozenset(
    {
        "scan_now",
        "approve",
        "mute",
        "open_datahub",
        "drop",
    }
)

#: Argument keys a command may carry. Anything else is dropped rather than
#: passed on to a handler that might one day read it.
ARGUMENT_KEYS: frozenset[str] = frozenset({"entity", "path"})

#: Longest accepted argument value. A URN is ~200 characters and a filesystem
#: path is bounded by the OS; anything longer is not something this window sent.
MAX_ARGUMENT_LENGTH = 4096


@dataclass(frozen=True)
class Hop:
    """One step of a blast radius: an entity, and the column that carried it."""

    urn: str
    column: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of this hop."""
        return {"urn": self.urn, "column": self.column}


@dataclass(frozen=True)
class Event:
    """One thing that happened, and how the dog should depict it.

    ``state`` is the only required field, because it is the only one the
    renderer cannot do without. Everything else is context: a title for the
    speech bubble, the entity a click should open, the severity that colours the
    bubble, and the path the blast-radius walk animates.
    """

    state: str
    title: str | None = None
    entity: str | None = None
    severity: str | None = None
    link: str | None = None
    path: tuple[Hop, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Refuse to build an event in a state the window cannot render.

        A producer inventing a state is a bug in the producer, and it fails here
        rather than three layers away as a dog that looks healthy.
        """
        if self.state not in STATES:
            raise ValueError(f"unknown Argos state {self.state!r}; one of {sorted(STATES)}")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form, omitting every field that carries nothing.

        Omission rather than nulls: the line is written on a pipe several times
        a second and the reader treats absent and null identically.
        """
        payload: dict[str, Any] = {"v": PROTOCOL_VERSION, "state": self.state}
        for key, value in (
            ("title", self.title),
            ("entity", self.entity),
            ("severity", self.severity),
            ("link", self.link),
        ):
            if value:
                payload[key] = value
        if self.path:
            payload["path"] = [hop.to_dict() for hop in self.path]
        return payload

    def to_json(self) -> str:
        """Return one line of JSON, with no newline of its own."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Event:
        """Rebuild an event from its JSON form.

        Used by the tests and by the fixture replay, so the file the browser
        demo reads is checked against the same rules the producer writes by.

        Raises:
            ValueError: The payload is not an event: wrong version, missing
                state, or a state this build does not know.
        """
        version = payload.get("v")
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version {version!r}")
        state = payload.get("state")
        if not isinstance(state, str):
            raise ValueError("event has no state")
        raw_path = payload.get("path") or ()
        return cls(
            state=state,
            title=payload.get("title"),
            entity=payload.get("entity"),
            severity=payload.get("severity"),
            link=payload.get("link"),
            path=tuple(Hop(urn=hop["urn"], column=hop.get("column")) for hop in raw_path),
        )


@dataclass(frozen=True)
class Command:
    """A validated instruction from the window.

    Only ever built by :meth:`parse`, which is the trust boundary: by the time a
    handler sees one of these, the name is in :data:`COMMANDS` and every
    argument key is in :data:`ARGUMENT_KEYS` with a bounded string value.
    """

    name: str
    args: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, line: str) -> Command | None:
        """Return the command in this line, or None when there is not one.

        None covers every kind of rubbish that can arrive on a pipe: a blank
        line, a GTK warning the child printed on the wrong stream, a truncated
        write, a command name we do not implement, an argument key we do not
        expect, and a value too long to be one of ours. The caller logs and
        moves on; nothing here raises, because a daemon must not die of a bad
        line from a window a user was clicking on.
        """
        text = line.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        name = payload.get("cmd")
        if not isinstance(name, str) or name not in COMMANDS:
            return None

        raw_args = payload.get("args") or {}
        if not isinstance(raw_args, dict):
            return None
        args: dict[str, str] = {}
        for key, value in raw_args.items():
            if key not in ARGUMENT_KEYS:
                return None
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > MAX_ARGUMENT_LENGTH:
                return None
            args[key] = value
        return cls(name=name, args=args)
