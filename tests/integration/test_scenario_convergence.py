"""Scenarios must converge, not accumulate, however often they are cycled.

The benchmark plants and reverts on every trial, so a scenario that leaves even a
little residue behind turns into a graph that drifts run over run, and into
numbers nobody can reproduce. One such bug already shipped and was caught here in
spirit: the leakage scenario briefly stamped ``transformOperation`` on the edge it
wrote, which is part of what GMS keys a fine-grained edge on, so the seeder then
added its own unmarked copy alongside and the column lineage grew (D-047). Every
offline test passed while that was true.

These are the live-graph checks that would have caught it directly: cycle the
scenarios, interleave them, re-seed underneath them, and assert the graph is
exactly where it started.
"""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import UpstreamLineageClass

from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.seed import graph_spec as spec
from janus.seed.scenarios import (
    plant_leakage,
    plant_schema_drift,
    plant_stale_source,
    revert_leakage,
    revert_schema_drift,
    revert_stale_source,
)
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def conn() -> DataHubConnection:
    """Connect to the local DataHub, or skip the whole module."""
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection) -> SeedResult:
    """Seed the ML graph once for the module."""
    return seed_ml_graph(conn)


def _column_edges(conn: DataHubConnection) -> list[tuple[str, str]]:
    """The feature table's column lineage, as sorted (downstream, upstream) pairs.

    Compared as a whole rather than by counting, so a scenario that swapped an
    edge for a different one of the same number would still be caught.
    """
    aspect = conn.graph.get_aspect(str(spec.feature_table_dataset_urn()), UpstreamLineageClass)
    pairs: list[tuple[str, str]] = []
    for edge in (aspect.fineGrainedLineages or []) if aspect else []:
        for downstream in edge.downstreams or []:
            for upstream in edge.upstreams or []:
                pairs.append(
                    (
                        downstream.split(",")[-1].rstrip(")"),
                        upstream.split(",")[-1].rstrip(")"),
                    )
                )
    return sorted(pairs)


@pytest.fixture
def planted(conn: DataHubConnection, seeded: SeedResult) -> list[tuple[str, str]]:
    """Put the graph in the seeded (leaking) state and return its lineage.

    Whatever an earlier test left behind is not a baseline, it is an accident, so
    this establishes one rather than reading one.
    """
    plant_leakage(conn)
    edges = _column_edges(conn)
    assert len(edges) == len(spec.COLUMN_LINEAGE), f"baseline is not the seeded shape: {edges}"
    return edges


def test_cycling_leakage_returns_the_lineage_to_exactly_where_it_started(
    conn: DataHubConnection, planted: list[tuple[str, str]]
):
    """Three revert/plant cycles must be indistinguishable from none."""
    for cycle in range(3):
        revert_leakage(conn)
        reverted = _column_edges(conn)
        assert len(reverted) == len(planted) - 1, f"revert removed more than one edge: {reverted}"

        plant_leakage(conn)
        assert _column_edges(conn) == planted, f"drifted on cycle {cycle + 1}"


def test_the_leaking_edge_is_the_only_one_a_revert_removes(
    conn: DataHubConnection, planted: list[tuple[str, str]]
):
    """A revert that blanked the lineage would silence the detector for the wrong reason."""
    revert_leakage(conn)
    reverted = set(_column_edges(conn))

    removed = set(planted) - reverted
    assert removed == {(spec.LEAKAGE_FEATURE, spec.LABEL_SOURCE_COLUMN)}
    assert reverted == set(planted) - removed, "a revert must not add or alter an edge"


def test_reseeding_a_reverted_graph_restores_the_leak_without_duplicating_it(
    conn: DataHubConnection, planted: list[tuple[str, str]]
):
    """The seeder owns that edge, so it must put back exactly one of it.

    This is the assertion the transformOperation bug failed: the seeder added its
    own copy alongside the scenario's, and the lineage grew by one every run.
    """
    revert_leakage(conn)
    seed_ml_graph(conn)

    assert _column_edges(conn) == planted


def test_interleaving_all_three_scenarios_leaves_the_graph_where_it_started(
    conn: DataHubConnection, planted: list[tuple[str, str]]
):
    """The benchmark runs them back to back; none may contaminate another."""
    plant_stale_source(conn, lag_hours=30.0)
    plant_schema_drift(conn)
    revert_leakage(conn)
    plant_leakage(conn)
    revert_schema_drift(conn)
    revert_stale_source(conn)
    seed_ml_graph(conn)

    assert _column_edges(conn) == planted


def test_no_scenario_writes_a_transform_operation_onto_an_edge(
    conn: DataHubConnection, planted: list[tuple[str, str]]
):
    """Directly guards the field that caused the duplication (D-047)."""
    aspect = conn.graph.get_aspect(str(spec.feature_table_dataset_urn()), UpstreamLineageClass)
    assert aspect is not None

    markers = [
        edge.transformOperation
        for edge in (aspect.fineGrainedLineages or [])
        if edge.transformOperation is not None
    ]
    assert markers == [], (
        f"a marked edge forks its identity and the seeder duplicates it: {markers}"
    )
