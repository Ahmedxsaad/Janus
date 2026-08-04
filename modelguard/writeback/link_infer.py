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
* **The feature table.** Four routes, tried in descending order of confidence,
  and the proposal says which one answered. (1) The model's training runs and
  their recorded inputs (``dataProcessInstanceInput``): a declaration, and the
  strongest answer available. (2) A training-run parameter that names a table
  (``dataProcessInstanceProperties.customProperties``, where DataHub's mlflow
  source puts a run's params): a convention, so it is proposed and labelled as
  one. (3) A dataset the catalog already declares upstream of the model, which
  Spark and some sources emit. (4) Nothing resolved, so the proposal is
  incomplete and carries a shortlist of tables to choose from. Route 1 alone is
  what a plain mlflow ingest does *not* produce (D-074), which is why there are
  four rather than one. Several tables from any route is a question rather than
  an answer: they become the shortlist instead of one of them being picked.
* **The label column.** First from a column that already carries the configured
  label term, which is not an inference at all: somebody declared it, in the UI
  or in an earlier link. Failing that, from the same declaration found *upstream*
  of one of the feature table's columns, which is where it usually sits: a
  warehouse almost never keeps the label beside the features, so the label lands
  in its own mart and the feature columns descend from it. That is still a
  declaration, and the reason line names the column it was reached from and the
  chain walked to get there. Failing that, from the column names in
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

import re
import shlex
from dataclasses import dataclass

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DataProcessInstancePropertiesClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, MlModelUrn, SchemaFieldUrn
from datahub.sdk.search_filters import FilterDsl as F

from modelguard.adapters import DeclaredLink, excluded_columns, missing_columns
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.column_marks import ColumnMarkIndex, marked_ancestor
from modelguard.writeback.link import LinkError

#: Run-parameter keys that conventionally name the training dataset, lowercase,
#: most specific first. A convention, not a standard, so a hit is proposed and
#: never assumed, and the reason line says which key it came from.
#: ``modelguard_features`` leads because it is the key this project's own README
#: tells a training script to log, so a team following that advice gets the
#: unambiguous answer rather than a generic key another tool may also write.
_DATASET_PARAM_KEYS: tuple[str, ...] = (
    "modelguard_features",
    "dataset",
    "dataset_name",
    "training_data",
    "training_table",
    "input_table",
    "table",
)

#: Word boundaries in a lowercased entity name: anything that is not a letter or
#: a digit (dots, underscores, dashes, slashes). Used only to shortlist by shared
#: word, never to decide anything.
_NAME_TOKENS = re.compile(r"[^a-z0-9]+")

#: How many candidates a shortlist offers. Long enough to contain the answer on a
#: normal catalog, short enough that reading it is faster than searching the UI.
_SHORTLIST_LIMIT = 5


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
    feature_dataset_urn: str | None
    """None when no route resolved a training table. The proposal is then
    incomplete, ``candidates`` holds the shortlist to choose from, and nothing
    downstream of the table (the label, the excluded columns) was inferred."""
    label_column_urn: str | None
    """None when nothing in the graph names a label. The proposal is then
    incomplete and cannot be executed without ``--label-column``."""
    exclude: frozenset[str]
    reasons: tuple[str, ...]
    """One line per decision, naming the aspect it was read from. This is what
    makes the proposal reviewable rather than something to be trusted."""
    candidates: tuple[str, ...] = ()
    """Dataset URNs offered for the human to pick from when a field could not be
    resolved. A shortlist, explicitly not a guess: nothing here is ever chosen
    automatically, and an empty tuple means not even a shortlist was worth
    offering."""

    @property
    def complete(self) -> bool:
        """Whether this proposal has everything ``link_model`` needs."""
        return self.feature_dataset_urn is not None and self.label_column_urn is not None

    def command(self) -> str:
        """Render the proposal as the exact command a person would have typed.

        Printed for confirmation, and worth pasting into the training script
        afterwards: the point of the join is that it is re-declared on every
        training run, and a command a human has seen once is one they can put in
        a pipeline.

        A field that could not be inferred is rendered as its placeholder rather
        than omitted, so what is printed stays the whole command with the blanks
        visible instead of a shorter one that looks finished.
        """
        model = MlModelUrn.from_string(self.model_urn).name
        table = (
            DatasetUrn.from_string(self.feature_dataset_urn).name
            if self.feature_dataset_urn is not None
            else "<table>"
        )
        parts = ["modelguard link", f"--model {shlex.quote(model)}", f"--features {table}"]
        if self.label_column_urn is not None:
            label = SchemaFieldUrn.from_string(self.label_column_urn)
            label_table = DatasetUrn.from_string(label.parent).name
            if label_table != table:
                parts.append(f"--label-table {label_table}")
            parts.append(f"--label-column {label.field_path}")
        parts += [f"--exclude {column}" for column in sorted(self.exclude)]
        return " \\\n  ".join(parts)


def _run_inputs(conn: DataHubConnection, properties: MLModelPropertiesClass) -> tuple[str, ...]:
    """Route 1: the datasets the model's training runs record as their inputs.

    The highest-confidence route by a distance, because the run is *declaring*
    what it read rather than describing it. Also the one a plain mlflow ingest
    does not produce (D-074), which is why there are three more below it.
    """
    inputs: dict[str, None] = {}
    for run_urn in properties.trainingJobs or []:
        recorded = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
        for dataset_urn in (recorded.inputs or []) if recorded else []:
            inputs.setdefault(dataset_urn, None)
    return tuple(inputs)


def _run_parameters(conn: DataHubConnection, properties: MLModelPropertiesClass) -> dict[str, str]:
    """Return the training runs' custom properties, which carry MLflow's params.

    DataHub's mlflow source copies a run's params and tags into
    ``dataProcessInstanceProperties.customProperties``, so a team that logged the
    training dataset as a param has already told the graph which table this is,
    in a place nothing else reads.
    """
    found: dict[str, str] = {}
    for run_urn in properties.trainingJobs or []:
        recorded = conn.graph.get_aspect(run_urn, DataProcessInstancePropertiesClass)
        for key, value in ((recorded.customProperties if recorded else None) or {}).items():
            found.setdefault(key.lower(), value)
    return found


def _datasets_named(conn: DataHubConnection, text: str) -> tuple[str, ...]:
    """Return the catalog's datasets whose name matches ``text``, exactly or by leaf.

    Search is used to narrow the catalog, never to rank: a fuzzy hit that shares
    no name with the value read from the run is not a match, because a wrong
    feature table makes every leakage verdict wrong about a table the user never
    named.
    """
    leaf = text.strip().strip("/").split("/")[-1]
    if not leaf:
        return ()
    matched: set[str] = set()
    for urn in conn.client.search.get_urns(query=leaf, filter=F.entity_type("dataset")):
        name = DatasetUrn.from_string(str(urn)).name
        if leaf in {name, name.split(".")[-1]}:
            matched.add(str(urn))
    return tuple(sorted(matched))


def _param_datasets(
    conn: DataHubConnection, properties: MLModelPropertiesClass
) -> tuple[tuple[str, ...], str | None]:
    """Route 2: a training-run parameter that names a table the catalog holds.

    Returns:
        The datasets the first matching parameter resolved to, and the key it was
        read from. A convention rather than a standard, so the key is carried
        back and quoted in the proposal: a medium-confidence match a reviewer can
        check against their own training script is worth more than a confident one
        they cannot.
    """
    parameters = _run_parameters(conn, properties)
    for key in _DATASET_PARAM_KEYS:
        value = parameters.get(key)
        if not value:
            continue
        resolved = _datasets_named(conn, value)
        if resolved:
            return resolved, key
    return (), None


def _lineage_datasets(conn: DataHubConnection, model_urn: str) -> tuple[str, ...]:
    """Route 3: datasets the catalog already declares upstream of the model.

    Spark and some ingestion sources emit dataset-to-model lineage directly. When
    one of them has, the answer is in the graph and no convention is involved.
    """
    results = conn.client.lineage.get_lineage(
        source_urn=model_urn, direction="upstream", max_hops=1
    )
    datasets = {
        str(result.urn) for result in results if str(result.urn).startswith("urn:li:dataset:")
    }
    return tuple(sorted(datasets))


def _shortlist(conn: DataHubConnection, model_name: str) -> tuple[str, ...]:
    """Route 4: datasets whose name shares a word with the model's, to choose from.

    Explicitly a shortlist and never an answer. It exists because the difference
    between a tool that refuses and a tool that helps is whether the user has to
    go and read somebody else's catalog to supply the argument themselves.
    """
    words = {word for word in _NAME_TOKENS.split(model_name.lower()) if len(word) > 2}
    if not words:
        return ()
    shared: set[str] = set()
    for urn in conn.client.search.get_urns(query=model_name, filter=F.entity_type("dataset")):
        name = DatasetUrn.from_string(str(urn)).name.lower()
        if words & set(_NAME_TOKENS.split(name)):
            shared.add(str(urn))
    return tuple(sorted(shared))[:_SHORTLIST_LIMIT]


def _feature_dataset(
    conn: DataHubConnection, model_urn: str, model_name: str
) -> tuple[str | None, str, tuple[str, ...]]:
    """Return the dataset this model trains on, how that was decided, and a shortlist.

    Four routes, tried in descending order of how much the answer is *declared*
    rather than inferred, and each one reports which it was. A route that resolves
    several tables does not pick one: several is a question, and the tables it
    found become the shortlist for a human to answer it with.

    Returns:
        The dataset URN or None, the reason line, and the candidates to choose
        from when the URN is None (empty otherwise).

    Raises:
        InferenceError: The model is not in DataHub at all, which is a missing
            ingest rather than a missing inference.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None:
        raise InferenceError(
            f"{model_urn} has no mlModelProperties in DataHub. Ingest the model "
            "before linking it (DataHub's mlflow source does this)."
        )

    inputs = _run_inputs(conn, properties)
    if len(inputs) == 1:
        return (
            inputs[0],
            (
                f"feature table: the only input recorded on {model_name}'s training "
                "run(s), from dataProcessInstanceInput"
            ),
            (),
        )

    parameters, key = _param_datasets(conn, properties)
    if len(parameters) == 1:
        return (
            parameters[0],
            (
                f"feature table: the training run logged it as the {key!r} parameter "
                "(dataProcessInstanceProperties.customProperties), resolved against the "
                "catalog. This one is a convention, not a declaration: check it"
            ),
            (),
        )

    upstream = _lineage_datasets(conn, model_urn)
    if len(upstream) == 1:
        return (
            upstream[0],
            (
                "feature table: the only dataset the catalog declares upstream of this "
                "model, from its own lineage"
            ),
            (),
        )

    ambiguous = inputs or parameters or upstream
    if ambiguous:
        names = ", ".join(sorted(DatasetUrn.from_string(urn).name for urn in ambiguous))
        return (
            None,
            (
                f"feature table: NOT FOUND. {model_name} is connected to several tables "
                f"({names}) and which one holds its features is not something the graph "
                "says. Choose one with --features"
            ),
            tuple(ambiguous),
        )

    return (
        None,
        (
            f"feature table: NOT FOUND. {model_name}'s training run records no inputs "
            "and no dataset parameter, which is the usual state after an mlflow ingest, "
            "and nothing in the catalog declares a dataset upstream of it. Pass "
            "--features <table>, or log the training table as an MLflow run parameter "
            f"({_DATASET_PARAM_KEYS[0]}=...) and re-ingest so this can be read rather "
            "than guessed"
        ),
        _shortlist(conn, model_name),
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


def _declared_upstream(
    conn: DataHubConnection,
    config: ScanConfig,
    dataset_urn: str,
    schema: SchemaMetadataClass,
    labels: ColumnMarkIndex,
) -> tuple[str, str] | None:
    """Return a labelled column reached by walking upstream of this table's columns.

    The same walk the leakage detector runs, asked a different question: not
    "does a feature descend from the label" but "which column is the label". A
    hit is still a declaration somebody made, so it is reported as one, and it
    names the column and the feature it was reached from, because a label found
    two joins away is exactly the kind of answer that deserves checking.

    Columns are walked in schema order and the first hit wins. Several columns of
    one feature table descending from one label is the normal case (that is what
    leakage looks like), and they all reach the same column, so there is nothing
    to choose between them.

    Returns:
        The label column's URN and the reason line, or None when no column's cone
        reaches anything carrying the label term.
    """
    for field in schema.fields:
        column_urn = str(SchemaFieldUrn(dataset_urn, field.fieldPath))
        walk = marked_ancestor(conn, column_urn, labels, config)
        if walk.hit is None:
            continue
        label_urn, marker_urn, path = walk.hit
        label = SchemaFieldUrn.from_string(label_urn)
        label_table = DatasetUrn.from_string(label.parent).name
        return label_urn, (
            f"label column: {label.field_path} in {label_table} carries {marker_urn}, "
            f"reached from {field.fieldPath} by column lineage ({' -> '.join(path)}). "
            "Declared rather than guessed, but in another table: check it is the "
            "label this model predicts"
        )
    return None


def _label_column(
    conn: DataHubConnection,
    config: ScanConfig,
    dataset_urn: str,
    schema: SchemaMetadataClass,
) -> tuple[str | None, str]:
    """Return the label column of a model's training data, and how it was determined.

    Three routes, in order of how much they are worth trusting. A column already
    carrying the label term was *declared* by a person or by an earlier link, and
    is not an inference at all. Failing that, the same declaration found upstream
    of one of these columns, which is where it usually is: a warehouse almost
    never puts the label and the features in one table, the label lands in its own
    mart, and the feature table's columns descend from it (link.py says the same
    thing about why the term is propagated up the label's own lineage). A name
    match is a genuine guess, reported as one.
    """
    labels = ColumnMarkIndex(conn, terms=frozenset({config.label_term_urn}))
    for field in schema.fields:
        column_urn = str(SchemaFieldUrn(dataset_urn, field.fieldPath))
        if labels.is_marked(column_urn):
            return column_urn, (
                f"label column: {field.fieldPath} already carries "
                f"{config.label_term_urn}, so it was declared rather than guessed"
            )

    upstream = _declared_upstream(conn, config, dataset_urn, schema, labels)
    if upstream is not None:
        return upstream

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


def declared_proposal(
    conn: DataHubConnection,
    model_urn: str,
    declaration: DeclaredLink,
    *,
    feature_dataset_urn: str,
    label_dataset_urn: str | None,
) -> LinkProposal:
    """Turn an adapter's declaration into the same proposal ``--infer`` produces.

    The two routes to a proposal are different in kind and identical in shape.
    ``--infer`` reads the graph and reasons about it; an adapter reads a file the
    team maintains and reasons about nothing. Both end in a command a human
    confirms, so both produce a :class:`LinkProposal` and everything downstream of
    the confirmation is one code path.

    The declaration names the features positively; ``link`` takes the complement,
    because a feature table is the unit and the join key is the exception. That
    join happens here, against the table's real schema, which is the one fact the
    declaration cannot hold.

    Args:
        conn: An open connection. Read from, never written to.
        model_urn: The model being linked.
        declaration: What the adapter read.
        feature_dataset_urn: The declaration's source table, already resolved
            against the catalog by the caller (only the CLI knows how a user's
            ``--features`` overrides it).
        label_dataset_urn: The table the declared label lives in, resolved the
            same way. None when the declaration names no label.

    Returns:
        The proposal, carrying the adapter's own reason lines followed by the
        two decisions made here: which columns are excluded, and why.

    Raises:
        LinkError: The declaration names a column the resolved table does not
            have. That is a disagreement between the declaration and the
            catalog, and it is fatal rather than filtered: linking the
            intersection would leave the undeclared columns unchecked while
            reporting a successful link.
    """
    schema = _schema(conn, feature_dataset_urn)
    columns = [field.fieldPath for field in schema.fields]
    declared = declaration.source_columns

    missing = missing_columns(declared, columns)
    if missing:
        table = DatasetUrn.from_string(feature_dataset_urn).name
        raise LinkError(
            f"{declaration.adapter} declares {len(missing)} column(s) that {table} "
            f"does not have: {', '.join(missing)}. The declaration and the catalog "
            "disagree about this table, so nothing was proposed: re-ingest the "
            "table, or pass --features to name the table the declaration means."
        )

    excluded = excluded_columns(declared, columns)
    label_column_urn = (
        str(SchemaFieldUrn(label_dataset_urn, declaration.label_column))
        if declaration.label_column is not None and label_dataset_urn is not None
        else None
    )
    reasons = [
        *declaration.reasons,
        (
            f"excluded columns: {', '.join(sorted(excluded))}. Every column of the "
            f"table that {declaration.name!r} does not declare as a feature"
        )
        if excluded
        else (
            f"excluded columns: none. {declaration.name!r} declares every column of "
            "the table as a feature"
        ),
    ]
    return LinkProposal(
        model_urn=model_urn,
        feature_dataset_urn=feature_dataset_urn,
        label_column_urn=label_column_urn,
        exclude=excluded,
        reasons=tuple(reasons),
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
        The proposal, with one reason per decision. An incomplete proposal is
        returned rather than raised on, because the rest of it is still worth
        showing and a shortlist the user picks from beats a refusal they have to
        answer by reading somebody else's catalog: the human supplies one
        argument instead of four.

    Raises:
        InferenceError: The model is not in DataHub, or its feature table has no
            schema. Both name the ingest that is missing rather than an argument.
    """
    model_name = MlModelUrn.from_string(model_urn).name
    dataset_urn, table_reason, candidates = _feature_dataset(conn, model_urn, model_name)
    if dataset_urn is None:
        # Everything else is read out of the feature table's schema, so with no
        # table there is nothing further to infer and nothing to pretend about.
        return LinkProposal(
            model_urn=model_urn,
            feature_dataset_urn=None,
            label_column_urn=None,
            exclude=frozenset(),
            reasons=(
                table_reason,
                "label column and excluded columns: not inferred. Both are read "
                "from the feature table's schema, and there is no feature table yet",
            ),
            candidates=candidates,
        )

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
