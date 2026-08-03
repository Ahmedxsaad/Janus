"""The general DataHub companion: what is wrong with the assets you own.

This is the producer that makes Argos more than a ModelGuard pet. It runs no
detector of its own and knows nothing about ML lineage. It asks the catalogue
three questions about the entities a given owner owns, and turns the answers
into the same protocol events `modelguard watch` sends:

* is an incident open on it,
* did its latest assertion run fail,
* did its owners deprecate it.

DataHub has no desktop presence today; it is a browser tab people forget to
open. This is that presence, and ModelGuard is one event source into it rather
than the whole point (docs/plan/08 section 7).

Read-only, always. Nothing here writes an aspect, so the companion is safe to
point at a production catalogue with a read-only token.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import (
    AssertionResultTypeClass,
    AssertionRunEventClass,
    DeprecationClass,
    IncidentInfoClass,
    IncidentStateClass,
)

from modelguard.argos.protocol import Event
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.env import required_value
from modelguard.logs import LOG_FIELDS, logfmt
from modelguard.writeback.incidents import attached_incident_urns

logger = logging.getLogger(__name__)

#: Whose assets to watch. An owner identifies an account, so per root CLAUDE.md
#: rule 6a it has no default and no fallback: a companion that guessed would
#: silently watch somebody else's catalogue, or all of it.
ENV_OWNER = "MODELGUARD_COMPANION_OWNER"

#: The entity types the companion sweeps. Datasets and models are the two a
#: person is on the hook for; containers and dashboards are somebody else's
#: product and would only dilute the one dog's attention.
ENTITY_TYPES: tuple[str, ...] = ("dataset", "mlModel")

#: The relationship from an assertion to what it asserts on.
_ASSERTS_RELATIONSHIP = "Asserts"

#: Severity per source, worst first. Not a detector's measured severity: these
#: are three different kinds of fact, and this is the order a person triages
#: them in. An open incident is somebody already saying this is broken.
_SEVERITY = {"incident": "high", "assertion": "medium", "deprecation": "low"}


@dataclass(frozen=True)
class CatalogueIssue:
    """One thing the catalogue itself says is wrong with an owned entity."""

    source: str
    """Which of the three questions answered yes: incident, assertion, deprecation."""

    entity_urn: str
    title: str

    @property
    def severity(self) -> str:
        """How urgently a person should look at this."""
        return _SEVERITY[self.source]


def owner_urn() -> str:
    """Return the configured owner, or fail loudly naming the variable."""
    return required_value(
        ENV_OWNER,
        "Set it to the owner whose assets Argos should watch, for example "
        "urn:li:corpuser:datahub or urn:li:corpGroup:analytics.",
    )


def owned_urns(conn: DataHubConnection, owner: str, config: ScanConfig) -> tuple[str, ...]:
    """Return the entities this owner owns, capped.

    [confirm] The ``owners`` filter field is the one part of this module that
    needs a live GMS to prove: the SDK signature is verified (``extraFilters``
    takes ``{"field", "values"}`` mappings), the field *name* is DataHub's and
    is checked against a running instance in tests/integration.

    The cap is not politeness. A sweep of an entire catalogue is a long
    traversal, and a companion that spends four minutes per poll is a companion
    that is always describing the past.
    """
    urns: list[str] = []
    for entity_type in ENTITY_TYPES:
        found = conn.graph.get_urns_by_filter(
            entity_types=[entity_type],
            extraFilters=[{"field": "owners", "values": [owner]}],
        )
        for urn in found:
            urns.append(urn)
            if len(urns) >= config.companion_entity_cap:
                logger.warning(
                    "companion sweep hit the entity cap %s",
                    logfmt({"cap": config.companion_entity_cap}),
                    extra={LOG_FIELDS: {"cap": config.companion_entity_cap}},
                )
                return tuple(urns)
    return tuple(urns)


def open_incidents(conn: DataHubConnection, entity_urn: str) -> Iterator[CatalogueIssue]:
    """Yield one issue per active incident attached to this entity.

    Reuses the read `writeback/incidents.py` already does before every write, so
    the companion and the write path agree on what "an incident is open" means.
    """
    for incident_urn in attached_incident_urns(conn, entity_urn):
        info = conn.graph.get_aspect(incident_urn, IncidentInfoClass)
        if info is None or info.status.state != IncidentStateClass.ACTIVE:
            continue
        yield CatalogueIssue(
            source="incident",
            entity_urn=entity_urn,
            title=info.title or "incident open",
        )


def failing_assertions(conn: DataHubConnection, entity_urn: str) -> Iterator[CatalogueIssue]:
    """Yield one issue per assertion whose most recent run failed.

    Assertion results are a timeseries aspect, so this reads the latest value
    rather than the current state of an aspect: an assertion has no "is failing"
    field, it has a history of runs, and the last one is the answer.
    """
    related = conn.graph.get_related_entities(
        entity_urn=entity_urn,
        relationship_types=[_ASSERTS_RELATIONSHIP],
        direction=RelationshipDirection.INCOMING,
    )
    for assertion in related:
        run = conn.graph.get_latest_timeseries_value(
            assertion.urn,
            AssertionRunEventClass,
            {"asserteeUrn": entity_urn},
        )
        if run is None or run.result is None:
            continue
        if run.result.type != AssertionResultTypeClass.FAILURE:
            continue
        yield CatalogueIssue(
            source="assertion",
            entity_urn=entity_urn,
            title="an assertion is failing",
        )


def deprecations(conn: DataHubConnection, entity_urn: str) -> Iterator[CatalogueIssue]:
    """Yield an issue when this entity's owners marked it deprecated.

    ``deprecated=False`` is how DataHub records a deprecation that was lifted,
    so the flag is checked rather than the aspect's presence.
    """
    deprecation = conn.graph.get_aspect(entity_urn, DeprecationClass)
    if deprecation is None or not deprecation.deprecated:
        return
    note = deprecation.note or "no note"
    yield CatalogueIssue(
        source="deprecation",
        entity_urn=entity_urn,
        title=f"deprecated: {note}"[:120],
    )


@dataclass(frozen=True)
class Sweep:
    """One companion poll: what it looked at, and what it found."""

    owned: int
    """How many entities were inspected. Carried alongside the issues because
    "nothing wrong" and "nothing checked" must never look the same, which is the
    rule detect/coverage.py exists for."""

    issues: tuple[CatalogueIssue, ...]


def poll(conn: DataHubConnection, owner: str, config: ScanConfig) -> Sweep:
    """Ask all three questions about every owned entity, worst source first."""
    issues: list[CatalogueIssue] = []
    urns = owned_urns(conn, owner, config)
    for entity_urn in urns:
        issues.extend(open_incidents(conn, entity_urn))
        issues.extend(failing_assertions(conn, entity_urn))
        issues.extend(deprecations(conn, entity_urn))
    return Sweep(
        owned=len(urns),
        issues=tuple(sorted(issues, key=lambda issue: list(_SEVERITY).index(issue.source))),
    )


def event_for(sweep: Sweep) -> Event:
    """Return the one event describing a completed companion poll.

    The dog has one body, so the worst issue is the one it barks about and the
    rest are counted. A clean sweep still says how much it looked at: "nothing
    wrong" and "nothing checked" must never look the same (the same rule
    detect/coverage.py exists for).
    """
    if not sweep.issues:
        return Event(state="patrolling", title=f"{sweep.owned} owned assets, nothing wrong")
    worst = sweep.issues[0]
    more = f" (+{len(sweep.issues) - 1} more)" if len(sweep.issues) > 1 else ""
    state = "barking" if worst.source == "incident" else "sick"
    return Event(
        state=state,
        title=f"{worst.title}{more}",
        entity=worst.entity_urn,
        severity=worst.severity,
    )


__all__ = [
    "ENTITY_TYPES",
    "ENV_OWNER",
    "CatalogueIssue",
    "Sweep",
    "deprecations",
    "event_for",
    "failing_assertions",
    "open_incidents",
    "owned_urns",
    "owner_urn",
    "poll",
]
