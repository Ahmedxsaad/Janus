"""The trust history against a live DataHub: a MULTIPLE structured property, read back.

Why a unit test does not cover this (F8): ``janus.trust_history`` is a
structured property with MULTIPLE cardinality holding up to twenty pipe-delimited
strings, and until this file ran, no such property had ever been written to a real
GMS and read back. ``FakeGraph`` stores whatever it is handed; a server validates
the property definition, the value type, and the cardinality, and any of those
rejecting a value is a failure the fake cannot express.

The second thing measured here is convergence, which is the whole idempotency
contract applied to a list: rerunning one scan under its own ``run_id`` must
replace that run's row rather than append a second one, or a watcher polling
every five minutes would fill the property in an afternoon.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TypeVar

import pytest

from janus.agent.pipeline import run_scan
from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.detect.blast_radius import freshness_signal
from janus.detect.leakage import leakage_findings
from janus.detect.trust_score import DEDUCTION_UPSTREAM_FAILURE
from janus.seed.scenarios import plant_leakage, plant_stale_source, revert_stale_source
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.trust_history import HISTORY_LIMIT, read_history

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
def seeded(conn: DataHubConnection, config: ScanConfig) -> SeedResult:
    """Seed the graph and wait until the leak the scans will score is visible."""
    result = seed_ml_graph(conn)
    plant_leakage(conn)
    _eventually(
        lambda: bool(leakage_findings(conn, result.model, config)),
        "the seeded leak to be indexed and scoreable",
    )
    return result


def test_three_scans_leave_three_entries_a_live_gms_serves_back(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The write, the read, and the order, against the real property store."""
    before = len(read_history(conn, seeded.model))
    run_ids = [f"history-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for run_id in run_ids:
        run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)

    history = read_history(conn, seeded.model)
    # Capped, so a graph that has been scanned many times before saturates rather
    # than growing: assert on the tail, which is this test's own three runs.
    assert len(history) == min(before + 3, HISTORY_LIMIT)
    assert [entry.run_id for entry in history[-3:]] == run_ids
    assert all(0 <= entry.score <= 100 for entry in history)
    assert all(entry.band for entry in history)


def test_rerunning_one_scan_replaces_its_row_rather_than_appending(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Idempotency for a list-valued property: the run id is the key (D-081)."""
    run_id = f"history-{uuid.uuid4().hex[:8]}"
    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)
    after_first = read_history(conn, seeded.model)

    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)
    after_rerun = read_history(conn, seeded.model)

    assert len(after_rerun) == len(after_first)
    assert [entry.run_id for entry in after_rerun].count(run_id) == 1


def test_a_score_that_moves_is_visible_as_a_trend_not_only_a_number(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The point of the history: 82 means one thing after a 95 and another after a 64.

    The upstream table is made stale and then fresh again, so the model's score
    drops and recovers across two scans of the same target, and the two entries
    have to differ. If they do not, the history is recording the write rather
    than the measurement.
    """
    table_urn = seeded.source_table
    revert_stale_source(conn)
    # Waited on, not assumed. operation is a timeseries aspect served from the
    # index, so an earlier module's planted staleness is still readable for a few
    # seconds and the "fresh" scan would score the table stale, leaving two equal
    # entries and a test that passes or fails on timing.
    _eventually(
        lambda: (
            (signal := freshness_signal(conn, table_urn, config)) is not None
            and signal.lag_hours <= config.freshness_sla_hours
        ),
        "the reverted table to read as fresh",
    )
    run_scan(
        conn,
        config,
        table_urn=table_urn,
        model_urn=seeded.model,
        run_id=f"history-{uuid.uuid4().hex[:8]}",
        llm=None,
    )

    plant_stale_source(conn, lag_hours=30.0)
    # operation is a timeseries aspect, served from the index rather than the
    # primary store, so the planted lag is awaited rather than assumed. Waiting
    # on the *lag*, never on a finding: waiting for the answer would manufacture
    # it (benchmarks/CLAUDE.md rule 7, which applies just as well here).
    _eventually(
        lambda: (
            (signal := freshness_signal(conn, table_urn, config)) is not None
            and signal.lag_hours > config.freshness_sla_hours
        ),
        "the planted staleness to be readable",
    )
    run_scan(
        conn,
        config,
        table_urn=table_urn,
        model_urn=seeded.model,
        run_id=f"history-{uuid.uuid4().hex[:8]}",
        llm=None,
    )

    history = read_history(conn, seeded.model)
    fresh, stale = history[-2], history[-1]
    assert stale.score < fresh.score, "a stale upstream must cost the model trust"
    assert DEDUCTION_UPSTREAM_FAILURE in stale.deductions

    # Leave the graph fresh, the way the demo and the other suites expect it.
    revert_stale_source(conn)
