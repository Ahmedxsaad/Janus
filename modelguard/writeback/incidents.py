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
incidents, found by traversing the ``IncidentOn`` relationship inbound.
``run_id`` is deliberately not part of that key: it changes every run, so
including it would make each scan raise a fresh duplicate of the same finding.
It is instead stamped into the description as provenance, which is what makes the
audit trail reconstructable while ``scan`` stays convergent (the second scan of
an unchanged graph writes nothing).

Incident types
--------------
The plan lists a ``COLUMN`` type. The installed metadata model has no such value:
the column-scoped type is ``FIELD``. :data:`INCIDENT_TYPES` is derived from the
installed enum, so it cannot drift from the server.

What an incident can attach to
------------------------------
**An incident cannot be raised on an mlModel.** The ``incidentInfo.entities``
relationship accepts only ``dataset, chart, dashboard, dataFlow, dataJob,
schemaField``; GMS rejects anything else with a 500. The plan assumed the model
was the target. It is not, and it cannot be.

So findings land where the data lives: a leakage finding becomes a ``FIELD``
incident on the offending ``schemaField``, and an upstream failure becomes a
``FRESHNESS`` or ``VOLUME`` incident on the source ``dataset``. Model-level risk
is carried by structured properties (``modelguard.trust_score``,
``modelguard.risk_flags``), which mlModel does accept. That split is arguably the
right one anyway: an incident is about a broken data asset, while a trust score is
a property of the model.

:data:`INCIDENT_ENTITY_TYPES` is read out of the aspect's own schema, so it tracks
whatever the connected server allows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import (
    IncidentInfoClass,
    IncidentStateClass,
    IncidentTypeClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import SchemaFieldUrn, Urn

from modelguard.client import DataHubConnection

#: The relationship an incident declares onto each entity it concerns.
INCIDENT_ON_RELATIONSHIP = "IncidentOn"

#: Every incident type the installed metadata model accepts.
INCIDENT_TYPES: frozenset[str] = frozenset(
    value
    for name, value in vars(IncidentTypeClass).items()
    if not name.startswith("_") and isinstance(value, str)
)


def _incident_entity_types() -> frozenset[str]:
    """Read the entity types an incident may attach to out of the aspect schema.

    Hardcoding the list would let it drift from the server. The schema is the
    same artifact GMS validates against.
    """
    schema = json.loads(str(IncidentInfoClass.RECORD_SCHEMA))
    for field in schema["fields"]:
        if field["name"] == "entities":
            return frozenset(field["Relationship"]["/*"]["entityTypes"])
    raise RuntimeError("incidentInfo has no 'entities' field; the metadata model changed")


#: dataset, chart, dashboard, dataFlow, dataJob, schemaField. Notably not mlModel.
INCIDENT_ENTITY_TYPES: frozenset[str] = _incident_entity_types()

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


def _resource_exists(conn: DataHubConnection, resource_urn: str) -> bool:
    """Report whether an incident may legitimately attach to this resource.

    ``graph.exists`` answers False for every ``schemaField``: columns are not
    materialized as standalone entities, they live inside the parent dataset's
    ``schemaMetadata``. Resolving the column against that aspect is both correct
    and stricter than ``exists``, because it also catches a misspelled column
    name, which no entity-level check ever would.
    """
    urn = Urn.from_string(resource_urn)
    if not isinstance(urn, SchemaFieldUrn):
        return conn.graph.exists(resource_urn)

    schema = conn.graph.get_aspect(str(urn.parent), SchemaMetadataClass)
    if schema is None:
        return False
    return any(field.fieldPath == urn.field_path for field in schema.fields)


def _active_incident_urns(conn: DataHubConnection, resource_urn: str) -> list[str]:
    """Return the URNs of every incident attached to this resource.

    Traverses the ``IncidentOn`` relationship inbound, from the resource to the
    incidents that name it.

    The obvious route, the resource's ``incidentsSummary`` aspect, does not work:
    on a Quickstart that aspect is never written, not for a dataset and not for a
    schemaField, so a summary-based dedup silently finds nothing and duplicates
    every finding on every run. The relationship index is populated. Searching
    incidents by their ``entities`` field also fails, with a GraphQL non-null
    violation from GMS.

    Filtering to the active ones is the caller's job, via each incident's status.
    """
    related = conn.graph.get_related_entities(
        entity_urn=resource_urn,
        relationship_types=[INCIDENT_ON_RELATIONSHIP],
        direction=RelationshipDirection.INCOMING,
    )
    return [entity.urn for entity in related]


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
        ValueError: The incident type is not one DataHub accepts, the resource is
            of an entity type that cannot carry an incident, or it does not exist.
        IncidentWriteError: The mutation ran but returned no URN.
    """
    if incident_type not in INCIDENT_TYPES:
        raise ValueError(
            f"{incident_type!r} is not a DataHub incident type; allowed: {sorted(INCIDENT_TYPES)}"
        )

    entity_type = Urn.from_string(resource_urn).entity_type
    if entity_type not in INCIDENT_ENTITY_TYPES:
        raise ValueError(
            f"DataHub cannot raise an incident on a {entity_type}; "
            f"allowed: {sorted(INCIDENT_ENTITY_TYPES)}. "
            "Attach the finding to the dataset or schemaField it concerns, and record "
            "model-level risk with the modelguard.trust_score structured property."
        )

    if not _resource_exists(conn, resource_urn):
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
