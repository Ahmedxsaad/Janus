from __future__ import annotations

import time

import pytest
from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
)

from modelguard.cli import TableResolutionError, _watch_once, resolve_table
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from tests.conftest import (
    DEPLOYMENT_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    lineage_result,
    make_connection,
)

OTHER_TABLE = "urn:li:dataset:(urn:li:dataPlatform:bigquery,analytics.public.loans_raw,PROD)"


def _conn(search_urns: list[str]) -> DataHubConnection:
    return make_connection(FakeGraph(), FakeClient(search_urns=search_urns))


def test_a_full_urn_is_used_as_given():
    assert resolve_table(_conn([]), TABLE_URN) == TABLE_URN


def test_a_malformed_urn_is_rejected_rather_than_scanned():
    with pytest.raises(Exception, match="urn"):
        resolve_table(_conn([]), "urn:li:dataset:not-a-real-urn")


def test_a_bare_table_name_resolves_through_search():
    assert resolve_table(_conn([TABLE_URN]), "loans_raw") == TABLE_URN


def test_a_fully_qualified_name_resolves_too():
    assert resolve_table(_conn([TABLE_URN]), "ecommerce.public.loans_raw") == TABLE_URN


def test_a_search_hit_whose_name_does_not_match_is_ignored():
    """Search is fuzzy. Only an exact name or last-segment match counts."""
    unrelated = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_archive,PROD)"
    with pytest.raises(TableResolutionError, match="no dataset named"):
        resolve_table(_conn([unrelated]), "loans_raw")


def test_an_unknown_table_fails_loudly_and_points_at_the_seeder():
    with pytest.raises(TableResolutionError, match="modelguard-seed"):
        resolve_table(_conn([]), "nonexistent")


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Two platforms hold a loans_raw. Scanning the wrong one silently is worse than failing."""
    with pytest.raises(TableResolutionError, match="matches 2 datasets"):
        resolve_table(_conn([TABLE_URN, OTHER_TABLE]), "loans_raw")


# --------------------------------------------------------------------------
# watch: poll, act on transitions only
# --------------------------------------------------------------------------

FEATURE_TABLE = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
)
HOUR = 3_600_000


def _watch_fixture(lag_hours: float) -> tuple[FakeGraph, FakeClient]:
    """A live model downstream of a table that is ``lag_hours`` old, per its operation aspect.

    ``watch`` measures staleness against real wall-clock time (it has no fixed
    ``now``), so the operation timestamp is anchored to ``time.time()`` rather than
    the fixed ``NOW_MS`` the ``now_ms``-driven tests use.
    """
    now_ms = int(time.time() * 1000)
    graph = FakeGraph(
        {
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                name="Credit Risk v3", deployments=[DEPLOYMENT_URN], mlFeatures=[LEAK_FEATURE_URN]
            ),
            (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): (
                MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
            ),
        },
        timeseries={
            (TABLE_URN, OperationClass): OperationClass(
                timestampMillis=now_ms,
                operationType=OperationTypeClass.UPDATE,
                lastUpdatedTimestamp=now_ms - int(lag_hours * HOUR),
                actor="urn:li:corpuser:datahub",
            )
        },
    )
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    client = FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(LEAK_FEATURE_URN, 2),
            lineage_result(MODEL_URN, 3),
        ]
    )
    return graph, client


def _poll(
    graph: FakeGraph, client: FakeClient, previous: frozenset | None
) -> frozenset[tuple[str, str, str]]:
    return _watch_once(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=None,
        llm=None,
        previous=previous,
    )


def test_the_signature_is_stable_across_polls_of_an_unchanged_stale_state():
    """Two polls of the same stale table compare equal, so watch does not re-fire."""
    graph, client = _watch_fixture(30.0)
    first = _poll(graph, client, previous=None)
    # A second poll of the same unchanged state must yield the identical signature.
    graph.emitted.clear()
    graph.graphql_calls.clear()
    second = _poll(graph, client, previous=first)
    assert first == second
    assert first != frozenset()


def test_a_newly_stale_table_is_written_back_on_the_first_poll():
    graph, client = _watch_fixture(30.0)
    signature = _poll(graph, client, previous=None)

    assert signature != frozenset()
    # The transition from clean to stale wrote the incident back.
    assert len(graph.graphql_calls) == 1
    _, variables = graph.graphql_calls[0]
    assert variables["input"]["resourceUrn"] == TABLE_URN


def test_an_unchanged_finding_set_writes_nothing_on_the_next_poll():
    """Idempotent by design, but re-writing every poll would be noise: stay quiet."""
    graph, client = _watch_fixture(30.0)
    signature = _poll(graph, client, previous=None)
    graph.emitted.clear()
    graph.graphql_calls.clear()

    unchanged = _poll(graph, client, previous=signature)

    assert unchanged == signature
    assert graph.emitted == [], "an unchanged finding set must not write again"
    assert graph.graphql_calls == []


def test_a_healthy_target_writes_nothing_and_has_an_empty_signature():
    graph, client = _watch_fixture(1.0)  # within the 6h default SLA
    signature = _poll(graph, client, previous=None)

    assert signature == frozenset()
    assert graph.emitted == []
    assert graph.graphql_calls == []


def test_finding_signature_carries_the_incident_dedup_key():
    """Signature entries are (finding_type, resource_urn, title): the dedup key itself."""
    graph, client = _watch_fixture(30.0)
    signature = _poll(graph, client, previous=None)
    (entry,) = signature
    finding_type, resource_urn, title = entry
    assert finding_type == "upstream-freshness"
    assert resource_urn == TABLE_URN
    assert title == "Stale upstream data in ecommerce.public.loans_raw"
