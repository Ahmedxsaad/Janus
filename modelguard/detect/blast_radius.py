"""P2: which models does a failing table put at risk?

Deterministic end to end. This module reads the graph, applies a threshold, and
returns typed findings. It never calls an LLM and never writes (detect/ is pure).

The traversal, verified against a live DataHub
----------------------------------------------
The plan flagged as unconfirmed whether ``get_lineage`` crosses out of the
warehouse and into ML entities. It does. Both edges that matter are declared with
``isLineage: true`` in the metadata model, so one downstream call spans the whole
supply chain::

    loans_raw --(UpstreamLineage)--> customer_features   hop 1, dataset
              --(MLFeature.sources: DerivedFrom)------->  hop 2, mlFeature
              --(MLModelProperties.mlFeatures: Consumes)-> hop 3, mlModel

A deployment is *not* reachable this way: ``MLModelProperties.deployments``
declares the ``DeployedTo`` relationship without ``isLineage``, so deployments
are read from the model's own aspect. That distinction decides severity, since a
model behind a live endpoint is the only one scoring live traffic on stale data.

Two behaviors of ``get_lineage`` are worth knowing. Above two hops DataHub
switches to a full-graph search and returns entities *beyond* ``max_hops`` (a
model group came back at hop 4 for a cap of 3), so results are filtered by hop
count here rather than trusted. And ``LineageResult.type`` is a display string;
the entity type is taken from the URN instead, which is authoritative.

Literature
----------
Sculley et al., "Hidden Technical Debt in Machine Learning Systems" (NeurIPS
2015), names the failure this detector addresses: *undeclared consumers*. A table
acquires model consumers that its owners never agreed to serve, so a change
upstream propagates into production scoring with nothing in the serving path to
signal it. Traversing the lineage graph is how those consumers get declared.
"""

from __future__ import annotations

import time

from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OwnershipClass,
)
from datahub.metadata.urns import DatasetUrn, Urn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.models import (
    BlastRadius,
    Finding,
    FindingType,
    FreshnessSignal,
    ModelAtRisk,
)

#: DataHub incident type for a table that stopped refreshing.
FRESHNESS_INCIDENT_TYPE = "FRESHNESS"

_DATASET = "dataset"
_ML_FEATURE = "mlFeature"
_ML_MODEL = "mlModel"


def _now_ms() -> int:
    """Return the current instant in epoch milliseconds."""
    return int(time.time() * 1000)


def freshness_signal(
    conn: DataHubConnection,
    dataset_urn: str,
    config: ScanConfig,
    *,
    now_ms: int | None = None,
) -> FreshnessSignal | None:
    """Measure how long a dataset has gone without changing.

    Reads the ``operation`` aspect, DataHub's own record of dataset change. It is
    a timeseries aspect, so it must be read with ``get_latest_timeseries_value``:
    ``get_aspect`` raises a TypeError for it.

    Args:
        conn: An open connection.
        dataset_urn: The dataset to measure.
        config: Supplies the freshness SLA.
        now_ms: The instant to measure against. Defaults to now.

    Returns:
        The measured signal, or None when the dataset has never reported an
        operation. Absence of evidence is not staleness: a table nobody has ever
        instrumented must not be reported as failing.
    """
    operation = conn.graph.get_latest_timeseries_value(dataset_urn, OperationClass, {})
    if operation is None or operation.lastUpdatedTimestamp is None:
        return None

    return FreshnessSignal(
        dataset_urn=dataset_urn,
        last_updated_ms=operation.lastUpdatedTimestamp,
        observed_at_ms=now_ms if now_ms is not None else _now_ms(),
        sla_hours=config.freshness_sla_hours,
    )


def _entity_type(urn: str) -> str:
    """Return an URN's entity type, the authoritative one, not the display name."""
    return Urn.from_string(urn).entity_type


def _live_deployments(conn: DataHubConnection, deployment_urns: list[str]) -> tuple[str, ...]:
    """Return the deployments that are currently serving.

    A deployment whose properties aspect is missing is treated as not live: we
    only escalate to CRITICAL on positive evidence that traffic is being served.
    """
    live: list[str] = []
    for urn in deployment_urns:
        properties = conn.graph.get_aspect(urn, MLModelDeploymentPropertiesClass)
        if properties is not None and properties.status == DeploymentStatusClass.IN_SERVICE:
            live.append(urn)
    return tuple(live)


def _has_owner(conn: DataHubConnection, entity_urn: str) -> bool:
    """Whether anyone is on the hook for this entity."""
    ownership = conn.graph.get_aspect(entity_urn, OwnershipClass)
    return bool(ownership and ownership.owners)


def _model_at_risk(
    conn: DataHubConnection,
    model_urn: str,
    hops: int,
    downstream_features: frozenset[str],
) -> ModelAtRisk:
    """Describe one at-risk model by reading the aspects lineage does not carry."""
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    deployments = tuple(properties.deployments or []) if properties else ()
    model_features = frozenset(properties.mlFeatures or []) if properties else frozenset()
    name = (properties.name if properties and properties.name else None) or model_urn

    return ModelAtRisk(
        urn=model_urn,
        name=name,
        hops=hops,
        deployments=deployments,
        live_deployments=_live_deployments(conn, list(deployments)),
        # Only the model's own features that the failure actually reaches.
        features_at_risk=tuple(sorted(model_features & downstream_features)),
        has_owner=_has_owner(conn, model_urn),
    )


def blast_radius(
    conn: DataHubConnection,
    failing_table_urn: str,
    config: ScanConfig,
    *,
    now_ms: int | None = None,
) -> BlastRadius | None:
    """Return everything a failing table endangers, or None when it is healthy.

    The freshness check gates the traversal: a fresh table has no blast radius to
    compute. This is the deterministic predicate the LLM is never allowed to
    make, and the reason a scan of a healthy graph writes nothing.

    Args:
        conn: An open connection.
        failing_table_urn: The dataset suspected of having stopped refreshing.
        config: Freshness SLA and hop cap.
        now_ms: The instant to measure staleness against. Defaults to now.

    Returns:
        The blast radius when the table is stale, otherwise None.
    """
    signal = freshness_signal(conn, failing_table_urn, config, now_ms=now_ms)
    if signal is None or not signal.is_stale:
        return None

    results = conn.client.lineage.get_lineage(
        source_urn=failing_table_urn,
        direction="downstream",
        max_hops=config.max_hops,
        count=config.lineage_result_cap,
    )
    # DataHub returns beyond the cap once max_hops exceeds 2. Honor the cap.
    within_cap = [r for r in results if r.hops <= config.max_hops]

    datasets = sorted(r.urn for r in within_cap if _entity_type(r.urn) == _DATASET)
    features = sorted(r.urn for r in within_cap if _entity_type(r.urn) == _ML_FEATURE)
    model_hops = {r.urn: r.hops for r in within_cap if _entity_type(r.urn) == _ML_MODEL}

    downstream_features = frozenset(features)
    models = tuple(
        sorted(
            (
                _model_at_risk(conn, urn, hops, downstream_features)
                for urn, hops in model_hops.items()
            ),
            key=ModelAtRisk.sort_key,
        )
    )

    return BlastRadius(
        signal=signal,
        failing_table_urn=failing_table_urn,
        failing_table_name=DatasetUrn.from_string(failing_table_urn).name,
        downstream_datasets=tuple(datasets),
        downstream_features=tuple(features),
        models=models,
    )


def finding_for(radius: BlastRadius) -> Finding:
    """Shape a blast radius into the finding the write-back layer consumes.

    The incident attaches to the failing dataset, never to a model: DataHub's
    ``incidentInfo.entities`` relationship rejects mlModel with a 500. Model-level
    risk is carried by the tag and the structured properties instead.
    """
    return Finding(
        finding_type=FindingType.UPSTREAM_FRESHNESS,
        resource_urn=radius.failing_table_urn,
        incident_type=FRESHNESS_INCIDENT_TYPE,
        severity=radius.severity,
        blast_radius=radius,
    )
