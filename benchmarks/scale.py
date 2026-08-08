"""What a whole-catalog sweep costs, measured rather than extrapolated.

``RESULTS.md`` has said "no scale test" since the benchmark landed, and it is the
first question anyone who runs a real catalog asks: ``janus scan
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
reporting the measurement plainly (docs/08-evaluation.md lists what is
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

from janus.agent import pipeline
from janus.agent.pipeline import run_scan
from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.seed import graph_spec as spec

#: Catalog sizes the sweep is measured at. Fixed, like every other constant in
#: this package: a run that cannot be re-derived by reading the file is a run
#: whose numbers nobody can check (docs/08-evaluation.md).
SWEEP_SIZES: tuple[int, ...] = (1, 10, 50)

#: Model id prefix for the replicas. Distinctive enough that anything left behind
#: by an interrupted run is obvious in the UI and greppable in the graph.
REPLICA_PREFIX = "janus_bench_scale_"


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


@dataclass(frozen=True)
class WritePathCost:
    """What the write path costs over the read path, for one scan, one finding.

    The sweep above is dry-run, so it measures the read path and says so. That
    leaves the more expensive half unmeasured, and it is the half a whole-catalog
    `scan --all-models --write` actually pays: reconciliation walks a resource's
    incidents to decide what to clear, and it does that per finding rather than
    per sweep.

    Reported as a ratio and a phase split rather than as a target. There is no
    published number for what reconciliation should cost; what a reader needs is
    that the write path is not the read path plus a few writes, and where the
    difference goes.
    """

    detect_reads: int
    write_reads: int
    reconcile_reads: int
    dry_run_seconds: float
    write_seconds: float

    @property
    def total_write_reads(self) -> int:
        """Every read one write-enabled scan issues."""
        return self.detect_reads + self.write_reads + self.reconcile_reads

    @property
    def amplification(self) -> float:
        """How many times the read path's cost a write-enabled scan pays."""
        return self.total_write_reads / self.detect_reads if self.detect_reads else 0.0

    @property
    def reconcile_share(self) -> float:
        """Reconciliation's share of the write path's reads, as a fraction."""
        total = self.total_write_reads
        return self.reconcile_reads / total if total else 0.0


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
            "replicate. Run janus-seed first."
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
                    description="Janus-Bench scale replica. Deleted when the run ends.",
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


def measure_write_path(conn: DataHubConnection, config: ScanConfig) -> WritePathCost:
    """Measure what a write-enabled scan costs over a dry run, and where it goes.

    Runs the same target twice: once dry, once writing. The write run is
    instrumented per phase, because the interesting number is not that writing
    costs more (it must) but that reconciliation, not the writes themselves,
    dominates it.

    Targets the seeded model, whose target leakage is part of the baseline rather
    than something a trial plants, and must therefore run *after*
    ``restore_baseline``. Both halves of that sentence are load-bearing and both
    were learned by getting them wrong:

    * Placed before the restore, it ran on a graph the counterfactuals had just
      cleared, timed a scan with nothing to write, and published 0 reads for
      write-back as the write path's cost.
    * Retargeted at the table with a freshly planted lag, it measured a resource
      carrying no incident history and reported 0 reads for reconciliation, which
      is the opposite error: the cheapest possible case published as the cost.

    What the number depends on is worth stating, because it is not a constant.
    Reconciliation walks the incidents already attached to the resources a scan
    touches, so its cost grows with the history the graph has accumulated and is
    near zero on a graph seeded a moment ago. The figure here is from a graph a
    full benchmark run has just written to, which is the realistic end of that
    range rather than the flattering one, and the report says so.

    The write is idempotent by construction (writeback keys on
    ``(resource_urn, incident_type, title)``), so this leaves the graph as it
    found it apart from a fresh run_id stamp.
    """
    model_urn = str(spec.model_urn())

    counting = _CountingGraph(conn.graph)
    instrumented = DataHubConnection(
        graph=counting,  # type: ignore[arg-type]
        client=conn.client,
        gms_url=conn.gms_url,
        has_token=conn.has_token,
    )

    started = time.monotonic()
    dry = run_scan(instrumented, config, model_urn=model_urn, llm=None, dry_run=True)
    dry_run_seconds = time.monotonic() - started
    if not dry.findings:
        raise RuntimeError(
            "the seeded model reported no finding, so there is no write path to "
            "measure. This runs after restore_baseline, where the seeded leak is "
            "part of the graph; a clean answer here means the restore did not land."
        )

    phases: dict[str, int] = {}
    original_write_back = pipeline._write_back
    original_reconcile = pipeline._reconcile_stale_findings

    def _counted(name: str, fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            before = counting.reads
            try:
                return fn(*args, **kwargs)
            finally:
                phases[name] = phases.get(name, 0) + (counting.reads - before)

        return wrapper

    pipeline._write_back = _counted("write", original_write_back)
    pipeline._reconcile_stale_findings = _counted("reconcile", original_reconcile)
    try:
        before_write = counting.reads
        started = time.monotonic()
        run_scan(instrumented, config, model_urn=model_urn, llm=None, dry_run=False)
        write_seconds = time.monotonic() - started
        write_total = counting.reads - before_write
    finally:
        pipeline._write_back = original_write_back
        pipeline._reconcile_stale_findings = original_reconcile

    reconcile_reads = phases.get("reconcile", 0)
    write_reads = phases.get("write", 0)
    return WritePathCost(
        # Detection's share is what the write run spent outside the two measured
        # phases, not the dry run's own total: a second run of the same scan
        # re-reads the same graph and the two are equal only by coincidence.
        detect_reads=max(write_total - reconcile_reads - write_reads, 1),
        write_reads=write_reads,
        reconcile_reads=reconcile_reads,
        dry_run_seconds=dry_run_seconds,
        write_seconds=write_seconds,
    )
