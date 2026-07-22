# CLAUDE.md - modelguard package

The Python package. Six-layer pipeline (docs/plan/architecture.md section 4):
trigger -> detection -> orchestration -> reasoning -> write-back, with
security and observability cross-cutting.

## Layer boundaries (enforce strictly)

- detect/ is pure: reads the graph, returns typed findings. No LLM, no writes.
- writeback/ mutates the graph: fixed parameterized functions, idempotent. No LLM.
- agent/ orchestrates and is the only place the LLM runs, gated by human approval.
- seed/ exists only to build demo/benchmark graphs; production code never imports it.
- env.py is the single entry point for configuration: the only module that calls
  load_dotenv and the only one that reads os.environ. Everything else asks it.
- client.py is the single factory for DataHubClient / DataHubGraph. It hands both
  handles out as a DataHubConnection and applies no defaults: a hardcoded fallback
  (a server URL, a username) is a machine-specific value in tracked code and turns
  a missing .env into a silent connection to the wrong place. Missing config fails
  loudly. Secrets are never logged, echoed, or placed in an exception message.
- llm.py is the single factory for the language model, and the only module allowed
  to import a vendor SDK or name a vendor's model. Provider, model, and key come
  from the environment together, with no defaults; all three or none.

## Local rules

1. Findings passed between layers are typed models (models.py), not dicts.
2. cli.py exposes two commands: scan (batch) and watch (polling). Both share the
   identical detect -> reason -> write core in agent/pipeline.py (run_scan), not in
   cli.py. watch polls and acts on finding-set transitions, auto-approving because
   it is unattended; it is polling by design (never Kafka-dependent), with the
   Actions/EntityChangeEvent framework as the documented upgrade path.
3. Keep hop caps, thresholds, and score weights in config.py, never hardcoded.
   Overrides come from MODELGUARD_* env vars and fail loudly when unusable.
4. Every run gets a run_id; every log line and write carries it. It is
   provenance, never part of a dedup key (D-013).
5. Nothing an LLM produces may reach a dedup key, a severity, a URN, or an enum.
   The LLM writes prose and only prose (D-027). Any number a human reads comes
   from a finding's evidence mapping.
6. A scan must complete with no LLM configured and no network beyond DataHub.
   The narrator is handed an LLMConfig or None; it degrades to a template on any
   failure and never raises. It reads no environment variable and names no vendor.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: layer boundaries and package rules |
| 2026-07-09 | Claude (for Ghassen Naouar) | client.py exists; note that the shared findings model lands with the first detector |
| 2026-07-10 | Claude (for Ghassen Naouar) | Phase 1: models.py and config.py exist; core loop lives in agent/pipeline.py; add the LLM-containment and offline-scan rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | env.py is the sole config entry point; llm.py is the sole vendor boundary; the narrator takes an injected LLMConfig |
| 2026-07-13 | Claude (for Ahmed Saad) | Phase 2 leakage detector lands. Finding is now an ABC (FreshnessFinding, LeakageFinding); a scan may target a table, a model, or both (D-033) |
| 2026-07-16 | Claude (for Ghassen Naouar) | Phase 2 P3/P4 land. SchemaDriftFinding joins the Finding ABC; a model scan runs leakage + schema drift. TrustScore/TrustBand roll a scan's findings per model into modelguard.trust_score + trust_band, written only for models a finding named (D-036, D-037) |
| 2026-07-16 | Claude (for Ghassen Naouar) | Section 7 lands. agent/graph.py adds the LangGraph human-approval agent (scan --review); the watch command polls and acts on finding-set transitions. Both reuse run_scan; langgraph is the optional `agent` extra (D-039) |
| 2026-07-17 | Codex | Recovery transitions reconcile incidents and model risk metadata; watcher failures retry with bounded backoff; agent API writes require explicit approval (D-040) |
| 2026-07-22 | Claude (for Ahmed Saad) | Security review (D-049): cli.py pins pretty_exceptions_show_locals=False, because a traceback carrying locals would print the DataHub token; env.ConfigError's contract corrected to name credentials rather than all values |
