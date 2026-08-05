# ModelGuard - Winning Strategy (Verified)

> **The missing CI for your ML supply chain.** An agent that lives on the warehouse↔ML
> boundary DataHub uniquely spans, reads end-to-end column-level lineage + ML metadata to
> catch silent data→model failures, and writes back incidents, model-risk scorecards,
> guarding assertions, and impact reports into the graph.
>
> **Primary category:** #3 Production ML Agents (verified least-crowded, highest-differentiation).
> **Also credible for:** Grand Prize (best overall).
> **OSS bonus:** first-ever `datahub-ml-guard` Skill + an MCP mutation-tool PR (both fill verified gaps).
> **Engineered production-grade:** *measured* against a benchmark, scalable (batch + event-driven),
> secure (human-gated, least-privilege, metadata-only), and observable - not a one-take demo.

This document is the *why* and *what*. The rest of the doc-set:
- `architecture.md` - *how it works*: system-context + six-layer diagrams, component catalog, runtime flows.
- `02-implementation-plan.md` - *the build*: environment, seeder, detectors, write-back, verified API cheat-sheet.
- `03-production-hardening.md` - *making it real*: the benchmark, scaling, and security model (summarized in §5 below).
- `resources.md` - the annotated literature/tool library (each entry: URL + what-to-read + where-it-maps).

---

## 1. Ground truth I verified (July 2026) - and how it corrects the earlier notes

The earlier `more.md`/`less.md` research carried heavy "unverified" caveats. These are now resolved
against DataHub's live docs, GitHub, and blog:

| Open question | Verified answer | Consequence |
|---|---|---|
| Can **open-source** DataHub raise **incidents** programmatically? | **Yes.** `raiseIncident` GraphQL mutation is explicitly available in OSS/self-hosted, designed to be called from Airflow/Prefect/Dagster DAGs. Python SDK wrapper is "coming soon" → call via `graph.execute_graphql()`. | The richest write-back primitive is **OSS-native**. The incident loop is real. |
| Can OSS **create assertions**? | **Partially.** OSS ships the **Open Data Quality Assertions Spec + Compiler** (YAML → dbt tests / Great Expectations / Snowflake DMFs) and assertion *entities*. `sync_smart_*` / anomaly "smart" assertions are **Cloud-only**. | Ship "guarding assertions" as portable **open-assertions YAML** + assertion entities; do detection ourselves. Disclose the Cloud boundary. |
| Does the **MCP server** expose assertion / incident / lineage-**write** tools? | **No** (v0.6.0, May 18 2026). Mutations = tags, terms, owners, **domains**, **structured properties**, descriptions, `save_document`. | **Verified gap = our bonus contribution:** a `raise_incident` / `create_assertion` MCP mutation tool, or a skill wrapping the GraphQL. |
| Does an **ML skill** exist in `datahub-skills`? | **No, as verified in early July 2026.** Repo had setup, search, lineage, enrich, quality, connector-planning, connector-pr-review, load-standards. **Zero ML skills** at that time. **[No longer true, D-043]:** by 2026-07-21 roughly seven overlapping ML-reliability skill PRs were open upstream (drift, trust-score, leakage, RCA), several predating our own submission. | `datahub-ml-guard` was pursued as the **first ML skill in the registry** - that framing did not hold; see D-043 for the differentiator actually shipped (a tested deterministic engine, not a prompt-only skill). |
| Is "silent ML failure" real / on-message? | **DataHub's own June-2026 blog** frames it verbatim: *silent data failures*, *target leakage* ("needs column-level lineage"), *"$250,000 lost in a single weekend because null values were misread as zeros,"* *"lineage graphs that stop at the warehouse boundary."* | Our villain is **DataHub's own stated problem.** |
| Is incident-triage-via-MCP already "taken"? | **Partly.** Block runs DataHub MCP + Goose for incident response; Nebius hackathon winner "MediGuard" did healthcare quality guardrails. | Don't ship a generic incident *chatbot*. Differentiate on **ML-boundary crossing** + **preventive** checks. |

---

## 2. Thesis: the ML supply chain has no CI - and DataHub is the only system that can build it

Software has CI: every change runs tests before it ships. **The ML data supply chain has nothing
equivalent.** Data-quality tooling stops at the warehouse edge; ML monitoring starts at the model.
The dangerous middle - features derived from tables, a schema change three hops upstream, nulls read
as zeros - is exactly where **silent failures** live.

DataHub is the **only** system holding *both* graphs in one place: the warehouse lineage *and* the ML
lineage (`Dataset → Training Run → Model → Deployment`, with column-level `DerivedFrom` from features to
source columns). ModelGuard turns that unified graph into the missing CI - the "from documenting the
enterprise to **operating** it" direction DataHub's VP of Product has publicly staked the roadmap on.

---

## 3. Four real problems it solves - deeply, each grounded in literature

Each problem uses a *different* slice of the graph, maps to a *distinct* write-back, and has a *named*
citation. They share one engine (cross-boundary lineage), so they compose one coherent product rather
than four taped-together demos.

### Problem 1 - Target leakage (preventive, catastrophic, novel to detect via the graph)
- **Failure:** a feature is derived - directly or transitively - from the label's source column (or a
  post-outcome column). Model looks brilliant offline, collapses in production.
- **Mechanism:** static analysis over **column-level lineage**. For each model, walk
  `Model → Features → (sources) → source columns`; flag any feature whose upstream column-cone intersects
  the label column's cone, or crosses a temporal boundary. **Deterministic graph reasoning - no training
  needed - and almost nobody exploits column-level lineage this way.**
- **Write-back:** `leakage-risk` glossary term on the feature + structured property on the model + an
  incident naming the exact offending column path.
- **Literature:** Kaufman et al. 2012, *Leakage in Data Mining* (ACM TKDD). DataHub's own blog names this
  as *the* case that "needs column-level lineage."

### Problem 2 - Silent upstream data failure → model blast radius (reactive, the core loop)
- **Failure:** a freshness/volume/schema/null-rate issue on a source table silently propagates into
  features and live model scoring. Nobody notices until revenue/accuracy bleeds.
- **Mechanism:** detect (assertion result / schema-history diff / profile drift) → traverse lineage
  **across the warehouse→ML boundary** to enumerate every `mlModel` and **live `mlModelDeployment`** in the
  blast radius → rank severity by deployment status + ownership + fan-out.
- **Write-back:** `raiseIncident` on the affected model/deployment (OSS-verified), `model-at-risk` tag,
  structured-property risk flag, and a **guarding assertion** so it's auto-caught next time.
- **Literature:** Sculley et al. 2015, *Hidden Technical Debt in ML Systems* (NeurIPS) - undeclared
  consumers, data dependencies, CACE; Sambasivan et al. 2021, *Data Cascades* (CHI).

### Problem 3 - Training/serving lineage divergence (schema drift the model can't see)
- **Failure:** the schema a model was **trained on** silently diverges from the current schema of the same
  source (renamed/retyped/dropped column) → training-serving skew.
- **Mechanism:** compare the **training run's input dataset schema snapshot** against the source's
  **current** `schemaMetadata` (via the Timeline/Schema-History API). Diff → flag drifted inputs per model.
- **Write-back:** `input-schema-drift` incident + structured property recording drifted fields + the
  training-run URN.
- **Literature:** Breck et al. 2019, *Data Validation for ML* (MLSys / TFDV); Polyzotis et al. 2017,
  *Data Management Challenges in Production ML*.

### Problem 4 - Model trust opacity / undeclared consumers (org-level governance)
- **Failure:** no one can see, in the catalog, whether a model's *inputs* are currently healthy. Risk is
  invisible until it's an incident.
- **Mechanism:** roll problems 1-3 into a per-model **Model Trust Score** =
  f(upstream assertion health, freshness lag, leakage flags, schema-drift flags, deployment exposure),
  continuously recomputed.
- **Write-back:** score + rationale as **structured properties on the `mlModel` / model card**
  (`intendedUse`, factors) + a human-readable **Model Impact Report** saved as a DataHub **Knowledge
  Document** (`save_document`). The catalog itself now shows model risk.
- **Literature:** Mitchell et al. 2019, *Model Cards for Model Reporting* (FAT\*); SR 11-7 (Fed model-risk
  guidance) for the finance framing.

---

## 4. The scoring rubric, read as a spec

Stage 1 = pass/fail viability (theme fit + real SDK use). Stage 2 = **five equally-weighted** criteria +
bonus. **Tie-breaks walk the criteria in listed order → "Use of DataHub" breaks ties first → over-invest
there.**

| Criterion (equal weight) | What scores | How ModelGuard maxes it |
|---|---|---|
| **1. Use of DataHub** *(1st tie-breaker)* | "Beyond reading" → **contribute back to the graph**; lineage, ownership, ML metadata, governance, MCP/ACK/Skills. | Reads the deepest, least-used slice: **column-level ML lineage across the warehouse boundary.** Writes back **6+** primitive types: incidents, structured properties, tags, glossary terms, model-card fields, knowledge docs, guarding assertions. Maximal surface area on the highest-value criterion. |
| **2. Technical Execution** | Works **end-to-end**, robust; code does what the claim says. | Deterministic detection (graph analysis + schema diff); LLM only for reasoning/narrative + human-gated writes. Runs on **local Quickstart + seeded ML graph + planted-issue datapack** → judges rerun, same result. **Measured, not asserted:** ModelGuard-Bench reports precision/recall/MTTD and **beats no-lineage baselines** (Great Expectations, Evidently). Idempotent write-back; batch **and** event-driven. One-command `quickstart.sh`. (§5) |
| **3. Originality** | Beyond out-of-the-box; compose, don't rebuild. | Not text-to-SQL (Analytics Agent), not PII tagging (`enrich`), not a chat triage bot (Block/Goose). **Target-leakage-as-static-graph-analysis** and **training-run-schema-diffing** are genuinely novel. README has an explicit "What we did NOT rebuild" section. |
| **4. Real-World Usefulness** | Would a real data/ML team see value? | Framed in **money and safety** using DataHub's own numbers ("$250K in a weekend"), a finance credit-risk model scoring live loans (SR 11-7), or a healthcare readmission model. **Metadata-only - no PHI/PII ever reaches the LLM** (a real deploy-blocker removed). SRE-framed (MTTD SLO, blameless impact reports). Excites the **Pinterest** and **Cloudflight** judges. |
| **5. Submission Quality** | ≤3-min video, README, clear setup. | Cold-open on the pain → agent catches it live → close on the **write-back visible in the DataHub UI**. README with architecture diagram, "reads AND writes" table, `examples/` folder of generated reports + assertion YAML + the Skill. |
| **Bonus: OSS contribution** | New connectors, **skills**, fixes, RFCs, docs. | `datahub-ml-guard`: not first (the gap closed while we built, D-043), differentiated by a tested deterministic engine behind it. Plus a `raise_incident` MCP mutation-tool PR (gap verified) and an RFC for first-class "ML incident" workflow. |
| **Stage-1 gate** | Theme fit + real SDK use. | A new Skill (`datahub-ml-guard`) and an MCP server of its own, both against real ML lineage, plus a merged-or-open PR to DataHub's own MCP server - unambiguous pass. The Agent Context Kit is read from (agent/context_kit.py) but cannot be *installed* beside this project: every release from 1.6.0.6 on pins acryl-datahub==1.6.0.6 against this project's 1.6.0.13, measured, reported as feedback #16 (D-135). The rule needs one of the four, and the Skill is the one that is unconditionally true. |

**Prize math:** Grand Prize = best overall; Challenge Winners = one per category. Categories 2 & 3 are the
thinnest fields. ModelGuard sits in **category 3** with Grand-Prize-quality build → strong EV on both the
category prize and the Grand Prize, and its write-back depth wins tie-breaks.

---

## 5. Production-grade posture - what turns a demo into a winner

Most entries stop at "it ran once in the video." ModelGuard is engineered - and *measured* - like a system a
real platform team would run. That is disproportionate scoring leverage on **Technical Execution** and
**Real-World Usefulness** (both equally weighted), and it's exactly what this judge panel (a data architect,
a Pinterest EM, DataHub PMs) recognizes. Full detail in `03-production-hardening.md`; the strategic headlines:

- **Measured, not asserted - "ModelGuard-Bench."** Detectors are scored against ground truth built from
  DataHub's planted-issue datapacks + **Jenga** corruption injection + synthetic leakage/drift. We report
  precision / recall / F1, MTTD, blast-radius recall, and false-positive rate, and publish
  `benchmarks/RESULTS.md`. Eval design borrows the perturbation grid from *Failing Loudly* (NeurIPS 2019).
- **Beats the obvious baselines.** The same scenarios run through Great Expectations/Deequ (table quality, no
  lineage) and Evidently/NannyML (drift only, *after* the fact). The money slide: **only** cross-boundary
  column-level lineage both roots the failure to the exact upstream column **and** names the exact model +
  live deployment - *before* it scores.
- **Scales two ways from one core.** `scan` (batch/CI) and `watch` (interval polling,
  shipped; event-driven via DataHub's MetadataChangeLog / Kafka is the documented
  upgrade path) share the identical detect→write engine. At-least-once + idempotent
  writes (dedup key `(urn, finding, run_id)`) = effectively-once; bounded traversal +
  batched reads keep cost flat. Grounded in *Designing Data-Intensive Applications*
  (Ch 11-12).
- **Secure by design - it writes to a governance graph.** Deterministic detection is prompt-injection-
  resistant (OWASP **LLM01**); every mutation is human-gated, least-privilege-scoped, and parameterized
  (OWASP **LLM06/LLM05**). Quotable privacy property: **ModelGuard reads metadata + profiles, never raw
  rows - no PHI/PII ever reaches the LLM** (removes a real finance/healthcare deploy-blocker).
- **Operable & SRE-framed.** OpenTelemetry traces + Prometheus metrics; impact reports *are* blameless
  postmortems; a stated MTTD SLO. This is the bar *Reliable Machine Learning* (O'Reilly) sets.

**How it works, in one glance** (diagrams in `architecture.md`): a six-layer pipeline -
**Trigger → deterministic Detection → LangGraph Orchestration (human-gated) → gated LLM Reasoning →
idempotent Write-back → cross-cutting security/observability** - reading column-level + ML lineage and
writing incidents, structured properties, tags, documents, and guarding assertions back into DataHub.

---

## 6. What NOT to build (already shipped - Originality penalty)

- Text-to-SQL / "chat with your catalog" → **Analytics Agent** already does this (Apr 30 2026, OSS).
- Auto-describe tables / PII tagging steward → **`datahub-enrich`** skill already does this.
- Generic incident-triage chatbot → **Block + Goose** and the Nebius "Agent On Call" winner already did this.
- A dashboard over DataHub metadata → **Data Health Dashboard** is native.

Compose and extend these; do not reimplement them.

---

## 7. The one real risk + the fallback

- **Week-1 kill-criterion:** the sample datapacks are warehouse/BI-centric - **you must seed the ML graph
  yourself.** By end of Week 1 you must be able to (a) read column-level ML lineage and (b) write one
  incident + one structured property back. This is the whole risk; budget it explicitly.
- **If ML seeding stalls → fall back to MigrationCopilot** (Category 2 codegen): PR-ready dbt/DAG
  artifacts from real schemas + column-level lineage, same write-back philosophy, **no ML-graph seeding
  required.** Graceful degradation, still a strong entry.
- **Cloud honesty:** smart/anomaly assertions + monitoring UI are Cloud. Build detection on OSS primitives
  and **disclose it** - judges reward candor, penalize hidden Cloud dependencies.
- **Scope discipline:** ship **one bulletproof loop** (Problem 2) first; add 1, 3, 4 as they harden.

---

## Sources

- MCP Server: [docs](https://docs.datahub.com/docs/features/feature-guides/mcp) · [repo v0.6.0](https://github.com/acryldata/mcp-server-datahub) · [Agents in Production blog](https://datahub.com/blog/agents-in-production-datahub-mcp/)
- Skills: [docs](https://docs.datahub.com/docs/dev-guides/agent-context/skills) · [datahub-skills repo](https://github.com/datahub-project/datahub-skills) · [Skills Registry announcement](https://datahub.com/blog/datahub-open-source-skills-registry/)
- Agent Context Kit: [docs](https://docs.datahub.com/docs/dev-guides/agent-context/agent-context) · [Building Autonomous Data Agents](https://datahub.com/blog/building-autonomous-data-agents/) · [Google ADK](https://docs.datahub.com/docs/dev-guides/agent-context/google-adk)
- ML lineage: [Data Lineage for ML blog ("$250K"/target leakage)](https://datahub.com/blog/data-lineage-for-ml/) · [MLModel entity](https://docs.datahub.com/docs/generated/metamodel/entities/mlmodel/) · [MLFeature entity](https://docs.datahub.com/docs/generated/metamodel/entities/mlfeature) · [MLflow ingestion](https://docs.datahub.com/docs/generated/ingestion/sources/mlflow) · [AI/ML tutorial](https://docs.datahub.com/docs/api/tutorials/ml)
- Write-back: [Incidents API (OSS `raiseIncident`)](https://docs.datahub.com/docs/api/tutorials/incidents) · [GraphQL mutations](https://docs.datahub.com/docs/graphql/mutations) · [Open Assertions Spec](https://docs.datahub.com/docs/assertions/open-assertions-spec) · [Structured Properties](https://docs.datahub.com/docs/api/tutorials/structured-properties)
- Core literature: Breck et al. 2019 *Data Validation for ML* · Sculley et al. 2015 *Hidden Technical Debt in ML Systems* (NeurIPS) · Sambasivan et al. 2021 *Data Cascades* (CHI) · Kaufman et al. 2012 *Leakage in Data Mining* (ACM TKDD) · Mitchell et al. 2019 *Model Cards for Model Reporting* (FAT\*)
- Production-grade grounding: Grafberger & Schelter 2021 *mlinspect* (SIGMOD) · Schelter et al. 2021 *Jenga* (EDBT) · Rabanser et al. 2019 *Failing Loudly* (NeurIPS) · Kleppmann *Designing Data-Intensive Applications* (Ch 11-12) · Chen et al. 2022 *Reliable Machine Learning* (O'Reilly) · OWASP *Top 10 for LLM Applications 2025* · MITRE ATLAS
- **Full annotated library** (every source with URL + what-to-read + where-it-maps): `resources.md`. **Architecture & diagrams:** `architecture.md`. **Benchmark/scaling/security detail:** `03-production-hardening.md`.
