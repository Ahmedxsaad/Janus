# CLAUDE.md - modelguard package

The Python package. Six-layer pipeline (docs/plan/architecture.md section 4):
trigger -> detection -> orchestration -> reasoning -> write-back, with
security and observability cross-cutting.

## Layer boundaries (enforce strictly)

- detect/ is pure: reads the graph, returns typed findings. No LLM, no writes.
- writeback/ mutates the graph: fixed parameterized functions, idempotent. No LLM.
- agent/ orchestrates and is the only place the LLM runs, gated by human approval.
- seed/ exists only to build demo/benchmark graphs; production code never imports it.
- client.py is the single factory for DataHubClient / DataHubGraph; nothing else
  reads env vars for connections. It hands both handles out as a DataHubConnection.

## Local rules

1. Findings passed between layers are typed models, not dicts. The shared models
   module (improvement P2-3) is not written yet: no detector exists to produce a
   finding. It lands with the first detector, before a second one can diverge.
2. cli.py exposes exactly two entry points: scan (batch) and watch (event-driven).
   Both share the identical detect -> reason -> write core.
3. Keep hop caps, thresholds, and score weights in config, never hardcoded.
4. Every run gets a run_id; every log line and write carries it.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: layer boundaries and package rules |
| 2026-07-09 | Claude (for Ghassen Naouar) | client.py exists; note that the shared findings model lands with the first detector |
