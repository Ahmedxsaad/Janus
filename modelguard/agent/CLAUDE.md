# CLAUDE.md - agent

LangGraph orchestration. Fixed node order:
detect -> investigate -> reason_and_score -> [human_approval] -> write_back -> END

## Local rules

1. The LLM runs only in reason_and_score, with temperature=0. It explains,
   ranks, and drafts prose; it never decides whether a finding exists.
2. interrupt() gates every mutation. --auto-approve exists only for the
   recorded demo and must be an explicit flag, never a default.
3. Metadata fed to the LLM is wrapped as delimited untrusted data, never as
   instructions (OWASP LLM01). The system prompt states this explicitly.
4. Self-check before hand-off: every URN the LLM emits must resolve in the
   graph; enum values and numbers validated programmatically.
5. Use a checkpointer so runs are replayable. Retry/backoff and circuit-breaker
   policy around GMS calls lives here, not in detect/ or writeback/.
6. Exact datahub-agent-context import symbols are [confirm]: introspect the
   installed package before writing imports.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: node order, LLM gating, security rules |
