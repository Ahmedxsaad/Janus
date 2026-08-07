"""Graph reads more than one detector needs.

Every detector has to answer the same question about a model before it can decide
how much a finding matters: is anyone serving it, and is anyone on the hook for
it. That read lives here rather than in whichever detector happened to need it
first, so the detectors stay independent of each other.

Read-only, like everything under detect/.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OwnershipClass,
)
from datahub.metadata.urns import MlModelUrn, Urn

from janus.client import DataHubConnection
from janus.models import ModelRef


def entity_type(urn: str) -> str:
    """Return an URN's entity type.

    Taken from the URN because it is authoritative. ``LineageResult.type`` is a
    display string and must never be used for this.
    """
    return Urn.from_string(urn).entity_type


def live_deployments(conn: DataHubConnection, deployment_urns: tuple[str, ...]) -> tuple[str, ...]:
    """Return the deployments that are currently serving.

    A deployment whose properties aspect is missing is treated as not live: we
    escalate severity only on positive evidence that traffic is being served
    (docs/04-detectors.md).

    Batched in one call regardless of how many deployments a model has
    (docs/04-detectors.md): a blast-radius scan grades every at-risk model,
    and an N+1 read here would turn one scan into one GMS round trip per
    deployment of every model in the radius.
    """
    if not deployment_urns:
        return ()

    aspect_name = MLModelDeploymentPropertiesClass.ASPECT_NAME
    entities = conn.graph.get_entities(
        entity_name=entity_type(deployment_urns[0]),
        urns=list(deployment_urns),
        aspects=[aspect_name],
    )
    live: list[str] = []
    for urn in deployment_urns:
        entry = entities.get(urn, {}).get(aspect_name)
        properties = entry[0] if entry is not None else None
        if (
            isinstance(properties, MLModelDeploymentPropertiesClass)
            and properties.status == DeploymentStatusClass.IN_SERVICE
        ):
            live.append(urn)
    return tuple(live)


def has_owner(conn: DataHubConnection, entity_urn: str) -> bool:
    """Whether anyone is on the hook for this entity."""
    ownership = conn.graph.get_aspect(entity_urn, OwnershipClass)
    return bool(ownership and ownership.owners)


def model_ref(
    conn: DataHubConnection,
    model_urn: str,
    *,
    properties: MLModelPropertiesClass | None = None,
) -> ModelRef:
    """Read the model facts every detector needs to grade a finding.

    Deployments come from ``mlModelProperties``, not from lineage: the
    ``DeployedTo`` relationship does not declare ``isLineage``, so a deployment is
    unreachable by a lineage traversal. That read is what decides whether a model
    is scoring live traffic, which is what separates a production incident from a
    training-time concern.

    Args:
        conn: An open connection.
        model_urn: The model to describe.
        properties: The model's ``mlModelProperties``, if the caller already
            fetched it for its own purposes (blast_radius reads ``mlFeatures``
            from it, leakage reads it too). Passing it in avoids a second
            round trip for the same aspect; omit it to have this function fetch
            its own.
    """
    if properties is None:
        properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    deployments = tuple(properties.deployments or []) if properties else ()
    # DataHub's own mlflow ingestion leaves mlModelProperties.name unset, so the
    # fallback is what a real, ingested model actually reads as. The URN's name
    # segment is short and recognisable ("telco_churn_1"); the whole URN in the
    # middle of a sentence of prose is not.
    name = (properties.name if properties and properties.name else None) or MlModelUrn.from_string(
        model_urn
    ).name

    return ModelRef(
        urn=model_urn,
        name=name,
        deployments=deployments,
        live_deployments=live_deployments(conn, deployments),
        has_owner=has_owner(conn, model_urn),
    )
