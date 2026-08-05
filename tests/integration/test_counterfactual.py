"""A counterfactual, applied to a live graph, and the finding it has to clear.

A remedy printed in an incident is a claim about what would happen if somebody
did it. Offline tests can only check that the sentence is well formed. This
module does the thing the sentence says, against a real DataHub, and asks the
same detector again (T-03).

Two states are exercised, and the second is the one worth having. A feature
reaching a declared label by one derivation is cleared by cutting it, which is
close to a construction proof: the remedy undoes the plant. A feature reaching a
label by *two* derivations is not cleared by cutting the one the incident quoted,
and that is the mistake a careful person makes: they read the proof, they cut the
path in it, and they believe they are done. The counterfactual has to name both
edges, and the finding has to stand until both are gone.

Reads are asynchronous. DataHub indexes lineage through Kafka and Elasticsearch,
so every assertion polls rather than assuming read-after-write consistency.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import TypeVar

import pytest

from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.detect.leakage import leakage_findings
from janus.models import LeakageFinding, RemedyKind
from janus.seed import graph_spec as spec
from janus.seed.scenarios import (
    BACKUP_LABEL_COLUMN,
    plant_leakage,
    plant_second_leak_path,
    revert_leakage,
    revert_second_leak_path,
)
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph

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
    """Connect to the local DataHub, or skip the whole module."""
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    return ScanConfig()


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection, config: ScanConfig) -> SeedResult:
    result = seed_ml_graph(conn)
    _eventually(
        lambda: bool(leakage_findings(conn, result.model, config)),
        "the seeded leak to be indexed and detectable",
    )
    return result


@pytest.fixture(autouse=True)
def _restore(conn: DataHubConnection, seeded: SeedResult) -> Iterator[None]:
    """Leave the graph the way the demo expects to find it, whatever the test did."""
    yield
    revert_second_leak_path(conn)
    plant_leakage(conn)


def _leak(conn: DataHubConnection, config: ScanConfig, model_urn: str) -> LeakageFinding:
    findings = _eventually(
        lambda: leakage_findings(conn, model_urn, config),
        "a leakage finding to remedy",
    )
    return findings[0]


def test_cutting_the_quoted_derivation_clears_a_single_path_leak(
    conn: DataHubConnection, config: ScanConfig, seeded: SeedResult
) -> None:
    """The remedy the incident printed, performed, and the detector asked again."""
    finding = _leak(conn, config, seeded.model)
    cut = next(r for r in finding.counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE)
    assert cut.targets == ("prior_default_flag <- default_status",)

    revert_leakage(conn)

    cleared = _eventually(
        lambda: not leakage_findings(conn, seeded.model, config),
        "the leak to clear once its only derivation was cut",
    )
    assert cleared


def test_cutting_one_derivation_of_two_does_not_clear_the_finding(
    conn: DataHubConnection, config: ScanConfig, seeded: SeedResult
) -> None:
    """The measurement this scenario exists for.

    Both derivations are planted, the counterfactual is read off the finding, the
    path the incident quoted is cut, and the finding must still be there. Then
    both are cut and it must go: without that second half, a detector that could
    not be silenced at all would pass the first half identically.
    """
    plant_second_leak_path(conn)
    finding = _eventually(
        lambda: next(
            (f for f in leakage_findings(conn, seeded.model, config) if f.counterfactual.paths > 1),
            None,
        ),
        "both derivations to be indexed",
    )

    cut = next(r for r in finding.counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE)
    assert cut.targets == (
        f"{spec.LEAKAGE_FEATURE} <- {spec.LABEL_SOURCE_COLUMN}",
        f"{spec.LEAKAGE_FEATURE} <- {BACKUP_LABEL_COLUMN.name}",
    )
    assert finding.leak.column_path == (spec.LEAKAGE_FEATURE, spec.LABEL_SOURCE_COLUMN)

    # Half the fix: the quoted path goes, the second one stays.
    plant_second_leak_path(conn, keep_first=False)
    _eventually(
        lambda: [
            f
            for f in leakage_findings(conn, seeded.model, config)
            if f.leak.column_path == (spec.LEAKAGE_FEATURE, BACKUP_LABEL_COLUMN.name)
        ],
        "the finding to stand on the derivation that was left",
    )

    # The whole fix, which is what the counterfactual actually asked for.
    revert_second_leak_path(conn)
    revert_leakage(conn)
    assert _eventually(
        lambda: not leakage_findings(conn, seeded.model, config),
        "the leak to clear once every derivation was cut",
    )
