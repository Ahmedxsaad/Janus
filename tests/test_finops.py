"""Tables that exist only to feed unused models. Offline: no DataHub.

This is the one report in the project whose recommended action is to delete
something, so every test here is about a way it could be wrong in that
direction: a live consumer it failed to see, a model it called abandoned on the
strength of a missing timestamp, a shared mart listed as an orphan.
"""

from __future__ import annotations

from dataclasses import replace

from datahub.metadata.schema_classes import (
    AuditStampClass,
    DataProcessInstanceInputClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    TimeStampClass,
)

from janus.config import ScanConfig
from janus.finops import model_usage, report
from tests.conftest import (
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    lineage_result,
    make_connection,
)

CONFIG = ScanConfig()
NOW_MS = 1_800_000_000_000
DAY_MS = 86_400_000

SECOND_MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,second,PROD)"
SECOND_RUN = "urn:li:dataProcessInstance:second_run"
RUN_URN = "urn:li:dataProcessInstance:credit_risk_v3_run"


def place_model(
    graph: FakeGraph,
    *,
    model_urn: str = MODEL_URN,
    run_urn: str = RUN_URN,
    inputs: tuple[str, ...] = (FEATURE_TABLE_URN,),
    last_modified_ms: int | None = NOW_MS - 200 * DAY_MS,
    live: bool = False,
) -> None:
    """Put a model, its training run and its inputs on the fake graph."""
    deployments = [DEPLOYMENT_URN] if live else []
    graph.set_aspect(
        model_urn,
        MLModelPropertiesClass(
            trainingJobs=[run_urn],
            deployments=deployments,
            lastModified=(
                TimeStampClass(time=last_modified_ms) if last_modified_ms is not None else None
            ),
        ),
    )
    graph.set_aspect(run_urn, DataProcessInstanceInputClass(inputs=list(inputs)))
    if live:
        graph.set_aspect(
            DEPLOYMENT_URN,
            MLModelDeploymentPropertiesClass(status="IN_SERVICE"),
        )


def connection(graph: FakeGraph, upstream: list | None = None):
    """Wrap the graph, with an upstream lineage answer for every dataset query."""
    return make_connection(graph, FakeClient(lineage_results=upstream or []))


def test_a_stale_model_with_no_live_deployment_is_unused():
    graph = FakeGraph()
    place_model(graph)

    result = report(connection(graph), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    assert [usage.model.urn for usage in result.unused] == [MODEL_URN]
    assert result.candidates[0].dataset_urn == FEATURE_TABLE_URN


def test_a_live_deployment_keeps_a_model_in_use_however_old_it_is():
    """A model nobody has retrained in a year is still one serving traffic."""
    graph = FakeGraph()
    place_model(graph, last_modified_ms=NOW_MS - 900 * DAY_MS, live=True)

    result = report(connection(graph), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    assert result.unused == ()
    assert result.candidates == ()


def test_a_model_with_no_recorded_date_is_undated_and_never_unused():
    """The most expensive mistake this module could make, pinned.

    DataHub's mlflow source leaves a model with no timestamps at all. Reading
    that absence as abandonment would recommend deleting the tables behind a
    model somebody trains every week.
    """
    graph = FakeGraph()
    place_model(graph, last_modified_ms=None)

    result = report(connection(graph), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    assert result.unused == ()
    assert result.candidates == ()
    assert [usage.model.urn for usage in result.undated] == [MODEL_URN]


def test_a_table_feeding_one_unused_and_one_live_model_is_not_a_candidate():
    """One live consumer and it is not a saving, it is a table somebody needs."""
    graph = FakeGraph()
    place_model(graph)
    place_model(graph, model_urn=SECOND_MODEL, run_urn=SECOND_RUN, live=True)

    result = report(connection(graph), CONFIG, [MODEL_URN, SECOND_MODEL], now_ms=NOW_MS)

    assert [usage.model.urn for usage in result.unused] == [MODEL_URN]
    assert result.candidates == ()


def test_a_model_left_out_of_the_sweep_cannot_hide_a_live_consumer():
    """The reason the model list comes from discovery and not from search.

    Handed only the unused model, the same graph reports its shared input as an
    orphan. That is the correct output for the input given, and it is why
    passing a filtered list is documented as the one way to make this report
    dangerous: GMS hides non-latest versions from search, and a hidden
    version is exactly the live consumer that would be missed.
    """
    graph = FakeGraph()
    place_model(graph)
    place_model(graph, model_urn=SECOND_MODEL, run_urn=SECOND_RUN, live=True)

    full = report(connection(graph), CONFIG, [MODEL_URN, SECOND_MODEL], now_ms=NOW_MS)
    partial = report(connection(graph), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    assert full.candidates == ()
    assert [item.dataset_urn for item in partial.candidates] == [FEATURE_TABLE_URN]


def test_the_tables_behind_an_input_are_candidates_too():
    """The staging and raw tables are where the saving actually is.

    An unused model's own feature table is usually one row of the answer; the
    two staging tables and the raw extract behind it are the rest.
    """
    graph = FakeGraph()
    place_model(graph)
    upstream = [lineage_result(TABLE_URN, hops=1, direction="upstream")]

    result = report(connection(graph, upstream), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    assert {item.dataset_urn for item in result.candidates} == {FEATURE_TABLE_URN, TABLE_URN}


def test_an_upstream_table_beyond_the_hop_cap_is_not_recommended_for_deletion():
    """Above two hops DataHub returns results past max_hops (detect rule 3).

    Trusting the server here would put a table this model has no relationship
    with on a retirement list.
    """
    graph = FakeGraph()
    place_model(graph)
    upstream = [lineage_result(TABLE_URN, hops=CONFIG.max_hops + 1, direction="upstream")]

    result = report(connection(graph, upstream), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    assert {item.dataset_urn for item in result.candidates} == {FEATURE_TABLE_URN}


def test_the_window_is_configuration_and_moves_the_answer():
    """The knob is real, not decorative: 90 days and 300 days differ here."""
    graph = FakeGraph()
    place_model(graph, last_modified_ms=NOW_MS - 200 * DAY_MS)
    conn = connection(graph)

    assert report(conn, CONFIG, [MODEL_URN], now_ms=NOW_MS).unused
    assert not report(
        conn, replace(CONFIG, unused_model_days=300), [MODEL_URN], now_ms=NOW_MS
    ).unused


def test_a_candidate_names_the_platform_because_the_name_alone_repeats():
    """A dbt project over a postgres warehouse gives two datasets one name."""
    postgres = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.customers,PROD)"
    dbt = "urn:li:dataset:(urn:li:dataPlatform:dbt,warehouse.customers,PROD)"
    graph = FakeGraph()
    place_model(graph, inputs=(postgres, dbt))

    result = report(connection(graph), CONFIG, [MODEL_URN], now_ms=NOW_MS)

    described = sorted(item.describe() for item in result.candidates)
    assert described == [
        "warehouse.customers (dbt)  feeds only: credit_risk_v3",
        "warehouse.customers (postgres)  feeds only: credit_risk_v3",
    ]


def test_the_newest_timestamp_wins_when_several_are_recorded():
    """`created` long ago and `lastModified` yesterday is a model in active use."""
    graph = FakeGraph()
    graph.set_aspect(
        MODEL_URN,
        MLModelPropertiesClass(
            created=AuditStampClass(time=NOW_MS - 900 * DAY_MS, actor="urn:li:corpuser:x"),
            lastModified=TimeStampClass(time=NOW_MS - DAY_MS),
        ),
    )

    usage = model_usage(connection(graph), MODEL_URN)

    assert usage.idle_days(NOW_MS) == 1.0
    assert not usage.is_unused(CONFIG, NOW_MS)
