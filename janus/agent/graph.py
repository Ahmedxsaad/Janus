"""The scan loop as a LangGraph state machine, with a real human-approval gate.

:mod:`~janus.agent.pipeline` runs the node order ``detect -> reason ->
[approval] -> write_back`` as plain function calls, with ``--dry-run`` standing in
for the approval gate: it is all-or-nothing, detect-and-explain **or** write
everything. This module runs the identical order as a compiled ``StateGraph`` and
replaces that stand-in with LangGraph's :func:`~langgraph.types.interrupt`: the
graph pauses after reasoning, hands the caller a preview of every finding, and
writes back only what the caller approves, in one run.

Nothing about detection or write-back changes. Every node here delegates to the
same deterministic functions the pipeline uses (``_detect``, ``_write_back``,
``_persist_trust``, ``_trust_scores``) and to the same narrator, so a finding is
detected, explained, and written back byte-for-byte the way ``scan`` already does
it (docs/02-architecture.md). The graph adds orchestration, not capability: the
approval interrupt and a process-local checkpointer for the synchronous approval
exchange. Cross-process resume requires a durable run store outside this CLI path.

The LLM still runs only in the reason node, and it still never decides whether a
finding exists (the design law). ``interrupt()`` gates every mutation: nothing in
the write node runs until the caller resumes with an approval.

Why the findings ride outside the graph state
----------------------------------------------
LangGraph checkpoints its control-flow state so a run can pause and resume within
one call. The checkpointer
serializes whatever the state holds, and it does not know Janus's finding and
report dataclasses, so carrying them in the state serializes them on every hop and
warns that unregistered types will be blocked in a future release. The state
therefore carries only one boolean (whether the caller approved the writes); the
findings, narratives, and reports live in an in-process holder the node closures
share. That is sound precisely because the checkpointer is the
in-memory :class:`~langgraph.checkpoint.memory.MemorySaver`: a paused run resumes
within the same ``run_agent`` call, in the same process, so the holder is always
there. A durable checkpointer is intentionally out of scope for this synchronous
CLI path; callers that need cross-process approval must provide their own durable
run store around the preview.

LangGraph is an optional extra (the ``agent`` install target). Importing this
module therefore requires it; :func:`~janus.cli.scan` imports it lazily and
turns a missing install into an actionable error, so the out-of-the-box
``janus scan`` never needs it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from janus.agent.narrate import Narrative, narrate
from janus.agent.pipeline import (
    FindingWrites,
    ScanReport,
    TrustWrite,
    _detect,
    _persist_trust,
    _project_trust_history,
    _reconcile_stale_findings,
    _record_reads,
    _record_writes,
    _trust_scores,
    _with_previous_scores,
    _write_back,
    new_run_id,
    written_assertion_yaml,
)
from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.detect.coverage import Unevaluated
from janus.llm import LLMConfig
from janus.logs import logfmt, phase
from janus.models import Finding
from janus.writeback.assertions import render_assertion_yaml
from janus.writeback.process_instance import scan_run

logger = logging.getLogger(__name__)

#: A caller's approval decision, given the preview of what would be written. The
#: CLI prints the preview and prompts before the callback returns a decision.
ApproveFn = Callable[[ScanReport], bool]


class AgentUnavailableError(RuntimeError):
    """LangGraph is not installed, so the human-approval agent cannot run."""


class ApprovalRequiredError(PermissionError):
    """A caller must explicitly approve writes on the agent API."""


class ScanState(TypedDict, total=False):
    """The only thing the checkpointed graph state carries: control flow.

    A plain boolean so the checkpointer serializes it natively. Every rich object
    a node produces lives in :class:`_RunArtifacts` instead (see the module
    docstring for why).
    """

    approved: bool


@dataclass
class _RunArtifacts:
    """The findings, prose, and reports of one run, held outside the graph state.

    Populated as the nodes run and read back by :func:`run_agent`. Shared by
    reference across the node closures of a single compiled graph, so it survives
    the approval pause without passing through the checkpointer.
    """

    findings: list[Finding] = field(default_factory=list)
    narratives: list[Narrative] = field(default_factory=list)
    trust: tuple[TrustWrite, ...] = ()
    warnings: list[str] = field(default_factory=list)
    not_evaluated: tuple[Unevaluated, ...] = ()
    """The checks that could not run, carried into both the preview and the report
    so approving a scan never hides what it was unable to look at."""
    preview: ScanReport | None = None
    """What the caller is shown at the interrupt: findings and prose, no writes."""
    report: ScanReport | None = None
    """The run's outcome: the written report when approved, else the preview."""


def build_scan_graph(
    conn: DataHubConnection,
    config: ScanConfig,
    llm: LLMConfig | None,
    run_id: str,
    observed_at: int,
    table_urn: str | None,
    model_urn: str | None,
) -> tuple[Any, _RunArtifacts]:
    """Compile the detect -> reason -> approve -> write state machine.

    Everything fixed for the run is captured in the node closures below, so the
    graph's state carries only control flow and the artifacts ride in the returned
    holder.

    Returns:
        The compiled graph (a langgraph generic, typed ``Any`` to keep the import
        surface small) and the :class:`_RunArtifacts` its nodes write into.

    Raises:
        AgentUnavailableError: LangGraph is not installed.
        ApprovalRequiredError: No callback or explicit unattended approval was given.
    """
    try:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:  # pragma: no cover - exercised via the CLI's lazy import
        raise AgentUnavailableError(
            # Names the distribution, not `-e .`: that form only works from a
            # clone, and the reader here installed a wheel from PyPI.
            "the human-approval agent needs LangGraph, which is an optional "
            'extra: pip install "janus-datahub[agent]"'
        ) from exc

    artifacts = _RunArtifacts()

    def _preview() -> ScanReport:
        """The report a caller sees before approving: findings and prose, no writes.

        Identical in shape to a ``--dry-run`` report. When the scan found nothing
        this is the clean report, which the caller is still asked to approve: a
        clean scan can carry a recovery to write.
        """
        return ScanReport(
            run_id=run_id,
            table_urn=table_urn,
            model_urn=model_urn,
            dry_run=True,
            writes=tuple(
                FindingWrites(finding=finding, narrative=narrative)
                for finding, narrative in zip(artifacts.findings, artifacts.narratives, strict=True)
            ),
            trust=artifacts.trust,
            assertion_yaml=(
                render_assertion_yaml(table_urn, config.freshness_sla_hours, config.freshness_field)
                if table_urn is not None
                else ""
            ),
            warnings=tuple(artifacts.warnings),
            not_evaluated=artifacts.not_evaluated,
        )

    def _detect_node(state: ScanState) -> ScanState:
        """Run every detector the targets call for. Deterministic, no writes."""
        findings, warnings, gaps = _detect(conn, config, table_urn, model_urn, observed_at)
        artifacts.findings = findings
        artifacts.warnings = warnings
        artifacts.not_evaluated = gaps
        artifacts.trust = _trust_scores(findings, config)
        return {}

    def _reason_node(state: ScanState) -> ScanState:
        """Draft the prose for each finding. The only node the LLM runs in."""
        artifacts.narratives = [narrate(finding, llm) for finding in artifacts.findings]
        return {}

    def _approval_node(state: ScanState) -> ScanState:
        """Pause and hand the caller the preview; resume with their decision.

        ``interrupt`` suspends the graph here and returns control to the driver,
        which reads the preview from the artifacts holder. Execution resumes at
        this same node once the caller supplies a decision, so no mutation in the
        write node can run before approval. Building the preview is pure, so the
        re-execution LangGraph performs on resume writes nothing.
        """
        artifacts.preview = _preview()
        # The sleeve tug: a write is waiting on a person. Logged before the
        # interrupt suspends the graph, which is the only moment anything gets
        # to say so (docs/11-argos.md).
        logger.info(
            "approval pending %s",
            logfmt({"run_id": run_id, "findings": len(artifacts.findings)}),
            extra=phase("tugging", run_id=run_id, findings=len(artifacts.findings)),
        )
        decision = interrupt(True)
        return {"approved": bool(decision)}

    def _write_node(state: ScanState) -> ScanState:
        """Perform every mutation, then persist the trust scores. Idempotent.

        Mirrors ``run_scan``'s write path exactly, reconciliation included: the
        agent is a different trigger for the same core, not a different core. It
        is reached with no findings too, which is the recovery case, a target
        whose problem is fixed still has an incident, a tag, and a trust score to
        clear.

        The process instance is opened here rather than at the start of the graph,
        because on this path nothing before approval writes anything: a declined
        run wrote nothing and emitting a run for it would put a process in the
        graph whose outputs are empty by design and indistinguishable from a
        crashed one.
        """
        with scan_run(
            conn,
            run_id,
            started_at_ms=observed_at,
            dry_run=False,
            table_urn=table_urn,
            model_urn=model_urn,
        ) as run:
            _record_reads(run, artifacts.findings)
            trust_history = _project_trust_history(conn, artifacts.trust, run_id)
            trust_by_model = {write.model_urn: write.score for write in artifacts.trust}
            writes = tuple(
                _write_back(
                    conn,
                    finding,
                    narrative,
                    config,
                    run_id,
                    observed_at,
                    trust_history,
                    trust_by_model,
                )
                for finding, narrative in zip(artifacts.findings, artifacts.narratives, strict=True)
            )
            # Assigned on the holder, not rebound: `artifacts` is the closure's
            # in-process carrier, and rebinding the name here would make it a local
            # and drop everything the earlier nodes put on it.
            artifacts.trust = _with_previous_scores(artifacts.trust, trust_history)
            _persist_trust(conn, artifacts.trust, trust_history)
            _record_writes(run, writes, artifacts.trust)
            _reconcile_stale_findings(
                conn,
                config,
                table_urn=table_urn,
                model_urn=model_urn,
                findings=artifacts.findings,
                run_id=run_id,
                observed_at=observed_at,
                run=run,
            )
        artifacts.report = ScanReport(
            run_id=run_id,
            table_urn=table_urn,
            model_urn=model_urn,
            dry_run=False,
            writes=writes,
            trust=artifacts.trust,
            assertion_yaml=written_assertion_yaml(writes),
            warnings=tuple(artifacts.warnings),
            not_evaluated=artifacts.not_evaluated,
        )
        return {}

    def _decline_node(state: ScanState) -> ScanState:
        """Terminal for a clean scan or a declined one: the preview, nothing written."""
        artifacts.report = artifacts.preview or _preview()
        return {}

    def _after_approval(state: ScanState) -> str:
        return "write" if state["approved"] else "decline"

    graph = StateGraph(ScanState)
    graph.add_node("detect", _detect_node)
    graph.add_node("reason", _reason_node)
    graph.add_node("approval", _approval_node)
    graph.add_node("write", _write_node)
    graph.add_node("decline", _decline_node)

    graph.add_edge(START, "detect")
    graph.add_edge("detect", "reason")
    # Every run reaches the interrupt, a clean one included. A clean scan is not
    # a no-op: it is how a recovery is written, resolving the incident and
    # clearing the tag and score of a target whose problem is now fixed. Routing
    # it straight to decline is what left the agent path unable to resolve
    # anything, and reconciliation is a mutation, so it goes through the
    # same approval gate as every other write (docs/02-architecture.md).
    graph.add_edge("reason", "approval")
    graph.add_conditional_edges(
        "approval", _after_approval, {"write": "write", "decline": "decline"}
    )
    graph.add_edge("write", END)
    graph.add_edge("decline", END)

    return graph.compile(checkpointer=MemorySaver()), artifacts


def run_agent(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None = None,
    model_urn: str | None = None,
    run_id: str | None = None,
    llm: LLMConfig | None = None,
    approve: ApproveFn | None = None,
    auto_approve: bool = False,
    now_ms: int | None = None,
) -> ScanReport:
    """Audit a table, a model, or both, pausing for approval before writing.

    The graph runs detect and reason, then pauses at the approval interrupt and
    hands ``approve`` the preview report. Approve returns True and the writes land;
    return False and nothing is written and the preview is what comes back.

    A clean scan pauses too. It has no finding to write, but it is the run that
    resolves a recovered target's incident and clears the tag and trust score it
    left behind, and that is a mutation like any other, so it is gated like any
    other. The preview reports what was found, not what will be resolved: seeing
    the pending recoveries first would mean splitting reconciliation into a read
    phase and an apply phase, which is the upgrade path if it matters.

    Args:
        conn: An open connection.
        config: SLA, hop caps, tag and term names.
        table_urn: The dataset to audit for freshness, if any.
        model_urn: The model to audit for leakage and schema drift, if any.
        run_id: Provenance stamp and the checkpointer thread id. Minted when omitted.
        llm: The configured model, or None for deterministic template prose.
        approve: Called with the preview; returns whether to write. It is required
            unless ``auto_approve`` is explicitly set.
        auto_approve: Explicitly allow unattended writes for a controlled caller.
        now_ms: The instant to measure staleness against. Defaults to now.

    Returns:
        The written report when approved, or the unwritten preview when declined.

    Raises:
        AgentUnavailableError: LangGraph is not installed.
    """
    run_id = run_id or new_run_id()
    observed_at = now_ms if now_ms is not None else int(time.time() * 1000)

    # build_scan_graph raises the actionable AgentUnavailableError if LangGraph is
    # missing, so it runs before any bare langgraph import the caller would see raw.
    app, artifacts = build_scan_graph(conn, config, llm, run_id, observed_at, table_urn, model_urn)
    thread = {"configurable": {"thread_id": run_id}}

    from langgraph.types import Command

    state = app.invoke({}, thread)
    if "__interrupt__" in state:
        assert artifacts.preview is not None  # set by the approval node before it paused
        if approve is None and not auto_approve:
            raise ApprovalRequiredError(
                "an approval callback is required; pass auto_approve=True explicitly "
                "for an unattended run"
            )
        decision = auto_approve if approve is None else approve(artifacts.preview)
        app.invoke(Command(resume=decision), thread)

    assert artifacts.report is not None  # a terminal node always sets it
    return artifacts.report
