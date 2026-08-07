"""Shared fixtures. Unit tests never touch a network or a live DataHub."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from datahub.metadata.schema_classes import (
    AuditStampClass,
    IncidentInfoClass,
    IncidentStateClass,
    IncidentStatusClass,
    OtherSchemaClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
)
from datahub.metadata.urns import SchemaFieldUrn, Urn
from datahub.sdk.lineage_client import LineagePath, LineageResult

from janus.argos.events import ENV_UI_URL
from janus.argos.window import ENV_BINARY
from janus.client import DataHubConnection
from janus.companion import ENV_OWNER
from janus.config import TABLE_LEVEL_PRECISION
from janus.llm import ENV_LLM_API_KEY, ENV_LLM_MODEL, ENV_LLM_PROVIDER
from janus.logs import ENV_LOG_FORMAT
from janus.mcl import (
    ENV_KAFKA_BOOTSTRAP,
    ENV_KAFKA_GROUP_ID,
    ENV_SCHEMA_REGISTRY_URL,
)
from janus.models import (
    BlastRadius,
    ChangeKind,
    Deduction,
    DeprecatedInputFinding,
    Finding,
    FreshnessFinding,
    FreshnessSignal,
    LeakageFinding,
    LeakingFeature,
    ModelAtRisk,
    ModelRef,
    ProxyCandidate,
    ProxyCandidateFinding,
    SchemaChange,
    SchemaDriftFinding,
    SensitiveFeature,
    SensitiveSourceFinding,
    TableLevelRiskFinding,
    TableRisk,
    TrustBand,
    TrustScore,
)
from janus.telemetry import ENV_OTEL_ENDPOINT, ENV_OTEL_HEADERS


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
        by_entity_type: dict[str, list[str]] | None = None,
    ) -> None:
        self._aspects = aspects or {}
        self._exists = exists
        self._related = related or {}
        self._timeseries = timeseries or {}
        self._by_entity_type = by_entity_type or {}
        # updateIncidentStatus defaults to accepted: resolve_incident now raises
        # on a rejected mutation (writeback/incidents.py), and the overwhelming
        # majority of tests that reach a resolve are testing something else
        # entirely (a risk flag clearing, a process instance's outputs) and never
        # meant to exercise GMS rejecting the call. A test of the rejection path
        # itself passes graphql_response explicitly, which still wins here.
        self.graphql_response = (
            graphql_response if graphql_response is not None else {"updateIncidentStatus": True}
        )
        self.emitted: list[Any] = []
        self.graphql_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.filter_calls: list[tuple[tuple[str, ...], tuple[Any, ...]]] = []
        #: What janus.discovery's model scroll answers with. Populated by
        #: make_connection from the same list FakeSearch replays, so a test that
        #: seeds search results gets them through both discovery paths without
        #: having to know which one the code under test happens to take.
        self.model_urns: list[str] = []

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
        if "janusScrollModels" in query:
            # janus.discovery hand-writes this one because the SDK's search
            # cannot turn off DataHub's hiding of non-latest versions. Answered
            # here rather than through graphql_response so a test setting that
            # for an incident mutation does not also have to describe a scroll.
            return {
                "scrollAcrossEntities": {
                    "nextScrollId": None,
                    "searchResults": [{"entity": {"urn": urn}} for urn in self.model_urns],
                }
            }
        return self.graphql_response

    def get_related_entities(
        self, entity_urn: str, relationship_types: list[str], direction: Any
    ) -> Any:
        return [_RelatedEntity(urn) for urn in self._related.get(entity_urn, [])]

    def get_urns_by_filter(
        self,
        *,
        entity_types: list[str] | None = None,
        extraFilters: list[dict[str, Any]] | None = None,  # noqa: N803 - the SDK's own spelling
        **_: Any,
    ) -> Any:
        """Entity discovery by filter, the companion's owned-asset sweep.

        Keyed by entity type so a test can hand back datasets for one call and
        models for the next, which is what the real sweep does.
        """
        self.filter_calls.append((tuple(entity_types or ()), tuple(extraFilters or ())))
        for entity_type in entity_types or []:
            yield from self._by_entity_type.get(entity_type, [])

    def get_entities(
        self,
        entity_name: str,
        urns: list[str],
        aspects: list[str] | None = None,
        with_system_metadata: bool = False,
    ) -> dict[str, dict[str, tuple[Any, Any]]]:
        """Batch aspect read, mirroring DataHubGraph.get_entities's shape.

        Reads the same ``_aspects`` store ``get_aspect``/``set_aspect`` use, keyed
        by ``ASPECT_NAME`` rather than a second fixture format, so a test that
        already did ``graph.set_aspect(urn, SomeAspect(...))`` needs no change to
        also be readable through the batched call graph_reads.live_deployments
        makes (docs/04-detectors.md: no N+1 single fetches).
        """
        wanted = set(aspects or [])
        result: dict[str, dict[str, tuple[Any, Any]]] = {}
        for urn in urns:
            entity_aspects: dict[str, tuple[Any, Any]] = {}
            for (stored_urn, aspect_type), value in self._aspects.items():
                if stored_urn != urn:
                    continue
                name = getattr(aspect_type, "ASPECT_NAME", None)
                if name in wanted:
                    entity_aspects[name] = (value, None)
            result[urn] = entity_aspects
        return result


class FakeEntities:
    """Records entities handed to client.entities.upsert and patch builders to update."""

    def __init__(self) -> None:
        self.upserted: list[Any] = []
        self.updated: list[Any] = []

    def upsert(self, entity: Any, **_: Any) -> None:
        self.upserted.append(entity)

    def update(self, patch_builder: Any, **_: Any) -> None:
        """Record a patch builder, the way the real client accepts one.

        Undoing one column-lineage edge has to go through a patch: add_lineage's
        own patch is additive, so re-sending a reduced mapping would leave the
        removed edge in place. The builder is kept whole so a test can build it
        and assert on the JSON patch that would actually reach GMS.
        """
        self.updated.append(patch_builder)


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
    fixture that omitted them would let a broken detector pass.
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
    """Wrap the fakes in the connection object the package expects.

    Model discovery reads through the graph's GraphQL scroll rather than the
    search client (janus/discovery.py), so the search fixture is copied
    across here. Without it a test would seed models one way and the code would
    look for them the other, and pass by finding nothing.
    """
    if client is not None and not graph.model_urns:
        graph.model_urns = list(client.search.urns)
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
    table_name: str = "ecommerce.public.loans_raw",
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
        failing_table_name=table_name,
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

LABEL_TERM_URN = "urn:li:glossaryTerm:janus.label"


def make_leakage_finding(
    *,
    live: bool = True,
    has_owner: bool = False,
    other_paths: tuple[tuple[str, ...], ...] = (),
) -> LeakageFinding:
    """Build a leakage finding the way the detector would, without touching a graph.

    ``other_paths`` is the multi-path case: the chains the walk found besides the
    one quoted as proof. Empty by default, which is the single-path finding every
    test before the counterfactual work described.
    """
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
            other_paths=other_paths,
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


#: Every optional configuration group (root rule 6c). None of these is set on a
#: machine running only the unit suite, but all of them are set on somebody's
#: development machine, which is the whole problem: see the fixture below.
OPTIONAL_GROUP_ENV = (
    ENV_LLM_PROVIDER,
    ENV_LLM_MODEL,
    ENV_LLM_API_KEY,
    ENV_OTEL_ENDPOINT,
    ENV_OTEL_HEADERS,
    ENV_KAFKA_BOOTSTRAP,
    ENV_SCHEMA_REGISTRY_URL,
    ENV_KAFKA_GROUP_ID,
    ENV_LOG_FORMAT,
    ENV_OWNER,
    ENV_BINARY,
    ENV_UI_URL,
)


@pytest.fixture(autouse=True)
def unconfigured_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hide the developer's own `.env` from the unit suite.

    `env.py` calls `load_dotenv` at import, so every `*_config_from_env()` a unit
    test calls reads whatever `.env` sits in the working directory. Three tests
    assert that an unset group reads as `None`, and each of them passes or fails
    according to who is running it: `test_mcl.py` is red on a machine whose `.env`
    carries the Kafka group, `test_llm.py` is red on any machine configured to run
    the agent at all, which is most of them. Green in CI and red locally is worse
    than red everywhere, because the machine that disagrees is assumed to be the
    broken one.

    Clearing the groups here rather than in each test is what makes it hold for
    the next such test too, which nobody will remember to write a `delenv` into.
    Only the optional groups are cleared: identity variables are a different
    question (rule 6a) and no offline test needs them.

    Integration tests are exempt. They are the ones that legitimately read a real
    configured environment, and they are already separated by this marker.
    """
    if "integration" in request.keywords:
        return
    for name in OPTIONAL_GROUP_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def conn(graph: FakeGraph) -> DataHubConnection:
    return make_connection(graph)


def active_incident(
    graph: FakeGraph,
    *,
    resource_urn: str,
    incident_urn: str,
    incident_type: str,
    title: str,
) -> None:
    """Pre-seed an active incident as if some earlier, now-gone run raised it.

    The fixture behind every reconciliation test: it is how a graph that already
    carries a finding, raised by a process that no longer exists, is set up.
    """
    stamp = AuditStampClass(time=0, actor="urn:li:corpuser:datahub")
    graph._related[resource_urn] = [incident_urn]
    graph._aspects[(incident_urn, IncidentInfoClass)] = IncidentInfoClass(
        type=incident_type,
        entities=[resource_urn],
        title=title,
        description="body",
        status=IncidentStatusClass(state=IncidentStateClass.ACTIVE, lastUpdated=stamp),
        created=stamp,
    )


def make_sensitive_source_finding(
    *, live: bool = True, has_owner: bool = False
) -> SensitiveSourceFinding:
    """Build a sensitive-source finding the way the detector would, without a graph."""
    return SensitiveSourceFinding(
        model=ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,) if live else (),
            has_owner=has_owner,
        ),
        exposure=SensitiveFeature(
            feature_urn=CLEAN_FEATURE_URN,
            feature_name="applicant_income",
            source_column_urn=CLEAN_COLUMN_URN,
            source_column_name="applicant_income",
            sensitive_column_urn=INCOME_COLUMN_URN,
            sensitive_column_name="income",
            sensitive_dataset_name="ecommerce.public.loans_raw",
            marker_urn="urn:li:tag:janus.sensitive",
            column_path=("applicant_income", "income"),
        ),
    )


def make_proxy_candidate_finding(
    *, live: bool = True, has_owner: bool = False
) -> ProxyCandidateFinding:
    """Build a proxy candidate the way the detector would, without a graph.

    The fork rather than a chain: `applicant_income` and the classified
    `income_bracket_demographic` both descend from `income`.
    """
    return ProxyCandidateFinding(
        model=ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,) if live else (),
            has_owner=has_owner,
        ),
        candidate=ProxyCandidate(
            feature_urn=CLEAN_FEATURE_URN,
            feature_name="applicant_income",
            source_column_urn=CLEAN_COLUMN_URN,
            source_column_name="applicant_income",
            protected_column_urn=(
                f"urn:li:schemaField:({FEATURE_TABLE_URN},income_bracket_demographic)"
            ),
            protected_column_name="income_bracket_demographic",
            protected_dataset_name="ecommerce.public.customer_features",
            marker_urn="urn:li:tag:janus.protected-attribute",
            ancestor_urn=INCOME_COLUMN_URN,
            ancestor_name="income",
            ancestor_dataset_name="ecommerce.public.loans_raw",
            feature_hops=1,
            protected_hops=1,
        ),
    )


def make_deprecated_input_finding(
    *, live: bool = True, has_owner: bool = False, note: str = "Replaced by loans_v2 on 2026-09-01."
) -> DeprecatedInputFinding:
    """Build a deprecated-input finding the way the detector would, without a graph."""
    return DeprecatedInputFinding(
        model=ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,) if live else (),
            has_owner=has_owner,
        ),
        dataset_urn=FEATURE_TABLE_URN,
        dataset_name="ecommerce.public.customer_features",
        note=note,
        decommission_time_ms=1_800_000_000_000,
    )


def make_table_level_finding(
    *,
    risk: TableRisk = TableRisk.DEPRECATED,
    live: bool = True,
    has_owner: bool = False,
) -> TableLevelRiskFinding:
    """Build a degraded-mode finding the way the detector would, without a graph."""
    return TableLevelRiskFinding(
        model=ModelRef(
            urn=MODEL_URN,
            name="Credit Risk v3",
            deployments=(DEPLOYMENT_URN,),
            live_deployments=(DEPLOYMENT_URN,) if live else (),
            has_owner=has_owner,
        ),
        risk=risk,
        dataset_urn=FEATURE_TABLE_URN,
        dataset_name="ecommerce.public.customer_features",
        measurement={"deprecation_note": "Replaced by loans_v2 on 2026-09-01."},
        precision=TABLE_LEVEL_PRECISION,
    )


def make_trust_score(
    value: int,
    *,
    band: TrustBand | None = None,
    deductions: Mapping[str, float] | None = None,
) -> TrustScore:
    """Build a trust score the way the detector would, without running it.

    Takes the deductions as a name-to-points mapping because that is what a test
    is actually saying something about; the cause is filled in from the name, so
    a test never has to invent prose it is not asserting on.
    """
    named = {"leakage": 20.0} if deductions is None else deductions
    return TrustScore(
        value=value,
        band=band or (TrustBand.HEALTHY if value >= 70 else TrustBand.AT_RISK),
        deductions=tuple(
            Deduction(name=name, points=points, cause=f"a {name} finding")
            for name, points in sorted(named.items(), key=lambda item: (-item[1], item[0]))
        ),
    )


#: URN prefixes of the entities Janus emits about *itself*: the agent's
#: dataFlow, its scan dataJob, and one dataProcessInstance per run.
OWN_RUN_PREFIXES = ("urn:li:dataFlow:", "urn:li:dataJob:", "urn:li:dataProcessInstance:")


def emitted_about_the_graph(graph: FakeGraph) -> list[Any]:
    """The MCPs a scan sent about somebody else's entities.

    Every scan writes its own process run into the graph, so ``graph.emitted``
    is never empty any more. A test that means "this healthy target was left
    untouched" is asking about the guarded entities, not about Janus's own
    run record, so it filters that record out rather than asserting a silence
    that no longer exists.
    """
    return [mcp for mcp in graph.emitted if not str(mcp.entityUrn).startswith(OWN_RUN_PREFIXES)]


def one_of_every_finding() -> tuple[Finding, ...]:
    """One instance of each concrete Finding subclass, built by the factories above.

    Several tests have to hold something true of *every* detector's output: that
    a report renders, that a title carries the prefix its finding type is
    registered under. Written out by hand each time, that list goes stale the
    moment a detector lands, which is how two governance findings once shipped
    with no impact report at all. Here it is asserted against
    ``Finding.__subclasses__()``, so a new detector with no factory fails this
    rather than quietly leaving a hole in whatever the caller was checking.
    """
    findings: tuple[Finding, ...] = (
        make_finding(),
        make_leakage_finding(),
        make_schema_drift_finding(),
        make_sensitive_source_finding(),
        make_proxy_candidate_finding(),
        make_deprecated_input_finding(),
        make_table_level_finding(),
    )
    covered = {type(finding) for finding in findings}
    concrete = {
        subclass
        for subclass in Finding.__subclasses__()
        if not getattr(subclass, "__abstractmethods__", frozenset())
    }
    missing = sorted(cls.__name__ for cls in concrete - covered)
    assert covered == concrete, f"no factory for: {missing}"
    return findings
