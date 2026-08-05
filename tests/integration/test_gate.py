"""The gate against a live graph: does it block, clear, and stay silent?

The offline tests in ``tests/test_gate.py`` prove the policy arithmetic. These
prove the two claims that only a real DataHub can settle: that the verdict tracks
the actual state of the graph, and that a gate run leaves no trace in it.

The second is the one worth a live test. "Writes nothing" is a promise about a
side effect, and a side effect is exactly what a fake cannot vouch for: a stubbed
graph would happily report no writes while the real one filled up with incidents
about branches that never merged.
"""

from __future__ import annotations

import time

import pytest

from janus.agent.pipeline import run_scan
from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.gate import GatePolicy, evaluate
from janus.models import Severity
from janus.seed import graph_spec as spec
from janus.seed.scenarios import plant_leakage, revert_leakage
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.incidents import attached_incident_urns
from janus.writeback.properties import read_properties

pytestmark = pytest.mark.integration

#: Lineage reaches the detector through an index, so a planted change needs a
#: moment before a scan agrees with it (D-048).
_INDEX_SETTLE_SECONDS = 10.0
_POLL_SECONDS = 1.0


@pytest.fixture(scope="module")
def conn() -> DataHubConnection:
    """Connect to the local DataHub, or skip the whole module."""
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection) -> SeedResult:
    return seed_ml_graph(conn)


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    return ScanConfig.from_env()


def _gate(conn: DataHubConnection, config: ScanConfig, policy: GatePolicy) -> int:
    """Run the gate's own code path and return the exit code it would give.

    Calls ``run_scan`` in dry-run and ``evaluate``, which is exactly what the CLI
    command does either side of its printing, so this tests the shipped decision
    rather than a paraphrase of it.
    """
    report = run_scan(conn, config, model_urn=str(spec.model_urn()), llm=None, dry_run=True)
    return evaluate(report, policy).exit_code


def _await_leakage(conn: DataHubConnection, config: ScanConfig, *, expected: bool) -> None:
    """Wait for the index to show the leakage state just planted."""
    from janus.detect.leakage import leakage_findings

    deadline = time.monotonic() + _INDEX_SETTLE_SECONDS
    while time.monotonic() < deadline:
        if bool(leakage_findings(conn, str(spec.model_urn()), config)) == expected:
            return
        time.sleep(_POLL_SECONDS)
    pytest.fail(f"the graph never reached leaking={expected}")


def test_a_leaking_model_blocks_the_build(conn: DataHubConnection, seeded: SeedResult, config):
    plant_leakage(conn)
    _await_leakage(conn, config, expected=True)

    assert _gate(conn, config, GatePolicy(block_at_or_above=Severity.HIGH)) == 1


def test_fixing_the_leak_clears_the_gate(conn: DataHubConnection, seeded: SeedResult, config):
    """The gate must track the graph, not a cached verdict about it."""
    revert_leakage(conn)
    _await_leakage(conn, config, expected=False)
    try:
        assert _gate(conn, config, GatePolicy(block_at_or_above=Severity.HIGH)) == 0
    finally:
        # Leave the seeded (leaking) baseline behind whatever this assertion did.
        plant_leakage(conn)


def test_a_gate_run_writes_nothing_to_the_graph(
    conn: DataHubConnection, seeded: SeedResult, config
):
    """The promise the whole design rests on, checked against a real DataHub.

    A gate runs on every push to every branch, most of which never merge. If it
    wrote, the governance graph would fill with findings about code that does not
    exist, and no amount of write-back idempotency would help: those are genuinely
    different runs, not repeats of one.
    """
    plant_leakage(conn)
    _await_leakage(conn, config, expected=True)

    column = str(spec.feature_column_urn(spec.LEAKAGE_FEATURE))
    model = str(spec.model_urn())
    incidents_before = len(attached_incident_urns(conn, column))
    properties_before = dict(read_properties(conn, model))

    # More than one, because a single run could coincidentally write nothing new
    # while a repeated one appends.
    for _ in range(3):
        assert _gate(conn, config, GatePolicy(block_at_or_above=Severity.HIGH)) == 1

    assert len(attached_incident_urns(conn, column)) == incidents_before, (
        "the gate raised an incident; it must read only"
    )
    assert dict(read_properties(conn, model)) == properties_before, (
        "the gate changed a structured property; it must read only"
    )
