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
7. Exact datahub-agent-context import symbols are [confirm]: introspect the
   installed package before writing imports.

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
