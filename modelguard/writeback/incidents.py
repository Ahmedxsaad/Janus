"""Raise and resolve DataHub incidents, idempotently.

Incidents have no Python SDK wrapper in acryl-datahub 1.6.0.13, so the writes go
through the ``raiseIncident`` and ``updateIncidentStatus`` GraphQL mutations. The
queries are constants with bound variables: the LLM never composes GraphQL, it
selects one of these functions and supplies validated arguments
(writeback/CLAUDE.md rule 1).

Idempotency
-----------
Reads happen before writes. An incident is deduplicated on
``(resource_urn, incident_type, title)`` across the resource's *active*
incidents. ``run_id`` is deliberately not part of that key: it changes every run,
so including it would make each scan raise a fresh duplicate of the same finding.
It is instead stamped into the description as provenance, which is what makes the
audit trail reconstructable while ``scan`` stays convergent (the second scan of
an unchanged graph writes nothing).

Incident types
--------------
The plan lists a ``COLUMN`` type. The installed metadata model has no such value:
the column-scoped type is ``FIELD``. :data:`INCIDENT_TYPES` is derived from the
installed enum, so it cannot drift from the server.
"""

from __future__ import annotations

from dataclasses import dataclass

from datahub.metadata.schema_classes import (
    IncidentInfoClass,
    IncidentsSummaryClass,
    IncidentStateClass,
    IncidentTypeClass,
)

from modelguard.client import DataHubConnection

#: Every incident type the installed metadata model accepts.
INCIDENT_TYPES: frozenset[str] = frozenset(
    value
    for name, value in vars(IncidentTypeClass).items()
    if not name.startswith("_") and isinstance(value, str)
)

_RAISE_INCIDENT = """
mutation raiseIncident($input: RaiseIncidentInput!) {
  raiseIncident(input: $input)
}
"""

_UPDATE_INCIDENT_STATUS = """
mutation updateIncidentStatus($urn: String!, $input: UpdateIncidentStatusInput!) {
  updateIncidentStatus(urn: $urn, input: $input)
}
"""


class IncidentWriteError(RuntimeError):
    """A DataHub incident mutation was rejected or returned nothing."""


@dataclass(frozen=True)
class IncidentWrite:
    """The outcome of a raise attempt."""

    urn: str
    created: bool
    """False when an identical active incident already existed and was reused."""


def _stamp_run_id(description: str, run_id: str) -> str:
    """Append the run provenance footer that ties this incident to a scan."""
    return f"{description}\n\nRaised by ModelGuard run {run_id}."


def _active_incident_urns(conn: DataHubConnection, resource_urn: str) -> list[str]:
    """Return the URNs of the resource's currently active incidents.

    DataHub maintains an ``incidentsSummary`` aspect on the resource. A resource
    that has never had an incident carries no such aspect.
    """
    summary = conn.graph.get_aspect(resource_urn, IncidentsSummaryClass)
    if summary is None:
        return []

    # activeIncidentDetails is the current field; activeIncidents is the older
    # plain-URN list. Older incidents may only be recorded in the latter.
    urns = [detail.urn for detail in summary.activeIncidentDetails or []]
    urns += [urn for urn in summary.activeIncidents or [] if urn not in urns]
    return urns


def find_active_incident(
    conn: DataHubConnection,
    resource_urn: str,
    incident_type: str,
    title: str,
) -> str | None:
    """Return the URN of a matching active incident, or None.

    Matches on type and title, which together identify a finding. The title is
    generated deterministically from the finding, so the same problem detected on
    a later run resolves to the same incident.
    """
    for urn in _active_incident_urns(conn, resource_urn):
        info = conn.graph.get_aspect(urn, IncidentInfoClass)
        if info is None:
            continue
        if info.status.state != IncidentStateClass.ACTIVE:
            continue
        if info.type == incident_type and info.title == title:
            return urn
    return None


def raise_incident(
    conn: DataHubConnection,
    *,
    resource_urn: str,
    incident_type: str,
    title: str,
    description: str,
    run_id: str,
) -> IncidentWrite:
    """Raise an incident on a resource unless an identical one is already open.

    Args:
        conn: A connection with write credentials.
        resource_urn: The entity the incident is attached to. Must already exist.
        incident_type: One of :data:`INCIDENT_TYPES`.
        title: Deterministic, human-readable summary. Part of the dedup key.
        description: The body. The run id is appended to it.
        run_id: Identifier of the current ModelGuard run, for the audit trail.

    Returns:
        The incident URN, and whether this call created it.

    Raises:
        ValueError: The incident type is not one DataHub accepts, or the resource
            does not exist.
        IncidentWriteError: The mutation ran but returned no URN.
    """
    if incident_type not in INCIDENT_TYPES:
        raise ValueError(
            f"{incident_type!r} is not a DataHub incident type; allowed: {sorted(INCIDENT_TYPES)}"
        )
    if not conn.graph.exists(resource_urn):
        raise ValueError(f"cannot raise an incident on {resource_urn}: it does not exist")

    existing = find_active_incident(conn, resource_urn, incident_type, title)
    if existing is not None:
        return IncidentWrite(urn=existing, created=False)

    response = conn.graph.execute_graphql(
        _RAISE_INCIDENT,
        variables={
            "input": {
                "resourceUrn": resource_urn,
                "type": incident_type,
                "title": title,
                "description": _stamp_run_id(description, run_id),
            }
        },
    )
    urn = response.get("raiseIncident")
    if not urn:
        raise IncidentWriteError(f"raiseIncident returned no URN for {resource_urn}: {response}")
    return IncidentWrite(urn=urn, created=True)


def resolve_incident(conn: DataHubConnection, incident_urn: str, message: str) -> bool:
    """Mark an incident resolved.

    Returns:
        Whether DataHub accepted the state change.
    """
    response = conn.graph.execute_graphql(
        _UPDATE_INCIDENT_STATUS,
        variables={
            "urn": incident_urn,
            "input": {"state": IncidentStateClass.RESOLVED, "message": message},
        },
    )
    return bool(response.get("updateIncidentStatus"))
