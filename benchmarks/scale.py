"""What a whole-catalog sweep costs, measured rather than extrapolated.

``RESULTS.md`` has said "no scale test" since the benchmark landed, and it is the
first question anyone who runs a real catalog asks: ``modelguard scan
--all-models`` performs one independent scan per model, so what does that cost at
two hundred models rather than at one?

The honest answer needs a graph with many models in it, so this builds one. Each
replica is a real ``mlModel`` carrying the seeded model's features and training
run, which means every detector does its full job on it: the leakage walk runs,
the drift diff runs, the coverage checks run. Replicas share one feature table on
purpose. The question is what a *sweep* costs, and duplicating the warehouse side
would measure the seeder instead.

Two numbers per size, and the second is the one that explains the first:

* **Wall clock**, total and per model, which is what a person waits for.
* **Graph reads issued**, counted at the connection, which is what the wall clock
  is made of and what would have to change to move it.

Nothing here is scored against a target. There is no published number for how
fast a metadata sweep should be, and inventing one to pass it would be worse than
reporting the measurement plainly (benchmarks/CLAUDE.md rule 2 lists what is
scored; this is not on it).

Cleaning up
-----------
Replicas are hard-deleted afterwards. A benchmark that left two hundred fake
models in the graph would corrupt every later run of ``inventory``, of the
detection trials, and of the demo a judge looks at.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import MLModelPropertiesClass
from datahub.metadata.urns import MlModelUrn

from modelguard.agent.pipeline import run_scan
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.seed import graph_spec as spec

#: Catalog sizes the sweep is measured at. Fixed, like every other constant in
#: this package: a run that cannot be re-derived by reading the file is a run
#: whose numbers nobody can check (benchmarks/CLAUDE.md rule 1).
SWEEP_SIZES: tuple[int, ...] = (1, 10, 50)

#: Model id prefix for the replicas. Distinctive enough that anything left behind
#: by an interrupted run is obvious in the UI and greppable in the graph.
REPLICA_PREFIX = "modelguard_bench_scale_"


class _CountingGraph:
    """Delegates to a real graph and counts the reads that go through it.

    A proxy rather than a patched method, so it counts what the code under test
    actually called and cannot drift when a detector starts using a different
    read. Anything not named here passes straight through untouched.
    """

    #: The read methods a detector can reach the graph through. Counted by name
    #: rather than by counting every attribute access, because an attribute that
    #: is fetched and not called is not a round trip.
    _COUNTED = frozenset(
        {
            "get_aspect",
            "get_latest_timeseries_value",
            "exists",
            "get_related_entities",
            "execute_graphql",
        }
    )

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.reads = 0

    def __getattr__(self, name: str) -> Any:
        """Return the underlying attribute, wrapped to count it when it is a read."""
        attribute = getattr(self._inner, name)
        if name not in self._COUNTED:
            return attribute

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.reads += 1
            return attribute(*args, **kwargs)

        return counted


@dataclass(frozen=True)
class ScaleMeasurement:
    """What one whole-catalog sweep of a given size cost."""

    models: int
    seconds: float
    graph_reads: int

    @property
    def seconds_per_model(self) -> float:
        """The number that says whether the cost is linear in the catalog."""
        return self.seconds / self.models if self.models else 0.0

    @property
    def reads_per_model(self) -> float:
        """Graph round trips one model costs, which the wall clock is made of."""
        return self.graph_reads / self.models if self.models else 0.0


def _replica_urn(index: int) -> str:
    """Return the URN of one replica model."""
    return str(MlModelUrn(platform=spec.ML_PLATFORM, name=f"{REPLICA_PREFIX}{index:04d}"))


def create_replicas(conn: DataHubConnection, count: int) -> tuple[str, ...]:
    """Emit ``count`` models carrying the seeded model's features and training run.

    Idempotent: the URNs are a function of the index, so re-running converges
    rather than growing the catalog.

    Returns:
        The replica URNs, in order.
    """
    seeded = conn.graph.get_aspect(str(spec.model_urn()), MLModelPropertiesClass)
    if seeded is None:
        raise RuntimeError(
            "the seeded model is not in this DataHub, so there is nothing to "
            "replicate. Run modelguard-seed first."
        )

    urns: list[str] = []
    for index in range(count):
        urn = _replica_urn(index)
        urns.append(urn)
        conn.graph.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=MLModelPropertiesClass(
                    name=f"{REPLICA_PREFIX}{index:04d}",
                    description="ModelGuard-Bench scale replica. Deleted when the run ends.",
                    mlFeatures=list(seeded.mlFeatures or []),
                    trainingJobs=list(seeded.trainingJobs or []),
                    deployments=list(seeded.deployments or []),
                ),
            )
        )
    return tuple(urns)


def remove_replicas(conn: DataHubConnection, urns: Sequence[str]) -> int:
    """Hard-delete the replicas. Returns how many were removed.

    Hard, not soft: a soft-deleted model still answers a search, so ``inventory``
    and the detection trials would keep tripping over two hundred fakes.
    """
    for urn in urns:
        conn.graph.delete_entity(urn, hard=True)
    return len(urns)


def measure_sweep(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urns: Sequence[str],
) -> ScaleMeasurement:
    """Scan every model once, in dry run, and report what it cost.

    Dry run because this measures the read path: the writes are already measured
    for idempotency by ``measure_writeback``, and writing an incident per replica
    would leave a mess that outlives the deletion of the models themselves.
    """
    counting = _CountingGraph(conn.graph)
    # A shallow copy with the graph swapped: the connection is frozen, and the
    # detectors take whatever handle they are given.
    instrumented = DataHubConnection(
        graph=counting,  # type: ignore[arg-type]
        client=conn.client,
        gms_url=conn.gms_url,
        has_token=conn.has_token,
    )

    started = time.monotonic()
    for model_urn in model_urns:
        run_scan(instrumented, config, model_urn=model_urn, llm=None, dry_run=True)
    elapsed = time.monotonic() - started

    return ScaleMeasurement(models=len(model_urns), seconds=elapsed, graph_reads=counting.reads)


def measure_scale(
    conn: DataHubConnection,
    config: ScanConfig,
    sizes: Sequence[int] = SWEEP_SIZES,
) -> tuple[ScaleMeasurement, ...]:
    """Measure a whole-catalog sweep at each size, then clean up.

    The replicas for the largest size are created once and the smaller sweeps
    scan a prefix of them, so the measurements differ in how many models were
    scanned and in nothing else.

    Args:
        conn: An open connection to a seeded graph.
        config: The same config the detection trials run with.
        sizes: Catalog sizes to measure, smallest first.

    Returns:
        One measurement per size.
    """
    largest = max(sizes)
    urns = create_replicas(conn, largest)
    try:
        return tuple(measure_sweep(conn, config, urns[:size]) for size in sorted(sizes))
    finally:
        # A failed measurement must not leave the catalog full of fakes.
        remove_replicas(conn, urns)
