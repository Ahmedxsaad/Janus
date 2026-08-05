"""A ``raise_incident`` mutation tool for ``acryldata/mcp-server-datahub``.

The MCP server (v0.6.0) ships read tools only: it has no incident, assertion, or
lineage-write tool, so an agent can find a data-to-model failure but cannot record
it. This module is the thin write tool that closes the incident half of that gap,
proposed for upstream (see ``RFC-ml-incidents.md``).

It is deliberately minimal and standalone: it targets a different repository and so
does not import ``janus``. The one non-trivial behaviour, building and
validating the ``raiseIncident`` payload, mirrors ``janus/writeback/incidents.py``
exactly (same GraphQL, same allowed-set derivation from the installed metadata
model), because both talk to the same GMS.

Conventions it follows, from the upstream server:

* the mutation is gated behind ``TOOLS_IS_MUTATION_ENABLED``; when it is not set to
  a truthy value the tool refuses rather than writes.
* the tool is annotated ``readOnlyHint: false`` at registration (see :func:`register`).
* the DataHub handle comes from the server's own ``get_datahub_client`` helper.

Run ``python raise_incident_tool.py`` to execute the offline self-check.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from datahub.metadata.schema_classes import IncidentInfoClass, IncidentTypeClass
from datahub.metadata.urns import Urn

#: The environment flag the upstream server uses to enable every mutation tool.
MUTATION_ENABLED_ENV = "TOOLS_IS_MUTATION_ENABLED"

# The exact mutation janus/writeback/incidents.py uses. raiseIncident returns
# the new incident's URN as a scalar string.
_RAISE_INCIDENT = """
mutation raiseIncident($input: RaiseIncidentInput!) {
  raiseIncident(input: $input)
}
"""


class _GraphLike(Protocol):
    """The one method this tool needs from a DataHub handle."""

    def execute_graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...


def _mutations_enabled() -> bool:
    """Whether write tools are permitted, read from the upstream server's flag."""
    return os.environ.get(MUTATION_ENABLED_ENV, "").strip().lower() in {"true", "1", "yes"}


def _incident_types() -> frozenset[str]:
    """Every incident type the installed metadata model accepts.

    Derived from the enum rather than hardcoded so it cannot drift from the server:
    there is no ``COLUMN`` type; the column-scoped one is ``FIELD``.
    """
    return frozenset(
        value
        for name, value in vars(IncidentTypeClass).items()
        if not name.startswith("_") and isinstance(value, str)
    )


def _incident_entity_types() -> frozenset[str]:
    """The entity types an incident may attach to, read from the aspect schema.

    Notably excludes ``mlModel``: GMS answers a 500 for one. This is the ML-incident
    gap the accompanying RFC is about.
    """
    schema = json.loads(str(IncidentInfoClass.RECORD_SCHEMA))
    for field in schema["fields"]:
        if field["name"] == "entities":
            return frozenset(field["Relationship"]["/*"]["entityTypes"])
    raise RuntimeError("incidentInfo has no 'entities' field; the metadata model changed")


class MutationDisabledError(RuntimeError):
    """The mutation was called while ``TOOLS_IS_MUTATION_ENABLED`` was not truthy."""


class IncidentToolError(RuntimeError):
    """The ``raiseIncident`` mutation ran but returned no URN."""


def raise_incident(
    graph: _GraphLike,
    *,
    resource_urn: str,
    title: str,
    description: str,
    incident_type: str = "CUSTOM",
) -> str:
    """Raise a DataHub incident on a dataset, column, chart, dashboard, or data job.

    Args:
        graph: A DataHub handle exposing ``execute_graphql``.
        resource_urn: The entity the incident attaches to. Must be one of the types
            in :func:`_incident_entity_types` (not an mlModel).
        title: A short, human-readable summary of the problem.
        description: The incident body.
        incident_type: One of the installed incident types (default ``CUSTOM``).

    Returns:
        The new incident's URN.

    Raises:
        MutationDisabledError: Write tools are not enabled on this server.
        ValueError: The incident type is unknown, or the resource is an entity type
            that cannot carry an incident.
        IncidentToolError: The mutation ran but returned no URN.
    """
    if not _mutations_enabled():
        raise MutationDisabledError(
            f"raise_incident is a mutation; set {MUTATION_ENABLED_ENV}=true to enable it"
        )

    allowed_types = _incident_types()
    if incident_type not in allowed_types:
        raise ValueError(
            f"{incident_type!r} is not a DataHub incident type; allowed: {sorted(allowed_types)}"
        )

    entity_type = Urn.from_string(resource_urn).entity_type
    allowed_entities = _incident_entity_types()
    if entity_type not in allowed_entities:
        raise ValueError(
            f"DataHub cannot raise an incident on a {entity_type}; "
            f"allowed: {sorted(allowed_entities)}. Attach the finding to the dataset or "
            "schemaField it concerns. (An mlModel cannot carry an incident; see the RFC.)"
        )

    response = graph.execute_graphql(
        _RAISE_INCIDENT,
        variables={
            "input": {
                "resourceUrn": resource_urn,
                "type": incident_type,
                "title": title,
                "description": description,
            }
        },
    )
    urn = response.get("raiseIncident")
    if not urn:
        raise IncidentToolError(f"raiseIncident returned no URN for {resource_urn}: {response}")
    return urn


def register(mcp: Any) -> None:
    """Register ``raise_incident`` on the upstream FastMCP server.

    Called from ``mcp_server.py`` alongside the read tools. The DataHub handle and
    the exact accessor for its GraphQL client come from the server's own helper;
    the ``getattr`` chain is a [confirm]: pin it to ``get_datahub_client()``'s real
    return shape when opening the PR.
    """
    from mcp_server_datahub.graphql_helpers import (
        get_datahub_client,  # type: ignore[import-not-found]
    )

    def raise_incident_tool(
        resource_urn: str,
        title: str,
        description: str,
        incident_type: str = "CUSTOM",
    ) -> str:
        """Raise a DataHub incident on a data asset and return its URN."""
        client = get_datahub_client()
        graph = getattr(client, "graph", None) or getattr(client, "_graph", None) or client
        return raise_incident(
            graph,
            resource_urn=resource_urn,
            title=title,
            description=description,
            incident_type=incident_type,
        )

    mcp.tool(
        name="raise_incident",
        description=raise_incident_tool.__doc__,
        annotations={"readOnlyHint": False},
    )(raise_incident_tool)


class _FakeGraph:
    """A graph stub for the offline self-check: records the last call, never networks."""

    def __init__(self) -> None:
        self.last_vars: dict[str, Any] | None = None

    def execute_graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.last_vars = variables
        return {"raiseIncident": "urn:li:incident:test"}


def demo() -> None:
    """Exercise the gating, the mlModel rejection, and the payload shape. No network."""
    dataset = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)"
    model = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"

    os.environ.pop(MUTATION_ENABLED_ENV, None)
    try:
        raise_incident(_FakeGraph(), resource_urn=dataset, title="t", description="d")
        raise AssertionError("expected MutationDisabledError when the flag is unset")
    except MutationDisabledError:
        pass

    os.environ[MUTATION_ENABLED_ENV] = "true"
    try:
        raise_incident(_FakeGraph(), resource_urn=model, title="t", description="d")
        raise AssertionError("expected an mlModel to be rejected")
    except ValueError:
        pass

    try:
        raise_incident(
            _FakeGraph(), resource_urn=dataset, title="t", description="d", incident_type="COLUMN"
        )
        raise AssertionError("expected COLUMN (not a real type) to be rejected")
    except ValueError:
        pass

    fake = _FakeGraph()
    urn = raise_incident(
        fake,
        resource_urn=dataset,
        title="Stale upstream",
        description="d",
        incident_type="FRESHNESS",
    )
    assert urn == "urn:li:incident:test", urn
    assert fake.last_vars == {
        "input": {
            "resourceUrn": dataset,
            "type": "FRESHNESS",
            "title": "Stale upstream",
            "description": "d",
        }
    }, fake.last_vars

    print("raise_incident_tool self-check: all assertions passed")


if __name__ == "__main__":
    demo()
