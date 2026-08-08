"""``link --infer`` against a live DataHub: does it propose the hand-written link?

Why a unit test does not settle this: every route the inference takes is a
read of a real aspect through a real index. ``dataProcessInstanceInput`` on a
training run, ``customProperties`` on that run, ``schemaMetadata.primaryKeys``,
and a search that resolves a bare table name are four separate places where the
fake and the server can disagree, and a proposal that is wrong on a live graph is
worse than one that never existed: it is a wrong feature table a human confirms.

The measure used here is the one that matters to a user: what the proposal says
about the seeded graph has to be true of the seeded graph, field by field,
including the field it cannot resolve. A proposal that is merely non-empty is
not evidence of anything.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import pytest

from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.seed import graph_spec as spec
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.link_infer import infer_link

pytestmark = pytest.mark.integration

T = TypeVar("T")

_INDEX_TIMEOUT_SECONDS = 90.0


def _eventually(probe: Callable[[], T], what: str, timeout: float = _INDEX_TIMEOUT_SECONDS) -> T:
    """Poll until the probe returns something truthy, or fail with what was awaited."""
    deadline = time.monotonic() + timeout
    last: T = probe()
    while not last and time.monotonic() < deadline:
        time.sleep(2.0)
        last = probe()
    if not last:
        raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")
    return last


@pytest.fixture(scope="module")
def conn() -> DataHubConnection:
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    return ScanConfig()


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection) -> SeedResult:
    """Seed the graph and wait until the model's own properties are readable."""
    result = seed_ml_graph(conn)
    _eventually(lambda: conn.graph.exists(result.model), "the seeded model to exist in DataHub")
    return result


def test_the_proposed_feature_table_is_the_one_the_seeder_linked(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Route 1 end to end: the run's recorded input, read off a live GMS."""
    proposal = infer_link(conn, config, seeded.model)

    assert proposal.feature_dataset_urn == seeded.feature_table_dataset
    assert "dataProcessInstanceInput" in proposal.reasons[0]


def test_the_label_declared_in_its_own_table_is_found_through_lineage(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The shape every real warehouse has, and the seeded graph with it.

    The label is declared on ``loans_raw.default_status`` while the features come
    from ``customer_features``, so a search of the feature table's own schema
    finds nothing and the proposal was permanently incomplete here. The upstream
    walk finds the declaration where somebody actually made it. Still a
    declaration, never a name match: pointing the config at a term nothing
    carries has to take it away again, which the offline suite pins.
    """
    proposal = infer_link(conn, config, seeded.model)

    assert proposal.label_column_urn == seeded.label_column
    assert proposal.complete, proposal.reasons
    assert "carries" in proposal.reasons[1]
    assert spec.SOURCE_TABLE in proposal.reasons[1]


def test_nothing_is_excluded_because_the_seeded_schema_declares_no_keys(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Exclusions come from declarations, never from names, and this graph has none.

    The seeded feature table carries an ``applicant_id`` column and an
    MLPrimaryKey entity, but its ``schemaMetadata`` declares no ``primaryKeys``
    and flags no field ``isPartOfKey``, which is exactly the state most ingested
    warehouse tables are in. So the proposal excludes nothing and says why,
    rather than excluding a column because it ends in ``_id``.
    """
    proposal = infer_link(conn, config, seeded.model)

    assert proposal.exclude == frozenset()
    assert "--exclude" in proposal.reasons[2]


def test_the_rendered_command_names_the_seeded_entities(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """What a user actually reads before saying yes."""
    command = infer_link(conn, config, seeded.model).command()

    assert f"--model {spec.MODEL_ID}" in command
    assert f"--features {spec.FEATURE_TABLE}" in command
    assert f"--label-column {spec.LABEL_SOURCE_COLUMN}" in command
    # The label lives in the source table, not the feature table, so its own flag
    # has to be rendered: a command without it would link against the wrong one.
    assert f"--label-table {spec.SOURCE_TABLE}" in command
