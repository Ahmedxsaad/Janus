# ModelGuard - Implementation Plan

> Companion to `01-strategy-modelguard.md`. This is the buildable blueprint: environment, architecture,
> concrete code, the ML-graph seeder (the #1 de-risker), the four detectors, the write-back layer, the
> agent, the OSS contribution, verification, demo, and a submission checklist mapped to the rubric.
>
> **Convention:** [verified] = verified against DataHub docs/repo (July 2026). [confirm] = exact symbol/behavior to
> confirm at build time (imports drift between package versions - always `pip show <pkg>` then introspect).
> Never trust a snippet here over the installed package's own signatures.
>
> **Companion docs:** `architecture.md` (how it works - diagrams + component catalog), `resources.md`
> (annotated papers/tools/architectures to borrow from), and `03-production-hardening.md` (the benchmark,
> scaling, and security model). Read `architecture.md` first for the mental model, then §9-§11 here.

---

## 0. Architecture at a glance

```
                 ┌──────────────────────────────────────────────────┐
   TRIGGERS      │  DataHub (local Quickstart, OSS, localhost:9002)  │
   • assertion   │   warehouse graph  +  ML graph (WE seed this)     │
     result      │   Dataset → TrainingRun → Model → Deployment       │
   • schema Δ    │   Feature → (sources) → source columns (col-level) │
   • profile     └───────────────▲───────────────────┬───────────────┘
     drift          reads (MCP /  │                   │  writes (GraphQL / SDK /
   • cron scan      Agent Ctx Kit)│                   │  MCP mutations / assertions YAML)
        │                         │                   ▼
        │            ┌────────────┴──────────────────────────────────┐
        └──────────► │  ModelGuard  (LangGraph orchestrator)          │
                     │   1. Detect      (deterministic, Python)       │
                     │   2. Investigate (traverse blast radius)       │
                     │   3. Reason+Score(LLM, human-gated)            │
                     │   4. Write back  (idempotent, upsert-by-URN)   │
                     └───────────────────────────────────────────────┘

WRITE-BACK PRIMITIVES (all OSS): raiseIncident [verified] · structured properties [verified] · tags/terms/owners [verified]
· knowledge documents (save_document) [verified] · guarding assertions as open-assertions YAML + entities [verified]
```

**Design law:** *detection is deterministic Python; the LLM does reasoning, ranking narrative, report
prose, and gates every write behind human approval.* This is what makes it robust **and** matches DataHub's
"no black box" ethos that judges reward.

---

## 1. Environment & prerequisites

```bash
# Python 3.11 exactly. acryl-datahub's classifiers advertise 3.12, but its CLI
# warns at runtime: "Python versions above 3.11 are not actively tested with
# yet." Pinned in .python-version and pyproject.toml (see D-011).   [verified]
python3 -m pip install --upgrade pip wheel setuptools
python3 -m pip install --upgrade acryl-datahub                  # CLI + Python SDK   [verified]
datahub version

# Start local DataHub (needs Docker + ~2 CPUs / 8GB free)       [verified]
datahub docker quickstart                                       # UI at http://localhost:9002
# login: datahub / datahub   ·   GMS API at http://localhost:8080

# Point the CLI at local, then load a rich sample graph
datahub init --username datahub --password datahub              [verified]
datahub datapack load showcase-ecommerce                        # ~1,050 cross-platform entities  [verified]
#   optional planted-issue packs for a deterministic "villain":
#   nyc-taxi   (planted freshness issues)   ·   healthcare (planted quality issues)

# Reset if wedged
datahub docker quickstart --stop      #   or full wipe:  datahub docker nuke     [verified]
```

**Project Python deps** (`pyproject.toml`, exact pins; `requirements.txt` was
retired in D-010):
```
acryl-datahub[datahub-rest]==1.6.0.13   # SDK + REST emitter               [verified]
pydantic  python-dotenv  pyyaml  rich  typer   # models, env, YAML, CLI    [verified]
# optional extra "agent" (unpinned until Phase 3 installs them):
langgraph  langchain  langchain-anthropic      # orchestration + Claude
datahub-agent-context                          # Agent Context Kit toolset  [confirm]
# optional extra "dev":
pytest  ruff  mypy  pre-commit                                             [verified]
```

**LLM:** BYO. Default to **Claude `claude-opus-4-8`** via `langchain-anthropic` (`ANTHROPIC_API_KEY`).
DataHub's own examples use `ChatOpenAI`, but any tool-calling model works - Claude is the strongest default.

**Secrets** (`.env`, git-ignored):
```
DATAHUB_GMS_URL=http://localhost:8080
DATAHUB_GMS_TOKEN=            # generate in UI: Settings → Access Tokens (needed for write ops)
ANTHROPIC_API_KEY=
TOOLS_IS_MUTATION_ENABLED=true   # required to expose MCP write tools
```

---

## 2. Repository layout (Apache-2.0 from commit #1)

```
modelguard/
├── LICENSE                      # Apache 2.0 - set repo License in GitHub "About" (judging requirement) [verified]
├── README.md                    # architecture diagram + "reads AND writes" table + "what we did NOT rebuild"
├── quickstart.sh                # one command: boot DataHub → load datapack → seed ML graph → run agent
├── pyproject.toml               # pinned deps + ruff/mypy/pytest config (replaced requirements.txt)
├── .env.example
├── modelguard/
│   ├── client.py                # DataHubClient / DataHubGraph factory, env config
│   ├── seed/                    # THE DE-RISKER - builds the ML graph the datapacks lack
│   │   ├── graph_spec.py        # fixed URNs + values: the single source of truth for the seeded graph
│   │   ├── seed_ml_graph.py     # models, features, training runs, deployments, col-level lineage
│   │   └── scenarios.py         # inject the planted failure(s) for the demo (Phase 1)
│   ├── detect/                  # deterministic detectors (one per Problem 1-4)
│   │   ├── leakage.py           # P1 target leakage (column-cone intersection)
│   │   ├── blast_radius.py      # P2 upstream failure → models/deployments at risk
│   │   ├── schema_drift.py      # P3 training-vs-current schema diff
│   │   └── trust_score.py       # P4 aggregate model trust score
│   ├── writeback/               # idempotent graph mutations
│   │   ├── incidents.py         # raiseIncident / updateIncidentStatus via execute_graphql
│   │   ├── properties.py        # structured properties define + assign
│   │   ├── labels.py            # tags / glossary terms / owners
│   │   ├── documents.py         # Model Impact Report → knowledge document
│   │   └── assertions.py        # guarding assertions → open-assertions YAML + entity
│   ├── agent/
│   │   ├── graph.py             # LangGraph: detect → investigate → reason → (approve) → writeback
│   │   └── tools.py             # datahub-agent-context toolset + custom write tools
│   └── cli.py                   # `modelguard scan` / `modelguard watch` (Typer)
├── skill/                       # OSS contribution → PR to datahub-project/datahub-skills
│   └── datahub-ml-guard/
│       ├── SKILL.md
│       ├── references/
│       └── scripts/
├── mcp_ext/                     # OSS contribution → PR to acryldata/mcp-server-datahub (stretch)
│   └── raise_incident_tool.py
├── examples/                    # SAMPLE OUTPUTS for judges (no run needed) - recommended by rules [verified]
│   ├── impact-report-credit-risk-model.md
│   ├── guarding-assertion-payments.yml
│   ├── input-data-contract.odcs.yaml   # ODCS contract emitted for a model's inputs (see §6.5)
│   └── incident-payload.json
├── benchmarks/                  # "ModelGuard-Bench" - see 03-production-hardening.md §A
│   ├── inject.py                # Jenga-based corruption + leakage/drift injection
│   ├── run_bench.py             # precision/recall/F1, MTTD, baselines, scale curve
│   ├── golden/                  # golden impact reports for regression diffing
│   └── RESULTS.md               # metrics table + baseline comparison (Great Expectations/Evidently)
└── tests/
```

---

## 3. Phase 0 - Week 1: environment + the de-risker (kill-criterion)

**Goal by end of Week 1 - a hard gate:** you can (a) programmatically read column-level ML lineage AND
(b) write one incident + one structured property back. If ML seeding stalls, **pivot to MigrationCopilot**
(see §11).

### 3.1 The ML-graph seeder (`seed/seed_ml_graph.py`)

The datapacks are warehouse/BI-centric; they contain **no** ML entities. We seed a small but complete ML
supply chain **on top of an existing datapack table** so real column-level lineage exists into the model.

Actual SDK surface, introspected from **acryl-datahub 1.6.0.13** [verified]. The snippet this section
originally carried was wrong on four symbols; see D-012. Implemented in `modelguard/seed/seed_ml_graph.py`.

```python
from datahub.sdk.main_client import DataHubClient
from datahub.sdk.mlmodel import MLModel                 # NOT datahub.sdk.ml_entities
from datahub.sdk.mlmodelgroup import MLModelGroup
from datahub.metadata.urns import DatasetUrn, DataProcessInstanceUrn

client = DataHubClient(graph=graph)   # or DataHubClient.from_env()

# 1) Model group + model. There is no MLModel.add_group and no client._emit_mcps.
group = MLModelGroup(id="credit_risk_models", platform="mlflow", name="Credit Risk Models")
client.entities.upsert(group)                                        # [verified] idempotent

model = MLModel(id="credit_risk_v3", platform="mlflow", name="Credit Risk v3",
                version="3", aliases=["champion"],
                hyper_params={"max_depth": "6"},                     # dict or [MLHyperParamClass]
                training_metrics={"auc": "0.88"},
                model_group=group.urn,                               # [verified] constructor arg
                training_jobs=[DataProcessInstanceUrn("credit_risk_v3_run")])
model.add_deployment(str(deployment_urn))                            # [verified]
client.entities.upsert(model)

# 2) Training run. client.create_training_run does not exist. Emit a
#    DataProcessInstance carrying mlTrainingRunProperties + dataProcessInstanceInput.
graph.emit_mcps([
    MetadataChangeProposalWrapper(entityUrn=run_urn, aspect=DataProcessInstancePropertiesClass(...)),
    MetadataChangeProposalWrapper(entityUrn=run_urn, aspect=SubTypesClass(["MLFLOW_TRAINING_RUN"])),
    MetadataChangeProposalWrapper(entityUrn=run_urn, aspect=MLTrainingRunPropertiesClass(...)),
    # client.add_input_datasets_to_run does not exist either:
    MetadataChangeProposalWrapper(entityUrn=run_urn,
        aspect=DataProcessInstanceInputClass(inputs=[str(train_tbl)])),
])
```

**Features + feature table + primary key + deployment.** The SDK has **no entity classes** for these
[verified]. Emit their aspects directly: `MLFeaturePropertiesClass`, `MLPrimaryKeyPropertiesClass`,
`MLFeatureTablePropertiesClass(mlFeatures=, mlPrimaryKeys=)`, `MLModelDeploymentPropertiesClass(status=)`.

> **`MLFeatureProperties.sources` is dataset-granular, not column-granular** [verified]. Its relationship
> declares `entityTypes: [dataset]`, so a feature may point at the dataset it derives from but **not** at a
> `schemaField`. Pointing it at a column URN creates a dangling edge. ModelGuard therefore records the exact
> source column in the feature's `customProperties` under `modelguard.source_column`. Problem 1's traversal
> starts from that column, not from `sources`.

**Attaching features to the model:** `MLModel` exposes no `mlFeatures` API. Upsert the model first, then read
`mlModelProperties` back, set `mlFeatures`, and re-emit. Order matters: an upsert replaces the whole aspect.

> **Column-level lineage is Dataset→Dataset only** [verified]. Feature→dataset lineage is expressed via the ML
> `sources` aspect, not `add_lineage`. So model-boundary traversal mixes: `add_lineage` (dataset↔dataset,
> column-level) **plus** ML-entity relationship edges (`sources`, `Consumes`, `trainingJobs`, deployment).
> Confirm in Week 1 that `get_lineage` traverses across ML entities; if it stops at the dataset boundary,
> fall back to GraphQL `scrollAcrossLineage` / `relationships` to cross into ML land.

### 3.2 Column-level lineage into the warehouse (`seed`, continued)

Give the seeded feature table real column lineage from a datapack source table so leakage/blast-radius
have something to traverse (verified SDK [verified]):

```python
client.lineage.add_lineage(
    upstream=DatasetUrn(platform="snowflake", name="ecommerce.public.loans_raw"),
    downstream=DatasetUrn(platform="snowflake", name="ecommerce.public.customer_features"),
    column_lineage={                       # strict, explicit mapping
        "applicant_income": ["income"],
        "prior_default_flag": ["default_status"],   # ← this will be our planted leakage column
    },
)
```

### 3.3 Prove write-back works (both primitives) - the gate

```python
# INCIDENT (OSS-native, via GraphQL; Python SDK "coming soon") [verified]
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
graph = DataHubGraph(DatahubClientConfig(server="http://localhost:8080", token=TOKEN))
graph.execute_graphql("""
mutation { raiseIncident(input:{
  resourceUrn:"urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)",
  type: OPERATIONAL, title:"ModelGuard smoke test",
  description:"If you can read this in the UI, write-back works." }) }
""")   # returns the new incident URN
```
```bash
# STRUCTURED PROPERTY definition (CLI) [verified] - then assign via GraphQL/OpenAPI (§6.2)
datahub properties upsert -f modelguard/writeback/props/modelguard_props.yaml
```
Open `http://localhost:9002`, find the model → **the incident + property must be visible.** That's the gate.

---

## 4. Phase 1 - Week 2: the core loop (Problem 2, end to end)

Build ONE bulletproof path first: **detect upstream failure → blast radius → write incident + tag +
guarding assertion → impact report.**

### 4.1 Detector - blast radius (`detect/blast_radius.py`)

```python
from datahub.sdk import DataHubClient
from datahub.sdk.search_filters import FilterDsl as F    # [verified]

def models_at_risk(client, failing_table_urn):
    """Everything downstream of a failing table that is (or feeds) a live model/deployment."""
    dl = client.lineage.get_lineage(                       # [verified] traverse downstream
        source_urn=failing_table_urn, direction="downstream", max_hops=5,
        filter=F.entity_type("mlModel"),                   # [confirm] confirm ML entity_type filter names
    )
    # rank: live deployment > staging; more downstream fan-out = higher severity; owner presence
    return rank_by_severity(dl)
```

Detection triggers (any of, deterministic):
- **assertion result = FAIL** (read via GraphQL / assertion entity),
- **freshness lag** > threshold (dataset `operation`/`lastModified` vs now),
- **null-rate / volume spike** from dataset profile (`datasetProfile` aspect),
- **planted issue** from `seed/scenarios.py` for the demo.

### 4.2 Write-back (`writeback/incidents.py`, `labels.py`, `assertions.py`, `documents.py`)

- **Incident** on each at-risk model/deployment - `raiseIncident` (`type` ∈ `OPERATIONAL, FRESHNESS,
  VOLUME, COLUMN, SQL, DATA_SCHEMA, CUSTOM` [verified]), title/description generated by the LLM from the traversal.
- **Tag** `model-at-risk` + **glossary term** on the model (MCP `add_tags`/`add_terms` [verified], or SDK).
- **Guarding assertion** on the offending upstream table as **open-assertions YAML** (verified format [verified]):
  ```yaml
  version: 1
  assertions:
    - entity: urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)
      type: freshness
      lookback_interval: "6 hours"
      last_modified_field: updated_at
      schedule: { type: interval, interval: "6 hours" }
  ```
  Emit it as an artifact in `examples/` **and** create the assertion entity via the SDK so it appears in the
  Quality tab. (Smart/anomaly *monitoring* is Cloud - say so; we provide the check logic ourselves.)
- **Model Impact Report** → knowledge **document** (`save_document` [verified]) linked to the model.

**Idempotency:** stamp every write with a `modelguard.run_id` structured property and upsert by
`(resourceUrn, run_id)` so reruns don't duplicate incidents. Judges notice this; it reads as production-grade.

### 4.3 Verify the loop
`modelguard scan --table loans_raw` → incident + tag + assertion + report all visible in the UI, same result
every run. Use the `/verify` skill / drive it end-to-end before moving on.

---

## 5. Phase 2 - Week 3: the three differentiators (Problems 1, 3, 4)

### 5.1 P1 - Target-leakage detector (`detect/leakage.py`) - the most original piece
```python
def leakage_findings(client, model_urn, label_source_column_urn):
    features = model_features(client, model_urn)                 # mlModelProperties.mlFeatures
    findings = []
    for feat in features:
        # The feature's exact source column comes from customProperties, NOT from
        # `sources`, which is dataset-granular (see section 3.1).
        col = feature_source_column(client, feat)                # modelguard.source_column
        cone = client.lineage.get_lineage(                        # [verified] column-level, upstream
            source_urn=col.dataset, source_column=col.field,
            direction="upstream", max_hops=6)
        if intersects(cone, label_source_column_urn):
            findings.append((feat, col, "derives from label source"))
    return findings
```
- **Label column** is declared once (glossary term `label` or a structured property on the training dataset).
- Also flag **temporal leakage**: feature derived from a column produced *after* the prediction timestamp.
- **Write-back:** `leakage-risk` term on the feature + structured property on the model + a `FIELD` incident
  (there is no `COLUMN` incident type, see section 6.1) quoting the exact `feature -> ... -> label` column
  path. Deterministic, no training required.
- **Cite** Kaufman et al. 2012 in the report prose.

### 5.2 P3 - Training/serving schema drift (`detect/schema_drift.py`)
- Read the training run's **input dataset schema as-of the run timestamp** vs the source's **current**
  `schemaMetadata`, using the **Timeline / Schema-History API** [verified]:
  ```bash
  datahub timeline --urn "urn:li:dataset:(...loans_raw,PROD)" --category TECHNICAL_SCHEMA
  ```
  (or the OpenAPI timeline endpoint). Diff added/removed/retyped columns.
- **Write-back:** `input-schema-drift` incident + structured property `{drifted_fields, training_run_urn}`.
- **Cite** Breck et al. 2019 (training-serving skew).

### 5.3 P4 - Model Trust Score (`detect/trust_score.py`)
```
trust = 100
  − 40·(upstream_assertion_failing)
  − 20·(has_leakage_finding)
  − 15·(has_schema_drift)
  − 15·(freshness_lag_hours / SLA_hours, capped)
  − 10·(missing_owner)      # weights are illustrative; expose in config
score ∈ [0,100] → band: healthy / watch / at-risk
```
- **Write-back:** `modelguard.trust_score` (number) + `modelguard.risk_flags` (multiple string) as
  **structured properties on the mlModel / model card**, plus a rollup **Model Impact Report** document.
- **Cite** Sculley et al. 2015 (undeclared consumers) + Mitchell et al. 2019 (model cards).

---

## 6. Write-back reference (exact, verified)

### 6.1 Incidents [verified] (`graph.execute_graphql`)
```graphql
mutation { raiseIncident(input:{ resourceUrn:"<urn>", type: FRESHNESS,
  title:"...", description:"..." }) }                       # → returns new incident urn
mutation { updateIncidentStatus(urn:"<incident urn>", input:{ state: RESOLVED, message:"..." }) }  # → true
```
Types (from `IncidentTypeClass` in the installed model [verified]):
`OPERATIONAL, FRESHNESS, VOLUME, FIELD, SQL, DATA_SCHEMA, CUSTOM`.
There is **no `COLUMN` type**; the column-scoped one is **`FIELD`** (D-012).

**Dedup:** an incident is keyed by `(resourceUrn, type, title)` over the resource's *active* incidents, read
from its `incidentsSummary` aspect. `run_id` is provenance in the description, not part of the key: it changes
every run, so including it would duplicate every finding on every scan (D-013).

### 6.2 Structured properties [verified]
Define (YAML → `datahub properties upsert -f props.yaml`):
```yaml
- id: modelguard.trust_score
  qualified_name: modelguard.trust_score
  type: number
  cardinality: SINGLE
  display_name: ModelGuard Trust Score
  entity_types: [mlModel]
- id: modelguard.risk_flags
  qualified_name: modelguard.risk_flags
  type: string
  cardinality: MULTIPLE
  display_name: ModelGuard Risk Flags
  entity_types: [mlModel]
```
Assign (GraphQL):
```graphql
mutation { upsertStructuredProperties(input:{
  assetUrn:"<mlModel urn>",
  structuredPropertyInputParams:[
    { structuredPropertyUrn:"urn:li:structuredProperty:modelguard.trust_score",
      values:[{ numberValue: 62 }] }]}) { properties { structuredProperty { urn } } } }
```
(or OpenAPI v3 `POST /openapi/v3/entity/mlModel/<url-encoded-urn>/structuredProperties`.) [verified]

### 6.3 Tags / terms / owners / domains / descriptions
MCP mutation tools (`add_tags`, `add_terms`, `add_owners`, `set_domains`, `add_structured_properties`,
`update_description`) - need `TOOLS_IS_MUTATION_ENABLED=true` [verified]. From deterministic scripts, use the SDK
PATCH builders instead of the agent so writes are reproducible in tests.

### 6.4 Knowledge documents [verified]
`save_document` (MCP write tool) attaches the Model Impact Report to the model; verify placement with
`search_documents` / `grep_documents`.

### 6.5 Data contract artifact (ODCS) - high-value extra write-back
For a model's input tables, emit an **Open Data Contract Standard (ODCS v3.1.0)** YAML capturing the schema
+ freshness/volume/quality expectations ModelGuard derived, and validate it with `datacontract-cli` before
committing it to `examples/`. This makes the "contract for the ML boundary" tangible and standards-based
(Linux Foundation Bitol, Apache-2.0). See `resources.md §6`. Optional but a strong Originality + Usefulness signal.

---

## 7. The agent (`agent/graph.py`) - LangGraph over the Agent Context Kit

`datahub-agent-context` gives the read toolset (`search`, `get_entities`, `get_lineage`,
`list_schema_fields`, `get_dataset_queries`, `search_documents`, `grep_documents`) + mutations
(`add_tags`, `update_description`, `add_glossary_terms`, `set_domains`, `add_owners`, `save_document`) [verified].
Incidents/assertions are **not** in the toolset → add them as **custom LangChain tools** wrapping our
`writeback/` functions.

[confirm] **Verify exact import symbols** (`pip show datahub-agent-context` then introspect - the public blog's
snippet was OCR-garbled). Approximate shape:
```python
from datahub.sdk import DataHubClient
from datahub_agent_context.langchain_tools import build_langchain_tools   # [confirm] confirm name
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate

client = DataHubClient.from_env()
tools = build_langchain_tools(client, include_mutations=True) + custom_writeback_tools(client)
llm = ChatAnthropic(model="claude-opus-4-8", temperature=0)
```

**LangGraph state machine** (why LangGraph over a bare AgentExecutor: explicit human-approval interrupt +
deterministic node ordering + replayable state → far better demo and robustness):
```
detect ─▶ investigate ─▶ reason_and_score ─▶ [human_approval interrupt] ─▶ write_back ─▶ END
```
- `detect`, `investigate` call **deterministic** `detect/` functions (LLM does not decide *whether* there's
  a problem - it explains and ranks).
- `reason_and_score` = LLM: severity narrative, incident text, impact-report prose, trust rationale.
- `human_approval` = LangGraph `interrupt()` before any mutation (config flag `--auto-approve` for the demo).
- `write_back` = idempotent mutations from §6.

Two entry points via `cli.py` (Typer):
- `modelguard scan` - one-shot audit of all models (great for the video's "before" state).
- `modelguard watch` - event-driven via the **DataHub Actions framework** (Kafka `EntityChangeEvent`),
  with a **polling fallback** so the demo never depends on Kafka timing. [confirm] Actions setup adds complexity -
  build `scan` first; add `watch` only once the loop is bulletproof.

---

## 8. OSS contribution (the bonus - two verified gaps to fill)

### 8.1 `datahub-ml-guard` Skill (primary - first ML skill in the registry) [verified] gap confirmed
Repo layout mirrors existing skills (`skills/<name>/SKILL.md`, `references/`, `templates/`; standards are
symlinked; shared CLI/MCP signatures live in `shared-references/`) [verified]. Anthropic Skill format = YAML
frontmatter (`name`, `description`) + Markdown body. Mirror `skills/datahub-enrich/SKILL.md` exactly.

```markdown
---
name: datahub-ml-guard
description: >
  Protect production ML models by tracing end-to-end column-level lineage from a model's
  features back to source tables. Detect target leakage, training-serving schema drift, and
  upstream-data-failure blast radius; write incidents, a model trust score, and guarding
  assertions back to DataHub.
---

## When to use
- A source table's quality/freshness/schema changed and you need the models/deployments at risk.
- Before promoting a model: check for target leakage and input schema drift.

## Workflow
1. Resolve the model → features → source columns (ML `sources`, column-level lineage).
2. Run the leakage / blast-radius / schema-drift checks (see scripts/).
3. Raise incidents, set trust-score structured properties, publish an impact report.
...
```
Contribute per `CONTRIBUTING.md` (commit conventions + release process). Even if upstream merge is slow, a
well-documented standalone skill repo linked from the README **still counts** as a contribution.

### 8.2 MCP mutation tool `raise_incident` (stretch) [verified] gap confirmed
The MCP server (v0.6.0) has **no** assertion/incident/lineage-write tools. A thin `raise_incident` /
`create_assertion` mutation tool (annotated `readOnlyHint: false`, gated by `TOOLS_IS_MUTATION_ENABLED`) is a
small, on-roadmap PR to `acryldata/mcp-server-datahub` - or file it as an **RFC** for a first-class "ML
incident" workflow. Also complete the **Most Valuable Feedback survey** ($50 pool + goodwill) with the real
bugs/gaps found (e.g., "MCP lacks incident/assertion mutations", "no ML skill", Python SDK incident wrapper).

---

## 9. Testing & verification

- **Unit** (`tests/`): detectors against fixture graphs - a known-leakage graph must flag exactly the seeded
  feature; a clean graph must flag nothing (no false positives). Schema-diff on a synthetic rename.
- **Integration:** `quickstart.sh` on a clean machine → seed → inject scenario → `modelguard scan` → assert
  the incident/property/document exist via GraphQL reads. This *is* the judge's reproduction path.
- **Determinism:** LLM `temperature=0`; detection independent of the LLM; scenario seeds fixed.
- **Idempotency test:** run `scan` twice → exactly one incident per finding.
- Use the `/verify` skill to drive the real flow (not just unit tests) before each milestone.

---

## 10. Demo video (≤3 min - a scored deliverable) & submission checklist

**Video arc:** (0:00) cold-open on the pain - "a credit-risk model scoring live loans; a source column
silently went stale - the kind of miss that cost one team \$250K in a weekend." (0:20) trigger the planted
issue. (0:35) ModelGuard runs: lights up the lineage graph, names the exact model + live deployment at risk,
detects the leakage feature, computes the trust score. (1:40) **cut to the DataHub UI** - the incident, the
`model-at-risk` tag, the trust-score property, the guarding assertion, the impact report, all written back.
(2:30) close on the `datahub-ml-guard` skill + the MCP PR. No slideware; one uninterrupted live loop.

**Submission checklist (maps to the rubric - see `01-strategy-modelguard.md` §4):**
- [ ] Public repo, **Apache-2.0 license file**, License shown in GitHub **About** [verified] (hard requirement)
- [ ] Project URL + testing instructions (local `quickstart.sh`; credentials if any)
- [ ] `examples/` folder: impact report, guarding-assertion YAML, incident payload, the skill [verified]
- [ ] README: architecture diagram · "How this uses DataHub (reads AND writes)" table · "What we did NOT
      rebuild" · one-command setup
- [ ] ≤3-min public video (YouTube/Vimeo), no copyrighted music/marks
- [ ] Text description (features, tech, data used)
- [ ] Link the skill PR / MCP PR / RFC prominently (bonus)
- [ ] Complete the Most Valuable Feedback survey
- [ ] Submit **24h early**

---

## 11. Risk register & fallback

| Risk | Likelihood | Mitigation |
|---|---|---|
| **ML-graph seeding harder than expected** | Med | Week-1 kill-criterion; if unmet by end of W1 → **pivot to MigrationCopilot** (Category 2): PR-ready dbt/DAG artifacts from real schemas + column-level lineage, same write-back philosophy, **no ML seeding**. |
| `get_lineage` won't cross into ML entities | Med | Fall back to GraphQL `scrollAcrossLineage` / `relationships` to traverse feature/model edges. Test in W1. |
| Exact SDK/agent import symbols differ | Med | [confirm]-flagged everywhere; `pip show` + introspect; pin versions in `pyproject.toml`. |
| Actions/Kafka setup eats time | Med | Ship `scan` first; `watch` is optional polish with a polling fallback. |
| Cloud-only features assumed OSS | Low | Smart assertions + monitoring UI are Cloud - disclosed; we provide detection logic + open-assertions YAML. |
| Over-scoping 4 problems | Med | P2 loop first and bulletproof; add P1/P3/P4 only as each hardens. A reliable single loop that mutates the graph beats a flaky swarm. |
| LLM nondeterminism in demo | Low | `temperature=0`, deterministic detection, fixed scenario seeds, `--auto-approve` for the recorded run. |

---

## 12. Five-week schedule (July 6 → Aug 10, 2026)

- **W1 - Foundation & de-risk.** Quickstart + datapack; build `seed_ml_graph.py`; render ML lineage in UI;
  prove incident + structured-property write-back. **Gate:** read col-level ML lineage + write both. Else pivot.
- **W2 - Core loop (P2).** detect → blast radius → incident + tag + guarding assertion + impact report,
  end-to-end on one planted issue. Idempotent write-back. Verify in UI.
- **W3 - Differentiators.** P1 leakage detector (flagship), P3 schema drift, P4 trust score. Draft the
  `datahub-ml-guard` SKILL.md and test it in Claude Code/Cursor. Add finance/healthcare framing toggle.
  Stand up **ModelGuard-Bench** (Jenga injection + precision/recall) so detectors are measured, not asserted
  (`03-production-hardening.md §A`).
- **W4 - Contribution + hardening.** Open the skill PR (+ MCP tool/RFC); write README + `examples/` (incl.
  ODCS contract); harden `quickstart.sh`; `watch` mode + Slack notify if time. Run the **baseline comparison**
  (vs Great Expectations/Evidently) and the **security pass** (OWASP LLM01/LLM06 - `§D`). Dry-run judge setup
  on a clean machine.
- **W5 - Submission craft.** Record + edit ≤3-min video; Devpost text; feedback survey; buffer for bugs;
  publish `benchmarks/RESULTS.md`; submit 24h early.

---

## 13. API cheat-sheet (verified signatures, one place)

All signatures below were introspected from **acryl-datahub 1.6.0.13** on 2026-07-09, not copied from docs.

```python
# --- client ---
DataHubClient.from_env()                                            # env: DATAHUB_GMS_URL/_TOKEN     [verified]
DataHubClient(graph=DataHubGraph(...))                              # share one session               [verified]
DataHubGraph(DatahubClientConfig(server=..., token=...))            # execute_graphql / get_aspect    [verified]
graph.execute_graphql(query, variables=) · graph.exists(urn)                                          [verified]
graph.get_aspect(urn, AspectClass) · graph.emit_mcp(s)([MCPW(...)])                                   [verified]

# --- entities (idempotent) ---
client.entities.upsert(entity) / .create / .get / .update / .delete                                   [verified]
# NOTE: client._emit_mcps does NOT exist.

# --- lineage (Dataset to Dataset; column-level here) ---
client.lineage.add_lineage(upstream=, downstream=,                  # keyword-only
    column_lineage={"dst_col":["src_col"]} | True | "auto_strict",
    transformation_text=...)                                        # [verified]
client.lineage.get_lineage(source_urn=, direction="upstream|downstream",
    max_hops=, source_column=, filter=, count=) -> [LineageResult]  # [verified]
#   LineageResult(urn, type, hops, direction, platform, name, description, paths)
client.lineage.infer_lineage_from_sql(query_text=, platform=, default_db=, default_schema=)  # [verified]

# --- ML entities: only MLModel and MLModelGroup have SDK classes ---
MLModelGroup(id=, platform=, name=, description=)                                                     [verified]
MLModel(id=, platform=, name=, version=, aliases=, hyper_params=, training_metrics=,
        model_group=, training_jobs=); .add_deployment(urn); .add_training_job(urn)                   [verified]
#   NO MLModel.add_group           -> use the model_group constructor argument
#   NO client.create_training_run  -> emit DataProcessInstance + MLTrainingRunPropertiesClass
#   NO client.add_input_datasets_to_run -> emit DataProcessInstanceInputClass(inputs=[...])
#   NO SDK class for MLFeature / MLPrimaryKey / MLFeatureTable / MLModelDeployment
#      -> emit MLFeaturePropertiesClass(sources=[DATASET urns], customProperties={...}),
#         MLPrimaryKeyPropertiesClass(sources=), MLFeatureTablePropertiesClass(mlFeatures=,mlPrimaryKeys=),
#         MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
#   MLModel has no mlFeatures API -> upsert, then read mlModelProperties back, set mlFeatures, re-emit.

# --- urns (all verified) ---
DatasetUrn(platform=, name=, env="PROD") · SchemaFieldUrn(parent=, field_path=)
MlModelUrn(platform=, name=, env=) · MlModelGroupUrn(platform=, name=, env=)
MlFeatureUrn(feature_namespace=, name=) · MlPrimaryKeyUrn(feature_namespace=, name=)
MlFeatureTableUrn(platform=, name=) · MlModelDeploymentUrn(platform=, name=, env=)
DataProcessInstanceUrn(id) · StructuredPropertyUrn(id) · DataTypeUrn(id) · EntityTypeUrn(id)

# --- write-back ---
graph.execute_graphql(RAISE_INCIDENT | UPDATE_INCIDENT_STATUS)                                        [verified]
# Structured properties: emit StructuredPropertyDefinitionClass on the property urn, then
#   StructuredPropertiesClass on the entity. No CLI subprocess, no hand-built GraphQL needed.         [verified]
# CLI:  datahub datapack load {showcase-ecommerce|nyc-taxi|healthcare}                                [verified]
#       (`datahub datapack --help` crashes in 1.6.0.13: resources/*.md are not packaged. `load` works.)
# CLI:  datahub timeline --urn "<urn>" --category TECHNICAL_SCHEMA  # schema history for P3           [verified]
# MCP write tools (TOOLS_IS_MUTATION_ENABLED=true): add_tags, add_terms, add_owners,
#   set_domains, add_structured_properties, update_description, save_document                         [verified]
```

**Incident types:** `OPERATIONAL, FRESHNESS, VOLUME, FIELD, SQL, DATA_SCHEMA, CUSTOM` [verified]
(no `COLUMN`; the column-scoped type is `FIELD`)
**MCP server:** v0.6.0 (May 18 2026); read tools `search, get_lineage, get_lineage_paths_between,
get_dataset_queries, get_entities, list_schema_fields` [verified]

---

## 14. Primary references

- Quickstart: https://docs.datahub.com/docs/quickstart
- AI/ML SDK tutorial: https://docs.datahub.com/docs/api/tutorials/ml · ML feature store: https://github.com/datahub-project/datahub/blob/master/docs/api/tutorials/ml_feature_store.md
- Lineage SDK: https://docs.datahub.com/docs/api/tutorials/lineage
- Incidents API: https://docs.datahub.com/docs/api/tutorials/incidents · GraphQL mutations: https://docs.datahub.com/docs/graphql/mutations
- Structured properties: https://docs.datahub.com/docs/api/tutorials/structured-properties
- Open assertions spec: https://docs.datahub.com/docs/assertions/open-assertions-spec
- Agent Context Kit: https://docs.datahub.com/docs/dev-guides/agent-context/agent-context · Building Autonomous Data Agents: https://datahub.com/blog/building-autonomous-data-agents/
- MCP server: https://github.com/acryldata/mcp-server-datahub · docs: https://docs.datahub.com/docs/features/feature-guides/mcp
- Skills: https://docs.datahub.com/docs/dev-guides/agent-context/skills · repo: https://github.com/datahub-project/datahub-skills
- MLflow ingestion: https://docs.datahub.com/docs/generated/ingestion/sources/mlflow
- Data Lineage for ML (the pain, verbatim): https://datahub.com/blog/data-lineage-for-ml/
