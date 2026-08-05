"""Reading DataHub's own change log, so Janus can react instead of ask.

``janus watch`` polls. That is deliberate and it stays the default: polling
depends on nothing but GMS, works through a proxy, and cannot silently fall
behind a Kafka consumer group somebody rebalanced. 03-production-hardening.md
section C.1 has always named the event-driven upgrade for it, and this is that
upgrade: a ``MetadataChangeLog`` consumer over the topic GMS already publishes.

Why it is worth the dependency
------------------------------
Two things a poll cannot do.

* **Latency without cost.** A poll interval is a trade between how fast a finding
  appears and how hard the catalog is hammered. An event has neither end of that
  trade: nothing is asked until something changed.
* **Reacting to a change nobody was watching.** This is the one that matters, and
  it is T-20. ``link``'s join is dropped by *any* ingestion run that upserts
  ``mlModelProperties``, on *any* model (D-074, F11). A poll only ever notices it
  on the target it was pointed at, so the catalog-wide failure needs a
  catalog-wide signal, and the change log is the only one there is.

What this module is, and what it is not
---------------------------------------
It is a consumer and a decoder: subscribe, deserialise, hand each event to a
callback as a typed :class:`MclEvent`. It holds no policy. Which events matter,
and what to do about them, belongs to the handlers in
:mod:`janus.reconcile` and to ``watch``, for the same reason detection lives
in ``detect/`` and not in the trigger that invoked it.

It is deliberately not ``datahub-actions``. That framework brings a plugin
system, its own YAML configuration and its own CLI, to deliver the same records
this reads directly; adopting it would mean a second configuration surface next
to ``env.py``, which root rule 6 exists to prevent. The topic and the Avro schema
are DataHub's published contract either way.

The wire format, verified
-------------------------
``MetadataChangeLog_Versioned_v1`` carries Confluent-framed Avro whose schema is
registered under ``MetadataChangeLog_Versioned_v1-value`` in the schema registry
GMS itself serves at ``/schema-registry/api/`` `[verified]` against a live
Quickstart. The record's ``aspect`` is a ``GenericAspect``: a ``contentType`` and
a ``value`` holding **JSON bytes**, not nested Avro, so an aspect payload is
parsed with :func:`json.loads` and never with the Avro reader. Confirmed by
consuming a real event before this module was written.

Configuration
-------------
Three variables, all identity, all-or-nothing (root rule 6a and 6c): the broker,
the schema registry, and this deployment's consumer group. The group id has no
default on purpose. Two Janus deployments sharing one group id would split
the partitions between them, and each would silently see half the events, which
is a failure that looks exactly like a quiet catalog.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from janus.env import ConfigError, optional_value

logger = logging.getLogger(__name__)

ENV_KAFKA_BOOTSTRAP = "JANUS_KAFKA_BOOTSTRAP"
ENV_SCHEMA_REGISTRY_URL = "JANUS_SCHEMA_REGISTRY_URL"
ENV_KAFKA_GROUP_ID = "JANUS_KAFKA_GROUP_ID"

#: The topic every versioned aspect write lands on. Timeseries aspects go to a
#: separate topic and are not consumed: nothing this project reacts to is one,
#: and subscribing to both would double the traffic for no handler.
TOPIC = "MetadataChangeLog_Versioned_v1"

#: How long one poll blocks before returning nothing. Short enough that Ctrl-C is
#: answered promptly, long enough that an idle consumer is not a busy loop.
POLL_TIMEOUT_SECONDS = 1.0

#: How long to wait at startup for the broker to assign this consumer its
#: partitions. Generous, because it is paid once per process and the alternative
#: is a daemon that silently misses everything written during a slow rebalance.
#: Exceeding it is logged and consumption starts anyway: a rebalance that has not
#: finished is not a reason to refuse to run.
ASSIGNMENT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class MclConfig:
    """Where the change log is, and which consumer this deployment is."""

    bootstrap_servers: str
    schema_registry_url: str
    group_id: str


def mcl_config_from_env() -> MclConfig | None:
    """Return the change-log configuration, or None when it is not configured.

    None is the normal case: polling is the default and needs none of this.

    Raises:
        ConfigError: Some of the three are set and some are not. A
            half-configured consumer would connect to a broker and never commit
            an offset, or commit under a group id somebody else owns, so the
            group fails as a unit rather than degrading (root rule 6c).
    """
    values = {
        ENV_KAFKA_BOOTSTRAP: optional_value(ENV_KAFKA_BOOTSTRAP),
        ENV_SCHEMA_REGISTRY_URL: optional_value(ENV_SCHEMA_REGISTRY_URL),
        ENV_KAFKA_GROUP_ID: optional_value(ENV_KAFKA_GROUP_ID),
    }
    present = {name for name, value in values.items() if value is not None}
    if not present:
        return None
    missing = sorted(set(values) - present)
    if missing:
        raise ConfigError(
            "The change-log consumer needs all three of "
            f"{', '.join(sorted(values))}, or none of them. Missing: {', '.join(missing)}."
        )

    return MclConfig(
        bootstrap_servers=values[ENV_KAFKA_BOOTSTRAP],  # type: ignore[arg-type]
        schema_registry_url=values[ENV_SCHEMA_REGISTRY_URL],  # type: ignore[arg-type]
        group_id=values[ENV_KAFKA_GROUP_ID],  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class MclEvent:
    """One aspect write, as the change log reports it."""

    entity_type: str
    """``mlModel``, ``dataset``, and so on. DataHub's own spelling."""
    entity_urn: str
    aspect_name: str
    change_type: str
    """``UPSERT``, ``RESTATE``, ``DELETE``, ``CREATE``, ``PATCH``."""
    aspect: dict[str, Any]
    """The new value, parsed from the ``GenericAspect``'s JSON bytes. Empty when
    the record carried none, which is what a DELETE looks like."""

    @property
    def removed(self) -> bool:
        """Whether this event took the aspect away rather than setting it."""
        return self.change_type == "DELETE"


def _decode_aspect(raw: Any) -> dict[str, Any]:
    """Parse a ``GenericAspect``'s payload, or return nothing usable.

    The payload is JSON bytes inside an Avro record, so anything unreadable is
    treated as absent rather than raised on: a consumer that died on one
    malformed record would stop reacting to every well-formed one behind it, and
    this stream is written by systems this project does not control.
    """
    if not raw:
        return {}
    value = raw.get("value")
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def to_event(record: dict[str, Any]) -> MclEvent | None:
    """Turn one deserialised change-log record into an event, or None.

    None for a record naming no entity, which nothing here can act on. Pure, so
    the decoding is testable without a broker: the interesting failure modes of
    this module are all in the shape of the record, not in Kafka.
    """
    urn = record.get("entityUrn")
    aspect_name = record.get("aspectName")
    if not urn or not aspect_name:
        return None
    return MclEvent(
        entity_type=str(record.get("entityType") or ""),
        entity_urn=str(urn),
        aspect_name=str(aspect_name),
        change_type=str(record.get("changeType") or ""),
        aspect=_decode_aspect(record.get("aspect")),
    )


class ChangeLog:
    """A subscription to DataHub's change log.

    Opened by ``janus watch --events`` and closed when it stops. The
    ``confluent_kafka`` import is inside :meth:`__enter__`, never at module
    import, so a core install neither needs the ``[kafka]`` extra nor pays for
    it, exactly as the OTLP exporter does for ``[otel]``.
    """

    def __init__(self, config: MclConfig) -> None:
        """Hold the configuration. Nothing connects until the context is entered."""
        self._config = config
        self._consumer: Any = None
        self._deserializer: Any = None
        self._context: Any = None
        self._pending: list[Any] = []
        """Messages that arrived while waiting for the partition assignment.
        Drained by :meth:`events` before it polls for more, so nothing this
        consumer was handed is dropped on the way in."""

    def __enter__(self) -> ChangeLog:
        """Connect, fetch the schema, and subscribe.

        Raises:
            ConfigError: The ``[kafka]`` extra is not installed. Raised here
                rather than at import, and named with its install command,
                because a consumer configured against a broker with no client
                library is a startup failure and not a runtime surprise.
        """
        try:
            from confluent_kafka import Consumer
            from confluent_kafka.schema_registry import SchemaRegistryClient
            from confluent_kafka.schema_registry.avro import AvroDeserializer
            from confluent_kafka.serialization import MessageField, SerializationContext
        except ImportError as exc:
            raise ConfigError(
                f"{ENV_KAFKA_BOOTSTRAP} is set but the Kafka client is not installed. "
                "Install it with: pip install 'janus-datahub[kafka]'"
            ) from exc

        registry = SchemaRegistryClient({"url": self._config.schema_registry_url})
        schema = registry.get_latest_version(f"{TOPIC}-value").schema
        self._deserializer = AvroDeserializer(registry, schema.schema_str)
        self._context = SerializationContext(TOPIC, MessageField.VALUE)

        self._consumer = Consumer(
            {
                "bootstrap.servers": self._config.bootstrap_servers,
                "group.id": self._config.group_id,
                # Latest, not earliest: a first run must react to what happens
                # next, not replay every aspect ever written to the catalog.
                # Replaying is what `link --all` and `scan --all-models` are for,
                # and they do it by reading the graph rather than its history.
                "auto.offset.reset": "latest",
                # Committed only after a handler returns, in `events()` below, so
                # a crash mid-handler re-delivers rather than skipping. Every
                # handler here is idempotent, which is what makes at-least-once
                # the right choice (03-hardening C.2).
                "enable.auto.commit": False,
            }
        )
        self._consumer.subscribe([TOPIC])
        self._await_assignment()
        logger.info("subscribed to %s as %s", TOPIC, self._config.group_id)
        return self

    def _await_assignment(self) -> None:
        """Block until the broker has actually given this consumer its partitions.

        ``subscribe`` returns immediately; the group rebalance that assigns
        partitions happens on subsequent ``poll`` calls, and with
        ``auto.offset.reset=latest`` the starting offset is fixed at *assignment*
        time. So everything written to the catalog between the call and the
        assignment is behind the cursor and is never seen. On a daemon that is a
        few seconds of blindness at startup, which is exactly when an operator is
        watching to see whether it works.

        Any message that does arrive while waiting is buffered rather than
        discarded: a consumer group resuming from a committed offset can be
        handed real records here, and dropping one would lose an event this
        process had already been told about.
        """
        deadline = time.monotonic() + ASSIGNMENT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._consumer.assignment():
                return
            message = self._consumer.poll(POLL_TIMEOUT_SECONDS)
            if message is not None and not message.error():
                self._pending.append(message)
        logger.warning(
            "no partition assigned within %.0fs; consuming anyway",
            ASSIGNMENT_TIMEOUT_SECONDS,
        )

    def __exit__(self, *_: object) -> None:
        """Leave the consumer group cleanly, so a restart does not wait on a timeout."""
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None

    def events(self) -> Iterator[MclEvent]:
        """Yield events as they arrive, forever.

        The offset is committed after the consumer resumes, which is after the
        caller's handler has returned: an exception escaping the handler leaves
        the offset where it was and the event is re-delivered. At-least-once with
        idempotent handlers is effectively-once, which is the property every
        write in this project already has (writeback/CLAUDE.md rule 2).
        """
        if self._consumer is None:
            raise RuntimeError("ChangeLog.events() outside its context manager")

        while True:
            message = self._pending.pop(0) if self._pending else None
            if message is None:
                message = self._consumer.poll(POLL_TIMEOUT_SECONDS)
            if message is None:
                continue
            if message.error():
                # Logged and skipped rather than raised: a partition rebalance
                # and an end-of-partition marker both arrive here, and neither is
                # a reason to stop a daemon.
                logger.warning("change log error %s", message.error())
                continue

            record = self._deserializer(message.value(), self._context)
            if record is None:
                continue
            event = to_event(record)
            if event is not None:
                yield event
            self._consumer.commit(message=message, asynchronous=False)
