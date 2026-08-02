"""P5/P6: is this model allowed to have learned from this data, and for how long?

The other detectors ask whether a model's data is *correct*: fresh, undrifted,
not contaminated by its own label. This one asks a question the organization has
already answered somewhere else in the graph, and that nothing joins back to the
model:

* **Sensitive source (P5).** Somebody classified a column as PII, PHI, a
  protected attribute, or whatever their taxonomy calls restricted. Three joins
  downstream, a feature derives from it, and a model trains on that feature.
  Nothing is broken. The model works. What is wrong is what it was allowed to
  see, and the derivation is far enough from the classified column that nobody
  looking at either end would notice.
* **Deprecated input (P6).** A table's own owners marked it deprecated, with a
  note and sometimes a decommission date. The people who set that flag have no
  way to know a model depends on the table, and the team that owns the model has
  no way to know the flag was set.

Both are failures of the same kind, and DataHub is the only place they are
findable: the classification and the deprecation live on the data side, the model
lives on the ML side, and the column-level lineage between them is what turns two
unrelated facts into one finding. Neither needs a training run, a query against
the warehouse, or a single row of data.

Why these are configured, and not on by default
-----------------------------------------------
The sensitive check does nothing until ``MODELGUARD_SENSITIVE_TERM_URNS`` or
``MODELGUARD_SENSITIVE_TAG_URNS`` names the organization's own classification.
There is no default worth shipping: every catalog uses its own taxonomy, so a
guess either matches nothing or matches a term that means something else
somewhere, and a false incident about a compliance exposure is the worst kind to
be wrong about. Unset means the check reports itself as not evaluated, never as
passed (see :mod:`modelguard.detect.coverage`).

The deprecation check needs no configuration, because ``deprecation`` is
DataHub's own aspect with one meaning everywhere.

Literature
----------
Sculley et al., "Hidden Technical Debt in Machine Learning Systems" (NeurIPS
2015), names undeclared consumers as a principal source of ML debt: a table
acquires consumers its owners never agreed to serve, and neither side can see
the dependency. A classified column feeding a model, and a deprecated table
feeding one, are both exactly that, made visible by reading the lineage the
catalog already holds.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DeprecationClass,
    MLModelPropertiesClass,
)
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.column_marks import ColumnMarkIndex, marked_ancestor
from modelguard.detect.graph_reads import model_ref
from modelguard.detect.leakage import feature_source_column
from modelguard.models import (
    DeprecatedInputFinding,
    ModelRef,
    SensitiveFeature,
    SensitiveSourceFinding,
)


def sensitive_index(conn: DataHubConnection, config: ScanConfig) -> ColumnMarkIndex:
    """Return the index of columns the organization classified as restricted.

    Terms and tags both, because catalogs classify through either surface and
    plenty use only one. An index built from an empty configuration reports
    itself as unconfigured rather than matching nothing quietly, so a caller can
    skip the traversal instead of walking a graph for a mark that cannot exist.
    """
    return ColumnMarkIndex(
        conn,
        terms=frozenset(config.sensitive_term_urns),
        tags=frozenset(config.sensitive_tag_urns),
    )


def sensitive_source_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[SensitiveSourceFinding, ...]:
    """Return every feature of a model that derives from a classified column.

    Deterministic and read-only. The LLM is never asked whether a feature is
    exposed; it is told that one is and writes the prose around it.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Supplies the classification URNs and the hop cap.

    Returns:
        One finding per exposed feature, ordered by the exposed column's name so
        a report reads identically on every run. Empty when no feature's lineage
        reaches a classified column, and also empty when nothing is configured to
        look for, which coverage reports separately as a check that never ran.
    """
    index = sensitive_index(conn, config)
    if not index.configured:
        return ()

    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.mlFeatures:
        return ()

    model = model_ref(conn, model_urn, properties=properties)

    findings: list[SensitiveSourceFinding] = []
    for feature_urn in properties.mlFeatures:
        source_column = feature_source_column(conn, feature_urn)
        if source_column is None:
            continue

        walk = marked_ancestor(conn, source_column, index, config)
        if walk.hit is None:
            continue

        sensitive_urn, marker_urn, column_path = walk.hit
        findings.append(
            _sensitive_finding(
                model, feature_urn, source_column, sensitive_urn, marker_urn, column_path
            )
        )

    return tuple(sorted(findings, key=lambda finding: finding.exposure.source_column_name))


def _sensitive_finding(
    model: ModelRef,
    feature_urn: str,
    source_column_urn: str,
    sensitive_column_urn: str,
    marker_urn: str,
    column_path: tuple[str, ...],
) -> SensitiveSourceFinding:
    """Assemble one finding from the columns the traversal actually proved."""
    source_field = SchemaFieldUrn.from_string(source_column_urn)
    sensitive_field = SchemaFieldUrn.from_string(sensitive_column_urn)

    return SensitiveSourceFinding(
        model=model,
        exposure=SensitiveFeature(
            feature_urn=feature_urn,
            feature_name=MlFeatureUrn.from_string(feature_urn).name,
            source_column_urn=source_column_urn,
            source_column_name=source_field.field_path,
            sensitive_column_urn=sensitive_column_urn,
            sensitive_column_name=sensitive_field.field_path,
            sensitive_dataset_name=DatasetUrn.from_string(sensitive_field.parent).name,
            marker_urn=marker_urn,
            column_path=column_path,
        ),
    )


def model_input_datasets(conn: DataHubConnection, model_urn: str) -> tuple[str, ...]:
    """Return the datasets a model's training runs read, deduplicated and ordered.

    The join between a model and its data on the run side rather than the feature
    side: ``dataProcessInstanceInput`` on each training run. A model trained more
    than once, or on more than one table, contributes each input once.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.trainingJobs:
        return ()

    seen: dict[str, None] = {}
    for run_urn in properties.trainingJobs:
        inputs = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
        for dataset_urn in (inputs.inputs or []) if inputs else []:
            seen.setdefault(dataset_urn, None)
    return tuple(sorted(seen))


def deprecated_input_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[DeprecatedInputFinding, ...]:
    """Return every training input its owners have marked deprecated.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Unused today; taken so this detector has the same signature as
            its siblings and `_detect` calls all of them the same way.

    Returns:
        One finding per deprecated input, ordered by dataset name. Empty when no
        input carries the aspect, or carries it with ``deprecated=False``, which
        is DataHub's way of recording that a deprecation was lifted and is
        positive evidence of health rather than absence of evidence.
    """
    del config

    input_urns = model_input_datasets(conn, model_urn)
    if not input_urns:
        return ()

    model = model_ref(conn, model_urn)

    findings: list[DeprecatedInputFinding] = []
    for dataset_urn in input_urns:
        deprecation = conn.graph.get_aspect(dataset_urn, DeprecationClass)
        if deprecation is None or not deprecation.deprecated:
            continue
        findings.append(
            DeprecatedInputFinding(
                model=model,
                dataset_urn=dataset_urn,
                dataset_name=DatasetUrn.from_string(dataset_urn).name,
                note=deprecation.note or "",
                decommission_time_ms=deprecation.decommissionTime,
            )
        )

    return tuple(sorted(findings, key=lambda finding: finding.dataset_name))
