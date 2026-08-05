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

import logging
import time

from datahub.metadata.schema_classes import (
    MLModelPropertiesClass,
    OperationClass,
)
from datahub.metadata.urns import DatasetUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.graph_reads import entity_type, model_ref
from modelguard.logs import logfmt, phase
from modelguard.models import (
    BlastRadius,
    FreshnessFinding,
    FreshnessSignal,
    ModelAtRisk,
)

logger = logging.getLogger(__name__)

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


def _model_at_risk(
    conn: DataHubConnection,
    model_urn: str,
    hops: int,
    downstream_features: frozenset[str],
) -> ModelAtRisk:
    """Describe one at-risk model by reading the aspects lineage does not carry."""
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    ref = model_ref(conn, model_urn, properties=properties)
    model_features = frozenset(properties.mlFeatures or []) if properties else frozenset()

    return ModelAtRisk(
        urn=ref.urn,
        name=ref.name,
        deployments=ref.deployments,
        live_deployments=ref.live_deployments,
        has_owner=ref.has_owner,
        hops=hops,
        # Only the model's own features that the failure actually reaches.
        features_at_risk=tuple(sorted(model_features & downstream_features)),
    )


def _downstream_traversal(
    conn: DataHubConnection,
    table_urn: str,
    config: ScanConfig,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[ModelAtRisk, ...], bool]:
    """Walk a table's downstream lineage: datasets, features, at-risk models.

    Unconditional on staleness. ``blast_radius`` below gates this on the table
    actually being stale, since that is the deterministic predicate the LLM is
    never allowed to make. ``downstream_models`` calls it directly, staleness
    aside, because reconciling a *recovered* finding needs exactly this list:
    which models this table's incident could have flagged, so their risk state
    can be cleared once the table is fresh again and there is no stale table
    left to traverse a fresh ``blast_radius`` from.

    Returns:
        The datasets, the features, the at-risk models, and whether the walk
        was truncated (F1, docs/plan/07): equality against the cap, not `>=`,
        since exactly-the-cap is the only observable signature that a result
        beyond it may exist.
    """
    # The sniff: the one phase of a scan that is slow enough for a person to see
    # it happen, so it is the one the desktop companion most wants to depict.
    # Identifiers and a hop cap only, never aspect content (modelguard/logs.py).
    logger.info(
        "lineage walk %s",
        logfmt({"urn": table_urn, "max_hops": config.max_hops}),
        extra=phase("sniffing", urn=table_urn, max_hops=config.max_hops),
    )
    results = conn.client.lineage.get_lineage(
        source_urn=table_urn,
        direction="downstream",
        max_hops=config.max_hops,
        count=config.lineage_result_cap,
    )
    truncated = len(results) == config.lineage_result_cap
    # DataHub returns beyond the cap once max_hops exceeds 2. Honor the cap.
    within_cap = [r for r in results if r.hops <= config.max_hops]

    # Deduplicated, for the same reason the model hops below are: that full-graph
    # search can return one entity via more than one path within the cap, and a
    # plain sorted() would list it twice and count it twice in "Downstream
    # datasets: N" on the impact report a human reads.
    datasets = sorted({r.urn for r in within_cap if entity_type(r.urn) == _DATASET})
    features = sorted({r.urn for r in within_cap if entity_type(r.urn) == _ML_FEATURE})
    # min(), not last-wins: DataHub's full-graph search past hop 2 (see the cap
    # comment above) can return the same model via more than one path within the
    # cap, in network-order rather than hop order. A dict comprehension would keep
    # whichever occurrence happened to come last, reporting a different "N hops
    # downstream" for the same graph state depending on response order.
    model_hops: dict[str, int] = {}
    for r in within_cap:
        if entity_type(r.urn) == _ML_MODEL:
            model_hops[r.urn] = min(r.hops, model_hops.get(r.urn, r.hops))

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
    return tuple(datasets), tuple(features), models, truncated


def upstream_datasets(
    conn: DataHubConnection,
    table_urn: str,
    config: ScanConfig,
) -> tuple[str, ...]:
    """Return the datasets a table is derived from, within the hop cap.

    The mirror of the traversal above and it lives here for that reason: two
    callers outside this module need it (which tables a freshness incident about
    this model's inputs could have landed on, T-16; which tables exist only to
    feed a model, T-18), and a second copy of a hop-capped lineage read is a
    second chance to get the cap wrong.

    Filtered by hop count rather than trusted, per rule 3: above two hops
    DataHub switches to a full-graph search and returns entities beyond the cap.
    Filtered to datasets by URN and never by ``LineageResult.type``, which is a
    display string (rule 4).
    """
    results = conn.client.lineage.get_lineage(
        source_urn=table_urn,
        direction="upstream",
        max_hops=config.max_hops,
        count=config.lineage_result_cap,
    )
    return tuple(
        sorted(
            {
                result.urn
                for result in results
                if result.hops <= config.max_hops and entity_type(result.urn) == _DATASET
            }
        )
    )


def downstream_models(
    conn: DataHubConnection,
    table_urn: str,
    config: ScanConfig,
) -> tuple[ModelAtRisk, ...]:
    """Return every model downstream of a table, regardless of its freshness.

    Used only for reconciliation: when a freshness incident on ``table_urn``
    resolves, this is how a caller learns which models' risk flags and tags to
    re-check, without a stale table to traverse a fresh ``blast_radius`` from.
    """
    _, _, models, _truncated = _downstream_traversal(conn, table_urn, config)
    return models


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

    datasets, features, models, truncated = _downstream_traversal(conn, failing_table_urn, config)

    return BlastRadius(
        signal=signal,
        failing_table_urn=failing_table_urn,
        failing_table_name=DatasetUrn.from_string(failing_table_urn).name,
        downstream_datasets=datasets,
        downstream_features=features,
        truncated=truncated,
        models=models,
    )


def finding_for(radius: BlastRadius) -> FreshnessFinding:
    """Shape a blast radius into the finding the write-back layer consumes.

    The incident attaches to the failing dataset, never to a model: DataHub's
    ``incidentInfo.entities`` relationship rejects mlModel with a 500. Model-level
    risk is carried by the tag and the structured properties instead.
    """
    return FreshnessFinding(blast_radius=radius)
