"""Guard coverage against a live DataHub (T-15).

Why a unit test cannot cover this: the trend hangs on a ``dataFlow``, and until
this file ran no structured property had ever been assigned to one on a real GMS.
``FakeGraph`` stores whatever it is handed. A server validates the property
definition's ``entityTypes`` against the entity it is being assigned to, and that
rejection is the failure the fake cannot express: the entity type here is the one
thing about this write that is not the same as every other structured-property
write in the package.

The second thing measured is convergence, which is the idempotency contract
applied to a list: rerunning one sweep under its own ``run_id`` must replace its
row rather than append a second.
"""

from __future__ import annotations

import uuid

import pytest

from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.detect.coverage import MODEL_CHECKS, coverage_gaps
from janus.detect.guard_coverage import ModelCoverage, aggregate
from janus.discovery import search_model_urns
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.coverage_history import (
    append_entry,
    read_history,
)

pytestmark = pytest.mark.integration


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
    return seed_ml_graph(conn)


def sweep(conn: DataHubConnection, config: ScanConfig, model_urns: tuple[str, ...]):
    """Run the coverage read over some models, without a full scan each.

    ``coverage_gaps`` is handed no findings, which is what the CLI's dry-run
    scans amount to for a clean model and is the only part of a scan this
    measurement reads. Skipping the detectors keeps a live sweep to seconds.
    """
    return [
        ModelCoverage(
            model_urn=urn,
            gaps=coverage_gaps(conn, config, table_urn=None, model_urn=urn, findings=()),
        )
        for urn in model_urns
    ]


def test_a_dataflow_carries_the_history_a_live_gms_serves_back(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The definition, the assignment and the read, all against a real server."""
    catalog = aggregate(sweep(conn, config, (seeded.model,)))
    run_id = f"coverage-{uuid.uuid4().hex[:8]}"

    append_entry(conn, catalog, run_id)
    history = read_history(conn)

    recorded = [entry for entry in history if entry.run_id == run_id]
    assert len(recorded) == 1
    entry = recorded[0]
    assert entry.models == 1
    assert entry.total == len(MODEL_CHECKS)
    # Read back off the server, not off the object that was written: the per-check
    # halves are the part that goes through the string encoding and back.
    assert tuple(check.check for check in entry.per_check) == MODEL_CHECKS


def test_rerunning_one_sweep_replaces_its_row_rather_than_appending(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Idempotency for a list-valued property: the run id is the key."""
    catalog = aggregate(sweep(conn, config, (seeded.model,)))
    run_id = f"coverage-{uuid.uuid4().hex[:8]}"

    append_entry(conn, catalog, run_id)
    after_first = read_history(conn)
    append_entry(conn, catalog, run_id)
    after_rerun = read_history(conn)

    assert len(after_rerun) == len(after_first)
    assert [entry.run_id for entry in after_rerun].count(run_id) == 1


def test_the_figure_counts_every_model_the_graph_holds_including_hidden_versions(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The denominator comes from discovery, so a hidden version is not omitted.

    A model GMS hides from search is one that silently stops being checked
    (D-100). Leaving it out of the denominator would report coverage over the
    models Janus happens to see rather than over the models that exist,
    which is the flattering error.
    """
    model_urns = search_model_urns(conn)
    assert seeded.model in model_urns

    catalog = aggregate(sweep(conn, config, model_urns))

    assert catalog.models == len(model_urns)
    assert catalog.total_checks == len(model_urns) * len(MODEL_CHECKS)
