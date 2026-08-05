"""Score the detectors on a graph this project did not build (T-14).

Every other measurement in this package runs against the graph
``modelguard-seed`` wrote. That graph is exactly the one where the links the
detectors read already exist, which is precisely the assumption a real project
breaks, so a perfect score on it is a claim about the seeder as much as about
the detectors (benchmarks/CLAUDE.md rule 8, and F6 in docs/plan/07).

This module removes the seeder. It measures against ``examples/real-project/``:
postgres holding a public dataset, dbt building the feature and label tables,
MLflow tracking the training run, and DataHub's own postgres, dbt and mlflow
sources ingesting all three. Nothing in that stack knows ModelGuard exists, the
leak is planted in the dbt model rather than by a seeding call, and the
column-level lineage the detectors walk is what DataHub's own SQL parser
produced from the compiled query.

What ground truth is here
-------------------------
The dbt model, read from disk. ``customer_features.sql`` builds
``contract_renewed_flag`` from the churn outcome, and that file is the thing a
person edits to play the fix; :func:`leaking_features` reads it, so removing the
column from the SQL and rebuilding flips this module's ground truth with no code
change. Nothing is derived from the graph, and nothing is derived from what a
detector said.

Why it restores the model first
-------------------------------
The interesting half of a foreign graph is the state it arrives in: an mlModel
with no features, no run inputs, and no link to a single column, where the only
honest answer is that nothing could be checked (D-074). ModelGuard's own ``link``
then destroys that state, so a second run of this benchmark would measure a
different graph from the first. :func:`restore_ingested_state` puts the model
back to what ingestion produced by clearing exactly the two aspects ``link``
writes, which is also what re-ingesting the mlflow source does to it in real life
(D-074 point 4). It plants nothing: every fact scored below is one an ingestion
source wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, SchemaFieldUrn, Urn

from benchmarks import metrics
from modelguard.adapters import ADAPTERS, AdapterError, DeclaredLink, read_declaration
from modelguard.agent.pipeline import run_scan
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.degraded import training_tables
from modelguard.detect.leakage import feature_source_column, leakage_findings
from modelguard.discovery import search_model_urns
from modelguard.models import LeakageFinding
from modelguard.writeback.link import link_model

#: The example stack, relative to the repository root. The benchmark is run from
#: there (``python -m benchmarks.run_bench``), the same way the dbt recipe in
#: that directory takes paths relative to itself.
REAL_PROJECT = Path("examples/real-project")

#: The dbt model that carries the mistake, and the source of ground truth.
FEATURE_MODEL_SQL = REAL_PROJECT / "churn_analytics" / "models" / "marts" / "customer_features.sql"

#: The feature whose definition reads the churn outcome. Ground truth is not this
#: constant on its own: :func:`leaking_features` confirms the SQL still defines
#: it, so the fix the README invites a reader to play (delete the column, rebuild,
#: re-ingest) is scored as the clean graph it produces rather than as a miss.
LEAKING_FEATURE = "contract_renewed_flag"

#: How the registered MLflow model is searched for. The mlflow source names the
#: mlModel after the registered model and its version (``telco_churn_1``), so the
#: search is on the registered name and the match is whatever version the graph
#: holds.
MODEL_QUERY = "telco_churn"

#: Which sibling of a dbt-built table to link the model to. DataHub's dbt source
#: emits a dbt-platform dataset beside the warehouse one and names both after the
#: same relation, so a declared table name resolves to two datasets. The
#: warehouse one is the table the training script actually queried.
WAREHOUSE_PLATFORM = "postgres"

#: Where each adapter reads its declaration from, inside the example stack. Both
#: describe the same seven columns of the same table, which is the point: either
#: route has to arrive at the same link or one of them is wrong.
DECLARATIONS = {
    "feast": REAL_PROJECT / "feature_repo",
    "dbt": REAL_PROJECT / "churn_analytics",
}


def leaking_features() -> frozenset[str]:
    """Return the features the dbt model builds out of the label, from the SQL.

    Ground truth, read from the file a person edits rather than from the graph a
    detector reads. An empty result is the fixed project, not a failure to find
    one: the README's fix is to delete that column and rebuild.

    Raises:
        OSError: The example's dbt model is not on disk, which means this is not
            being run from the repository root and every number below would be
            about the wrong project.
    """
    sql = FEATURE_MODEL_SQL.read_text(encoding="utf-8")
    # The column is only ground truth where it is *built*, not where the comment
    # above it explains the mistake, so the alias is what is looked for.
    return frozenset({LEAKING_FEATURE} if f"as {LEAKING_FEATURE}" in sql else set())


@dataclass(frozen=True)
class Route:
    """What one adapter read, and whether the ingested catalog agreed with it."""

    adapter: str
    declared_table: str
    """The feature table, spelled the way the declaration spells it."""
    candidates: int
    """Datasets in the catalog whose name ends with that. Two on a dbt stack: the
    warehouse table and its dbt sibling."""
    source_columns: tuple[str, ...]
    """The warehouse columns the declaration names as features, sorted."""
    label_column: str | None
    excluded: tuple[str, ...]
    """Columns of the resolved table the declaration does not claim as features."""
    error: str | None = None
    """Why the route produced nothing, when it did."""


@dataclass(frozen=True)
class IngestedScore:
    """The whole ingested-graph measurement."""

    model_urn: str
    feature_dataset_urn: str
    unlinked_findings: int
    """Findings on the model in the state ingestion left it. Anything above zero
    is a claim made with no link to make it from."""
    unlinked_not_evaluated: tuple[str, ...]
    """What the scan said it could not check, in that state."""
    unlinked_training_tables: int
    """Tables the degraded mode (T-07) could read about the unlinked model. Zero
    means even the table-level answer had nothing to stand on, which is the state
    DataHub's mlflow source leaves a model in: it records no run inputs, and it
    emits no lineage from the model to the table it trained on."""
    routes: tuple[Route, ...]
    routes_agree: bool
    """Whether every adapter that read a declaration named the same columns."""
    leakage: metrics.Confusion
    """One decision per feature of the ingested table: did the detector flag it."""
    truth: tuple[str, ...]
    flagged: tuple[str, ...]
    leak_path: str
    """The derivation the finding quotes, rendered the way the product renders it
    for a user (repeated column names across a sibling hop collapsed), so this
    section and an incident quote the same string."""
    label_reached: str | None
    """The label column the walk arrived at."""
    linked_not_evaluated: tuple[str, ...]
    """What still could not be checked once the model was linked."""

    @property
    def exact(self) -> bool:
        """Whether the detector named the leaking features and nothing else."""
        return set(self.flagged) == set(self.truth)


def find_model(conn: DataHubConnection) -> str | None:
    """Return the ingested model's URN, or None when the stack is not ingested.

    None is the ordinary case for anyone running this benchmark: the seeded
    measurements need a Quickstart, this one needs a warehouse, a dbt run and an
    MLflow server as well. The report then says the section was not run, and how
    to run it, rather than reporting zeros.
    """
    matches = search_model_urns(conn, query=MODEL_QUERY)
    return matches[0] if matches else None


def resolve_in_warehouse(conn: DataHubConnection, table: str) -> tuple[str | None, int]:
    """Resolve a declared relation to the warehouse dataset, and count the rest.

    ``modelguard link`` resolves a name through :func:`modelguard.cli.resolve_table`,
    which refuses an ambiguous one and prints every candidate for a human to
    choose from. That refusal is correct and it is not a thing a benchmark can
    answer, so the choice is made here, explicitly, by platform: the table the
    training script queried. The count is reported, because "a declared relation
    names two datasets on a dbt stack" is a fact about real graphs worth
    publishing rather than hiding behind a resolved URN.
    """
    candidates = []
    for urn in conn.client.search.get_urns(query=table):
        parsed = Urn.from_string(str(urn))
        if not isinstance(parsed, DatasetUrn):
            continue
        if parsed.name == table or parsed.name.endswith(f".{table}"):
            candidates.append(parsed)
    chosen = [urn for urn in candidates if urn.platform.endswith(f":{WAREHOUSE_PLATFORM}")]
    return (str(chosen[0]) if chosen else None), len(candidates)


def _table_columns(conn: DataHubConnection, dataset_urn: str) -> tuple[str, ...]:
    """Return the ingested table's column names, from its schema aspect."""
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    return tuple(field.fieldPath for field in schema.fields) if schema else ()


def read_routes(conn: DataHubConnection) -> tuple[tuple[Route, ...], DeclaredLink | None]:
    """Read every adapter's declaration and check it against the ingested table.

    This is T-05 and T-06 re-verified against a graph neither was developed on:
    the columns each declaration names have to be columns the ingested table
    actually has, and the two declarations have to agree with each other.

    Returns:
        One route per adapter, and the declaration to link from: the one that
        names a label, since the dbt semantic layer has no notion of one.
    """
    routes: list[Route] = []
    linkable: DeclaredLink | None = None
    for adapter in ADAPTERS:
        path = DECLARATIONS[adapter]
        try:
            declaration = read_declaration(adapter, path)
        except AdapterError as exc:
            routes.append(
                Route(
                    adapter=adapter,
                    declared_table="-",
                    candidates=0,
                    source_columns=(),
                    label_column=None,
                    excluded=(),
                    error=str(exc),
                )
            )
            continue

        dataset_urn, candidates = resolve_in_warehouse(conn, declaration.source_table)
        columns = _table_columns(conn, dataset_urn) if dataset_urn else ()
        declared = declaration.source_columns
        missing = sorted(declared - set(columns))
        routes.append(
            Route(
                adapter=adapter,
                declared_table=declaration.source_table,
                candidates=candidates,
                source_columns=tuple(sorted(declared)),
                label_column=declaration.label_column,
                excluded=tuple(sorted(column for column in columns if column not in declared)),
                error=(
                    f"the ingested table has no column {', '.join(missing)}" if missing else None
                ),
            )
        )
        if declaration.label_column is not None and not missing:
            linkable = declaration
    return tuple(routes), linkable


def restore_ingested_state(conn: DataHubConnection, model_urn: str) -> None:
    """Put the model back to the state DataHub's ingestion produced.

    Clears exactly what ``link`` writes onto entities ingestion owns: the model's
    features, and the training run's declared inputs. Nothing is planted, and the
    rest of the graph (the tables, their column lineage, the run) is untouched,
    because none of it came from here.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None:
        return
    for run_urn in properties.trainingJobs or ():
        conn.graph.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=run_urn, aspect=DataProcessInstanceInputClass(inputs=[])
            )
        )
    if properties.mlFeatures:
        properties.mlFeatures = []
        conn.graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=model_urn, aspect=properties))


def _flagged_features(
    conn: DataHubConnection, config: ScanConfig, model_urn: str
) -> tuple[tuple[str, ...], tuple[LeakageFinding, ...]]:
    """Return the source columns the leakage detector flagged, and its findings."""
    findings = leakage_findings(conn, model_urn, config)
    flagged = tuple(sorted(finding.leak.source_column_name for finding in findings))
    return flagged, findings


def _scored_features(conn: DataHubConnection, model_urn: str) -> tuple[str, ...]:
    """Return the warehouse column behind every feature the model declares.

    The features are the unit of the leakage measurement, not the model: every
    approach can tell that a leaking model leaks, and the question a data
    scientist has to act on is which column carries it.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    columns = []
    for feature_urn in (properties.mlFeatures if properties else None) or ():
        column_urn = feature_source_column(conn, feature_urn)
        if column_urn is not None:
            columns.append(SchemaFieldUrn.from_string(column_urn).field_path)
    return tuple(sorted(columns))


def _not_evaluated(conn: DataHubConnection, config: ScanConfig, model_urn: str) -> tuple[str, ...]:
    """Return the check names a dry-run scan reported it could not run."""
    report = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)
    return tuple(sorted(gap.check for gap in report.not_evaluated))


def measure_ingested(conn: DataHubConnection, config: ScanConfig) -> IngestedScore | None:
    """Run the whole ingested-graph measurement, or return None if not ingested.

    Order matters and is the same order a user meets: the model as ingestion left
    it, then what the declarations say, then the link, then the detectors.
    """
    model_urn = find_model(conn)
    if model_urn is None:
        return None

    restore_ingested_state(conn, model_urn)
    unlinked = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)
    unlinked_tables = training_tables(conn, model_urn, config)

    routes, declaration = read_routes(conn)
    if declaration is None:
        return None
    feature_dataset_urn, _ = resolve_in_warehouse(conn, declaration.source_table)
    label_dataset_urn, _ = resolve_in_warehouse(conn, declaration.label_table or "")
    if feature_dataset_urn is None or label_dataset_urn is None:
        return None

    columns = _table_columns(conn, feature_dataset_urn)
    link_model(
        conn,
        config,
        model_urn=model_urn,
        feature_dataset_urn=feature_dataset_urn,
        label_column_urn=str(SchemaFieldUrn(label_dataset_urn, declaration.label_column or "")),
        exclude=frozenset(column for column in columns if column not in declaration.source_columns),
    )

    truth = leaking_features()
    scored = _scored_features(conn, model_urn)
    flagged, findings = _flagged_features(conn, config, model_urn)
    hit = findings[0] if findings else None
    return IngestedScore(
        model_urn=model_urn,
        feature_dataset_urn=feature_dataset_urn,
        unlinked_findings=len(unlinked.findings),
        unlinked_not_evaluated=tuple(sorted(gap.check for gap in unlinked.not_evaluated)),
        unlinked_training_tables=len(unlinked_tables),
        routes=routes,
        routes_agree=len({route.source_columns for route in routes if route.error is None}) == 1,
        leakage=metrics.confusion([(column in truth, column in flagged) for column in scored]),
        truth=tuple(sorted(truth)),
        flagged=flagged,
        leak_path=hit.leak.path_text if hit else "",
        label_reached=(
            f"{hit.leak.label_dataset_name}.{hit.leak.label_column_name}" if hit else None
        ),
        linked_not_evaluated=_not_evaluated(conn, config, model_urn),
    )


__all__ = [
    "IngestedScore",
    "Route",
    "leaking_features",
    "measure_ingested",
]
