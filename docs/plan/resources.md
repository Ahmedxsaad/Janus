# ModelGuard — Resource Library

> Curated, annotated resources for building ModelGuard **production-grade**. Every entry has: a **URL**,
> **📖 Read** (the exact chapters / sections / concepts to focus on — not the whole thing), and **🎯 Maps to**
> (the precise place in *our* project it informs — a detector, a file, or a plan section).
>
> Cross-refs: `architecture.md` (§ = its sections), `02-implementation-plan.md` (IP §), `03-production-hardening.md` (PH §).
> Legend: 📄 paper · 📕 book · 🛠️ tool/repo · 📐 standard · 🔒 security · 🏛️ architecture.
> ⚠️ Deep chapter/page URLs can drift — the landing links below are stable; confirm a specific page before quoting it in the public README.

---

## 0. The 10 that matter most

| # | Resource | One-line relevance |
|---|---|---|
| 1 | 📄 Breck 2019, *Data Validation for ML* (TFDV) | The check taxonomy behind P2/P3 detectors. |
| 2 | 📄 Sculley 2015, *Hidden Technical Debt in ML* | Names our villains (undeclared consumers, CACE). |
| 3 | 📄 Sambasivan 2021, *Data Cascades* | Field proof that silent data issues compound. |
| 4 | 🛠️ mlinspect (SIGMOD 2021) | Lineage-based ML-pipeline inspection = our technique's ancestor. |
| 5 | 🛠️ Jenga (EDBT 2021) | Corruption injection = our benchmark harness. |
| 6 | 📄 Kaufman 2012, *Leakage in Data Mining* | The target-leakage theory behind P1. |
| 7 | 🛠️ Anthropic, *Building Effective Agents* | Justifies deterministic-core + gated-LLM + HITL. |
| 8 | 🔒 OWASP Top 10 for LLM Apps (2025) | Our exact threat model (LLM01, LLM06). |
| 9 | 📕 *Reliable Machine Learning* (O'Reilly) | "SRE for the ML supply chain" framing. |
| 10 | 📐 OpenLineage / SQLGlot | The column-lineage substrate we traverse. |

---

## 1. Foundational research papers (the intellectual backbone)

**Breck, Polyzotis, Roy, Whang, Zinkevich — Data Validation for Machine Learning (MLSys 2019)**
https://mlsys.org/Conferences/2019/doc/2019/167.pdf
- 📖 **Read:** §2 "Data validation as part of the ML pipeline"; §3 **single-batch validation** (schema + anomaly types); §4 **training-serving skew detection**; Table 1 of anomaly categories.
- 🎯 **Maps to:** `detect/schema_drift.py` (P3) directly implements the training-serving-skew idea over DataHub schema history; the anomaly categories seed `detect/blast_radius.py` trigger types (IP §4.1, §5.2).

**Sculley et al. — Hidden Technical Debt in Machine Learning Systems (NeurIPS 2015)**
https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf
- 📖 **Read:** §3 "Entanglement / CACE"; §4 **"Data Dependencies Cost More than Code Dependencies"** and **"Undeclared Consumers"**; §5 feedback loops.
- 🎯 **Maps to:** the *why* of P2 blast radius (undeclared consumers = models silently consuming a table) and P4 trust score. Quote "undeclared consumers" verbatim in impact-report prose (`writeback/documents.py`, `architecture.md §5④`).

**Sambasivan et al. — Data Cascades in High-Stakes AI (CHI 2021)**
https://research.google/blog/data-cascades-in-machine-learning/ (paper linked from the post)
- 📖 **Read:** the **four cascade types** and the "compounding, invisible until downstream" finding; the healthcare/field examples.
- 🎯 **Maps to:** the video cold-open + README "why" section; justifies catching issues *upstream* before the cascade (IP §10).

**Kaufman, Rosset, Perlich — Leakage in Data Mining: Formulation, Detection, and Avoidance (ACM TKDD 2012)**
https://dl.acm.org/doi/10.1145/2020408.2020496
- 📖 **Read:** §3 **leakage formulation** (leaking features; leakage in training examples); the **"legitimacy"** condition; §5 avoidance methodology.
- 🎯 **Maps to:** the exact predicate in `detect/leakage.py` (P1) — a feature is illegitimate if its column cone intersects the label's cone or crosses the prediction-time boundary (IP §5.1).

**Mitchell et al. — Model Cards for Model Reporting (FAT* 2019)**
https://arxiv.org/abs/1810.03993
- 📖 **Read:** §3 the **model-card section schema** (Model Details, Intended Use, Factors, Metrics, Evaluation/Training Data, Ethical Considerations).
- 🎯 **Maps to:** P4 write-back — we populate DataHub's model-card fields + `trust_score` structured property (`writeback/properties.py`; `architecture.md §7`).

**Polyzotis, Roy, Whang, Zinkevich — Data Lifecycle Challenges in Production ML: A Survey (SIGMOD Record 2018)**
https://sigmodrecord.org/publications/sigmodRecord/1806/pdfs/09_Surveys_Polyzotis.pdf
- 📖 **Read:** the **data understanding / validation / cleaning** sections; the taxonomy of data issues in prod ML.
- 🎯 **Maps to:** structures the four-detector suite; use its taxonomy in the README to show completeness (IP §3–§5).

**Amershi et al. — Software Engineering for Machine Learning: A Case Study (ICSE-SEIP 2019)**
https://www.microsoft.com/en-us/research/publication/software-engineering-for-machine-learning-a-case-study/
- 📖 **Read:** the **9-stage ML workflow** figure; the data-management + "data quality" pain points; the "AI-specific" challenges list.
- 🎯 **Maps to:** positions ModelGuard as the *validation* stage tooling; framing for "Real-World Usefulness" (PH §E).

**Shankar, Garcia, Hellerstein, Parameswaran — Operationalizing Machine Learning: An Interview Study (2022)**
https://arxiv.org/abs/2209.09125
- 📖 **Read:** §4 the **"Three Vs" (Velocity, Validation, Versioning)**; §5 pain points (undead features, pipeline jungles, "I don't know it broke until users complain").
- 🎯 **Maps to:** the narrative spine — ModelGuard is a *Validation* accelerator that protects *Velocity*. Use the "until production" quote in the pitch (`01-strategy §2`).

**Rabanser, Günnemann, Lipton — Failing Loudly: Detecting Dataset Shift (NeurIPS 2019)**
https://arxiv.org/abs/1810.11953
- 📖 **Read:** §3 the **experimental protocol** (perturbation type × magnitude × fraction affected); §4 which detectors win (dimensionality reduction + two-sample tests); the **shift malignancy** idea.
- 🎯 **Maps to:** the **benchmark design** in PH §A — copy the "perturbation magnitude × fraction affected" grid for injecting failures and measuring detector recall.

**Schelter et al. — Automating Large-Scale Data Quality Verification (VLDB 2018, Deequ)**
https://www.vldb.org/pvldb/vol11/p1781-schelter.pdf
- 📖 **Read:** §3 **declarative constraints**; §4 **metrics computation**; §5 **constraint suggestion** and **anomaly detection on metrics**.
- 🎯 **Maps to:** `writeback/assertions.py` — auto-*suggest* guarding assertions from profiles instead of hand-writing them (Auto-Validate below refines this).

**Grafberger, Guha, Stoyanovich, Schelter — mlinspect: A Data Distribution Debugger for ML Pipelines (SIGMOD 2021)**
Paper: https://stefan-grafberger.com/mlinspect-demo.pdf · Repo: https://github.com/stefan-grafberger/mlinspect
- 📖 **Read:** the **inspection abstraction** (annotate a pipeline DAG without manual instrumentation); the intermediate representation; example inspections (histogram-for-columns).
- 🎯 **Maps to:** the closest ancestor of our approach — study how it *statically* reasons over a lineage DAG to find distribution bugs; informs `detect/leakage.py` + `blast_radius.py` design (`architecture.md §5②`).

**Schelter, Rukat, Biessmann — JENGA: Impact of Data Errors on ML Predictions (EDBT 2021)**
Paper: https://openproceedings.org/2021/conf/edbt/p134.pdf · Repo: https://github.com/schelterlabs/jenga
- 📖 **Read:** the **corruption classes** (MissingValues, SwappedValues, Scaling, GaussianNoise, brokenCharacters); the **corrupt → evaluate impact** API.
- 🎯 **Maps to:** `benchmarks/inject.py` uses Jenga's corruptions to create labeled positives with known downstream impact (PH §A.1).

**Renggli et al. — A Data Quality-Driven View of MLOps (IEEE Data Eng. Bull. 2021)**
https://arxiv.org/abs/2102.07750
- 📖 **Read:** the mapping of **data-quality dimensions across the ML lifecycle**; the "data-centric" argument.
- 🎯 **Maps to:** validates the "CI for the ML supply chain" thesis; framing for README + `01-strategy §2`.

**Song & He — Auto-Validate (SIGMOD 2021)** https://arxiv.org/abs/2104.04659 · **Auto-Validate by-History (KDD 2023)** https://arxiv.org/abs/2306.02421
- 📖 **Read:** the **pattern-based constraint inference** (derive validation rules from data domains / history) — Auto-Validate §3–4.
- 🎯 **Maps to:** how `writeback/assertions.py` *auto-proposes* freshness/volume/value bounds rather than requiring hand-tuned thresholds.

---

## 2. Books

**Designing Data-Intensive Applications — Martin Kleppmann (O'Reilly)** — https://dataintensive.net/
- 📖 **Read:** **Ch 11 "Stream Processing"** (CDC, event sourcing, message brokers, exactly-once, idempotent consumers); **Ch 12 "The Future of Data Systems"** (the end-to-end idempotence argument); Ch 5 idempotence in replication.
- 🎯 **Maps to:** `watch`-mode design + idempotent write-back (PH §C.2, `architecture.md §8, §11`).

**Reliable Machine Learning: Applying SRE Principles to ML in Production — Chen, Murphy, Parisa, Sculley, Underwood (O'Reilly 2022)** — https://www.oreilly.com/library/view/reliable-machine-learning/9781098106218/
- 📖 **Read:** the **Monitoring** chapter (what to alert on), **Data Management Principles** (free preprint: https://research.google/pubs/chapter-1b-data-management-principles-reliable-machine-learning-applying-sre-principles-to-ml-in-production/), and the **Incident Response / postmortem** chapter.
- 🎯 **Maps to:** SLO/MTTD framing + impact-reports-as-postmortems (PH §C.4, §E).

**Designing Machine Learning Systems — Chip Huyen (O'Reilly 2022)** — https://www.oreilly.com/library/view/designing-machine-learning/9781098107956/
- 📖 **Read:** **Ch 5 "Feature Engineering" → the "Data Leakage" section**; **Ch 8 "Data Distribution Shifts and Monitoring"** (shift types, training-serving skew, monitoring).
- 🎯 **Maps to:** practitioner grounding for P1 (leakage) and P3 (drift); use its leakage examples in tests.

**Data Quality Fundamentals — Moses, Gavish, Vorwerck (O'Reilly 2022)** — https://www.oreilly.com/library/view/data-quality-fundamentals/9781098112035/
- 📖 **Read:** the **"five pillars of data observability"** (freshness, volume, schema, distribution, lineage); the chapters on building monitors + root-cause analysis with lineage.
- 🎯 **Maps to:** the five pillars *are* our detector categories; RCA-via-lineage is exactly P2 (`architecture.md §5②`).

**Fundamentals of Data Engineering — Reis & Housley (O'Reilly 2022)** — https://www.oreilly.com/library/view/fundamentals-of-data/9781098108298/
- 📖 **Read:** **Ch 2 the data-engineering lifecycle** + the **"undercurrents"** (security, data management, DataOps, orchestration).
- 🎯 **Maps to:** positions ModelGuard among data-eng concerns; the "undercurrents" map to PH cross-cutting sections.

**Driving Data Quality with Data Contracts — Andrew Jones (Packt 2023)** — https://www.packtpub.com/product/driving-data-quality-with-data-contracts/9781837635009
- 📖 **Read:** the chapters on **what a contract contains** and **enforcing contracts in pipelines**.
- 🎯 **Maps to:** the ODCS contract write-back (IP §6.5; `writeback/assertions.py`).

**Site Reliability Engineering + The Site Reliability Workbook — Google (free)** — https://sre.google/sre-book/table-of-contents/
- 📖 **Read:** **Ch 4 SLOs**, **Ch 6 Monitoring Distributed Systems** (the four golden signals), **Ch 14 Managing Incidents**, **Ch 15 Postmortem Culture**.
- 🎯 **Maps to:** ModelGuard's SLO/error-budget + incident lifecycle + blameless impact reports (PH §C.3–C.4).

**Machine Learning Design Patterns — Lakshmanan, Robinson, Munn (O'Reilly 2020)** — https://www.oreilly.com/library/view/machine-learning-design/9781098115777/
- 📖 **Read:** the **Reproducibility patterns** (Transform, Repeatable Splitting, **Bridged Schema**, Workflow Pipeline, Feature Store).
- 🎯 **Maps to:** the ML-graph **seeder** (IP §3.1) + P3 "bridged schema" reasoning about train-vs-serve schema.

---

## 3. Data quality & observability tools (compose / benchmark against)

**Great Expectations (GX Core)** — https://greatexpectations.io/ · docs https://docs.greatexpectations.io/
- 📖 **Read:** the **"Expectations"** concept + Expectation Gallery + Checkpoints + Data Docs quickstart.
- 🎯 **Maps to:** UX model for guarding assertions; a **baseline** in PH §A.3 (catches table issues, *no* lineage/model awareness).

**Soda Core / SodaCL** — https://github.com/sodadata/soda-core
- 📖 **Read:** the **SodaCL checks reference** (YAML check syntax: freshness, row_count, missing, schema).
- 🎯 **Maps to:** the shape of our open-assertions YAML artifacts (`writeback/assertions.py`).

**Elementary Data** — https://github.com/elementary-data/elementary
- 📖 **Read:** the **anomaly-detection tests** + **lineage-aware alerts** docs.
- 🎯 **Maps to:** closest OSS analog to "quality + lineage"; study its alert grouping for our incident dedup (PH §B.6). Differentiate: we cross into ML entities.

**Amazon Deequ / PyDeequ** — https://github.com/awslabs/deequ
- 📖 **Read:** the **Constraint Suggestion**, **Metrics Repository**, and **Anomaly Detection** README sections.
- 🎯 **Maps to:** auto-suggesting guarding assertions from computed metrics (`writeback/assertions.py`, PH §A).

**re_data** — https://github.com/re-data/re-data
- 📖 **Read:** freshness/volume metric computation + the alerting model.
- 🎯 **Maps to:** reference for freshness/null-rate thresholds in `detect/blast_radius.py` triggers.

---

## 4. ML observability & drift detection

**Evidently AI** — https://github.com/evidentlyai/evidently
- 📖 **Read:** **Presets** (`DataDriftPreset`, `TestSuite`) + the drift-method docs (PSI, KS, Jensen-Shannon).
- 🎯 **Maps to:** optional P4 drift signal + a **baseline** in PH §A.3 (drift *after* the fact, no upstream root cause).

**NannyML** — https://github.com/NannyML/nannyml
- 📖 **Read:** **CBPE / DLE performance estimation without labels** concept pages.
- 🎯 **Maps to:** strengthens the "silent degradation" narrative; a baseline that still can't root-cause upstream.

**Alibi Detect (Seldon)** — https://github.com/SeldonIO/alibi-detect
- 📖 **Read:** the **online/streaming drift detectors** (MMD, KS, chi²) API.
- 🎯 **Maps to:** if `watch` mode adds distribution-drift triggers, use these detectors (`architecture.md §5①`).

**whylogs (WhyLabs)** — https://github.com/whylabs/whylogs
- 📖 **Read:** **data profiling / DatasetProfile** + constraints (profile without storing raw data).
- 🎯 **Maps to:** justifies our **metadata-only / no-raw-rows** privacy property (PH §D.5).

**Frouros** — https://github.com/IFCA-Advanced-Computing/frouros
- 📖 **Read:** the **pluggable detector interface** (concept + data drift modules).
- 🎯 **Maps to:** clean design reference for making our detectors swappable.

**Open-Source Drift Detection Tools in Action (arXiv 2024)** — https://arxiv.org/abs/2404.18673
- 📖 **Read:** the head-to-head evaluation + which method to trust when.
- 🎯 **Maps to:** informs which drift lib to wire in and how to evaluate it (PH §A.5).

---

## 5. Lineage engines & standards

**OpenLineage** — https://openlineage.io/ · repo https://github.com/OpenLineage/OpenLineage
- 📖 **Read:** the **object model** (Run / Job / Dataset + **facets**), and the **columnLineage facet** spec.
- 🎯 **Maps to:** if ModelGuard emits lineage, speak OpenLineage for interop; conceptual model for `architecture.md §7`.

**Marquez** — https://github.com/MarquezProject/marquez
- 📖 **Read:** the **data model** + lineage API docs.
- 🎯 **Maps to:** reference lineage-graph schema + API ergonomics.

**SQLGlot** — https://github.com/tobymao/sqlglot · lineage API https://sqlglot.com/sqlglot/lineage.html
- 📖 **Read:** the **`sqlglot.lineage` module** + `qualify`/optimizer (how column references resolve).
- 🎯 **Maps to:** **the parser DataHub uses**; use directly if we must (re)derive column lineage from `get_dataset_queries` SQL in `detect/*`.

**DataHub: Extracting Column-Level Lineage from SQL (blog)** — https://datahub.com/blog/extracting-column-level-lineage-from-sql/
- 📖 **Read:** the full post — how DataHub builds the column graph we traverse.
- 🎯 **Maps to:** **read before writing `detect/leakage.py`** — it defines the graph our P1 cone-intersection walks.

**sqllineage** — https://github.com/reata/sqllineage
- 📖 **Read:** usage + supported dialects.
- 🎯 **Maps to:** fallback/cross-check parser for edge-case SQL.

**OpenMetadata** — https://github.com/open-metadata/OpenMetadata
- 📖 **Read:** its **lineage + data-quality test** architecture docs (for contrast).
- 🎯 **Maps to:** competitor design study — differentiate, don't copy (`01-strategy §5`).

---

## 6. Data contracts (extra write-back)

**Open Data Contract Standard (ODCS) v3.1.0 — LF Bitol** — https://github.com/bitol-io/open-data-contract-standard
- 📖 **Read:** the **v3.1.0 schema** — the `schema`, `quality`, and `SLA` sections + an example contract.
- 🎯 **Maps to:** `examples/input-data-contract.odcs.yaml` — the contract ModelGuard emits for a model's inputs (IP §6.5).

**datacontract-cli** — https://github.com/datacontract/datacontract-cli
- 📖 **Read:** `datacontract test`, `import`, `export odcs` commands.
- 🎯 **Maps to:** validate our generated contracts in CI + `benchmarks`; ship validated contracts in `examples/`.

**Data Contract Specification** — https://datacontract-specification.com/
- 📖 **Read:** the spec overview (note ODCS is now dominant).
- 🎯 **Maps to:** background; prefer ODCS for the emitted artifact.

---

## 7. Agent design, orchestration & context

**Anthropic — Building Effective Agents** — https://www.anthropic.com/research/building-effective-agents
- 📖 **Read:** **"Workflows vs. Agents"**, the **Orchestrator-workers** and **Evaluator-optimizer** patterns, **"When (not) to use agents"**, and the tool-engineering appendix.
- 🎯 **Maps to:** our LangGraph = orchestrator-workers + a human gate; evaluator-optimizer = the LLM self-check (`architecture.md §5③④`).

**LangGraph** — https://langchain-ai.github.io/langgraph/
- 📖 **Read:** **StateGraph**, **persistence/checkpointer**, **human-in-the-loop `interrupt`**, time-travel/replay.
- 🎯 **Maps to:** `agent/graph.py` (IP §7; `architecture.md §5③`).

**Model Context Protocol (spec)** — https://modelcontextprotocol.io/ · spec https://modelcontextprotocol.io/specification
- 📖 **Read:** **Tools** + **tool annotations** (`readOnlyHint`) + server structure.
- 🎯 **Maps to:** authoring the `raise_incident` MCP mutation tool contribution (IP §8.2).

**DataHub Agent Context Kit** — https://docs.datahub.com/docs/dev-guides/agent-context/agent-context
- 📖 **Read:** the toolset list + LangChain binding + connection config.
- 🎯 **Maps to:** `agent/tools.py` read/write toolset (IP §7).

**ReAct (Yao et al. 2022)** https://arxiv.org/abs/2210.03629 · **Reflexion (Shinn et al. 2023)** https://arxiv.org/abs/2303.11366
- 📖 **Read:** ReAct's reason+act loop; Reflexion's **self-critique/verbal-reinforcement** loop.
- 🎯 **Maps to:** the LLM **self-check** node that validates URNs/enums before write-back (`architecture.md §5④`, PH §A.5).

---

## 8. DevOps / CI-for-ML / SRE

**CML — Continuous Machine Learning (Iterative/DVC)** — https://github.com/iterative/cml · https://cml.dev/
- 📖 **Read:** the GitHub Action setup + **`cml comment create`** PR-report pattern.
- 🎯 **Maps to:** the stretch **PR-comment / GitHub Action** that posts ModelGuard findings on a PR (IP §8, MigrationCopilot fallback).

**DVC** — https://dvc.org/
- 📖 **Read:** data/model versioning + pipeline reproducibility use-case docs.
- 🎯 **Maps to:** the "Versioning" V + reproducible demo/benchmark state.

**Datafold `data-diff` + dbt PR impact analysis** — https://github.com/datafold/data-diff *(OSS in maintenance)*
- 📖 **Read:** the **impact-analysis-on-dbt-PRs** docs (column-level lineage → downstream diff).
- 🎯 **Maps to:** the same blast-radius idea applied to codegen; template for the migration/PR stretch.

**Recce (DataRecce)** — https://github.com/DataRecce/recce
- 📖 **Read:** the PR-review checks + diff UX.
- 🎯 **Maps to:** UX inspiration for our impact reports / PR comments.

**Open Policy Agent (OPA) / Rego** — https://www.openpolicyagent.org/
- 📖 **Read:** Rego basics + policy-as-code intro.
- 🎯 **Maps to:** express governance rules ("no model may depend on a table failing a freshness assertion") as policy ModelGuard enforces (PH §C, stretch).

**The DevOps Handbook — Kim, Humble, Debois, Willis** — https://itrevolution.com/product/the-devops-handbook/
- 📖 **Read:** the **"Three Ways"** (Flow, Feedback, Continual Learning) + the telemetry chapters.
- 🎯 **Maps to:** the "shift-left ML data reliability" cultural framing (README narrative).

---

## 9. System design & scaling

**DataHub — MCP & MCL events / architecture** — https://docs.datahub.com/docs/advanced/mcp-mcl · https://docs.datahub.com/docs/metadata-service
- 📖 **Read:** **MetadataChangeProposal vs MetadataChangeLog**; versioned vs timeseries aspects; the Kafka + Elasticsearch storage split.
- 🎯 **Maps to:** `watch`-mode MCL consumer (`architecture.md §5①, §10`, PH §C.1).

**Jay Kreps — The Log** — https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying-abstraction
- 📖 **Read:** the whole essay (short) — the append-only log as an integration backbone.
- 🎯 **Maps to:** grounds the event-driven design decision (`architecture.md §11`).

**DataHub Actions Framework** — https://docs.datahub.com/docs/actions/actions/
- 📖 **Read:** declarative **event filters** (entityType/category/operation) + pluggable actions.
- 🎯 **Maps to:** the trigger layer for `watch` mode, with a polling fallback (IP §7; `architecture.md §5①`).

**(DDIA Ch 11–12 — see §2)** — stream-processing + idempotence.
- 🎯 **Maps to:** consumer groups, at-least-once + idempotent consumers, backpressure (PH §C.2).

---

## 10. Security & governance

**OWASP Top 10 for LLM Applications (2025)** — https://genai.owasp.org/llm-top-10/
- 📖 **Read:** **LLM01 Prompt Injection**, **LLM06 Excessive Agency**, **LLM05 Improper Output Handling**, **LLM03 Supply Chain** — each entry's *Prevention* subsection.
- 🎯 **Maps to:** the concrete controls in PH §D (deterministic detection vs injection; HITL + least-priv PAT vs excessive agency; arg validation vs output handling).

**MITRE ATLAS** — https://atlas.mitre.org/
- 📖 **Read:** the **tactics matrix** + **"ML Supply Chain Compromise"** technique + case studies.
- 🎯 **Maps to:** reasoning about *malicious* vs *accidental* drift; what ModelGuard should flag as suspicious (PH §D.4).

**SLSA** — https://slsa.dev/ · **Sigstore model signing** — https://github.com/sigstore/model-transparency
- 📖 **Read:** SLSA **Levels** + **Provenance**; the model-transparency README (sign/verify model files).
- 🎯 **Maps to:** conceptual pairing — our trust score is the *governance* companion to *cryptographic* provenance (PH §D, `01-strategy §3 P4`).

**NIST AI Risk Management Framework** — https://www.nist.gov/itl/ai-risk-management-framework
- 📖 **Read:** the four functions **Govern / Map / Measure / Manage** + the Playbook.
- 🎯 **Maps to:** maps our model-risk write-back to a recognized governance vocabulary (pairs with SR 11-7 for finance).

---

## 11. Reference implementations to study (GitHub)

| Repo | 📖 Read | 🎯 Maps to |
|---|---|---|
| [stefan-grafberger/mlinspect](https://github.com/stefan-grafberger/mlinspect) | the inspection/DAG-annotation code | the technique behind `detect/*` |
| [schelterlabs/jenga](https://github.com/schelterlabs/jenga) | the corruption API | `benchmarks/inject.py` |
| [evidentlyai/evidently](https://github.com/evidentlyai/evidently) | Report/TestSuite generation | report UX + baseline |
| [MarquezProject/marquez](https://github.com/MarquezProject/marquez) | lineage data model | `architecture.md §7` |
| [elementary-data/elementary](https://github.com/elementary-data/elementary) | anomaly + lineage alerts | incident grouping/dedup (PH §B.6) |
| [datacontract/datacontract-cli](https://github.com/datacontract/datacontract-cli) | test/export odcs | `writeback/assertions.py`, `examples/` |
| [acryldata/mcp-server-datahub](https://github.com/acryldata/mcp-server-datahub) | tool definitions + mutation gating | the `raise_incident` tool PR (IP §8.2) |
| [datahub-project/datahub-skills](https://github.com/datahub-project/datahub-skills) | an existing `SKILL.md` (mirror it) | `skill/datahub-ml-guard/` (IP §8.1) |
| [iterative/cml](https://github.com/iterative/cml) | PR-comment action | the PR-bot stretch (IP §8) |
| [DataHub blog — Agents in Production (Block+Goose)](https://datahub.com/blog/agents-in-production-datahub-mcp/) | the MCP incident-response pattern | differentiate from it (`01-strategy §1`) |

---

## How to use this library (by phase)

- **Before Week 1:** #0.10 DataHub column-lineage blog (§5) + §9 MCP/MCL — you traverse this graph and consume these events.
- **Weeks 2–3 (detectors):** §1 papers give the exact check definitions; §5 SQLGlot if you must re-derive lineage; §3/§4 tools are the baselines to beat.
- **Weeks 3–4 (hardening):** §1 Jenga + Failing Loudly → `benchmarks/`; §10 OWASP/ATLAS → the security pass; §8 CML/Datafold → the PR stretch.
- **Throughout:** cite §1 papers **by name** in impact-report prose + README — it's what makes the submission read as "grounded in literature," which this judging panel will recognize instantly.
