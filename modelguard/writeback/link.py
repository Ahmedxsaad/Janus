"""Connect a model to the data it was trained on, so the detectors can see it.

Why this exists
---------------
Every detector in this package reads a link between a model and a warehouse
column: leakage walks a feature's source column upstream, drift diffs a training
run's captured schema against the current one, and the blast radius arrives at a
model through its features. On the seeded demo graph those links are there
because ``modelguard-seed`` wrote them.

On a real project they are not there, and nothing in the ecosystem writes them.
Verified end to end (D-074) on an ordinary stack: postgres holding the warehouse,
dbt building the feature and label tables, MLflow tracking the training run, and
DataHub's own postgres, dbt and mlflow ingestion sources run against all three.
That produces excellent column-level lineage between the *tables*, and an mlModel
that knows nothing at all: no features, no inputs on its training run, no link to
a single column. A scan of that model can therefore only report that it had
nothing to check.

So this module is the bridge, and it is deliberately a one-line command a
training script can call rather than a document telling somebody to write SDK
code. It declares, from facts the caller already knows at training time:

* the model's features, one ``mlFeature`` per column of the feature table, each
  carrying the exact source column it came from;
* which column is the label, as a glossary term, propagated up the label's own
  lineage so a feature derived from the same ancestor as the label is caught;
* the training run's inputs and the schema those inputs had at training time.

Everything here is idempotent by read-merge-emit, like the rest of writeback/:
running it again after a retrain converges rather than duplicating.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    AuditStampClass,
    DataProcessInstanceInputClass,
    DataProcessInstancePropertiesClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from modelguard.discovery import search_model_urns
from modelguard.writeback.properties import (
    EXCLUDED_COLUMNS,
    FEATURE_TABLE,
    LABEL_COLUMN,
    assign_properties,
    define_properties,
    read_properties,
)
from modelguard.writeback.terms import add_term, ensure_term

#: Name and definition written when the configured label term does not exist yet.
#: A bare URN renders as a bare URN in the UI, and a person who finds it on their
#: column deserves to be able to read what it claims.
LABEL_TERM_NAME = "ML Label"
LABEL_TERM_DEFINITION = (
    "The column a model is trained to predict. ModelGuard treats any feature "
    "whose upstream column lineage reaches a column carrying this term as target "
    "leakage: the feature would not be knowable at prediction time."
)


#: Structured properties recording what `link` was told, so it can be replayed.
#: Declared in properties.py with the rest of the registry, and aliased here
#: under the names this module has always used. See props/modelguard_props.yaml
#: for why they do not live on mlModelProperties.
PROP_FEATURE_TABLE = FEATURE_TABLE
PROP_LABEL_COLUMN = LABEL_COLUMN
PROP_EXCLUDED = EXCLUDED_COLUMNS


@dataclass(frozen=True)
class RecordedLink:
    """A previous ``link`` call, read back off the model."""

    feature_dataset_urn: str
    label_column_urn: str
    exclude: frozenset[str]


def recorded_link(conn: DataHubConnection, model_urn: str) -> RecordedLink | None:
    """Return what ``link`` was last told about this model, or None.

    This is what makes re-linking a one-argument command. An ingestion run
    rewrites mlModelProperties wholesale and drops the features (D-074), so on a
    real project the link has to be replayed after every ingest; asking somebody
    to keep the original arguments in their head, or in a runbook, is asking for
    the wrong table to be linked six months later.
    """
    values = read_properties(conn, model_urn)
    table = values.get(PROP_FEATURE_TABLE)
    label = values.get(PROP_LABEL_COLUMN)
    if not table or not label:
        return None
    return RecordedLink(
        feature_dataset_urn=str(table[0]),
        label_column_urn=str(label[0]),
        exclude=frozenset(str(value) for value in values.get(PROP_EXCLUDED, [])),
    )


def models_with_recorded_link(conn: DataHubConnection) -> tuple[tuple[str, RecordedLink], ...]:
    """Return every model in the graph that a previous ``link`` call described.

    This is what makes "replay the links after tonight's ingestion" one command
    rather than one per model. A model nobody has linked is skipped rather than
    guessed at: there is no honest default for which table a model trained on.

    Discovery goes through :mod:`modelguard.discovery` rather than plain search
    because the ingestion run that drops the link is frequently the same one
    that registers a new version and makes this model non-latest. Plain search
    hides it from that moment on, so the replay this command promises would skip
    exactly the models that most needed it (D-100).
    """
    found: list[tuple[str, RecordedLink]] = []
    for urn in search_model_urns(conn):
        model_urn = str(urn)
        previous = recorded_link(conn, model_urn)
        if previous is not None:
            found.append((model_urn, previous))
    return tuple(sorted(found, key=lambda pair: pair[0]))


class LinkError(ValueError):
    """The model, the feature table, or the label column cannot be linked."""


@dataclass(frozen=True)
class LinkResult:
    """What linking declared, so the caller can print it and a test can assert it."""

    model_urn: str
    feature_urns: tuple[str, ...]
    label_column_urns: tuple[str, ...]
    """The label column, plus the ancestors its own lineage reaches. A feature is
    leakage if it descends from any of them, which is what makes a label declared
    on a downstream label table catch a feature built from the raw outcome."""
    training_runs: tuple[str, ...]
    snapshot_columns: int
    """Columns captured in the training-time schema, across every run."""


def _columns(conn: DataHubConnection, dataset_urn: str) -> dict[str, str]:
    """Return a dataset's columns as field path to native type.

    Raises:
        LinkError: The dataset has no schema recorded, so there are no columns to
            declare. Guessing them is not an option: a feature that does not
            exist would be declared to a detector as if it did.
    """
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    if schema is None or not schema.fields:
        raise LinkError(
            f"{dataset_urn} has no schemaMetadata in DataHub, so its columns are "
            "unknown. Ingest the dataset before linking a model to it."
        )
    return {field.fieldPath: field.nativeDataType for field in schema.fields}


def _label_lineage(
    conn: DataHubConnection,
    label_column_urn: str,
    config: ScanConfig,
) -> tuple[str, ...]:
    """Return the label column and every column its own lineage descends from.

    A warehouse almost never puts the label and the features in one table. The
    label lands in its own mart, built from the same raw outcome column a
    carelessly-built feature is built from, so the feature's upstream cone passes
    through that raw column and never through the label mart at all. Declaring
    only the mart's column would leave the detector looking in a place no feature
    can reach, and it would report a leaking model clean.

    Declaring the ancestors too is not an inference about intent: each of those
    columns *is* where this label's values come from, by DataHub's own lineage.
    """
    field = SchemaFieldUrn.from_string(label_column_urn)
    results = conn.client.lineage.get_lineage(
        source_urn=field.parent,
        source_column=field.field_path,
        direction="upstream",
        max_hops=config.leakage_max_hops,
        count=config.lineage_result_cap,
    )

    columns = {label_column_urn}
    for result in results:
        if result.hops > config.leakage_max_hops:
            continue
        for step in result.paths or []:
            if step.urn.startswith("urn:li:schemaField:"):
                columns.add(step.urn)
    return tuple(sorted(columns))


def _declare_features(
    conn: DataHubConnection,
    feature_dataset_urn: str,
    columns: dict[str, str],
    *,
    dry_run: bool,
) -> tuple[str, ...]:
    """Create one mlFeature per column, each naming the column it came from.

    The namespace is the feature table's own name, so the URNs are stable across
    reruns and readable in the UI, and two models trained on the same table share
    one set of features rather than each minting its own.
    """
    namespace = DatasetUrn.from_string(feature_dataset_urn).name
    urns: list[str] = []
    for column in sorted(columns):
        feature_urn = str(MlFeatureUrn(namespace, column))
        urns.append(feature_urn)
        if dry_run:
            continue
        conn.graph.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=feature_urn,
                aspect=MLFeaturePropertiesClass(
                    description=f"{column} of {namespace}",
                    sources=[feature_dataset_urn],
                    customProperties={
                        SOURCE_COLUMN_PROPERTY: str(SchemaFieldUrn(feature_dataset_urn, column))
                    },
                ),
            )
        )
    return tuple(urns)


def _attach_features(
    conn: DataHubConnection,
    model_urn: str,
    feature_urns: tuple[str, ...],
    *,
    dry_run: bool,
) -> MLModelPropertiesClass:
    """Merge the features onto the model, keeping whatever was already there.

    Read-merge-emit: mlModelProperties is an upsert of the whole aspect, so a
    blind write would drop the training run MLflow's own ingestion recorded.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None:
        raise LinkError(
            f"{model_urn} has no mlModelProperties in DataHub. Ingest the model "
            "before linking it (DataHub's mlflow source does this)."
        )

    merged = sorted(set(properties.mlFeatures or ()) | set(feature_urns))
    if merged == list(properties.mlFeatures or ()) or dry_run:
        return properties

    properties.mlFeatures = merged
    conn.graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=model_urn, aspect=properties))
    return properties


def _capture_training_schema(
    conn: DataHubConnection,
    run_urn: str,
    feature_dataset_urn: str,
    schema: dict[str, str],
    config: ScanConfig,
    *,
    dry_run: bool,
) -> int:
    """Record the run's input and the schema that input has right now.

    ``schema`` is the input dataset's whole schema, not the feature subset: drift
    diffs a dataset against its baseline, so a baseline missing the columns the
    model chose not to use would report every one of them as newly added.

    "Right now" is the honest description, and it is why this belongs in a
    command a training script calls: run at training time, now *is* training
    time, and the snapshot is the baseline drift is measured against. Run it
    later and it would baseline a schema the model never saw, which is why
    nothing here tries to reconstruct a past schema from version history.
    """
    if dry_run:
        return len(schema)

    existing = conn.graph.get_aspect(run_urn, DataProcessInstancePropertiesClass)
    properties = existing or DataProcessInstancePropertiesClass(
        name=run_urn.split(":")[-1],
        created=AuditStampClass(time=int(time.time() * 1000), actor="urn:li:corpuser:datahub"),
    )
    custom = dict(properties.customProperties or {})
    custom[config.training_schema_property] = json.dumps({feature_dataset_urn: schema})
    properties.customProperties = custom
    conn.graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=run_urn, aspect=properties))

    inputs = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
    current = list(inputs.inputs) if inputs and inputs.inputs else []
    if feature_dataset_urn not in current:
        conn.graph.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=run_urn,
                aspect=DataProcessInstanceInputClass(inputs=[*current, feature_dataset_urn]),
            )
        )
    return len(schema)


def link_model(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    model_urn: str,
    feature_dataset_urn: str,
    label_column_urn: str,
    exclude: frozenset[str] = frozenset(),
    dry_run: bool = False,
) -> LinkResult:
    """Declare a model's features, its label, and its training-time schema.

    Args:
        conn: An open connection.
        config: Supplies the label term URN, the hop cap for the label's own
            lineage, and the name the training snapshot is stored under.
        model_urn: The model to link. It must already exist in DataHub.
        feature_dataset_urn: The table whose columns the model trains on.
        label_column_urn: The ``schemaField`` URN of the label column.
        exclude: Column names of the feature table that are not features (a join
            key, a partition column). They get no mlFeature.
        dry_run: Work out everything and write nothing.

    Returns:
        What was declared.

    Raises:
        LinkError: The model or the feature table is not in DataHub, or the
            label column is not one of a dataset's columns.
    """
    try:
        SchemaFieldUrn.from_string(label_column_urn)
    except Exception as exc:
        raise LinkError(
            f"{label_column_urn!r} is not a column URN. Pass the label as "
            "table.column, or as a full schemaField URN."
        ) from exc

    # Two different sets, deliberately. The features are what the model consumes,
    # so an excluded join key must not become one. The training snapshot is the
    # *dataset's* schema at training time, so it must include every column: leave
    # the excluded ones out and drift would report each of them as a column that
    # appeared out of nowhere, on the very first scan after linking.
    schema = _columns(conn, feature_dataset_urn)
    columns = {path: native for path, native in schema.items() if path not in exclude}
    if not columns:
        raise LinkError(
            f"every column of {feature_dataset_urn} was excluded; there is nothing to declare"
        )

    feature_urns = _declare_features(conn, feature_dataset_urn, columns, dry_run=dry_run)
    properties = _attach_features(conn, model_urn, feature_urns, dry_run=dry_run)

    label_columns = _label_lineage(conn, label_column_urn, config)
    if not dry_run:
        ensure_term(conn, config.label_term_urn, LABEL_TERM_NAME, LABEL_TERM_DEFINITION)
        for column_urn in label_columns:
            add_term(conn, column_urn, config.label_term_urn)

    runs = tuple(properties.trainingJobs or ())
    captured = sum(
        _capture_training_schema(
            conn, run_urn, feature_dataset_urn, schema, config, dry_run=dry_run
        )
        for run_urn in runs
    )

    if not dry_run:
        # Recorded last, so a half-failed link never claims to be replayable.
        define_properties(conn)
        values: dict[str, list[str | float]] = {
            PROP_FEATURE_TABLE: [feature_dataset_urn],
            PROP_LABEL_COLUMN: [label_column_urn],
        }
        if exclude:
            values[PROP_EXCLUDED] = sorted(exclude)
        assign_properties(conn, model_urn, values)

    return LinkResult(
        model_urn=model_urn,
        feature_urns=feature_urns,
        label_column_urns=label_columns,
        training_runs=runs,
        snapshot_columns=captured,
    )
