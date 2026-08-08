"""P3: does a model's input schema still match the one it was trained on?

Training-serving skew is the failure that hides in a passing pipeline. The model
was trained against a feature table with a given set of columns and types. Weeks
later a column is retyped, dropped, or added upstream, and the serving pipeline
feeds the live model values it was never trained to parse: a numeric feature
arrives as a string, a column the model expects is gone. Nothing errors. The
model keeps scoring, on inputs that no longer mean what they meant at training.

The training-time schema is not reconstructed from the catalog's version history,
which is fragile (versions compact, ingestion lag). It is captured *on the
training run* when the model is trained, and the detector diffs the input
dataset's current schema against that frozen snapshot. This is exactly how
TFX/TFDV guard against skew: freeze a schema at training, validate serving data
against it. Reading the graph is what makes the finding auditable: the snapshot
and the current schema are both facts DataHub holds.

Where the snapshot lives
------------------------
On the training run (a DataProcessInstance), in ``customProperties`` under the
name in ``config.training_schema_property``, as a JSON object keyed by input
dataset URN, each value a ``field_path -> native_type`` map. Keying by dataset
means a run with several inputs is diffed input-by-input against the schema each
input actually had, never against another input's.

Positive evidence only
----------------------
A run with no snapshot is skipped, not cleared: without a baseline there is
nothing to diff, and absence of a baseline is not absence of drift. An input
dataset with no current ``schemaMetadata`` is skipped for the same reason
(docs/04-detectors.md).

Literature
----------
Breck et al., "Data Validation for Machine Learning" (MLSys 2019), names this
failure and prescribes this remedy: a schema fixed at training time, against
which serving data is continuously validated. This detector is that check,
driven off metadata DataHub already holds.
"""

from __future__ import annotations

import json

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DataProcessInstancePropertiesClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn

from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.detect.graph_reads import model_ref
from janus.models import ChangeKind, ModelRef, SchemaChange, SchemaDriftFinding


def training_snapshot(
    conn: DataHubConnection,
    run_urn: str,
    property_name: str,
) -> dict[str, dict[str, str]] | None:
    """Return the schemas captured on a training run, keyed by input dataset URN.

    None when the run carries no snapshot, or when the snapshot is not the JSON
    object this detector wrote. A malformed snapshot is treated as absent rather
    than raising: a detector fires on positive evidence, and a value it cannot
    read is not evidence of drift.
    """
    properties = conn.graph.get_aspect(run_urn, DataProcessInstancePropertiesClass)
    if properties is None or not properties.customProperties:
        return None

    raw = properties.customProperties.get(property_name)
    if raw is None:
        return None

    try:
        snapshot = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(snapshot, dict):
        return None
    return snapshot


def _current_schema(conn: DataHubConnection, dataset_urn: str) -> dict[str, str] | None:
    """Return a dataset's current schema as field path to native type, or None.

    None when the dataset has no ``schemaMetadata``. A dataset whose schema was
    never recorded cannot be judged to have drifted (docs/04-detectors.md).
    """
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    if schema is None:
        return None
    return {field.fieldPath: field.nativeDataType for field in schema.fields}


def diff_schema(
    training: dict[str, str],
    current: dict[str, str],
) -> tuple[SchemaChange, ...]:
    """Return every column that differs between the training and current schema.

    Ordered by field path so a report reads identically on every run. A column
    present in both with the same native type is not a change and is omitted:
    this is what makes an unchanged schema produce no finding.
    """
    changes: list[SchemaChange] = []
    for field_path in sorted(training.keys() | current.keys()):
        training_type = training.get(field_path)
        current_type = current.get(field_path)

        if training_type is None:
            changes.append(SchemaChange(field_path, ChangeKind.ADDED, None, current_type))
        elif current_type is None:
            changes.append(SchemaChange(field_path, ChangeKind.REMOVED, training_type, None))
        elif training_type != current_type:
            changes.append(
                SchemaChange(field_path, ChangeKind.RETYPED, training_type, current_type)
            )
    return tuple(changes)


def _run_findings(
    conn: DataHubConnection,
    model: ModelRef,
    run_urn: str,
    config: ScanConfig,
) -> list[SchemaDriftFinding]:
    """Return the drift findings for one training run of a model."""
    snapshot = training_snapshot(conn, run_urn, config.training_schema_property)
    if snapshot is None:
        return []

    inputs = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
    input_urns = list(inputs.inputs or []) if inputs else []

    findings: list[SchemaDriftFinding] = []
    for dataset_urn in input_urns:
        training_schema = snapshot.get(dataset_urn)
        if training_schema is None:
            # This input had no baseline captured, so it cannot be judged. Another
            # input on the same run still can. A baseline captured as genuinely
            # empty (`{}`) is positive evidence, not absence, and falls through to
            # be diffed below: every current column would then be a real ADDED
            # change relative to a training-time schema of zero columns.
            continue

        current_schema = _current_schema(conn, dataset_urn)
        if current_schema is None:
            continue

        changes = diff_schema(training_schema, current_schema)
        if not changes:
            continue

        findings.append(
            SchemaDriftFinding(
                model=model,
                training_run_urn=run_urn,
                dataset_urn=dataset_urn,
                dataset_name=DatasetUrn.from_string(dataset_urn).name,
                changes=changes,
            )
        )
    return findings


def schema_drift_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[SchemaDriftFinding, ...]:
    """Return every input dataset whose schema drifted from a model's training.

    Deterministic and read-only. The LLM is never asked whether a schema drifted;
    it is told that one did and writes the prose around it.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Supplies the training-schema property name.

    Returns:
        One finding per drifted input dataset, ordered by dataset name so a report
        reads identically on every run. Empty when every input still matches the
        schema the model was trained on, which is the answer a clean model gives.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.trainingJobs:
        return ()

    model = model_ref(conn, model_urn, properties=properties)

    findings: list[SchemaDriftFinding] = []
    for run_urn in properties.trainingJobs:
        findings.extend(_run_findings(conn, model, run_urn, config))

    return tuple(sorted(findings, key=lambda finding: finding.dataset_name))


def schema_drift_candidate_resources(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[str, ...]:
    """Return every input dataset this detector could raise a drift incident on.

    Present whether or not the dataset is currently drifting: an input with a
    captured training-time snapshot is a resource ``schema_drift_findings``
    could name, which is exactly what reconciliation needs to check whether a
    previously-raised incident on it is now stale.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.trainingJobs:
        return ()

    resources: list[str] = []
    for run_urn in properties.trainingJobs:
        snapshot = training_snapshot(conn, run_urn, config.training_schema_property)
        if snapshot is None:
            continue
        inputs = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
        for dataset_urn in list(inputs.inputs or []) if inputs else []:
            if snapshot.get(dataset_urn) is not None:
                resources.append(dataset_urn)
    return tuple(resources)
