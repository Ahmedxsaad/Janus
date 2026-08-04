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
2. Four triggers, one core: cli.py exposes scan (batch), watch (polling), and
   gate (CI); mcp_server.py adds a fourth, conversational, over MCP rather than a
   shell. All four share the identical detect -> reason -> write core in
   agent/pipeline.py (run_scan), not in the trigger itself. watch polls and acts
   on finding-set transitions, auto-approving because it is unattended; it is
   polling by design (never Kafka-dependent), with the Actions/EntityChangeEvent
   framework as the documented upgrade path.
   gate is the preventive one: it judges a dry-run scan against a policy and
   answers in an exit code (0 shippable, 1 blocked, 2 could not tell). It reads
   and does not write, because it runs on every push to every branch and one
   incident per run would fill the graph with findings about code that never
   merged. Exit 2 is never a finding: a gate that reports "I could not connect"
   as a violation teaches a team to ignore every red build.
   link is not a trigger and runs no detector: it writes the join between a
   model and the columns it trained on, because DataHub's own ingestion does
   not (mlflow gives a model with no features, dbt gives column-level lineage
   between tables, nothing joins them), and without it the detectors have
   nothing to read on a real graph, and `link --all` replays every link the
   graph itself records, which is the step after an ingestion run. inventory is
   run_scan in read-only bulk: every model in the graph, and what can and cannot
   be checked on it; `scan --all-models` is the same sweep that writes.
   mcp_server.py exposes the same detectors to an MCP client (Claude Desktop or
   similar) as three tools, all read-only: the calling model is outside this
   project's control, so it gets to ask what is wrong, never to fix it. Writes
   stay behind scan --write and gate --write, invoked by a human who typed the
   command.
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
| 2026-07-23 | Claude (for Ahmed Saad) | gate.py and `modelguard gate` land: the preventive half, a policy over a dry-run scan answered in an exit code, read-only by default, with a reusable GitHub Action at action.yml (D-052) |
| 2026-07-23 | Claude (for Ahmed Saad) | mcp_server.py lands: check_leakage, check_freshness, check_gate exposed to an MCP client, all annotated readOnlyHint and wrapping run_scan in dry-run only. modelguard-mcp serves over stdio (D-053) |
| 2026-07-23 | Claude (for Ahmed Saad) | Dockerfile + docker-compose.yml land: a non-root image with all four console scripts, composed onto the datahub_network `datahub docker quickstart` already creates rather than reimplementing DataHub's own stack; CI gains a docker build/smoke job (D-054) |
| 2026-07-30 | Claude (for Ahmed Saad) | Live product testing found trust_score ignoring severity and D-040's recovery only ever working inside one continuous watch process; run_scan now reconciles stale incidents itself, graph-driven, so scan/gate --write/watch --once/a restarted watch all resolve correctly, not just an uninterrupted watch (D-067) |
| 2026-07-30 | Claude (for Ghassen Naouar) | Five defects found in D-067's reconciliation: a partial recovery cleared a still-failing model's flags, tag and score; the agent path never reconciled at all; a drift incident's title named only the dataset, so two models trained on one input shared a dedup key and each recovery closed the other's incident; a recovered table never recorded its passing assertion run (D-070) |
| 2026-08-01 | Claude (for Ghassen Naouar) | Full-implementation review (D-073): seven defects fixed across detect, writeback, agent and cli. run_scan now emits one logfmt line per scan (run_id, counts, detect_ms/total_ms); `watch` is the entry point that configures the handler, the library only emits |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-074, from running the product on a real dbt + MLflow project: detect/coverage.py reports the checks a scan could not run, so silence is never rendered as "healthy"; `modelguard link` writes the model-to-column join no ingestion source produces, and records its own arguments so it survives the next ingest; `modelguard inventory` lists a graph's models and their coverage |
| 2026-08-01 | Claude (for Ghassen Naouar) | The two sweeps land: `scan --all-models` audits every model in the graph, `link --all` replays every recorded link. Both refuse the single-target options rather than picking a winner among them (D-074) |
| 2026-08-02 | Claude (for Ghassen Naouar) | render.py holds the JSON and CI job-summary renderings, pure functions of a ScanReport like gate.py (D-077). logs.py adds MODELGUARD_LOG_FORMAT=json, closing P2-5 (D-078). Reconciliation now keys on the finding type, not on (resource, incident_type) alone: leakage and a sensitive source both raise a FIELD incident on the same column, and a flat key set would leave a fixed leak open forever (D-079) |
| 2026-08-02 | Claude (for Ghassen Naouar) | api.py lands: link_model and scan_model, re-exported from the package root as the supported public surface a training script may pin to. Thin wrappers over the same functions the CLI calls, so rule 2's four-triggers-one-core now reads five (D-083) |
| 2026-08-03 | Claude (for Ghassen Naouar) | argos/ and companion.py land. Rule 2's triggers now number six: `watch --pet` drives the desktop window from the existing poll, and `companion` is a read-only sweep of the assets one owner owns (incidents, failing assertions, deprecations) that runs no detector at all. logs.py gains phase(), so the four mid-scan states reach a renderer through the log channel rather than through a callback in a detector's signature (D-098) |
| 2026-08-03 | Claude (for Ahmed Saad) | discovery.py lands: every model-discovery path (the bulk sweeps and `--model <name>`) goes through it, because DataHub hides non-latest versions of a versioned model from search. A model it cannot see is one whose link never replays and whose incident never closes, so the search flag that turns the hiding off is not optional (D-100) |
| 2026-08-03 | Claude (for Ghassen Naouar) | The Argos event carries the trust band the detector decided, not just the score. A renderer that re-applied the band thresholds painted a WATCH model healthy, because a critical finding caps the band below what its points would give (D-067). Rule 3's thresholds-live-in-config now reaches the second surface too (D-099) |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-01/F7 (D-108): a trust score now leads with its deductions, each naming the finding that caused it, and carries a SCORING_VERSION into its history and onto the model. Rule 3's thresholds-in-config gains the version and the provenance sentence, both facts about the code rather than knobs, so neither is overridable from the environment |
| 2026-08-04 | Claude (for Ghassen Naouar) | render.py gains the NIST AI RMF crosswalk and `modelguard crosswalk` prints it, so rule 2's trigger list gains a seventh entry that connects to nothing: the crosswalk is a fact about the detectors, not about a catalog, and it runs before a token exists (D-109, T-02) |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-03 (D-110): every finding now carries a counterfactual, the changes that would make it not exist, each sufficient alone. Rule 5's no-LLM-in-a-decision rule covers it: it is derived from the same graph facts as the finding, and the benchmark verifies it by performing it |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-04 (D-111): the agent is now an entity in the graph it guards. Every scan emits a dataProcessInstance under a modelguard dataFlow and scan dataJob, keyed by the run_id rule 4 already threads everywhere, so the provenance stamp on a write is something a reader can open rather than a string to grep. A dry run emits nothing, keeping rule 2's no-write contract intact |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-05/T-06 (D-112): adapters/ is a new layer boundary, read-only and offline. It parses a declaration on disk and returns what `link` takes; it never connects to a vendor service, never writes, and never invents an argument the file does not carry. Rule 2's `link` paragraph gains --from, which proposes like --infer and writes nothing until a human answers |
| 2026-08-04 | Claude (for Ghassen Naouar) | T-07 (D-113): a model with no column link now gets the table-level answer instead of silence, as its own finding type, capped at MEDIUM, scored separately by the benchmark and excluded from the trust score. Rule 3's thresholds-in-config gains TABLE_LEVEL_PRECISION, which is a measurement rather than a knob: run_bench compares it against the baseline it measures and says so in RESULTS.md |
