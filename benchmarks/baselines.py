"""What the same graph looks like to tools that lack column-level lineage.

ModelGuard's central claim is that only cross-boundary, *column-level* lineage
both roots a failure to the exact upstream column and names the model at risk.
Everywhere else that claim is argued. Here it is measured, by running the same
trials through the approaches a team would otherwise reach for and scoring all of
them against the same ground truth.

Writing your own opposition
---------------------------
A baseline built to lose proves nothing, so these are written as the honest
version of each approach rather than as strawmen. The table-level detector reuses
ModelGuard's own label index and its own source-column resolution: it is handed
exactly the same facts, and differs in one respect only, that it asks lineage
questions of *tables* where ModelGuard asks them of *columns*. Every advantage
that could be shared is shared.

What these are not
------------------
They are implementations of an *approach*, not of a product. Nothing here runs
Great Expectations, Deequ, Evidently or NannyML, and RESULTS.md says so. Claiming
to have benchmarked somebody's tool by writing a hundred lines in its general
spirit would be the same overstatement this benchmark exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass

from datahub.metadata.schema_classes import (
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, SchemaFieldUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.leakage import _LabelIndex, feature_source_column


@dataclass(frozen=True)
class Approach:
    """One way of answering "which of this model's features leak?"."""

    key: str
    name: str
    sees_column_lineage: bool
    note: str
    """One line on what the approach can and cannot see, quoted into RESULTS.md."""


COLUMN_LEVEL = Approach(
    key="column-level",
    name="ModelGuard (column-level lineage)",
    sees_column_lineage=True,
    note="follows each feature's own column cone to the declared label column",
)
TABLE_LEVEL = Approach(
    key="table-level",
    name="Table-level lineage",
    sees_column_lineage=False,
    note="knows the feature table derives from the labelled table, not which column does",
)
NO_LINEAGE = Approach(
    key="no-lineage",
    name="Table quality checks, no lineage",
    sees_column_lineage=False,
    note="can assert the table is stale or malformed; has no path to a model at all",
)


def _dataset_has_label_column(
    conn: DataHubConnection, dataset_urn: str, labels: _LabelIndex
) -> bool:
    """Whether any column of a dataset is declared to be a label.

    This is the question a table-level tool can actually answer: the catalog says
    the table holds the label, but not which of the downstream columns descends
    from it.
    """
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    if schema is None:
        return False

    dataset = DatasetUrn.from_string(dataset_urn)
    return any(
        labels.is_label(str(SchemaFieldUrn(parent=dataset, field_path=field.fieldPath)))
        for field in schema.fields
    )


def table_level_leakage(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[str, ...]:
    """Flag every feature whose source *table* descends from a table holding the label.

    The honest reading of the evidence a table-level tool has. It can see that
    ``customer_features`` derives from ``loans_raw``, and that ``loans_raw`` holds
    the label. It cannot see which column of ``customer_features`` descends from
    which column of ``loans_raw``. Having no way to separate them, it must treat
    every feature of that table as suspect, which is the correct conservative
    behaviour for a tool with this much information, and is precisely the cost.

    Returns:
        The feature URNs flagged, sorted, so a report reads the same every run.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.mlFeatures:
        return ()

    labels = _LabelIndex(conn, config)
    flagged: list[str] = []

    for feature_urn in properties.mlFeatures:
        # The same resolution ModelGuard uses, so the comparison is not decided by
        # one approach being handed a better starting point than the other.
        source_column = feature_source_column(conn, feature_urn)
        if source_column is None:
            continue

        feature_table = str(SchemaFieldUrn.from_string(source_column).parent)
        upstream_tables = {
            result.urn
            for result in conn.client.lineage.get_lineage(
                source_urn=feature_table,
                direction="upstream",
                max_hops=config.leakage_max_hops,
                count=config.lineage_result_cap,
            )
        }
        if any(
            _dataset_has_label_column(conn, table, labels)
            for table in upstream_tables | {feature_table}
        ):
            flagged.append(feature_urn)

    return tuple(sorted(flagged))


def no_lineage_leakage(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[str, ...]:
    """Flag nothing: leakage is not expressible without lineage.

    Stated as a function rather than as a footnote so it is scored on the same
    trials as the others and appears in the same table. A tool that asserts on
    column values can tell you ``default_status`` is null more often than it was.
    It has no way to reach the sentence "and a feature of a live credit model was
    computed from it", because that sentence is a path, and it holds no paths.

    This one is true by construction rather than by measurement, and RESULTS.md
    labels it that way.
    """
    return ()


#: The leakage approaches, in the order RESULTS.md compares them.
LEAKAGE_APPROACHES = (
    (COLUMN_LEVEL, None),
    (TABLE_LEVEL, table_level_leakage),
    (NO_LINEAGE, no_lineage_leakage),
)
