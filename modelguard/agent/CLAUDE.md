# CLAUDE.md - agent

Orchestration. Fixed node order:
detect -> investigate -> reason_and_score -> [human_approval] -> write_back -> END

pipeline.py runs that order as plain function calls (run_scan) and narrate.py is
the reason node. graph.py now runs the identical order as a LangGraph StateGraph
whose nodes delegate to the same pipeline functions, adding a real interrupt()
approval gate; it wraps pipeline.py, it does not replace it. run_scan stays the
core that scan (default), watch, and the tests share. graph.py is the optional
`agent` extra: cli.py imports it lazily so a plain scan needs no langgraph.

## Local rules

1. The LLM runs only in the reason node (narrate.py), with temperature=0. It
   explains, ranks, and drafts prose; it never decides whether a finding exists,
   and nothing it emits may reach a dedup key, a severity, a URN, or an enum.
2. interrupt() gates every mutation on the agent path (graph.py, `scan --review`):
   the graph pauses after reasoning and writes only what the caller approves.
   --dry-run remains the no-write preview on the default path. --auto-approve
   writes without prompting and exists only for the recorded demo; it must be an
   explicit flag, never a default. The default `scan` writes directly (core deps,
   no langgraph): the interrupt is opt-in so the out-of-the-box path stays light.
3. The narrator never raises, reads no environment variable, and names no vendor.
   It is handed an LLMConfig or None (modelguard/llm.py builds it). An
   unconfigured LLM, an uninstalled provider binding, a network error, a rate
   limit, an empty or over-long reply all fall back to the deterministic template
   and record the source on the Narrative. A scan must run with no LLM at all.
   Third-party exception text goes through env.scrub() before it is logged: we
   handed that SDK the key, so we cannot assume its error message is clean.
4. Metadata fed to the LLM is wrapped as delimited untrusted data, never as
   instructions (OWASP LLM01). The system prompt states this explicitly.
5. Self-check before hand-off: every URN the LLM emits must resolve in the
   graph; enum values and numbers validated programmatically.
6. Use the process-local checkpointer for the synchronous approval exchange; do
   not claim cross-process replay without a durable run store. Retry/backoff and
   circuit-breaker policy around GMS calls lives here, not in detect/ or writeback/.
7. datahub-agent-context symbols are [verified] against 1.7.0, not [confirm]:
   `build_langchain_tools(client)` returns ten read-only LangChain tools and
   defaults `include_mutations=False`; `get_entities` returns a bare list, and a
   dataset keeps its description under `editableProperties` while an mlModel
   keeps it at the top level. context_kit.py uses only that surface, and only as
   a library: the kit supplies the read, never the decision to read, because
   handing an LLM those tools would let it choose which parts of the catalog an
   incident may mention. The kit cannot be installed alongside this project
   (every release from 1.6.0.6 on pins acryl-datahub==1.6.0.6, this project pins
   1.6.0.13, pip answers ResolutionImpossible), so it is absent by default and
   every function degrades to None.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: node order, LLM gating, security rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | Phase 1: pipeline.py runs the node order, narrate.py is the reason node; --dry-run is the gate until interrupt() lands; the narrator must never raise |
| 2026-07-10 | Claude (for Ghassen Naouar) | The narrator is vendor-blind and env-free: LLMConfig is injected, provider errors are scrubbed before logging |
| 2026-07-16 | Claude (for Ghassen Naouar) | pipeline runs schema drift on a model target and, after every per-finding write, a trust-score pass that aggregates the scan's findings per model and persists the score + band. narrate.py dispatches the drift finding (P3/P4, D-036, D-037) |
| 2026-07-16 | Claude (for Ghassen Naouar) | graph.py lands: LangGraph StateGraph over the pipeline nodes with a real interrupt() approval gate and MemorySaver checkpointer, exposed as `scan --review`/`--auto-approve`; findings ride an in-process holder, not the checkpointed state. Optional `agent` extra, lazily imported (D-039) |
| 2026-07-17 | Codex | Agent API requires an approval callback unless explicit `auto_approve=True`; recovery and watcher retry behavior are covered by regression tests (D-040) |
| 2026-07-22 | Claude (for Ahmed Saad) | Security review: the evidence block was delimited but its delimiter was not escaped, so catalog text containing </evidence> escaped the untrusted region. _neutralize strips lookalikes before wrapping; rule 4 now holds in practice, not only in intent (D-049) |
| 2026-07-30 | Claude (for Ahmed Saad) | D-040's recovery only ever worked inside one continuous watch process; a restart, `watch --once`, or a plain scan could raise a finding but never resolve it. `run_scan` gains a graph-driven `_reconcile_stale_findings`, called from its own write path for all three finding types, superseding cli.py's in-memory-only `_reconcile_recovery` (D-067) |
| 2026-07-30 | Claude (for Ghassen Naouar) | Review of D-067's own code: graph.py's write node reconciles like run_scan, and every run reaches the interrupt, because a clean scan is the recovery path and a resolve is a mutation. Reconciliation no longer drops a risk flag another resource still earns, and a recovered table records its passing assertion run (D-070) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-073: `_evidence_detail` raises for an unregistered finding type like its three siblings, instead of silently returning no evidence. pipeline.py gains the per-scan logfmt line rule 4's run_id was already threaded for |
| 2026-08-02 | Claude (for Ghassen Naouar) | The four singledispatch tables in narrate.py gain the two governance finding types. `_log_scan` assembles its facts once and renders them twice, logfmt in the message and structured fields on the record (D-078, D-079) |
| 2026-08-04 | Claude (for Ghassen Naouar) | _write_back is handed the run's trust scores alongside the projected history, so each impact report's waterfall and the model's own property describe one computation rather than two reads that can disagree (D-108, T-01) |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-04 (D-111): run_scan runs inside a scan_run context manager, so detection and write-back both happen within a process instance the graph holds, and any exception leaves a FAILURE run event before it is re-raised. Reconciliation records the assets it clears on the run, because a recovery-only scan is clean and a report-derived output list would be empty for exactly the run whose outputs matter. graph.py opens its run in the write node: nothing before the approval interrupt writes, and a declined run would otherwise be indistinguishable from a crashed one |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-07 (D-113): _detect runs the degraded detector last and only where the four column-level ones had no link to read, so a scan never prints a maybe beside a proof. narrate.py's four singledispatch tables gain the table-level type, and its brief tells the narrator to say the mode out loud and never to write it as if the model were known to be affected |
| 2026-08-05 | Claude (for Ahmed Saad) | context_kit.py lands (D-135): DataHub's own Agent Context Kit, read-only, grounding the narrator in what a detector does not collect (owners, domain, description, and the catalog's own health for the asset). Rule 7's [confirm] is closed by introspecting 1.7.0 against a live GMS. The context joins `grounding_facts` rather than the prompt alone, so the faithfulness checker sees every fact the model does; it enters inside the delimited untrusted block, because a description is catalog text anybody can edit; and it is never fetched when no LLM is configured, so `--no-llm` is byte-identical with or without the kit |
