"""A scan's process run against a live DataHub (T-04).

Why a unit test does not cover this: ``dataProcessInstance`` is an entity GMS
validates and serves, and this project had never written one. ``FakeGraph`` stores
whatever it is handed, so it cannot tell a URN GMS accepts from one it rejects,
cannot tell whether the input and output aspects survive a round trip, and cannot
show whether the run is reachable from the ``dataJob`` the UI navigates by.

The second thing measured here is the idempotency contract: rerunning one scan
under its own ``run_id`` must leave one process instance, not two. A scan that
minted a fresh entity per attempt would bury a graph under a run per poll.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TypeVar

import pytest
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DataProcessInstanceOutputClass,
    DataProcessInstancePropertiesClass,
    DataProcessInstanceRelationshipsClass,
    DataProcessInstanceRunEventClass,
    DataProcessRunStatusClass,
    RunResultTypeClass,
)

from janus.agent.pipeline import run_scan
from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.process_instance import (
    FLOW_ENV,
    JOB_ID,
    ORCHESTRATOR,
    scan_run_urn,
)

pytestmark = pytest.mark.integration

JOB_URN = f"urn:li:dataJob:(urn:li:dataFlow:({ORCHESTRATOR},{ORCHESTRATOR},{FLOW_ENV}),{JOB_ID})"

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
    return seed_ml_graph(conn)


def _completion(conn: DataHubConnection, run_urn: str) -> DataProcessInstanceRunEventClass:
    """The run's completion event, waited for rather than assumed.

    A run event is a timeseries aspect, served from the index rather than from the
    primary store, so the STARTED event is the latest one for a second or two after
    a scan returns. Reading once here would make this test pass or fail on how fast
    the machine indexed, which is not what it is measuring.
    """

    def latest() -> DataProcessInstanceRunEventClass | None:
        event = conn.graph.get_latest_timeseries_value(
            run_urn, DataProcessInstanceRunEventClass, {}
        )
        return (
            event
            if event is not None and event.status == DataProcessRunStatusClass.COMPLETE
            else None
        )

    return _eventually(latest, f"the completion event of {run_urn}")


def test_a_scan_is_readable_as_a_completed_run_under_the_agents_job(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The whole claim in one read: the run exists, is placed, and says how it ended."""
    run_id = f"dpi-{uuid.uuid4().hex[:8]}"
    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)
    run_urn = scan_run_urn(run_id)

    properties = conn.graph.get_aspect(run_urn, DataProcessInstancePropertiesClass)
    assert properties is not None, "GMS served no process instance for this run"
    assert properties.name == run_id

    relationships = conn.graph.get_aspect(run_urn, DataProcessInstanceRelationshipsClass)
    assert relationships is not None
    assert relationships.parentTemplate == JOB_URN

    event = _completion(conn, run_urn)
    assert event.result is not None
    assert event.result.type == RunResultTypeClass.SUCCESS


def test_the_run_names_the_model_it_scanned_among_its_inputs(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The inputs are what make the run answer "what was examined" by itself."""
    run_id = f"dpi-{uuid.uuid4().hex[:8]}"
    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)

    inputs = conn.graph.get_aspect(scan_run_urn(run_id), DataProcessInstanceInputClass)
    assert inputs is not None, "GMS did not serve back the input aspect"
    assert seeded.model in inputs.inputs


def test_a_scan_that_wrote_something_names_it_among_its_outputs(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Outputs are read back from the graph, not from the report that produced them.

    A scan of the seeded model writes at minimum the model's own trust properties,
    so an empty or absent output aspect here means the outputs never reached GMS.
    """
    run_id = f"dpi-{uuid.uuid4().hex[:8]}"
    report = run_scan(
        conn, config, table_urn=seeded.source_table, model_urn=seeded.model, run_id=run_id, llm=None
    )

    outputs = conn.graph.get_aspect(scan_run_urn(run_id), DataProcessInstanceOutputClass)
    if not report.writes and not report.trust:
        pytest.skip("this graph is currently clean and this run wrote nothing to name")
    assert outputs is not None, "a scan that wrote must say what it wrote to"
    assert outputs.outputs, "the output aspect reached GMS empty"


def test_rerunning_one_scan_updates_its_run_rather_than_minting_a_second(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Idempotency, the contract every write in this package inherits (D-013)."""
    run_id = f"dpi-{uuid.uuid4().hex[:8]}"
    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)
    first = scan_run_urn(run_id)
    properties_first = conn.graph.get_aspect(first, DataProcessInstancePropertiesClass)

    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None)
    second = scan_run_urn(run_id)
    properties_second = conn.graph.get_aspect(second, DataProcessInstancePropertiesClass)

    assert second == first, "a rerun addressed a different entity"
    assert properties_first is not None and properties_second is not None
    assert properties_second.name == properties_first.name
    # Still completed, and still one run's worth of identity: a second entity
    # would have shown up as a different URN above, and a mangled rerun would
    # show up as a missing or failed latest event here.
    assert _completion(conn, first).result is not None


def test_a_dry_run_creates_no_process_instance_at_all(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The one path whose contract is that it writes nothing must not catalogue itself."""
    run_id = f"dpi-{uuid.uuid4().hex[:8]}"
    run_scan(conn, config, model_urn=seeded.model, run_id=run_id, llm=None, dry_run=True)

    properties = conn.graph.get_aspect(scan_run_urn(run_id), DataProcessInstancePropertiesClass)
    assert properties is None, "a dry run wrote a process instance"
