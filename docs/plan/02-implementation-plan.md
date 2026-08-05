# Janus - Implementation Plan

> Companion to `01-strategy-janus.md`. This is the buildable blueprint: environment, architecture,
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
        └──────────► │  Janus  (LangGraph orchestrator)          │
                     │   1. Detect      (deterministic, Python)       │
                     │   2. Investigate (traverse blast radius)       │
                     │   3. Reason+Score(LLM, human-gated)            │
                     │   4. Write back  (idempotent, upsert-by-URN)   │
                     └───────────────────────────────────────────────┘

WRITE-BACK PRIMITIVES (all OSS): raiseIncident [verified] · structured properties [verified] · tags/terms/owners [verified]
· knowledge documents (sdk Document entity, NOT only MCP save_document) [verified] · guarding assertions as
open-assertions YAML + assertionInfo entity + assertionRunEvent [verified]
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
# optional per-provider extras: install exactly the one you configure   [verified]
langchain-anthropic==1.4.8  |  langchain-openai==1.3.4  |  langchain-google-genai==4.2.7
# optional extra "agent":
langgraph  langchain                    # Phase 3 orchestration, unpinned until installed
datahub-agent-context                          # Agent Context Kit toolset  [confirm]
# optional extra "dev":
pytest  ruff  mypy  pre-commit                                             [verified]
```

**LLM:** BYO, **optional**, and **provider-agnostic** (D-030). Three variables, set together or not at all:
`JANUS_LLM_PROVIDER` (`anthropic` | `openai` | `google`), `JANUS_LLM_MODEL` (the provider's model id
verbatim), `JANUS_LLM_API_KEY`. `janus/llm.py` is the only module that may import a vendor SDK or
name a vendor's model; nothing is hardcoded, including the default provider. With no LLM configured, `scan`
writes deterministic template prose and everything else behaves identically: detection, severity, and the
incident title never depend on the LLM (D-027). All three chat classes were introspected: they accept the same
`model` / `api_key` / `temperature` / `max_tokens` keywords despite differing field names [verified].

**Configuration** (`.env`, git-ignored; `.env.example` carries the identical key set). Read only through
`janus/env.py`, the one module that calls `load_dotenv` or touches `os.environ`. Values that identify a
system, an account, or a vendor have **no defaults**; thresholds do (D-029).
```
DATAHUB_GMS_URL=http://localhost:8080
DATAHUB_GMS_TOKEN=            # generate in UI: Settings → Access Tokens (only if auth is enabled)
JANUS_LLM_PROVIDER=      # anthropic | openai | google. All three LLM vars, or none.
JANUS_LLM_MODEL=         # the provider's model id, verbatim
JANUS_LLM_API_KEY=       # never passed as a CLI flag: argv leaks into shell history
JANUS_FRESHNESS_SLA_HOURS=   # blank -> 6, the documented default in config.py
JANUS_MAX_HOPS=              # blank -> 3
JANUS_LINEAGE_RESULT_CAP=    # blank -> 500
TOOLS_IS_MUTATION_ENABLED=true    # required to expose MCP write tools
```

---

## 2. Repository layout (Apache-2.0 from commit #1)

```
janus/
├── LICENSE                      # Apache 2.0 - set repo License in GitHub "About" (judging requirement) [verified]
├── README.md                    # architecture diagram + "reads AND writes" table + "what we did NOT rebuild"
├── quickstart.sh                # one command: boot DataHub → load datapack → seed ML graph → run agent
├── pyproject.toml               # pinned deps + ruff/mypy/pytest config (replaced requirements.txt)
├── .env.example
├── janus/
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
│   └── cli.py                   # `janus scan` / `janus watch` (Typer)
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
├── benchmarks/                  # "Janus-Bench" - see 03-production-hardening.md §A
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
originally carried was wrong on four symbols; see D-012. Implemented in `janus/seed/seed_ml_graph.py`.

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
> `schemaField`. Pointing it at a column URN creates a dangling edge. Janus therefore records the exact
> source column in the feature's `customProperties` under `janus.source_column`. Problem 1's traversal
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
# The resourceUrn must be a dataset/schemaField/chart/dashboard/dataFlow/dataJob.
# An mlModel URN here returns a 500 (see §6.1 and D-017).
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
graph = DataHubGraph(DatahubClientConfig(server=GMS_URL, token=TOKEN))
graph.execute_graphql("""
mutation { raiseIncident(input:{
  resourceUrn:"urn:li:schemaField:(urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD),prior_default_flag)",
  type: FIELD, title:"Janus smoke test",
  description:"If you can read this in the UI, write-back works." }) }
""")   # returns the new incident URN
```
Structured properties are emitted as aspects, not via the CLI (§6.2): the definition on the property URN, the
assignment on the mlModel.

**The gate is executable, not a UI inspection:** `pytest -m integration` seeds the graph, reads column-level
lineage from the leaking feature up to the label's table, writes the incident and the properties, then reruns
to prove nothing duplicates. Status: **PASSED 2026-07-10** (D-019), so the MigrationCopilot pivot is off.
The UI at `http://localhost:9002` shows the same result for the demo.

---

## 4. Phase 1 - Week 2: the core loop (Problem 2, end to end)

> **Phase 1 gate PASSED on 2026-07-10** (D-028). `tests/integration/test_phase1_loop.py`
> is the executable criterion: 14 tests, green, repeatable back to back.

Build ONE bulletproof path first: **detect upstream failure → blast radius → write incident + tag +
guarding assertion → impact report.**

### 4.1 Detector - blast radius (`detect/blast_radius.py`)

**Lineage does cross into ML entities** [verified]. The `[confirm]` above is resolved: both
`MLFeatureProperties.sources` (`DerivedFrom`) and `MLModelProperties.mlFeatures` (`Consumes`) declare
`isLineage: true`, so **one** downstream call spans the whole supply chain (D-020):

```
loans_raw --(UpstreamLineage)--> customer_features   hop 1, dataset
          --(DerivedFrom)------> mlFeature           hop 2
          --(Consumes)---------> mlModel             hop 3
```

```python
results = client.lineage.get_lineage(                    # [verified]
    source_urn=failing_table_urn, direction="downstream",
    max_hops=3, count=500)                               # F.entity_type("mlModel") also works
# Two gotchas, both verified against a live GMS:
#  1. above 2 hops DataHub does a full-graph search and returns results BEYOND
#     max_hops (a model group came back at hop 4 for a cap of 3) -> filter on r.hops
#  2. LineageResult.type is a display string -> take the entity type from the URN
within_cap = [r for r in results if r.hops <= max_hops]
```

**Deployments are not lineage** [verified]. `MLModelProperties.deployments` declares `DeployedTo`
*without* `isLineage`, so read it from the aspect and check
`MLModelDeploymentProperties.status == IN_SERVICE`. That single fact decides severity: live → CRITICAL,
deployed but idle → HIGH, undeployed → MEDIUM. Fan-out and ownership order models *within* a band.

Detection triggers (any of, deterministic):
- **freshness lag** > threshold. Implemented. Read from the dataset's `operation` aspect
  (`lastUpdatedTimestamp`), which is a **timeseries** aspect: use
  `graph.get_latest_timeseries_value(urn, OperationClass, {})`; `get_aspect` raises a TypeError (D-021).
- **planted issue** from `seed/scenarios.py`. Implemented, reversible.
- **assertion result = FAIL**, **null-rate / volume spike** from `datasetProfile`: later phases. The
  assertion trigger is circular in Phase 1, since the assertion is what we write.

A detector fires only on **positive evidence**: a table that never reported an `operation` is not stale,
and a deployment with no properties aspect is not live.

### 4.2 Write-back (`writeback/incidents.py`, `labels.py`, `assertions.py`, `documents.py`)

- **Incident** on the **offending upstream dataset** (not on the model, which DataHub forbids: section 6.1) -
  `raiseIncident` (`type` in `OPERATIONAL, FRESHNESS, VOLUME, FIELD, SQL, DATA_SCHEMA, CUSTOM` [verified]).
  The **title is deterministic** and carries no measurement: it is part of the dedup key, so an LLM-reworded
  title would raise a duplicate incident every scan (D-027). The **description** is a deterministic fact block
  with the LLM's assessment appended after it. Each at-risk model is marked instead with the `model-at-risk`
  tag and structured properties.
- **Tag** `model-at-risk` on the model. There is **no mlModel patch builder** in `datahub.specific`
  [verified], so tagging is read-merge-emit on `globalTags`; a blind write drops other people's tags.
  Glossary terms are deferred to P1, where `leakage-risk` on the leaking feature is the natural term.
- **Structured properties** on the model: `janus.risk_flags` and `janus.run_id`. **Not**
  `trust_score`: no detector computes it yet, and writing a number nothing measured is fabrication.
- **Guarding assertion** on the offending upstream table as **open-assertions YAML** (verified format):
  ```yaml
  version: 1
  assertions:
    - entity: urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)
      type: freshness
      id_raw: janus.freshness.ecommerce.public.loans_raw   # stable -> stable assertion guid
      lookback_interval: "6 hours"
      last_modified_field: updated_at
      schedule: { type: interval, interval: "6 hours" }
  ```
  Emit it as an artifact in `examples/` **and** create the assertion entity so it appears in the Quality tab,
  **plus** an `assertionRunEvent` carrying the result Janus actually measured on this scan (D-026). The
  assertion's declared type is `DATASET_CHANGE` and the detector reads the `operation` aspect, so the declared
  check and the executed check are the same check: a fresh table writes SUCCESS. Three traps, all verified:
  - `DataHubClient.assertions` is **Cloud only** (it imports `acryl_datahub_cloud`). On OSS, parse the YAML
    back through `AssertionsConfigSpec` and emit `assertionInfo` yourself.
  - Never call `get_assertion_info_aspect()`: it restamps `source.created` with *now*, so the aspect never
    converges. Call `get_assertion_info()` and set the source yourself, preserving any existing stamp (D-025).
  - `FixedIntervalFreshnessAssertion` reads `timedelta.seconds`, not `total_seconds()`, so a **30 hour**
    lookback silently emits as **6 hours**. Refuse any SLA of a day or more (D-024).

  (Smart/anomaly *monitoring* and scheduled evaluation are Cloud - say so; we provide the check logic.)
- **Model Impact Report** → a first-class `datahub.sdk.document.Document` entity, linked to the model through
  `related_assets` [verified on an OSS Quickstart]. The plan assumed MCP `save_document` was the only route;
  the SDK entity works on OSS, so no fallback ships (D-022).

**Idempotency:** dedup incidents on `(resourceUrn, type, title)`, never on `run_id` (D-013). `run_id` is a
structured property and a description footer: provenance, not a key. The assertion URN is a guid over the
declaration and the document id derives from the model, so both update in place. Judges notice this; it reads
as production-grade.

### 4.3 Verify the loop
`janus scan --table loans_raw` → incident + tag + properties + assertion + run event + report, all visible
in the UI, same result every run. The gate is executable, not a UI inspection:
`pytest -m integration tests/integration/test_phase1_loop.py` seeds, plants the failure, scans, asserts every
write landed, rescans to prove nothing duplicates, then reverts and asserts a clean scan writes nothing. It
resolves any incident an earlier run left open first, so it exercises the create path and passes twice in a
row against a dirty graph. Status: **PASSED 2026-07-10** (D-028).

---

## 5. Phase 2 - Week 3: the three differentiators (Problems 1, 3, 4)

### 5.1 P1 - Target-leakage detector (`detect/leakage.py`) - the most original piece

> **Landed 2026-07-13** (D-031, D-032, D-033). `leakage_findings(conn, model_urn,
> config)` traverses each feature's upstream column cone and returns a
> `LeakageFinding` for every column that reaches a declared label. Verified
> against a live GMS: 5 integration tests
> (`tests/integration/test_phase2_leakage.py`) plus 14 unit tests including a
> false-positive control and four killed mutants.

The plan's original sketch, `intersects(cone, label_source_column_urn)`, is
unsound as written: `client.lineage.get_lineage(source_column=...)` does not put
the label column in the cone's URNs at all. On the seeded graph,
`LineageResult.urn` for a column-level upstream query is the **dataset**
(`loans_raw`), never the column, even one hop up. The column identity survives
only in `LineageResult.paths`, a list of `LineagePath(urn, entity_name,
column_name)`. `intersects` against `.urn` would report a leaking graph clean.
The shipped detector reads `.paths` and never `.urn` for this comparison
(D-031); this is the sharpest addition to the Most Valuable Feedback list
(section 8.3).

**Label column** is declared as a glossary term (`urn:li:glossaryTerm:
janus.label`), read from two aspects and unioned: directly on the
`schemaField` (what Janus and the seeder write), and via
`editableSchemaMetadata` on the parent dataset (what the DataHub UI writes when
a human tags a column by hand). Both routes were emitted and read back against
a live Quickstart before the detector was written (D-032). A structured
property on the training dataset was considered and rejected: it is invisible
in the UI's own vocabulary, and a term reaches a human declaring their own
label with zero Janus configuration.

Temporal leakage (a feature derived from a column produced after the
prediction timestamp) is not yet implemented; the column-lineage leakage above
is the shipped detector.

**Write-back:** a `FIELD` incident on the leaking `schemaField` (never on the
model: section 6.1), quoting the exact `feature <- ... <- label` column path in
its evidence, plus a `leakage-risk` glossary term on the feature and a
`risk_flags` structured property on the model. Deterministic, no training
required. `agent/narrate.py` drafts the prose; `writeback/documents.py` renders
the impact report's leakage section.

**Cite** Kaufman et al. 2012 in the report prose.

### 5.2 P3 - Training/serving schema drift (`detect/schema_drift.py`)

> **Landed 2026-07-16** (D-036). `schema_drift_findings(conn, model_urn, config)`
> diffs each input dataset's current schema against the schema the model was
> trained on and returns a `SchemaDriftFinding` per drifted input. 8 unit tests
> plus the Phase 2 drift/trust integration gate.

The plan's original sketch reconstructed the training-time schema from the
**Timeline / Schema-History API** (`datahub timeline --urn ... --category
TECHNICAL_SCHEMA`). That reconstruction is fragile: catalog versions compact,
ingestion lags training, and the `lastModified` stamps are unreliable as an
"as-of training" cursor. The shipped detector instead captures a **schema
fingerprint on the training run at training time** (a JSON map of input dataset
URN to `field_path -> native_type`, in `customProperties` under
`janus.training_schema`) and diffs the input dataset's **current**
`schemaMetadata` against it. This is exactly the training-serving skew guard
TFX/TFDV perform (Breck et al. 2019): freeze a schema at training, validate
serving data against it. Deterministic, testable, and more robust than trusting
version history (D-036).

- Diff added/removed/retyped columns; the fingerprint is keyed by input dataset
  URN so a multi-input run diffs each input against its own baseline.
- **Write-back:** a `DATA_SCHEMA` incident on the **drifted input dataset** (never
  on the mlModel: section 6.1), quoting every changed column in its evidence, plus
  the `model-at-risk` tag and the `input-schema-drift` risk flag on the model. The
  drifted-field list and training-run URN are carried in the incident description
  and the impact report rather than as extra structured properties (D-036).
- **Cite** Breck et al. 2019 (training-serving skew).

### 5.3 P4 - Model Trust Score (`detect/trust_score.py`)

> **Landed 2026-07-16** (D-037). `trust_inputs_from_findings` reduces a scan's
> findings about one model to deterministic inputs, `trust_score` applies the
> weights below, and the pipeline writes `janus.trust_score` +
> `janus.trust_band` on each model a finding named. 7 unit tests plus
> pipeline and integration coverage.

```
trust = 100
  − 40·(upstream_assertion_failing)
  − 20·(has_leakage_finding)
  − 15·(has_schema_drift)
  − 15·(freshness_lag_hours / SLA_hours, capped)
  − 10·(missing_owner)      # weights are illustrative; expose in config
score ∈ [0,100] → band: healthy / watch / at-risk
```
- **Write-back:** `janus.trust_score` (number) + `janus.risk_flags` (multiple string) as
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

> **An incident cannot be raised on an `mlModel`** [verified]. `incidentInfo.entities` declares
> `entityTypes: [dataset, chart, dashboard, dataFlow, dataJob, schemaField]`; GMS answers a 500 for anything
> else. Findings therefore attach to the **dataset or column** they concern (a leakage finding goes on the
> leaking `schemaField`), and **model-level risk is carried by structured properties** on the mlModel, which
> it does accept. See D-017. `graph.exists()` is also always False for a `schemaField`: resolve a column
> through its parent dataset's `schemaMetadata` instead.

**Dedup:** an incident is keyed by `(resourceUrn, type, title)` over the resource's *active* incidents, found
by traversing the **`IncidentOn` relationship inbound** (`graph.get_related_entities`). Do **not** read the
resource's `incidentsSummary` aspect: a Quickstart GMS never writes it, so a summary-based dedup finds nothing
and duplicates every finding on every scan (D-018). `run_id` is provenance in the description, not part of the
key: it changes every run, so including it would duplicate every finding too (D-013).

### 6.2 Structured properties [verified]
Define (YAML → `datahub properties upsert -f props.yaml`):
```yaml
- id: janus.trust_score
  qualified_name: janus.trust_score
  type: number
  cardinality: SINGLE
  display_name: Janus Trust Score
  entity_types: [mlModel]
- id: janus.risk_flags
  qualified_name: janus.risk_flags
  type: string
  cardinality: MULTIPLE
  display_name: Janus Risk Flags
  entity_types: [mlModel]
```
Assign (GraphQL):
```graphql
mutation { upsertStructuredProperties(input:{
  assetUrn:"<mlModel urn>",
  structuredPropertyInputParams:[
    { structuredPropertyUrn:"urn:li:structuredProperty:janus.trust_score",
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

> **Landed 2026-07-16** (D-038). `writeback/contract.py`'s `render_input_contract(conn,
> model_urn, config)` reads a model's training-run input datasets and their current
> `schemaMetadata` and returns an ODCS v3.1.0 YAML: one schema object per input table
> (native types verbatim as `physicalType`, `logicalType` mapped where unambiguous,
> `required` from `nullable`) plus one `slaProperties` freshness entry per table for the
> SLA Janus guards. Exposed as `janus scan --model <m> --contract-out <path>`;
> it renders a file and never mutates the graph, so it runs on a clean or dry-run scan.
> `examples/input-data-contract.odcs.yaml` was generated from a real seeded scan and
> lints green against datacontract-cli's bundled ODCS 3.1.0 JSON Schema. 10 unit tests.
> No volume/quality expectation is emitted: Janus measures none (writeback rule 10).

For a model's input tables, emit an **Open Data Contract Standard (ODCS v3.1.0)** YAML capturing the schema
+ freshness/volume/quality expectations Janus derived, and validate it with `datacontract-cli` before
committing it to `examples/`. This makes the "contract for the ML boundary" tangible and standards-based
(Linux Foundation Bitol, Apache-2.0). See `resources.md §6`. Optional but a strong Originality + Usefulness signal.

---

## 7. The agent (`agent/graph.py`) - LangGraph over the Agent Context Kit

> **Landed 2026-07-16** (D-039). `agent/graph.py` runs the pipeline's node order
> (`detect -> reason -> [approval] -> write_back`) as a compiled `StateGraph`, but
> the nodes delegate to the pipeline's own deterministic functions and to
> `narrate`; there is **no** Agent Context Kit toolset and **no** `AgentExecutor`,
> because an LLM tool-caller that could decide to write contradicts the design law.
> The one new capability is a real `interrupt()` approval gate: `run_agent` pauses
> after reasoning, hands the caller a preview, and writes only what is approved.
> `scan --review` (or `--auto-approve` for the demo) opts into it; the default
> `scan` and `watch` keep using `run_scan`, so the out-of-the-box path needs no
> langgraph (the `agent` extra, pinned to langgraph 1.2.9, is lazily imported).
> `watch` shipped as a **polling** loop that acts on finding-set transitions, not
> the Actions/Kafka consumer sketched below; that remains the documented upgrade
> path. Findings ride an in-process holder rather than the checkpointed state, so
> no Janus dataclass is msgpack-serialized. 9 unit tests (4 on the approval
> gate, 5 on watch transitions). The `tools.py` sketch below did not ship.

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
deterministic node ordering + process-local control-flow checkpointing):
```
detect ─▶ investigate ─▶ reason_and_score ─▶ [human_approval interrupt] ─▶ write_back ─▶ END
```
- `detect`, `investigate` call **deterministic** `detect/` functions (LLM does not decide *whether* there's
  a problem - it explains and ranks).
- `reason_and_score` = LLM: severity narrative, incident text, impact-report prose, trust rationale.
- `human_approval` = LangGraph `interrupt()` before any mutation (config flag `--auto-approve` for the demo).
  The public API requires an approval callback unless `auto_approve=True` is explicit. The current CLI
  approval exchange is synchronous and process-local; durable cross-process resume is not claimed.
- `write_back` = idempotent mutations from §6.

Two entry points via `cli.py` (Typer):
- `janus scan` - one-shot audit of all models (great for the video's "before" state).
- `janus watch` - polling audit with finding-set transition detection, recovery
  reconciliation, bounded retry/backoff, and the DataHub Actions framework as a future
  event-driven upgrade. The demo never depends on Kafka timing.

---

## 8. OSS contribution (the bonus - two verified gaps to fill)

> **All three points landed 2026-07-21** (D-041). The `datahub-ml-guard` skill is
> under `skill/datahub-ml-guard/`; the MCP `raise_incident` tool + RFC are under
> `mcp_ext/`; the Most Valuable Feedback survey is `docs/most-valuable-feedback.md`.

### 8.1 `datahub-ml-guard` Skill (primary - a deterministic-engine ML skill) [verified] gap confirmed

> **Landed 2026-07-21** (D-041). `skill/datahub-ml-guard/` ships `SKILL.md` (the
> frontmatter below plus When-to-use / Workflow / Cloud-boundary), `scripts/` (thin
> bash wrappers shelling to `janus-seed` and `janus scan --table/--model`,
> no logic fork), and `references/` (`detectors.md`, `datahub-write-surface.md`). It
> mirrors the upstream datahub-enrich format (frontmatter fields `name`,
> `description`, `user-invocable`, `allowed-tools`).
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

> **Landed 2026-07-21** (D-041). `mcp_ext/raise_incident_tool.py` is a thin,
> standalone tool: it wraps the same `raiseIncident` GraphQL mutation as
> `writeback/incidents.py`, derives the allowed incident/entity types from the
> installed metadata model, refuses unless `TOOLS_IS_MUTATION_ENABLED` is truthy, and
> registers with `readOnlyHint: false`. It carries an offline `demo()` self-check
> (gating, mlModel rejection, payload shape) that runs with no network.
> `mcp_ext/RFC-ml-incidents.md` files the larger gap: incidents cannot attach to an
> mlModel at all.

The MCP server (v0.6.0) has **no** assertion/incident/lineage-write tools. A thin `raise_incident` /
`create_assertion` mutation tool (annotated `readOnlyHint: false`, gated by `TOOLS_IS_MUTATION_ENABLED`) is a
small, on-roadmap PR to `acryldata/mcp-server-datahub` - or file it as an **RFC** for a first-class "ML
incident" workflow.

### 8.3 Most Valuable Feedback survey - real bugs found while building [verified]

> **Landed 2026-07-21** (D-041). Assembled into `docs/most-valuable-feedback.md`: the
> 13 findings below, each with its affected package/version, symptom, minimal repro,
> and workaround. Version strings cross-checked against the installed `acryl-datahub`.
>
> That count is what landed on the date and is left as written. The live document
> has since grown to 16, each addition dated and logged: #14 (D-074, the mlflow
> source dropping `mlFeatures`), #15 (D-121, a dbt semantic model colliding URNs
> with its own model) and #16 (D-135, the Agent Context Kit's exact
> `acryl-datahub` pin). `docs/most-valuable-feedback.md` is the count that
> matters; this line is a record of a phase, not a running total.

Concrete, reproducible findings from Phase 0, worth far more than generic praise:
1. **`datahub datapack --help` crashes** (acryl-datahub 1.6.0.13): `FileNotFoundError` for
   `datahub/cli/datapack/resources/DATAPACK_AGENT_CONTEXT.md`. The `resources/` directory ships with only
   `__init__.py`; the markdown files are missing from the wheel. `datapack load` itself works.
2. **`incidentsSummary` is never written** by GMS v1.5.0.6. After `raiseIncident` succeeds, neither the
   dataset nor the schemaField carries the aspect, so the documented way to list a resource's incidents
   returns nothing. Workaround: traverse the `IncidentOn` relationship.
3. **Searching incidents by their `entities` field 500s**: "The field at path
   `/scrollAcrossEntities/searchResults[0]/entity` was declared as a non null type, but the code involved in
   retrieving data has wrongly returned a null value."
4. **Incidents cannot attach to `mlModel`**, which makes the ML-incident story impossible today. This is the
   RFC to file: ML entities are first-class in the graph but second-class in the incident model.
5. **No Python SDK wrapper for incidents**, and **no SDK entity classes** for MLFeature, MLPrimaryKey,
   MLFeatureTable, or MLModelDeployment.
6. **`graph.exists()` returns False for every `schemaField`**, with no documented alternative.
7. **`updateIncidentStatus` takes `IncidentStatusInput`, not `UpdateIncidentStatusInput`** as the docs and
   the GraphQL mutation reference state. GMS 1.5.0.6 answers
   `Validation error (VariableTypeMismatch@[updateIncidentStatus])`. Introspecting `Mutation` on a live
   server is the only way to find the right name (D-023).
8. **`FixedIntervalFreshnessAssertion` truncates any lookback of a day or more.**
   `get_assertion_info()` builds `FixedIntervalSchedule(multiple=self.lookback_interval.seconds)`; it must be
   `total_seconds()`. `lookback_interval: "30 hours"` therefore emits an assertion of **6 hours**, silently
   (`timedelta(hours=30).seconds == 21600`). A one-word fix, and a silently wrong data-quality check today.
9. **`BaseEntityAssertion.get_assertion_info_aspect()` cannot be used idempotently.** It calls
   `_ensure_source_created` → `make_assertion_source()`, which stamps `source.created` with the current time,
   so re-upserting an unchanged assertion rewrites the aspect on every run. There is no way to pass a
   creation stamp in. Callers who need convergence must bypass it and build `AssertionSource` themselves.
10. **`DataHubClient.assertions` is Cloud-only but is not documented as such**, and it is discoverable as a
    plain property on the OSS client. It raises `SdkUsageError` telling you to `pip install
    acryl-datahub-cloud`, which is a paid product. The OSS path (parse `AssertionsConfigSpec`, emit
    `assertionInfo`) is undocumented.
11. **`operation` is a timeseries aspect but reads like a versioned one.** `graph.get_aspect(urn,
    OperationClass)` raises `TypeError: Cannot get a timeseries aspect using "get_aspect"`. The required
    `get_latest_timeseries_value(urn, aspect, filter_criteria_map)` has a mandatory third positional
    argument that is almost always `{}`, which is undiscoverable without reading the source.
12. **`LineageResult.urn` is the upstream dataset, not the column, for a column-scoped query.**
    `client.lineage.get_lineage(source_urn=t, source_column=c, direction="upstream")` on the seeded
    graph returns a result whose `.urn` is the upstream table (`loans_raw`), not the schemaField the
    query asked about, even at one hop. The column identity is carried only in `LineageResult.paths`
    (a list of `LineagePath(urn, entity_name, column_name)`), which is undocumented as the source of
    column granularity. A caller who compares `.urn` against a target column, the obvious thing to do,
    silently gets zero matches on a graph where the column-level edge plainly exists (D-031).

---

## 9. Testing & verification

> **Janus-Bench core landed 2026-07-22** (D-047), covering the measured half of this section:
> `python -m benchmarks.run_bench` scores the detectors against a live graph and writes
> `benchmarks/RESULTS.md`. `quickstart.sh` is still **not** written, so the one-command judge path
> below remains the seed-plus-scan sequence in the README.

- **Unit** (`tests/`): detectors against fixture graphs - a known-leakage graph must flag exactly the seeded
  feature; a clean graph must flag nothing (no false positives). Schema-diff on a synthetic rename.
- **Integration:** `quickstart.sh` on a clean machine → seed → inject scenario → `janus scan` → assert
  the incident/property/document exist via GraphQL reads. This *is* the judge's reproduction path.
- **Determinism:** LLM `temperature=0`; detection independent of the LLM; scenario seeds fixed.
- **Idempotency test:** run `scan` twice → exactly one incident per finding.
- Use the `/verify` skill to drive the real flow (not just unit tests) before each milestone.

---

## 10. Demo video (≤3 min - a scored deliverable) & submission checklist

**Video arc:** (0:00) cold-open on the pain - "a credit-risk model scoring live loans; a source column
silently went stale - the kind of miss that cost one team \$250K in a weekend." (0:20) trigger the planted
issue. (0:35) Janus runs: lights up the lineage graph, names the exact model + live deployment at risk,
detects the leakage feature, computes the trust score. (1:40) **cut to the DataHub UI** - the incident, the
`model-at-risk` tag, the trust-score property, the guarding assertion, the impact report, all written back.
(2:30) close on the `datahub-ml-guard` skill + the MCP PR. No slideware; one uninterrupted live loop.

**Submission checklist (maps to the rubric - see `01-strategy-janus.md` §4):**
- [ ] Public repo, **Apache-2.0 license file**, License shown in GitHub **About** [verified] (hard requirement)
- [ ] Project URL + testing instructions (local `quickstart.sh`; credentials if any)
- [ ] `examples/` folder: impact report, guarding-assertion YAML, incident payload, the skill [verified]
- [ ] README: architecture diagram · "How this uses DataHub (reads AND writes)" table · "What we did NOT
      rebuild" · one-command setup
- [ ] ≤3-min public video (YouTube/Vimeo), no copyrighted music/marks
- [ ] Text description (features, tech, data used)
- [x] Link the skill PR / MCP PR / RFC prominently (bonus) - README OSS-contributions section (D-041)
- [x] Complete the Most Valuable Feedback survey - docs/most-valuable-feedback.md (D-041)
- [ ] Submit **24h early**

---

## 11. Risk register & fallback

> **Week 1 gate PASSED on 2026-07-10** (D-019). The first two risks below are **retired**. The pivot to
> MigrationCopilot is off the table.

| Risk | Likelihood | Mitigation |
|---|---|---|
| ~~**ML-graph seeding harder than expected**~~ | RETIRED | Seeder works and is idempotent; gate passed. |
| ~~`get_lineage` won't cross into ML entities~~ | RETIRED | Column lineage is dataset-to-dataset and resolves; ML entities are reached through `mlModelProperties` and the feature `sources` aspect plus the `janus.source_column` bridge. |
| **Incidents cannot attach to mlModel** | REALIZED | Findings go on the dataset/schemaField; model risk goes on structured properties (D-017). Reframed as an OSS RFC (section 8.3). |
| Exact SDK/agent import symbols differ | Med | [confirm]-flagged everywhere; `pip show` + introspect; pin versions in `pyproject.toml`. |
| Actions/Kafka setup eats time | Med | Ship `scan` first; `watch` is optional polish with a polling fallback. |
| Cloud-only features assumed OSS | Low | Smart assertions + monitoring UI are Cloud - disclosed; we provide detection logic + open-assertions YAML. |
| Over-scoping 4 problems | Med | P2 loop first and bulletproof; add P1/P3/P4 only as each hardens. A reliable single loop that mutates the graph beats a flaky swarm. |
| LLM nondeterminism in demo | Low | `temperature=0`, deterministic detection, fixed scenario seeds, `--auto-approve` for the recorded run. |

---

## 12. Five-week schedule (July 6 → Aug 10, 2026)

- **W1 - Foundation & de-risk.** Quickstart + datapack; build `seed_ml_graph.py`; render ML lineage in UI;
  prove incident + structured-property write-back. **Gate:** read col-level ML lineage + write both. Else pivot.
- **W2 - Core loop (P2). DONE 2026-07-10 (D-028).** detect → blast radius → incident + tag + properties +
  guarding assertion + run event + impact report, end-to-end on one planted issue. Idempotent write-back,
  proven by an executable integration gate that reruns and asserts nothing duplicates.
- **W3 - Differentiators.** P1 leakage detector (flagship), P3 schema drift, P4 trust score. Draft the
  `datahub-ml-guard` SKILL.md and test it in Claude Code/Cursor. Add finance/healthcare framing toggle.
  Stand up **Janus-Bench** (Jenga injection + precision/recall) so detectors are measured, not asserted
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
#   raiseIncident resourceUrn MUST be dataset|schemaField|chart|dashboard|dataFlow|dataJob (NOT mlModel)
graph.get_related_entities(entity_urn, ["IncidentOn"], RelationshipDirection.INCOMING)  # dedup read  [verified]
#   from datahub.ingestion.graph.openapi import RelationshipDirection
#   incidentsSummary is NEVER written by GMS; searching incidents by `entities` 500s. Use the relationship.
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
