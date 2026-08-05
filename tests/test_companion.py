"""The general DataHub companion: three questions about the assets you own."""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    AssertionResultClass,
    AssertionResultTypeClass,
    AssertionRunEventClass,
    AssertionRunStatusClass,
    DeprecationClass,
)

from janus import companion
from janus.config import ScanConfig
from janus.env import ConfigError

from .conftest import FakeGraph, active_incident, make_connection

DATASET = "urn:li:dataset:(urn:li:dataPlatform:postgres,analytics.loans_raw,PROD)"
MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
OWNER = "urn:li:corpuser:datahub"
ASSERTION = "urn:li:assertion:abc123"


def _run_event(result: str) -> AssertionRunEventClass:
    return AssertionRunEventClass(
        timestampMillis=1,
        runId="r",
        asserteeUrn=DATASET,
        status=AssertionRunStatusClass.COMPLETE,
        assertionUrn=ASSERTION,
        result=AssertionResultClass(type=result),
    )


def test_the_sweep_asks_for_each_entity_type_filtered_by_owner():
    graph = FakeGraph(by_entity_type={"dataset": [DATASET], "mlModel": [MODEL]})
    urns = companion.owned_urns(make_connection(graph), OWNER, ScanConfig())
    assert set(urns) == {DATASET, MODEL}
    # The filter is the point: an unfiltered sweep would watch the whole
    # catalogue rather than one person's assets.
    for _types, filters in graph.filter_calls:
        assert filters == ({"field": "owners", "values": [OWNER]},)


def test_the_sweep_stops_at_the_cap_rather_than_walking_a_whole_catalogue():
    many = [f"urn:li:dataset:(urn:li:dataPlatform:postgres,t{i},PROD)" for i in range(50)]
    graph = FakeGraph(by_entity_type={"dataset": many})
    urns = companion.owned_urns(make_connection(graph), OWNER, ScanConfig(companion_entity_cap=10))
    assert len(urns) == 10


def test_an_active_incident_is_reported():
    graph = FakeGraph(by_entity_type={"dataset": [DATASET]})
    active_incident(
        graph,
        resource_urn=DATASET,
        incident_urn="urn:li:incident:1",
        incident_type="DATA_QUALITY",
        title="loans_raw is stale",
    )
    sweep = companion.poll(make_connection(graph), OWNER, ScanConfig())
    assert [(issue.source, issue.title) for issue in sweep.issues] == [
        ("incident", "loans_raw is stale")
    ]
    assert sweep.owned == 1


def test_a_failing_assertion_run_is_reported_and_a_passing_one_is_not():
    graph = FakeGraph(
        by_entity_type={"dataset": [DATASET]},
        related={DATASET: [ASSERTION]},
        timeseries={
            (ASSERTION, AssertionRunEventClass): _run_event(AssertionResultTypeClass.FAILURE)
        },
    )
    failing = companion.poll(make_connection(graph), OWNER, ScanConfig())
    assert [issue.source for issue in failing.issues] == ["assertion"]

    graph._timeseries[(ASSERTION, AssertionRunEventClass)] = _run_event(
        AssertionResultTypeClass.SUCCESS
    )
    passing = companion.poll(make_connection(graph), OWNER, ScanConfig())
    assert passing.issues == ()


def test_a_lifted_deprecation_is_not_a_deprecation():
    graph = FakeGraph(by_entity_type={"dataset": [DATASET]})
    graph.set_aspect(DATASET, DeprecationClass(deprecated=True, note="moving to v2", actor=OWNER))
    assert [
        issue.source for issue in companion.poll(make_connection(graph), OWNER, ScanConfig()).issues
    ] == ["deprecation"]

    graph.set_aspect(DATASET, DeprecationClass(deprecated=False, note="", actor=OWNER))
    assert companion.poll(make_connection(graph), OWNER, ScanConfig()).issues == ()


def test_the_worst_source_is_the_one_the_dog_barks_about():
    graph = FakeGraph(by_entity_type={"dataset": [DATASET]}, related={DATASET: [ASSERTION]})
    graph._timeseries[(ASSERTION, AssertionRunEventClass)] = _run_event(
        AssertionResultTypeClass.FAILURE
    )
    graph.set_aspect(DATASET, DeprecationClass(deprecated=True, note="going", actor=OWNER))
    active_incident(
        graph,
        resource_urn=DATASET,
        incident_urn="urn:li:incident:1",
        incident_type="DATA_QUALITY",
        title="incident first",
    )
    # The fake keys relationships by entity alone, so both edges live in one
    # list; a real GMS separates them by relationship type. The incident read
    # skips the assertion URN because it carries no IncidentInfo.
    graph._related[DATASET] = ["urn:li:incident:1", ASSERTION]
    event = companion.event_for(companion.poll(make_connection(graph), OWNER, ScanConfig()))
    assert event.state == "barking"
    assert event.title.startswith("incident first")
    assert "+2 more" in event.title


def test_a_clean_sweep_says_how_much_it_looked_at():
    graph = FakeGraph(by_entity_type={"dataset": [DATASET], "mlModel": [MODEL]})
    event = companion.event_for(companion.poll(make_connection(graph), OWNER, ScanConfig()))
    assert event.state == "patrolling"
    # "nothing wrong" and "nothing checked" must never read the same.
    assert "2 owned assets" in event.title


def test_a_missing_owner_fails_loudly_naming_the_variable(monkeypatch):
    monkeypatch.delenv(companion.ENV_OWNER, raising=False)
    with pytest.raises(ConfigError, match=companion.ENV_OWNER):
        companion.owner_urn()
