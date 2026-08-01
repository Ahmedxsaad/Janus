"""Propose a ``link`` from what the graph already says, instead of demanding it.

``modelguard link`` is the join no ingestion source writes, and every detector
reads it. It is also, until now, four arguments a human has to know and type per
model: the feature table, the label column, the label's table, and every join key
that is not a feature. On one model that is a minute. On a catalog of two hundred
it is the reason ``modelguard inventory`` says "not checked" forever.

Most of those arguments are already in DataHub. This module reads them out and
proposes the exact command a person would have typed, so the human's job becomes
reading one line and saying yes.

Read-only, and it decides nothing
---------------------------------
Nothing here writes, and nothing here is a detector: it produces a *proposal*,
which a human confirms and :func:`~modelguard.writeback.link.link_model` then
executes. It lives beside ``link`` because it is meaningless apart from it.

There is no LLM in it either, and that is a design law rather than a preference
(root CLAUDE.md rule 4). Every field of a proposal comes from an aspect, and
every one carries the aspect it came from, so a reviewer can check the reasoning
rather than trust it.

What is inferred, and how confident each step is
------------------------------------------------
* **The feature table.** From the model's training runs and their recorded
  inputs (``dataProcessInstanceInput``). Exactly one input is a proposal; several
  is a question, because there is no honest way to pick, and none means the graph
  does not connect this model to any table and nothing can be proposed.
* **The label column.** First from a column that already carries the configured
  label term, which is not an inference at all: somebody declared it, in the UI
  or in an earlier link. Failing that, from the column names in
  :attr:`~modelguard.config.ScanConfig.label_column_names`, matched
  case-insensitively. Failing that, nothing: the proposal says so and the human
  supplies it, because a wrongly guessed label makes every leakage verdict wrong
  in both directions.
* **The excluded columns.** From the schema's own key declarations:
  ``schemaMetadata.primaryKeys``, and any field flagged ``isPartOfKey`` or
  ``isPartitioningKey``. These are facts the warehouse recorded, not a guess
  about which columns look like identifiers, and a join key nobody declared is
  what ``--exclude`` remains for.

A wrong proposal a human reads and corrects costs seconds. A blank ``--features``
flag costs an afternoon reading somebody else's catalog. That asymmetry is the
whole argument for this module, and it only holds while the confirmation step
does: nothing here may ever write without one.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, MlModelUrn, SchemaFieldUrn

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.column_marks import ColumnMarkIndex
from modelguard.writeback.link import LinkError


class InferenceError(LinkError):
    """The graph does not say enough to propose a link.

    A subclass of :class:`~modelguard.writeback.link.LinkError` so a caller that
    already handles "this cannot be linked" keeps working, and so the CLI reports
    both through one path. Every message names what was missing and the argument
    that would supply it: an inference that fails silently and leaves the user
    guessing is worse than one that never existed.
    """


@dataclass(frozen=True)
class LinkProposal:
    """A ``link`` invocation worked out from the graph, for a human to confirm."""

    model_urn: str
    feature_dataset_urn: str
    label_column_urn: str | None
    """None when nothing in the graph names a label. The proposal is then
    incomplete and cannot be executed without ``--label-column``."""
    exclude: frozenset[str]
    reasons: tuple[str, ...]
    """One line per decision, naming the aspect it was read from. This is what
    makes the proposal reviewable rather than something to be trusted."""

    @property
    def complete(self) -> bool:
        """Whether this proposal has everything ``link_model`` needs."""
        return self.label_column_urn is not None

    def command(self) -> str:
        """Render the proposal as the exact command a person would have typed.

        Printed for confirmation, and worth pasting into the training script
        afterwards: the point of the join is that it is re-declared on every
        training run, and a command a human has seen once is one they can put in
        a pipeline.
        """
        model = MlModelUrn.from_string(self.model_urn).name
        table = DatasetUrn.from_string(self.feature_dataset_urn).name
        parts = ["modelguard link", f"--model {shlex.quote(model)}", f"--features {table}"]
        if self.label_column_urn is not None:
            label = SchemaFieldUrn.from_string(self.label_column_urn)
            label_table = DatasetUrn.from_string(label.parent).name
            if label_table != table:
                parts.append(f"--label-table {label_table}")
            parts.append(f"--label-column {label.field_path}")
        parts += [f"--exclude {column}" for column in sorted(self.exclude)]
        return " \\\n  ".join(parts)


def _feature_dataset(conn: DataHubConnection, model_urn: str, model_name: str) -> tuple[str, str]:
    """Return the dataset this model trains on, and how that was determined.

    Raises:
        InferenceError: The model records no training run, its runs record no
            inputs, or they record several and there is no honest way to choose.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None:
        raise InferenceError(
            f"{model_urn} has no mlModelProperties in DataHub. Ingest the model "
            "before linking it (DataHub's mlflow source does this)."
        )

    if not properties.trainingJobs:
        raise InferenceError(
            f"{model_name} records no training run, so nothing in the graph says "
            "which table it read. Pass --features <table> --label-column <column> "
            "instead of --infer."
        )

    inputs: dict[str, None] = {}
    for run_urn in properties.trainingJobs:
        recorded = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
        for dataset_urn in (recorded.inputs or []) if recorded else []:
            inputs.setdefault(dataset_urn, None)

    if not inputs:
        raise InferenceError(
            f"{model_name}'s training run records no input datasets "
            "(dataProcessInstanceInput is empty), which is the usual state after "
            "an mlflow ingest. Pass --features <table> --label-column <column> "
            "instead of --infer."
        )

    if len(inputs) > 1:
        names = ", ".join(sorted(DatasetUrn.from_string(urn).name for urn in inputs))
        raise InferenceError(
            f"{model_name}'s training runs read several tables ({names}), and "
            "which one holds its features is not something the graph says. Pass "
            "--features <table> to choose."
        )

    dataset_urn = next(iter(inputs))
    return dataset_urn, (
        f"feature table: the only input recorded on {model_name}'s training "
        "run(s), from dataProcessInstanceInput"
    )


def _schema(conn: DataHubConnection, dataset_urn: str) -> SchemaMetadataClass:
    """Return a dataset's schema, or explain that there is nothing to read."""
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    if schema is None or not schema.fields:
        raise InferenceError(
            f"{DatasetUrn.from_string(dataset_urn).name} has no schemaMetadata in "
            "DataHub, so its columns are unknown and nothing can be proposed. "
            "Ingest the dataset first."
        )
    return schema


def _label_column(
    conn: DataHubConnection,
    config: ScanConfig,
    dataset_urn: str,
    schema: SchemaMetadataClass,
) -> tuple[str | None, str]:
    """Return the label column of a dataset, and how it was determined.

    Two routes, in order of how much they are worth trusting. A column already
    carrying the label term was *declared* by a person or by an earlier link, and
    is not an inference at all. A name match is a genuine guess, reported as one.
    """
    labels = ColumnMarkIndex(conn, terms=frozenset({config.label_term_urn}))
    for field in schema.fields:
        column_urn = str(SchemaFieldUrn(dataset_urn, field.fieldPath))
        if labels.is_marked(column_urn):
            return column_urn, (
                f"label column: {field.fieldPath} already carries "
                f"{config.label_term_urn}, so it was declared rather than guessed"
            )

    wanted = {name.lower() for name in config.label_column_names}
    for field in schema.fields:
        if field.fieldPath.lower() in wanted:
            return str(SchemaFieldUrn(dataset_urn, field.fieldPath)), (
                f"label column: {field.fieldPath} matches a known label name "
                "(MODELGUARD_LABEL_COLUMN_NAMES). This one is a guess: check it"
            )

    return None, (
        "label column: NOT FOUND. No column carries the label term and none is "
        "named like a label, so this has to be supplied with --label-column. A "
        "wrong label makes every leakage verdict wrong in both directions, so "
        "nothing was guessed here"
    )


def _excluded_columns(
    schema: SchemaMetadataClass,
    label_column_urn: str | None,
    dataset_urn: str,
) -> tuple[frozenset[str], str]:
    """Return the columns that are not features, and why each one is not.

    Only declared keys, never a guess about which names look like identifiers.
    ``customer_id`` is usually a join key and ``score_id`` is usually a feature,
    and no rule over names separates them; the warehouse already recorded which
    is which, so that record is what gets read.
    """
    excluded = set(schema.primaryKeys or [])
    for field in schema.fields:
        if field.isPartOfKey or field.isPartitioningKey:
            excluded.add(field.fieldPath)

    # The label is not a feature of the model that predicts it. Excluded only
    # when it lives in this table: a label in its own mart is not among these
    # columns to begin with.
    if label_column_urn is not None:
        label = SchemaFieldUrn.from_string(label_column_urn)
        if label.parent == dataset_urn:
            excluded.add(label.field_path)

    if not excluded:
        return frozenset(), (
            "excluded columns: none. The schema declares no primary or "
            "partitioning key, so every column is proposed as a feature; pass "
            "--exclude for any join key the warehouse never declared"
        )
    return frozenset(excluded), (
        f"excluded columns: {', '.join(sorted(excluded))}, from the schema's own "
        "key declarations (primaryKeys, isPartOfKey, isPartitioningKey) and the "
        "label itself; pass --exclude for any join key the warehouse never declared"
    )


def infer_link(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
) -> LinkProposal:
    """Work out a model's link from the graph, for a human to confirm.

    Args:
        conn: An open connection. Read from, never written to.
        config: Supplies the label term to look for and the label column names to
            fall back on.
        model_urn: The model to propose a link for.

    Returns:
        The proposal, with one reason per decision. A proposal whose label could
        not be determined is returned rather than raised on, because the rest of
        it is still worth showing: the human supplies one argument instead of
        four.

    Raises:
        InferenceError: The graph does not connect this model to any table, or
            connects it to several. Both name the argument that resolves it.
    """
    model_name = MlModelUrn.from_string(model_urn).name
    dataset_urn, table_reason = _feature_dataset(conn, model_urn, model_name)
    schema = _schema(conn, dataset_urn)
    label_column_urn, label_reason = _label_column(conn, config, dataset_urn, schema)
    excluded, exclude_reason = _excluded_columns(schema, label_column_urn, dataset_urn)

    return LinkProposal(
        model_urn=model_urn,
        feature_dataset_urn=dataset_urn,
        label_column_urn=label_column_urn,
        exclude=excluded,
        reasons=(table_reason, label_reason, exclude_reason),
    )
