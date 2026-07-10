# CLAUDE.md - agent

Orchestration. Fixed node order:
detect -> investigate -> reason_and_score -> [human_approval] -> write_back -> END

Today pipeline.py runs that order as plain function calls and narrate.py is the
reason node. graph.py replaces pipeline.py with a LangGraph StateGraph in a
later phase; the boundaries are drawn so that swap touches nothing else.

## Local rules

1. The LLM runs only in the reason node (narrate.py), with temperature=0. It
   explains, ranks, and drafts prose; it never decides whether a finding exists,
   and nothing it emits may reach a dedup key, a severity, a URN, or an enum.
2. interrupt() gates every mutation. Until it lands, --dry-run is the gate:
   it must detect and explain while writing absolutely nothing. --auto-approve
   exists only for the recorded demo and must be an explicit flag, never a default.
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
6. Use a checkpointer so runs are replayable. Retry/backoff and circuit-breaker
   policy around GMS calls lives here, not in detect/ or writeback/.
7. Exact datahub-agent-context import symbols are [confirm]: introspect the
   installed package before writing imports.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: node order, LLM gating, security rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | Phase 1: pipeline.py runs the node order, narrate.py is the reason node; --dry-run is the gate until interrupt() lands; the narrator must never raise |
| 2026-07-10 | Claude (for Ghassen Naouar) | The narrator is vendor-blind and env-free: LLMConfig is injected, provider errors are scrubbed before logging |
