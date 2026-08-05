"""The guard-coverage trend on ModelGuard's own dataFlow (T-15). Offline."""

from __future__ import annotations

from datetime import UTC, datetime

from datahub.metadata.schema_classes import (
    StructuredPropertiesClass,
    StructuredPropertyValueAssignmentClass,
)
from datahub.metadata.urns import StructuredPropertyUrn

from modelguard.detect.coverage import CHECK_LEAKAGE, CHECK_SCHEMA_DRIFT
from modelguard.detect.guard_coverage import CatalogCoverage, CheckCoverage
from modelguard.writeback.coverage_history import (
    HISTORY_LIMIT,
    CoverageEntry,
    append_entry,
    parse_entry,
    project_history,
    read_history,
)
from modelguard.writeback.process_instance import agent_flow_urn
from modelguard.writeback.properties import COVERAGE_HISTORY
from tests.conftest import FakeGraph, make_connection

AT = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)


def make_coverage(*, models: int = 4, leakage_covered: int = 1) -> CatalogCoverage:
    """A two-check catalog figure, so an entry has something to carry."""
    return CatalogCoverage(
        models=models,
        checks=(
            CheckCoverage(check=CHECK_LEAKAGE, covered=leakage_covered, total=models),
            CheckCoverage(check=CHECK_SCHEMA_DRIFT, covered=0, total=models),
        ),
        next_join=None,
    )


def stored(graph: FakeGraph) -> list[str]:
    """The raw history values the graph now holds on the agent's flow."""
    aspect = graph.get_aspect(agent_flow_urn(), StructuredPropertiesClass)
    urn = str(StructuredPropertyUrn(COVERAGE_HISTORY))
    for assignment in aspect.properties:
        if assignment.propertyUrn == urn:
            return [str(value) for value in assignment.values]
    return []


def seed(graph: FakeGraph, *entries: str) -> None:
    """Place stored history lines on the flow, as an earlier release would have."""
    graph.set_aspect(
        agent_flow_urn(),
        StructuredPropertiesClass(
            properties=[
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(COVERAGE_HISTORY)),
                    values=list(entries),
                )
            ]
        ),
    )


def test_an_entry_round_trips_through_its_stored_form():
    """Render then parse returns the same facts, per-check halves included."""
    entry = CoverageEntry(
        recorded_at="2026-08-05T09:30:00Z",
        run_id="scan-abc",
        models=4,
        covered=1,
        total=8,
        per_check=(
            CheckCoverage(check=CHECK_LEAKAGE, covered=1, total=4),
            CheckCoverage(check=CHECK_SCHEMA_DRIFT, covered=0, total=4),
        ),
    )

    assert parse_entry(entry.render()) == entry


def test_a_check_name_containing_a_space_survives_the_round_trip():
    """Every check name has a space in it, so the parse may not tokenise on one.

    A row whose name came back as "proxy" would silently open a new category in
    a figure that is supposed to have five, and it would look like a real one.
    """
    entry = CoverageEntry(
        recorded_at="2026-08-05T09:30:00Z",
        run_id="scan-abc",
        models=1,
        covered=0,
        total=1,
        per_check=(CheckCoverage(check="proxy candidate", covered=0, total=1),),
    )

    parsed = parse_entry(entry.render())

    assert parsed is not None
    assert parsed.per_check[0].check == "proxy candidate"


def test_a_hand_edited_line_is_dropped_rather_than_raised_on():
    """The property is editable by anyone; a bad line costs a point, not a sweep."""
    assert parse_entry("not an entry") is None
    assert parse_entry("2026-08-05T09:30:00Z|scan-abc|four|1/8|") is None
    assert parse_entry("2026-08-05T09:30:00Z|scan-abc|4|one-eighth|") is None


def test_a_malformed_per_check_fraction_is_not_read_as_zero():
    """A row whose numbers cannot be parsed drops the entry, never reports 0/0.

    Reading an unparseable fraction as zero coverage would manufacture a drop in
    a trend nobody caused, which is the one thing a trend must not do.
    """
    assert parse_entry("2026-08-05T09:30:00Z|scan-abc|4|1/8|target leakage=x/4") is None


def test_reading_a_graph_no_sweep_has_touched_returns_nothing():
    """No history is empty, not a zeroed entry: never measured is not measured zero."""
    assert read_history(make_connection(FakeGraph())) == ()


def test_a_sweep_appends_to_what_is_already_there():
    graph = FakeGraph()
    seed(graph, "2026-08-01T09:00:00Z|scan-old|4|0/8|target leakage=0/4,schema drift=0/4")

    history = project_history(make_connection(graph), make_coverage(), "scan-new", now=AT)

    assert [entry.run_id for entry in history] == ["scan-old", "scan-new"]
    assert history[-1].covered == 1
    assert history[-1].recorded_at == "2026-08-05T09:30:00Z"


def test_rerunning_one_sweep_replaces_its_own_row_instead_of_adding_a_second():
    """Idempotent on the run id, like every other write in this package."""
    graph = FakeGraph()
    conn = make_connection(graph)
    append_entry(conn, make_coverage(leakage_covered=1), "scan-same", now=AT)
    append_entry(conn, make_coverage(leakage_covered=3), "scan-same", now=AT)

    lines = stored(graph)

    assert len(lines) == 1
    entry = parse_entry(lines[0])
    assert entry is not None
    assert entry.covered == 3


def test_the_history_is_capped_oldest_first():
    """A sweep every hour must not grow a property forever."""
    graph = FakeGraph()
    seed(
        graph,
        *[
            f"2026-08-01T09:00:0{index % 10}Z|scan-{index}|4|0/8|target leakage=0/4"
            for index in range(HISTORY_LIMIT + 5)
        ],
    )

    history = project_history(make_connection(graph), make_coverage(), "scan-new", now=AT)

    assert len(history) == HISTORY_LIMIT
    assert history[-1].run_id == "scan-new"
    # The far past is what stops mattering, so it is what goes.
    # 25 seeded plus this one is 26; the cap keeps the last 20, so the first six
    # are what falls off.
    assert history[0].run_id == "scan-6"


def test_the_trend_lands_on_the_agents_own_flow_and_not_on_a_guarded_asset():
    """The figure is about the whole graph, so it hangs on ModelGuard's entity."""
    graph = FakeGraph()
    append_entry(make_connection(graph), make_coverage(), "scan-abc", now=AT)

    written = {
        str(mcp.entityUrn)
        for mcp in graph.emitted
        if isinstance(mcp.aspect, StructuredPropertiesClass)
    }

    assert written == {agent_flow_urn()}
    assert agent_flow_urn().startswith("urn:li:dataFlow:")
