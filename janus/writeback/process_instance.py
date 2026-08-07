"""Janus's own scans, emitted into the graph as process runs.

Every other module in this package writes *about* somebody else's data. This one
writes about Janus: each scan becomes a ``dataProcessInstance`` under a
``dataJob`` under a ``dataFlow``, with the entities it read as inputs and the
entities it wrote as outputs, and a run event carrying whether it finished.

Why this is worth an entity rather than a log line
--------------------------------------------------
The product's thesis is that a pipeline nobody catalogued is a pipeline nobody
can reason about. An agent that writes incidents into a graph while remaining
invisible in that same graph is exempting itself from its own argument. Emitting
the run closes three concrete gaps:

- An incident stamped ``Raised by Janus run scan-abc123`` currently sends a
  reader to grep a log. With the run in the graph, the ``run_id`` is an entity
  they can open, whose inputs say what was examined and whose outputs say what
  was touched.
- A scan that dies halfway leaves the graph half-written and says nothing.
  A failed run event is the record that something was attempted and did not
  finish, which is the difference between "clean" and "never completed".
- Janus's own freshness becomes checkable by Janus, using the same
  ``operation``-shaped reasoning it applies to a warehouse table.

No new dependency and no second system: ``dataFlow``, ``dataJob`` and
``dataProcessInstance`` are shipped DataHub entities, and the high-level helpers
in ``datahub.api.entities`` build the URNs and the properties aspects.

What is hand-rolled, and why
----------------------------
The helper's ``emit_process_start`` / ``emit_process_end`` call ``emitter.emit``
and would also emit an unnamed template flow and job derived from the URN. This
module instead takes the helper's generated MCPs and sends them through
``conn.graph.emit_mcp``, like the other seven emit sites in this package, so the
flow and the job carry a real name and description.

The two run events are built here rather than by the helper for one reason: the
helper cannot set ``messageId``. A run event is a timeseries aspect (writeback
rule 8), so emitting one appends. Deriving the message id from the ``run_id`` and
the phase makes a replay of the same run at the same instant converge on the same
event instead of stacking a second copy, which is as close to idempotent as a
timeseries aspect gets. The entity itself is exactly idempotent: its URN is a
guid over the ``run_id``, so a rerun updates one process instance and never
creates a second.

What a run's inputs and outputs may name
----------------------------------------
Only ``dataset`` and ``mlModel``. That is DataHub's model, not a choice made here,
and a live GMS answers 422 for anything else `[verified]`. A scan touches columns,
incidents, assertions and documents too, so :func:`_iolet` maps a column to its
parent dataset and drops the rest from the aspect. Nothing becomes unreachable:
every one of those hangs off a dataset or a model the run does name, and every one
carries the ``run_id`` that leads back here.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from datahub.api.entities.datajob.dataflow import DataFlow
from datahub.api.entities.datajob.datajob import DataJob
from datahub.api.entities.dataprocess.dataprocess_instance import DataProcessInstance
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DataProcessInstanceOutputClass,
    DataProcessInstanceRunEventClass,
    DataProcessInstanceRunResultClass,
    DataProcessRunStatusClass,
    DataProcessTypeClass,
    GlobalTagsClass,
    OwnershipClass,
    RunResultTypeClass,
)
from datahub.metadata.urns import SchemaFieldUrn

from janus.client import DataHubConnection

#: The platform every Janus flow, job and run hangs off. It names this
#: project, not the customer's warehouse, so it is a constant here rather than
#: configuration: a scan that emitted its runs under a name the operator chose
#: would make two deployments' histories unmergeable for no gain.
ORCHESTRATOR = "janus"

#: The one flow. Janus has a single pipeline, so the flow id is the
#: product name and the job id says which of its entry points ran.
FLOW_ID = "janus"
JOB_ID = "scan"

#: The fabric the flow's URN is minted in. DataHub requires one, and the agent
#: guards whatever graph it is pointed at; PROD is the fabric a catalog someone
#: relies on lives in, and picking per-deployment would split one agent's history
#: across several flows.
FLOW_ENV = "PROD"

FLOW_NAME = "Janus"
FLOW_DESCRIPTION = (
    "The Janus reliability agent. It reads column-level and ML lineage to find "
    "silent data-to-model failures, and writes incidents, trust scores, impact reports "
    "and guarding assertions back into this graph. Every run below is one scan."
)

JOB_NAME = "scan"
JOB_DESCRIPTION = (
    "One detect-explain-write pass over a table, a model, or both. Inputs are the "
    "entities the scan resolved out of the graph; outputs are the entities it wrote to."
)

#: Ad hoc rather than scheduled: nothing in this library schedules a scan. What
#: wakes one up is the caller's business (a CLI invocation, a CI job, the poll
#: loop in `janus watch`), and claiming a schedule the process does not keep
#: would be a fact about Janus that Janus made up.
RUN_TYPE = DataProcessTypeClass.BATCH_AD_HOC


def _flow() -> DataFlow:
    """The single dataFlow every Janus run belongs to."""
    return DataFlow(
        orchestrator=ORCHESTRATOR,
        id=FLOW_ID,
        env=FLOW_ENV,
        name=FLOW_NAME,
        description=FLOW_DESCRIPTION,
    )


def _job(flow: DataFlow) -> DataJob:
    """The dataJob a scan is an instance of."""
    return DataJob(
        id=JOB_ID,
        flow_urn=flow.urn,
        name=JOB_NAME,
        description=JOB_DESCRIPTION,
    )


@dataclass
class ScanRun:
    """One scan, and the entities it turns out to have touched.

    Mutable on purpose, and the only mutable type in this package. The inputs and
    outputs of a run are not knowable when the run starts, which is precisely when
    the STARTED event has to be emitted; a caller therefore records them as it
    goes and the completion writes what accumulated. Deriving them afterwards from
    the finished report instead would miss every write a recovery-only scan makes,
    which is the scan whose outputs are most worth having.
    """

    run_id: str
    urn: str
    started_at_ms: int
    inputs: list[str] = field(default_factory=list)
    """Entities the scan resolved out of the graph."""
    outputs: list[str] = field(default_factory=list)
    """Entities the scan wrote to."""

    def read(self, *urns: str | None) -> None:
        """Record entities this scan read. ``None`` and duplicates are ignored."""
        self.inputs.extend(urn for urn in urns if urn and urn not in self.inputs)

    def wrote(self, *urns: str | None) -> None:
        """Record entities this scan wrote to. ``None`` and duplicates are ignored."""
        self.outputs.extend(urn for urn in urns if urn and urn not in self.outputs)


def _emit_template(conn: DataHubConnection, mcps: Iterable[MetadataChangeProposalWrapper]) -> None:
    """Emit a flow's or a job's aspects, minus the empty ones the helper always adds.

    ``DataFlow.generate_mcp`` and ``DataJob.generate_mcp`` yield ``globalTags`` and
    ``ownership`` whether or not any were set, and both aspects are whole-list
    upserts (writeback rule 9). Sending the empty pair on every scan would strip a
    tag or an owner somebody had put on Janus's own flow, every few seconds
    under ``janus watch``. This module sets neither, so it emits neither.
    """
    for mcp in mcps:
        if isinstance(mcp.aspect, GlobalTagsClass | OwnershipClass):
            continue
        conn.graph.emit_mcp(mcp)


def _instance(run_id: str, job: DataJob) -> DataProcessInstance:
    """Build the process instance for one scan.

    The URN is a guid over ``(cluster, orchestrator, id)`` and ``id`` is the
    ``run_id``, so the same run always lands on the same entity.
    """
    return DataProcessInstance(
        id=run_id,
        orchestrator=ORCHESTRATOR,
        cluster=FLOW_ENV,
        type=RUN_TYPE,
        template_urn=job.urn,
    )


def _iolet(urn: str) -> str | None:
    """Map one touched entity to the asset a run's iolets are allowed to name.

    ``dataProcessInstanceInput.inputs`` and ``.outputs`` accept ``dataset`` and
    ``mlModel`` only. That is not a convention, it is the relationship annotation
    in DataHub's own model `[verified]`, and a live GMS answers 422 naming the
    offending path for anything else, which is how this was found.

    A column is therefore reported as its parent dataset: the run did touch that
    dataset, and reporting it is strictly better than dropping the fact. Anything
    else a scan writes (an incident, an assertion, a knowledge document, a
    glossary term) is left out of the aspect. Nothing is lost by that: each of
    those hangs off a dataset or a model that *is* named here, and each carries
    the ``run_id`` that leads back to this run.
    """
    if urn.startswith("urn:li:dataset:") or urn.startswith("urn:li:mlModel:"):
        return urn
    if urn.startswith("urn:li:schemaField:"):
        return str(SchemaFieldUrn.from_string(urn).parent)
    return None


def _iolets(urns: Sequence[str]) -> list[str]:
    """The sorted, deduplicated assets these touched entities resolve to."""
    return sorted({mapped for urn in urns if (mapped := _iolet(urn)) is not None})


def agent_flow_urn() -> str:
    """The URN of Janus's own dataFlow, without emitting anything.

    Derived from the same constants :func:`_flow` builds the entity from, so a
    caller hanging a catalog-level fact off the agent (the guard-coverage trend,
    addresses the entity a scan run already created rather than a second
    one that happens to look like it.
    """
    return str(_flow().urn)


def scan_run_urn(run_id: str) -> str:
    """The URN the run with this id occupies, without emitting anything.

    Derived, not looked up: the id is a guid over the run id, so anybody holding
    the ``run_id`` stamped on an incident can address that incident's run.
    """
    return str(_instance(run_id, _job(_flow())).urn)


def _run_event(
    run: ScanRun,
    *,
    timestamp_ms: int,
    status: str,
    phase: str,
    result: DataProcessInstanceRunResultClass | None = None,
    duration_ms: int | None = None,
) -> MetadataChangeProposalWrapper:
    """One run event, with a message id derived from the run and the phase.

    ``messageId`` is what a timeseries store keys a document on alongside the
    timestamp, so a replay of the same run at the same instant overwrites its own
    event rather than appending a second one.
    """
    return MetadataChangeProposalWrapper(
        entityUrn=run.urn,
        aspect=DataProcessInstanceRunEventClass(
            timestampMillis=timestamp_ms,
            status=status,
            messageId=f"{run.run_id}-{phase}",
            result=result,
            durationMillis=duration_ms,
        ),
    )


def start_scan_run(
    conn: DataHubConnection,
    run_id: str,
    *,
    started_at_ms: int,
    table_urn: str | None = None,
    model_urn: str | None = None,
) -> ScanRun:
    """Declare a scan as started, and return the handle to record against.

    Emits the flow, the job, the process instance's properties and relationships,
    and a STARTED run event. All four are versioned aspects except the event, so a
    rerun converges rather than duplicating.

    The scan's targets are recorded as inputs immediately: they are the only
    entities known to have been read before a single detector has run, and a scan
    that dies during detection should still say what it was pointed at.

    Args:
        conn: An open connection.
        run_id: This scan's identifier, which is also the instance's id.
        started_at_ms: When the scan began.
        table_urn: The dataset this scan targets, if any.
        model_urn: The model this scan targets, if any.

    Returns:
        The handle. Pass it to :func:`complete_scan_run` when the scan ends.
    """
    flow = _flow()
    job = _job(flow)
    instance = _instance(run_id, job)

    _emit_template(conn, flow.generate_mcp())
    # generate_lineage=False: the job's own inlets and outlets are a property of
    # each run, not of the job, and emitting the empty aspect here would overwrite
    # nothing useful while claiming the scan job reads and writes nothing.
    _emit_template(conn, job.generate_mcp(generate_lineage=False, materialize_iolets=False))
    # materialize_iolets=False: the iolets already exist in this graph, since a
    # scan only ever names entities it read out of it. Materializing would emit a
    # key aspect per entity, which on a mistyped URN would conjure the very stub
    # entity the detectors are supposed to notice the absence of.
    for mcp in instance.generate_mcp(created_ts_millis=started_at_ms, materialize_iolets=False):
        conn.graph.emit_mcp(mcp)

    run = ScanRun(run_id=run_id, urn=str(instance.urn), started_at_ms=started_at_ms)
    run.read(table_urn, model_urn)
    conn.graph.emit_mcp(
        _run_event(
            run,
            timestamp_ms=started_at_ms,
            status=DataProcessRunStatusClass.STARTED,
            phase="started",
        )
    )
    return run


def complete_scan_run(
    conn: DataHubConnection,
    run: ScanRun,
    *,
    ended_at_ms: int,
    failed: bool = False,
) -> None:
    """Close a scan out: write what it touched, then how it ended.

    Inputs and outputs are emitted before the completion event so that a reader
    who sees COMPLETE is looking at an instance whose iolets are already there.
    Both are narrowed by :func:`_iolets` to what DataHub's model accepts.

    A failed scan writes a FAILURE result rather than nothing. Silence and success
    are indistinguishable to anyone reading the graph, and a scan that crashed
    mid-write is exactly the case where the difference matters.

    Args:
        conn: An open connection.
        run: The handle :func:`start_scan_run` returned.
        ended_at_ms: When the scan finished, successfully or not.
        failed: Whether the scan raised instead of completing its work.
    """
    inputs = _iolets(run.inputs)
    outputs = _iolets(run.outputs)
    if inputs:
        conn.graph.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=run.urn,
                aspect=DataProcessInstanceInputClass(inputs=inputs),
            )
        )
    if outputs:
        conn.graph.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=run.urn,
                aspect=DataProcessInstanceOutputClass(outputs=outputs),
            )
        )

    result_type = RunResultTypeClass.FAILURE if failed else RunResultTypeClass.SUCCESS
    conn.graph.emit_mcp(
        _run_event(
            run,
            timestamp_ms=ended_at_ms,
            status=DataProcessRunStatusClass.COMPLETE,
            phase="complete",
            result=DataProcessInstanceRunResultClass(
                type=result_type,
                # The system the verdict came from. Not the orchestrator string
                # the helper would default to: the result is Janus's own
                # scan outcome, and naming the job is what tells a reader which
                # of the agent's entry points produced it.
                nativeResultType=f"{ORCHESTRATOR}-{JOB_ID}",
            ),
            duration_ms=max(ended_at_ms - run.started_at_ms, 0),
        )
    )


@contextmanager
def scan_run(
    conn: DataHubConnection,
    run_id: str,
    *,
    started_at_ms: int,
    dry_run: bool,
    table_urn: str | None = None,
    model_urn: str | None = None,
) -> Iterator[ScanRun]:
    """Wrap a scan so that however it ends, the graph is told.

    On a dry run nothing is emitted at all, and the yielded handle is inert: a
    preview that announced itself as a completed run in the graph would be a write
    performed by the one code path whose contract is that it writes nothing.

    An exception propagating out of the body writes a FAILURE run event and is
    then re-raised unchanged. Swallowing it would turn a crashed scan into a
    successful-looking one for the caller, which is the failure this exists to
    make visible, moved rather than fixed.
    """
    if dry_run:
        yield ScanRun(run_id=run_id, urn="", started_at_ms=started_at_ms)
        return

    run = start_scan_run(
        conn,
        run_id,
        started_at_ms=started_at_ms,
        table_urn=table_urn,
        model_urn=model_urn,
    )
    try:
        yield run
    except BaseException:
        complete_scan_run(conn, run, ended_at_ms=_now_ms(), failed=True)
        raise
    complete_scan_run(conn, run, ended_at_ms=_now_ms())


def _now_ms() -> int:
    """Return the wall clock in milliseconds.

    A run event records an instant, not an elapsed duration, so this is the clock
    and not ``perf_counter``.
    """
    return int(time.time() * 1000)
