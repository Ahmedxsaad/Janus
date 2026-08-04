"""T-09's confusable negatives, against a live DataHub.

09 section 2.2: precision of 1.00 is close to vacuous while the negative trials
are absent positives rather than hard negatives. Three things here can only be
checked against a real GMS: whether an upstream-only walk genuinely never
confuses a shared ancestor for a derivation, whether the diamond's shortest
chain stays the same answer across repeated walks (DataHub answers a walk past
two hops from a full-graph search, in network order), and whether the hop cap's
own coverage gap actually names the knob that would raise it, against the real
lineage response shape the unit tests fake.

Reads are asynchronous, so every assertion polls.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar

import pytest

from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.coverage import coverage_gaps
from modelguard.detect.leakage import leakage_findings
from modelguard.seed.scenarios import (
    plant_common_ancestor_label,
    plant_label_lookalike,
    plant_second_leak_path,
    revert_common_ancestor_label,
    revert_label_lookalike,
    revert_second_leak_path,
)
from modelguard.seed.seed_ml_graph import SeedResult, seed_ml_graph

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
def seeded(conn: DataHubConnection) -> SeedResult:
    return seed_ml_graph(conn)


def test_a_feature_and_a_label_sharing_an_ancestor_is_not_flagged(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """applicant_income and a sibling label both derive from income.

    Neither descends from the other, so a walk that is genuinely upstream-only
    must never reach the sibling from applicant_income's own cone.
    """
    plant_common_ancestor_label(conn)
    try:
        _eventually(
            lambda: bool(
                conn.client.lineage.get_lineage(
                    source_urn=str(seeded.feature_table_dataset),
                    source_column="income_verified_label",
                    direction="upstream",
                    max_hops=1,
                )
            ),
            "the sibling label's own lineage to income to be indexed",
        )
        findings = leakage_findings(conn, seeded.model, config)
        flagged = {finding.leak.source_column_name for finding in findings}
        assert "applicant_income" not in flagged
    finally:
        revert_common_ancestor_label(conn)


def test_a_column_named_like_a_label_with_no_term_is_not_flagged(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """Only the upstream column's name changed from the clean baseline."""
    plant_label_lookalike(conn)
    try:
        _eventually(
            lambda: any(
                step.column_name == "target_indicator"
                for result in conn.client.lineage.get_lineage(
                    source_urn=str(seeded.feature_table_dataset),
                    source_column="applicant_income",
                    direction="upstream",
                    max_hops=1,
                )
                for step in (result.paths or [])
            ),
            "applicant_income's derivation from target_indicator to be indexed",
        )
        findings = leakage_findings(conn, seeded.model, config)
        flagged = {finding.leak.source_column_name for finding in findings}
        assert "applicant_income" not in flagged
    finally:
        revert_label_lookalike(conn)


def test_the_diamonds_shortest_chain_is_stable_across_repeated_walks(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The same proof, asked for five times in a row.

    Above two hops DataHub answers a lineage query from a full-graph search in
    network order (column_marks.py's module docstring), so this is the one
    place non-determinism in that order could actually surface: WalkResult.hit
    breaks ties on more than length, but only a live GMS can disagree with
    itself between two calls.
    """
    plant_second_leak_path(conn)
    try:
        _eventually(
            lambda: len(leakage_findings(conn, seeded.model, config)) == 1,
            "both label derivations to be indexed",
        )
        chains = [
            finding.leak.column_path
            for _ in range(5)
            for finding in leakage_findings(conn, seeded.model, config)
        ]
        assert len(chains) == 5
        assert len(set(chains)) == 1, f"the quoted chain moved between calls: {set(chains)}"
    finally:
        revert_second_leak_path(conn)


def test_a_leak_beyond_the_hop_cap_is_reported_as_a_gap_naming_the_hop_cap(
    conn: DataHubConnection, seeded: SeedResult, config: ScanConfig
) -> None:
    """The seeded leak sits one hop away; a cap of zero puts it out of reach.

    Fixture-based unit tests fake GMS returning the hop-capped result at all;
    this is the one place that actually asks a live GMS for it, over the real
    LineageResult shape column_marks.py reads.
    """
    capped = replace(config, leakage_max_hops=0)
    findings = leakage_findings(conn, seeded.model, capped)
    assert findings == (), "the leak must be out of reach, not merely unproven"

    gaps = coverage_gaps(
        conn, capped, table_urn=None, model_urn=str(seeded.model), findings=findings
    )
    described = [gap.describe() for gap in gaps]
    line = next((line for line in described if "target leakage" in line), None)
    assert line is not None, f"expected a target-leakage gap, got: {described}"
    assert "MODELGUARD_LEAKAGE_MAX_HOPS" in line
