# Deep Research: How to Win - DataHub Agent Hackathon Strategy

*Research date: July 6, 2026. Sources: docs.datahub.com, github.com/datahub-project, DataHub blog.*

---

## 1. What DataHub Already Ships (DO NOT REBUILD - judges penalize this)

Originality criterion (verbatim): *"Submissions should clearly go beyond features DataHub already provides out of the box. Building on top of, extending, or composing shipped features is welcome; rebuilding them as if from scratch isn't."*

### MCP Server (open source)
Tools agents get automatically: `search`, `get_entities`, `get_lineage`, `list_schema_fields`, `get_dataset_queries`, `search_documents`/`grep_documents`, plus **write tools**: `add_tags`/`remove_tags`, `update_description`, `add_glossary_terms`/`remove_glossary_terms`, `set_domains`, `add_owners`, `save_document`. Endpoints: Cloud `https://<tenant>.acryl.io/integrations/ai/mcp`, self-hosted `http://<gms-host>:8080/mcp`. Python SDK: `pip install datahub-agent-context` (LangChain + Google ADK toolsets).

### DataHub Skills (shipped April 2026, repo: datahub-project/datahub-skills)
- **Catalog skills:** `datahub-setup`, `datahub-search`, `datahub-lineage`, `datahub-enrich`, `datahub-quality` (assertions, incidents, subscriptions)
- **Connector dev skills:** `connector-planning`, `connector-pr-review` (5 parallel review agents), `load-standards` (22 connector standards)
- Works in Claude Code, Cursor, Copilot, Codex, Gemini CLI, Windsurf
- **Repo is small and active (23 stars, 5 forks, v1.4.1 Apr 2026) - a contributed new skill is very feasible and hits the bonus criterion**

### Analytics Agent (shipped April 30, 2026 - open source, Apache 2.0)
Already does: plain English → SQL → chart; conversation context; visible tool calls; **context quality score (1-5)**; `/improve-context` write-back (proposes + writes doc improvements to DataHub); "Publish analysis" (saves analyses as DataHub Documents); "Save correction" skill; multi-warehouse; BYO LLM.
**Consequence: plain text-to-SQL / "ask your data" chatbots are DEAD ideas for this hackathon.**

### DataHub Actions Framework (open source)
Event-driven plumbing: Kafka consumer over `EntityChangeEvent_v1` / `MetadataChangeLog_v1` with declarative filters (entityType, category=TAG/DOCUMENTATION/LIFECYCLE, operation, modifier), pluggable transformers/actions, consumer groups, at-least-once delivery. **Ships NO intelligence - it's a trigger mechanism. Pairing it with an LLM agent is unexplored, high-originality territory.**

### DataHub Cloud (not OSS, so don't depend on it, but don't rebuild it either)
Assertions/monitors, Incidents, Data Contracts, Data Health Dashboard, Compliance Forms, Automations (doc propagation), Subscriptions & notifications, Slack/Teams apps. OSS `datahub-quality` skill is "diagnostic" only; assertion *creation* is Cloud.

### ML metadata model (open source - deep and underused)
Entities: `MLModel`, `MLModelGroup`, `MLFeature`, `MLFeatureTable`, `MLModelDeployment`, training runs (`DataProcessInstance` w/ MLFLOW_TRAINING_RUN subtype, metrics + hyperparameters), experiments (containers), `versionSet`.
Lineage chain: **Training Datasets → Training Run → Model → Deployment; Feature Tables → Features → Models; Features → source datasets (column-level "DerivedFrom")**.
Ingestion: MLflow, SageMaker, Vertex AI, Databricks/Unity Catalog.
DataHub's own blog (June 2026) names the unsolved pains: **target leakage** (needs column-level lineage), silent upstream data failures breaking models, drift vs. data-issue root-cause confusion, "lineage graphs that stop at the warehouse boundary."
**No ML-specific skill exists in datahub-skills. No shipped ML-protection agent. This is the widest-open challenge track.**

---

## 2. The Meta-Game: Read the Judging Criteria as a Spec

| Criterion | What actually scores |
|---|---|
| Use of DataHub | **Read AND write back.** The criterion literally says the strongest submissions "contribute back to the graph." Use lineage + ownership + assertions + incidents + documents, not just search. |
| Technical Execution | Works end-to-end on a reproducible demo. Use the **sample datasets with planted issues** (nyc-taxi = planted freshness issues; healthcare = planted quality issues) → your demo is deterministic and judges can rerun it. |
| Originality | Not text-to-SQL, not PII tagging, not "describe my tables" - those are docs examples + shipped products. |
| Real-World Usefulness | Target on-call/data-engineering pain: broken pipelines, schema-change blast radius, silent ML degradation, incident root-cause. |
| Submission Quality | <3 min video, crisp README, `examples/` folder with sample outputs. |
| Bonus | **PR a new skill to datahub-skills** (small active repo) or a connector/docs fix to datahub core. "Existing contributions extended for the hackathon also count." |

Also note: 4 Challenge Winner prizes = one per category. **Categories 2 (codegen) and 3 (ML) will have far fewer entries than 1 and 4** - most participants will build chat agents. Strategically, targeting 2 or 3 (while being Grand-Prize-worthy) maximizes expected value.

Multiple submissions are allowed if substantially different - a team could enter a big project in one category and a small sharp one in another.

---

## 3. Candidate Paths (ranked)

### Path A - "Sentinel": Autonomous Incident-Response & ML-Guardian Agent (Challenges 1 + 3)
**The idea:** An always-on agent (or team of agents) that *wakes up on DataHub events* instead of waiting for a chat prompt.

Flow:
1. **Trigger** - DataHub Actions pipeline (or polling fallback) catches an event: assertion failure, schema change (`MetadataChangeLog` on schemaMetadata), deprecation, incident raised.
2. **Investigate** - Agent uses MCP `get_lineage` (upstream) to find root cause candidates (e.g., upstream table freshness failure), `get_dataset_queries` + schema history for evidence.
3. **Blast radius** - Traces *downstream* lineage across the warehouse boundary into **ML land**: which features, models, and live deployments consume the broken data. Ranks severity by usage stats + ownership + deployment status.
4. **Act & write back** - Raises/updates a DataHub Incident, tags at-risk models (`model-at-risk`), writes a full RCA document into the DataHub Knowledge Base (`save_document`), notifies owners (Slack webhook), and posts a timeline. The next human/agent inherits everything - exactly the challenge-1 brief.
5. **(Optional codegen bridge)** - If root cause is a schema change (column rename), generate the downstream fix (dbt model patch) as a PR → touches Challenge 2 too.

**Why it wins:** Nothing shipped does event-driven + LLM reasoning; it uses the deepest parts of the graph (column-level lineage, ML entities, assertions, incidents, ownership); it writes back massively; it's a real on-call pain (DataHub's own marketing describes this exact pain as unsolved detective work); and the planted-issue sample datasets make a perfect deterministic demo (trip data goes stale → agent autonomously finds it, maps 2 models + 1 dashboard at risk, files RCA).
**Demo narrative for the video:** "3 AM. A pipeline silently breaks. No human notices. Watch what happens."
**Risks:** Actions framework + Kafka adds setup complexity - mitigate with a polling mode fallback; incidents API on OSS needs checking (GraphQL supports raising incidents on OSS; verify early).

### Path B - "ModelGuard": Production ML Protection Suite (Challenge 3, sharper scope)
Subset of A, focused purely on ML:
- **Target-leakage detector:** walk column-level lineage from each model's features back to sources; flag features derived from the label's source column. (DataHub blog names this exact failure mode as needing column-level lineage.)
- **Training-serving contract:** compare training dataset schema snapshot vs. current schema (schema history) → flag drifted inputs.
- **Upstream-quality → model-risk propagation:** failing assertion upstream ⇒ auto-tag dependent models/deployments, compute "trust score" per model, write scorecard back as structured properties + document.
- Ship it also as a **`datahub-ml` skill contributed to datahub-skills** (no ML skill exists) → bonus criterion, and judges include DataHub PMs who'd love this.
**Why:** Least crowded category, extremely concrete value, clean scope for 5 weeks.
**Needs:** MLflow-ingested demo environment - build a small training pipeline on healthcare/nyc-taxi data, ingest via the MLflow source, so end-to-end ML lineage exists.

### Path C - Schema-Change Copilot / Migration PR Generator (Challenge 2)
Detect (or be told about) an upstream schema change → use lineage to find every downstream dbt model / Airflow DAG affected → generate the migration PR (updated SQL, renamed columns, updated tests) grounded in real DataHub schemas → include generated artifacts in `examples/`. Write an impact report back to DataHub and link the PR on affected entities.
**Why:** Challenge 2 explicitly wants "artifact lives in a Git repo, goes into a PR, your data team would actually merge it" + sample artifacts. Low competition. Very demoable.
**Risk:** dbt-project plumbing takes time; quality of generated SQL must be high because judges are told to inspect samples.

### Path D - Compliance/Lineage Wildcard (Challenge 4)
"GDPR cartographer": agent walks PII-tagged columns through lineage to produce an always-current Article-30 Record of Processing / data-flow map, flags PII leaking into unauthorized domains or ML training sets, writes findings back as compliance tags + documents. Real regulatory value; combines with Compliance Forms concepts without needing Cloud.
**Risk:** overlaps with the "governance agent" doc example (PII tagging) - must go clearly beyond tagging into flow-analysis/reporting to stay original.

### Path E - Knowledge-Capture Agent (Challenge 4)
Agent that ingests tribal knowledge (Slack threads, PR descriptions, incident postmortems) and converts it into DataHub Context Documents attached to the right entities, deduplicated against existing docs. Feeds the exact "context gap" DataHub says makes agents dumb. Fully write-back oriented.
**Risk:** fuzzier demo, harder to judge "does it work."

### Dead ideas (already shipped / docs examples - expect dozens of these from other teams)
Text-to-SQL chatbot · "chat with your catalog" · auto-describe tables / PII tagging steward · basic quality-report generator · a generic dashboard over DataHub metadata.

---

## 4. Recommended Play

**Build Path A ("Sentinel") scoped around the ML story of Path B**, i.e.:
> *An autonomous agent team that watches DataHub events, root-causes data incidents via lineage, maps blast radius all the way into production ML models, writes RCA + risk metadata back to the graph, and (stretch) opens the fix PR.*

- **Primary category:** Agents That Do Real Work (or Production ML Agents - decide at submission based on which story is stronger in the final build; the rules let you combine).
- **Bonus:** extract the ML-risk workflow into a `datahub-ml-guard` skill and PR it to datahub-skills; file at least one real bug/docs PR found along the way; complete the Feedback survey ($50 pool + goodwill).
- **Stack suggestion:** Python + `datahub-agent-context` (LangChain) or direct MCP; DataHub quickstart via Docker; `datahub datapack load showcase-ecommerce` + nyc-taxi (planted freshness) + a tiny MLflow training script ingested into DataHub for ML lineage; Slack webhook for notifications; Apache 2.0 from day one.

## 5. Execution Checklist (5 weeks)
- **Week 1:** Quickstart DataHub locally, load datapacks, ingest an MLflow toy model → verify end-to-end ML lineage renders. Validate: raising incidents via API on OSS, Actions framework event capture, MCP write tools. *De-risk everything now.*
- **Week 2:** Core agent loop: event → lineage investigation → blast radius → write-back (tags, incident, document). Hard-code one scenario first.
- **Week 3:** Generalize (schema-change scenario, ML-risk scenario), add Slack notify, build minimal UI or rich terminal/report output (judges love visible reasoning - mirror the Analytics Agent's "no black box" ethos).
- **Week 4:** Skill extraction + PR to datahub-skills; `examples/` folder with sample RCA docs/PRs; README with 5-minute setup; polish determinism of demo.
- **Week 5:** Sub-3-minute video (problem → trigger → autonomous run → what got written back to DataHub → PR), Devpost text, feedback survey, submit early.

## 6. Open Questions to Verify Early (in #agent-hackathon Slack)
1. Can OSS (non-Cloud) raise Incidents and create assertions programmatically? (Skill docs say assertion *creation* is Cloud; incidents appear OSS-capable via GraphQL - confirm.)
2. Does the sample nyc-taxi datapack's planted freshness issue surface as assertion events, or must we simulate the trigger?
3. Is judging done against our hosted demo or their local run? (Rules: judges *may* judge from video/description only - so the video must carry the whole story.)
