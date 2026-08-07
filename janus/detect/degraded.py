"""The degraded mode: what is knowable about a model nobody has linked.

Every other detector in this package starts from a model's declared features and
walks the column graph. A model with no declared features has no columns to walk
from, so those detectors correctly return nothing and
:mod:`janus.detect.coverage` correctly says why. That is honest, and on a
stranger's catalog it is also the whole answer: nothing was checked, come back
after somebody runs ``janus link``.

This module is the weaker answer offered in the meantime. Without column-level
lineage Janus can still read three facts about the *tables* a model trains
on, all of which DataHub already holds:

* the table stopped refreshing (its ``operation`` aspect, the same signal P2
  uses),
* its owners marked it deprecated (its ``deprecation`` aspect, the same signal
  P6 uses),
* it holds a column the organization classified as restricted (the same tags and
  terms P5 looks for, read on the table's own columns rather than walked to).

What it cannot say is which feature carries any of that into the model, which is
the entire value of the column-level mode. So every finding carries the mode, the
limitation, and the mode's *measured* precision from the benchmark's table-level
baseline, and its first remedy is to declare the link .

Where the model's tables come from
----------------------------------
Two routes, both declarations rather than inferences: the inputs recorded on the
model's training runs (``dataProcessInstanceInput``, which is what
:mod:`janus.detect.governance` already reads), and the datasets the catalog
itself declares one hop upstream of the model, which Spark and some ingestion
sources emit. A model neither route reaches has nothing here either, and that is
reported by ``coverage`` exactly as before.

Read-only, deterministic, and no LLM, like the rest of detect/.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    DeprecationClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, SchemaFieldUrn

from janus.client import DataHubConnection
from janus.config import TABLE_LEVEL_PRECISION, ScanConfig
from janus.detect.blast_radius import freshness_signal
from janus.detect.governance import model_input_datasets, sensitive_index
from janus.detect.graph_reads import model_ref
from janus.detect.leakage import feature_source_column
from janus.models import (
    ModelRef,
    TableLevelRiskFinding,
    TableRisk,
)


def has_column_link(conn: DataHubConnection, properties: MLModelPropertiesClass | None) -> bool:
    """Whether this model can be answered at column level at all.

    Features alone are not enough: a feature that carries no source column is a
    feature the column-level detectors skip, so a model whose features all lack
    one is exactly as unanswerable as one with no features. The degraded mode is
    for both.
    """
    if properties is None or not properties.mlFeatures:
        return False
    return any(feature_source_column(conn, urn) is not None for urn in properties.mlFeatures)


def _upstream_datasets(
    conn: DataHubConnection, model_urn: str, config: ScanConfig
) -> tuple[str, ...]:
    """Return datasets the catalog declares one hop upstream of the model.

    ``max_hops=1`` is fixed, not ``config.max_hops``: this is a direct
    declaration Spark and some ingestion sources emit, not a depth-bounded
    walk, and widening it would start pulling in tables the model does not
    itself train on. ``count`` still comes from config, like every other
    ``get_lineage`` call in this package (docs/04-detectors.md): a model
    with a wide fan-in of declared upstream tables must not have that list
    silently capped at the SDK's own default.
    """
    results = conn.client.lineage.get_lineage(
        source_urn=model_urn,
        direction="upstream",
        max_hops=1,
        count=config.lineage_result_cap,
    )
    return tuple(
        sorted(
            {str(result.urn) for result in results if str(result.urn).startswith("urn:li:dataset:")}
        )
    )


def training_tables(conn: DataHubConnection, model_urn: str, config: ScanConfig) -> tuple[str, ...]:
    """Return every table this model is declared to train on, however declared.

    Both routes are unioned rather than ordered by confidence: unlike
    ``link --infer``, nothing here has to pick one table, so a model that
    declares two inputs gets both checked instead of one chosen.
    """
    found = set(model_input_datasets(conn, model_urn)) | set(
        _upstream_datasets(conn, model_urn, config)
    )
    return tuple(sorted(found))


def _stale(
    conn: DataHubConnection,
    config: ScanConfig,
    dataset_urn: str,
    now_ms: int | None,
) -> dict[str, str] | None:
    """Measure the table's staleness, or return None when it is inside its SLA."""
    signal = freshness_signal(conn, dataset_urn, config, now_ms=now_ms)
    if signal is None or not signal.is_stale:
        return None
    return {
        "lag_hours": f"{signal.lag_hours:.1f}",
        "sla_hours": f"{signal.sla_hours:.1f}",
        "last_updated_ms": str(signal.last_updated_ms),
    }


def _deprecated(conn: DataHubConnection, dataset_urn: str) -> dict[str, str] | None:
    """Read the table's deprecation, or return None when it is not deprecated.

    ``deprecated=false`` is how DataHub records a *withdrawn* deprecation, so the
    aspect's presence is never the signal.
    """
    aspect = conn.graph.get_aspect(dataset_urn, DeprecationClass)
    if aspect is None or not aspect.deprecated:
        return None
    measurement = {}
    if aspect.note:
        measurement["deprecation_note"] = aspect.note
    if aspect.decommissionTime is not None:
        measurement["decommission_time_ms"] = str(aspect.decommissionTime)
    return measurement


def _classified(
    conn: DataHubConnection,
    config: ScanConfig,
    dataset_urn: str,
) -> dict[str, str] | None:
    """Return the table's classified columns, or None when it holds none.

    The table's own columns, checked directly against the classification index.
    No walk: at table level there is no derivation to follow, which is precisely
    the difference between this and P5.
    """
    index = sensitive_index(conn, config)
    if not index.configured:
        return None
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    if schema is None:
        return None

    dataset = DatasetUrn.from_string(dataset_urn)
    marked = [
        field.fieldPath
        for field in schema.fields
        if index.is_marked(str(SchemaFieldUrn(parent=dataset, field_path=field.fieldPath)))
    ]
    if not marked:
        return None
    return {
        "classified_columns": ", ".join(sorted(marked)),
        "classified_column_count": str(len(marked)),
    }


def table_level_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
    *,
    now_ms: int | None = None,
) -> tuple[TableLevelRiskFinding, ...]:
    """Return what can be said about a model that has no column-level link.

    Runs only in the degraded case. A model whose features reach source columns is
    answered by the four column-level detectors, and running this beside them
    would report the same table twice, once with proof and once with a maybe.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Supplies the freshness SLA and the classification URNs.
        now_ms: The instant staleness is measured against. Defaults to now.

    Returns:
        One finding per (table, risk), ordered by table then by risk so a report
        reads the same way twice. Empty when the model can be answered at column
        level, when nothing declares what it trains on, or when every table it
        trains on is fine.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if has_column_link(conn, properties):
        return ()

    tables = training_tables(conn, model_urn, config)
    if not tables:
        return ()

    model: ModelRef = model_ref(conn, model_urn, properties=properties)
    findings: list[TableLevelRiskFinding] = []
    for dataset_urn in tables:
        name = DatasetUrn.from_string(dataset_urn).name
        for risk, measurement in (
            (TableRisk.STALE, _stale(conn, config, dataset_urn, now_ms)),
            (TableRisk.DEPRECATED, _deprecated(conn, dataset_urn)),
            (TableRisk.CLASSIFIED, _classified(conn, config, dataset_urn)),
        ):
            if measurement is None:
                continue
            findings.append(
                TableLevelRiskFinding(
                    model=model,
                    risk=risk,
                    dataset_urn=dataset_urn,
                    dataset_name=name,
                    measurement=measurement,
                    precision=TABLE_LEVEL_PRECISION,
                )
            )
    return tuple(findings)
