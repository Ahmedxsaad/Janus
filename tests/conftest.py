"""Shared fixtures. Unit tests never touch a network or a live DataHub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    OtherSchemaClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
)
from datahub.metadata.urns import SchemaFieldUrn, Urn
from datahub.sdk.lineage_client import LineagePath, LineageResult

from modelguard.client import DataHubConnection
from modelguard.models import (
    BlastRadius,
    ChangeKind,
    FreshnessFinding,
    FreshnessSignal,
    LeakageFinding,
    LeakingFeature,
    ModelAtRisk,
    ModelRef,
    SchemaChange,
    SchemaDriftFinding,
)


@dataclass(frozen=True)
class _RelatedEntity:
    """Mirrors the shape DataHubGraph.get_related_entities yields."""

    urn: str


class FakeGraph:
    """A stand-in for DataHubGraph that records writes instead of sending them.

    Only the handful of methods the write-back layer calls are implemented, so a
    test that reaches for anything else fails loudly rather than silently passing.
    """

    def __init__(
        self,
        aspects: dict[tuple[str, type], Any] | None = None,
        *,
        exists: bool = True,
        graphql_response: dict[str, Any] | None = None,
        related: dict[str, list[str]] | None = None,
        timeseries: dict[tuple[str, type], Any] | None = None,
    ) -> None:
        self._aspects = aspects or {}
        self._exists = exists
        self._related = related or {}
        self._timeseries = timeseries or {}
        self.graphql_response = graphql_response or {}
        self.emitted: list[Any] = []
        self.graphql_calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_aspect(self, entity_urn: str, aspect_type: type, version: int = 0) -> Any:
        return self._aspects.get((entity_urn, aspect_type))

    def set_aspect(self, entity_urn: str, aspect: Any) -> None:
        """Place an aspect on the fake graph, keyed the way get_aspect looks it up.

        Lets a test vary one fact about the graph without reaching into a private
        dict, and keeps the key derivation in one place.
        """
        self._aspects[(entity_urn, type(aspect))] = aspect

    def get_latest_timeseries_value(
        self, entity_urn: str, aspect_type: type, filter_criteria_map: dict[str, str]
    ) -> Any:
        """Timeseries aspects (operation, assertionRunEvent) read through this, not get_aspect."""
        return self._timeseries.get((entity_urn, aspect_type))

    def exists(self, entity_urn: str) -> bool:
        return self._exists

    def emit_mcp(self, mcp: Any, **_: Any) -> None:
        self.emitted.append(mcp)
        # A real GMS applies a versioned aspect to its primary store synchronously
        # on emit; only search, relationships, and timeseries indexing lag behind
        # (that asynchrony is what the integration gate's polling is for, not this
        # read). Mirroring that here lets a test exercise a read-your-own-write
        # sequence within one scan, such as two findings on one model each reading
        # the model's current risk flags before writing theirs back.
        self.set_aspect(mcp.entityUrn, mcp.aspect)

    def emit_mcps(self, mcps: Any, **_: Any) -> list[Any]:
        for mcp in mcps:
            self.emit_mcp(mcp)
        return []

    def execute_graphql(self, query: str, variables: dict[str, Any] | None = None, **_: Any) -> Any:
        self.graphql_calls.append((query, variables))
        return self.graphql_response

    def get_related_entities(
        self, entity_urn: str, relationship_types: list[str], direction: Any
    ) -> Any:
        return [_RelatedEntity(urn) for urn in self._related.get(entity_urn, [])]


class FakeEntities:
    """Records entities handed to client.entities.upsert."""

    def __init__(self) -> None:
        self.upserted: list[Any] = []

    def upsert(self, entity: Any, **_: Any) -> None:
        self.upserted.append(entity)


class FakeLineage:
    """Records calls to client.lineage.add_lineage and replays canned get_lineage results.

    ``by_column`` answers a column-level query with the cone of *that* column, the
    way a real GMS does. A fake that returned one canned cone for every column
    would hand the leaking column's lineage to the clean column too, and every
    feature would look like a leak. That is a fixture bug that manufactures a
    passing false-positive test, so the distinction is kept honest here.
    """

    def __init__(
        self,
        results: list[LineageResult] | None = None,
        by_column: dict[str, list[LineageResult]] | None = None,
    ) -> None:
        self.edges: list[dict[str, Any]] = []
        self.results = results or []
        self.by_column = by_column or {}
        self.lineage_calls: list[dict[str, Any]] = []

    def add_lineage(
        self,
        *,
        upstream: Any,
        downstream: Any,
        column_lineage: Any = None,
        **_: Any,
    ) -> None:
        self.edges.append(
            {
                "upstream": str(upstream),
                "downstream": str(downstream),
                "column_lineage": column_lineage,
            }
        )

    def get_lineage(self, **kwargs: Any) -> list[LineageResult]:
        self.lineage_calls.append(kwargs)
        column = kwargs.get("source_column")
        if column is not None and self.by_column:
            return self.by_column.get(column, [])
        return self.results


class FakeSearch:
    """Replays canned search results for client.search.get_urns."""

    def __init__(self, urns: list[str] | None = None) -> None:
        self.urns = urns or []

    def get_urns(self, query: str | None = None, filter: Any = None, **_: Any) -> list[Any]:  # noqa: A002
        return [Urn.from_string(urn) for urn in self.urns]


class FakeClient:
    """A stand-in for DataHubClient exposing only the sub-clients the package uses."""

    def __init__(
        self,
        lineage_results: list[LineageResult] | None = None,
        search_urns: list[str] | None = None,
        lineage_by_column: dict[str, list[LineageResult]] | None = None,
    ) -> None:
        self.entities = FakeEntities()
        self.lineage = FakeLineage(lineage_results, lineage_by_column)
        self.search = FakeSearch(search_urns)


def lineage_result(
    urn: str,
    hops: int,
    *,
    direction: str = "downstream",
    paths: list[LineagePath] | None = None,
) -> LineageResult:
    """Build a real LineageResult, so the fake cannot drift from the SDK's shape.

    ``paths`` is what carries column granularity. A live GMS puts the *dataset* in
    ``urn`` even for a column-level query, and the columns only in ``paths``, so a
    fixture that omitted them would let a broken detector pass (D-031).
    """
    return LineageResult(
        urn=urn,
        type=Urn.from_string(urn).entity_type,
        hops=hops,
        direction=direction,
        paths=paths,
    )


def column_path(*column_urns: str) -> list[LineagePath]:
    """Build the LineagePath chain a column-level lineage query returns."""
    return [
        LineagePath(
            urn=urn,
            entity_name=SchemaFieldUrn.from_string(urn).parent,
            column_name=SchemaFieldUrn.from_string(urn).field_path,
        )
        for urn in column_urns
    ]


def make_connection(graph: FakeGraph, client: FakeClient | None = None) -> DataHubConnection:
    """Wrap the fakes in the connection object the package expects."""
    return DataHubConnection(
        graph=graph,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        gms_url="http://fake-gms:8080",
        has_token=True,
    )


# The seeded graph's URNs, so tests speak the same language as the demo.
TABLE_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)"
MODEL_URN = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
DEPLOYMENT_URN = "urn:li:mlModelDeployment:(urn:li:dataPlatform:mlflow,credit_risk_v3_prod,PROD)"
LEAK_FEATURE_URN = "urn:li:mlFeature:(credit_risk,prior_default_flag)"

#: A fixed instant, so lag arithmetic in tests is exact.
NOW_MS = 1_800_000_000_000
_HOUR_MS = 3_600_000


def make_finding(
    *,
    live: bool = True,
    with_model: bool = True,
    lag_hours: float = 30.0,
    sla_hours: float = 6.0,
    has_owner: bool = False,
) -> FreshnessFinding:
    """Build a finding the way the detector would, without touching a graph.

    Shared by the narrator, document, and pipeline tests so they all describe the
    same scenario the integration test exercises for real.
    """
    models: tuple[ModelAtRisk, ...] = ()
    if with_model:
        models = (
            ModelAtRisk(
                urn=MODEL_URN,
                name="Credit Risk v3",
                hops=3,
                deployments=(DEPLOYMENT_URN,),
                live_deployments=(DEPLOYMENT_URN,) if live else (),
                features_at_risk=(LEAK_FEATURE_URN,),
                has_owner=has_owner,
            ),
        )

    radius = BlastRadius(
        signal=FreshnessSignal(
            dataset_urn=TABLE_URN,
            last_updated_ms=NOW_MS - int(lag_hours * _HOUR_MS),
            observed_at_ms=NOW_MS,
            sla_hours=sla_hours,
        ),
        failing_table_urn=TABLE_URN,
        failing_table_name="ecommerce.public.loans_raw",
        downstream_datasets=(TABLE_URN,),
        downstream_features=(LEAK_FEATURE_URN,),
        models=models,
    )
    return FreshnessFinding(blast_radius=radius)


#: The two columns the leak path runs between, in the seeded graph.
FEATURE_TABLE_URN = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
)
LABEL_COLUMN_URN = f"urn:li:schemaField:({TABLE_URN},default_status)"
LEAK_COLUMN_URN = f"urn:li:schemaField:({FEATURE_TABLE_URN},prior_default_flag)"
CLEAN_COLUMN_URN = f"urn:li:schemaField:({FEATURE_TABLE_URN},applicant_income)"
INCOME_COLUMN_URN = f"urn:li:schemaField:({TABLE_URN},income)"
CLEAN_FEATURE_URN = "urn:li:mlFeature:(credit_risk,applicant_income)"

LABEL_TERM_URN = "urn:li:glossaryTerm:modelguard.label"


def make_leakage_finding(*, live: bool = True, has_owner: bool = False) -> LeakageFinding:
    """Build a leakage finding the way the detector would, without touching a graph."""
    return LeakageFinding(
        model=ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,) if live else (),
            has_owner=has_owner,
        ),
        leak=LeakingFeature(
            feature_urn=LEAK_FEATURE_URN,
            feature_name="prior_default_flag",
            source_column_urn=LEAK_COLUMN_URN,
            source_column_name="prior_default_flag",
            label_column_urn=LABEL_COLUMN_URN,
            label_column_name="default_status",
            label_dataset_name="ecommerce.public.loans_raw",
            column_path=("prior_default_flag", "default_status"),
        ),
    )


TRAINING_RUN_URN = "urn:li:dataProcessInstance:credit_risk_v3_run"


def schema_metadata(fields: dict[str, str]) -> SchemaMetadataClass:
    """Build a SchemaMetadata carrying the given field-path to native-type map.

    Only ``fieldPath`` and ``nativeDataType`` matter to the drift detector, so the
    logical type is a fixed placeholder. Built from the real SDK class so the fake
    cannot drift from the shape a live GMS returns.
    """
    return SchemaMetadataClass(
        schemaName="s",
        platform="urn:li:dataPlatform:snowflake",
        version=0,
        hash="",
        platformSchema=OtherSchemaClass(rawSchema=""),
        fields=[
            SchemaFieldClass(
                fieldPath=path,
                type=SchemaFieldDataTypeClass(type=StringTypeClass()),
                nativeDataType=native_type,
            )
            for path, native_type in fields.items()
        ],
    )


def make_schema_drift_finding(
    *,
    live: bool = True,
    has_owner: bool = False,
    changes: tuple[SchemaChange, ...] = (
        SchemaChange("applicant_income", ChangeKind.RETYPED, "NUMBER", "VARCHAR"),
        SchemaChange("debt_to_income", ChangeKind.ADDED, None, "NUMBER"),
        SchemaChange("updated_at", ChangeKind.REMOVED, "TIMESTAMP", None),
    ),
) -> SchemaDriftFinding:
    """Build a schema-drift finding the way the detector would, without a graph."""
    return SchemaDriftFinding(
        model=ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,) if live else (),
            has_owner=has_owner,
        ),
        training_run_urn=TRAINING_RUN_URN,
        dataset_urn=FEATURE_TABLE_URN,
        dataset_name="ecommerce.public.customer_features",
        changes=changes,
    )


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def conn(graph: FakeGraph) -> DataHubConnection:
    return make_connection(graph)
