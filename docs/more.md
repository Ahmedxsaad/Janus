# Winning "Build with DataHub: The Agent Hackathon" - Strategic Playbook

## TL;DR
- **Build a write-back agent, not a chatbot.** The single highest-leverage move is an agent that *reads* DataHub context (via MCP + Agent Context Kit) and then *writes results back into the metadata graph* - assertions, structured properties, lineage edges, tags, descriptions, glossary terms. This is explicitly the strongest signal for the "Use of DataHub" criterion, and DataHub's own tooling already exposes every write primitive you need.
- **Target the "Production ML Agents" category with a healthcare or finance framing.** It is the thinnest-competition, highest-differentiation lane: DataHub ships deep end-to-end ML lineage (training data → features → training runs → models → deployments) that almost no hackathon team will exploit, and "silent ML failure before it costs money" maps directly to the judge from Pinterest - a company that went from 400,000+ mostly-undocumented tables to a curated 100,000-asset DataHub foundation powering a text-to-SQL agent with "10x the usage of the next-best AI tool" and a "70% reduction in manual documentation effort." Our #1 concept - **"Janus: a silent-ML-failure sentinel"** - sits here.
- **Ship an open-source contribution alongside the demo.** A new DataHub Skill, a connector, or an RFC earns explicit bonus credit and is exactly what founding PM Maggie Hays and the DataHub team reward - the April 2026 Town Hall spotlighted Peter Whitehead's Omni connector ("his first open-source commit landed in the project") and a community member's Pinecone connector, both built with DataHub Skills. A polished ≤3-min video + clean README + working `quickstart.sh` closes out Submission Quality.

---

## Key Findings

**1. DataHub is now a "context platform," and the whole product direction is aimed at agents.** DataHub's 2026 roadmap (framed by VP of Product James Mayfield across four pillars - AI, Discover, Observe, Govern) is an explicit shift "from documenting the enterprise data landscape to actively operating it." Mayfield stated the thesis directly at the Feb 2026 Town Hall: *"Tomorrow, the big bet that we're making is that both humans and agents will be able to leverage DataHub."* Judges will reward projects that live on the "operating" end of that spectrum - agents that take governed actions - not read-only dashboards.

**2. The write-back surface is rich and fully open-source.** Through the MCP server's mutation tools (gated by `TOOLS_IS_MUTATION_ENABLED=true`) plus the Python SDK, an agent can write: tags, glossary terms, ownership, domain membership, descriptions, and typed **structured properties**; **lineage edges** (table- and column-level, with `transformation_text` query nodes and `infer_lineage_from_sql`); **assertions** (freshness, volume, column value/metric, schema, custom SQL) via `upsertDataset*AssertionMonitor` GraphQL mutations and the `sync_*_assertion` SDK; **data contracts** (YAML `DataContract` model → MCPs); and arbitrary metadata via `MetadataChangeProposalWrapper`. The Analytics Agent already demonstrates the read→act→write-back loop (its "Save correction" skill and `/improve-context` command publish documentation fixes back to DataHub).

**3. Several things already ship out of the box - do not rebuild them.** The **DataHub Analytics Agent** is a full talk-to-data web app confirmed verbatim in the docs as "OPEN SOURCE ANALYTICS AGENT - APRIL 30, 2026 … Apache 2.0, bring your own LLM" (installs via `pip install datahub-analytics-agent` / `bash quickstart.sh`, server at `localhost:8100`): NL question → context enrichment via MCP → SQL → chart, conversation memory, multi-user, a 1-5 "context quality score," and write-back of doc improvements. **DataHub Skills** launched as the "Open Source Skills Registry for Data - April 2, 2026" with five catalog skills - `datahub-setup`, `datahub-search`, `datahub-enrich`, `datahub-lineage`, `datahub-quality` - plus connector-building skills. **Assertions, anomaly detection, column-level lineage, blast-radius impact analysis, and the Data Health Dashboard** are all native. Originality points come from *composing and extending* these, not re-implementing a talk-to-data bot or a basic search agent.

**4. The MCP server has clear gaps = opportunity.** Today's MCP tools are read-heavy: `search` (with SQL-like filter strings as of v0.5.3), `get_lineage`, `get_lineage_paths` between two assets, `get_queries`, `get_entities` (batch by URN), list schema fields, and document tools (search/save). The mutation tools cover metadata curation (tags/terms/owners/domains/descriptions/structured properties) but **do not** expose higher-order workflows like creating assertions, opening/resolving incidents, writing data contracts, or ML-lineage-aware operations. Those gaps are precisely where a new **DataHub Skill** or a thin MCP extension can be contributed for bonus credit.

**5. The precedent for what wins here is documented.** The prior in-person DataHub × Nebius hackathon - co-hosted with Nebius and Entrepreneurs First (EF), San Francisco - "wrapped on April 10 with three winning teams: **DataHub Agent On Call** (a four-agent on-call triage loop), **Project North Star** (an anomaly-to-resolution co-pilot), and **MediGuard** (healthcare data quality guardrails)." The pattern is unmistakable: **multi-agent, incident/quality-focused, action-taking, and domain-framed (healthcare) projects win.** Our recommendations lean directly into this proven pattern.

> **Verification note:** The live `datahub.devpost.com` page was not yet search-indexed at research time, so the exact prize split ($20,500 total / $6,000 Grand Prize / four Challenge Winner prizes), the five weighted judging criteria, the four challenge-category descriptions, submission rules, and the healthcare/nyc-taxi/fiction-retail dataset specs are taken from your brief and could not be independently re-confirmed against the page. Everything about DataHub's product, APIs, skills, and prior hackathons below is verified against DataHub's own docs, GitHub, and blog.

---

## Details

### A. Map of DataHub's agent-facing capabilities (and where the gaps are)

**Metadata model & APIs.** DataHub's graph is built from *entities* (datasets, dashboards, charts, dataJobs/dataFlows, mlModel, mlModelGroup, mlModelDeployment, mlFeature, mlFeatureTable, assertion, dataContract, glossaryTerm, domain, corpuser/corpgroup, structuredProperty, and more) composed of *aspects* (ownership, tags, globalTags, schemaMetadata, upstreamLineage, datasetProperties, etc.). You interact via GraphQL, REST/OpenAPI, and Python + Java SDKs. Writes go through `MetadataChangeProposalWrapper` (MCP objects) emitted over REST or Kafka; targeted field updates use PATCH builders (`DatasetPatchBuilder`, etc.).

**Lineage.** Table- and column-level. `client.lineage.add_lineage(upstream, downstream, transformation_text=..., column_lineage={...})`; `infer_lineage_from_sql` parses SQL into column-level edges using DataHub's schema-aware SQLGlot-based parser. `get_lineage()` traverses with hop control and filters. Column-level lineage powers **blast-radius impact analysis** - "which two of five connected tables actually depend on the column I'm about to change" - the operational workflow the judges from Cloudflight (data architecture consulting) and Pinterest (large platform) live inside daily.

**Data quality / assertions.** Types: freshness, volume, column (field-value and field-metric), schema, and custom SQL. Two evaluation modes: active-query (Snowflake/Redshift/BigQuery/Databricks) and ingestion-driven (any platform via the `operation` aspect). Smart/anomaly-detection assertions and Monitoring Rules exist but are largely **DataHub Cloud** features - for an open-source hackathon build, prefer the open assertions YAML spec + `run_assertions_for_asset` + your own detection logic, and note the Cloud dependency honestly.

**ML lineage (the underused crown jewel).** DataHub models the full ML supply chain: `mlModelTrainingData`/`mlModelEvaluationData` aspects, `dataProcessInstance` entities (subtype `MLFLOW_TRAINING_RUN`) for training runs, `mlFeature`/`mlFeatureTable` (Feast/etc.) consumed by models, `mlModelGroup` for versioning, and `mlModelDeployment` for endpoints. The graph is: **Training Datasets → Training Run → ML Model → ML Model Deployment**, with features and experiments attached. Connectors auto-populate this from MLflow, SageMaker, Vertex AI, Databricks, and Unity Catalog. Model Cards fields (`intendedUse`, `mlModelFactorPrompts`, `mlModelEthicalConsiderations`) support governance/model-risk framing.

**Agent surfaces.**
- **MCP Server** (`acryldata/mcp-server-datahub`, install via `uvx mcp-server-datahub` or `npx -y @acryldata/mcp-server-datahub init`): gives agents *tools* (discrete read + mutation actions). Works with Claude, Cursor, Windsurf, Cline, Codex, etc.
- **Agent Context Kit** (`datahub-agent-context` Python package): bundles those tools directly into custom agents (LangChain, Snowflake Cortex, Google ADK, Crew.ai) and locks tool versions; recommended when you want customization vs. the always-latest MCP path.
- **DataHub Skills** (`datahub-project/datahub-skills`): *instructions* that chain tools into judgment-driven workflows. SKILL.md format = YAML frontmatter (`name`, `description`) + Markdown body, optional `scripts/`, `references/`, `assets/`. Portable across Claude Code, Cursor, Codex, Copilot, Gemini CLI, Windsurf. **This is the easiest high-value open-source contribution** - a new well-scoped skill is a small, reviewable PR.

**Sample data / datapacks.** `datahub datapack load showcase-ecommerce` (1,049 entities across Snowflake/Looker/PowerBI/Tableau/dbt/Spark/Postgres/S3) and `bootstrap`; time-shifting via `--as-of`. The hackathon's `nyc-taxi` (~500k trips, 3-stage pipeline with planted freshness issues), `healthcare` (~55k synthetic patient records with planted quality issues), and `fiction-retail` (50k customers / 150k orders / clean schema) packs are purpose-built demo fixtures - the planted-issue packs are effectively pre-baked demo scripts. **Use nyc-taxi's planted freshness issues or healthcare's planted quality issues as your demo's "villain."**

### B. What wins *this* hackathon

The five criteria in your brief - Use of DataHub, Technical Execution, Originality, Real-World Usefulness, Submission Quality (+ open-source bonus) - combined with the judge panel and the Nebius-hackathon precedent, point to a clear formula:

1. **Use of DataHub - go beyond reading to writing.** Every top concept below closes a loop that *mutates the graph*: creating assertions, writing lineage, stamping structured properties, opening/annotating incidents, or publishing governance reports as DataHub documents.
2. **Technical Execution - end-to-end on the provided datapacks.** Judges must be able to run `bash quickstart.sh`-style setup and see it work. Build against a *local* DataHub Quickstart + a provided datapack so there's zero external dependency.
3. **Originality - compose, don't clone.** Do not rebuild the Analytics Agent or a search bot. Extend the graph into a workflow it doesn't yet automate (ML failure triage, model-risk audit, migration impact PRs).
4. **Real-World Usefulness - frame with money or safety.** "This schema change would have silently corrupted a production fraud model" / "this null spike in a patient-safety field would have reached a clinical dashboard." Quantify.
5. **Submission Quality - the 3-minute video is the deliverable that gets scored.** Cold-open on the pain, show the agent catching/fixing it live, end on the write-back in the DataHub UI. README with copy-paste setup + an `examples/` folder of generated artifacts.

**Category competition analysis (win-probability read):**
- **(c) Production ML Agents - LOWEST competition, HIGHEST fit.** Requires understanding DataHub's ML entities, which most teams won't invest in; directly excites the Pinterest judge (whose team's DataHub work delivered a text-to-SQL agent with 10x the usage of the next-best tool and a 70% documentation-effort reduction); "silent ML failure" is a visceral, quantifiable story. **Best expected value for a strong 2-person team.**
- **(b) Metadata-Aware Code Generation - MEDIUM competition, HIGH usefulness.** Generating PR-ready dbt/Airflow/Dagster code from *real* schemas + lineage is squarely on DataHub's "Data Engineering Agent" roadmap and thrilling to the Cloudflight architect judge. Slightly crowded because code-gen is the trendy demo.
- **(a) Agents That Do Real Work - HIGHEST competition.** The obvious category; many teams will build incident-triage/steward agents (and the Nebius winners already set a high bar). Win here only with a genuinely novel action loop.
- **(d) Open/Wildcard - VARIABLE.** Good for a distinctive regulatory/finance angle, but you're judged against everything, so the concept must be exceptional.

**Grand Prize logic:** the Grand Prize almost certainly goes to the best *overall* project across categories. A Production-ML-Agent build that also ships an open-source Skill and demos flawlessly is a credible Grand Prize contender *and* the favorite for its category prize.

### C. Project concepts, ranked

Ranking = win-probability × real-world value × 4-week feasibility for 2 strong builders.

---

**#1 - Janus: the silent-ML-failure sentinel** *(Category c: Production ML Agents)*
- **Problem:** ML pipelines fail upstream far more often than at the model - null values read as zeros, stale feature tables, silent schema changes, target leakage. Model monitoring alone never catches these; they surface as slow revenue/accuracy bleed.
- **How it uses DataHub deeply (read + write):** Reads end-to-end ML lineage (feature tables → training runs → model → deployment) and column-level lineage back to source tables. When an upstream schema/freshness/volume assertion fails or a column distribution shifts, the agent (i) traverses lineage to find every *model and deployment* in the blast radius, (ii) writes a structured-property "risk flag" + a `deprecation`/incident annotation onto the affected `mlModel`/`mlModelDeployment`, (iii) creates freshness/volume/column assertions on the offending upstream tables so the failure is caught automatically next time, and (iv) publishes a "Model Impact Report" back to DataHub as a knowledge document. **Every step writes to the graph.**
- **Domain framing:** Finance - "this stale feature table feeds the credit-risk model scoring live loan applications" (SR 11-7 / model-risk resonance). Or healthcare - "this null spike feeds a patient-readmission model."
- **Open-source contribution:** A new **`datahub-ml-guard` Skill** (SKILL.md that teaches any agent to trace ML lineage, assess model blast radius, and provision assertions) - plus optionally an RFC toward first-class "ML incident" workflow in the MCP server. Directly on-roadmap (DataHub has an open RFC on modeling AI assets).
- **Demo angle (3 min):** Cold-open on a deployed model quietly degrading. Trigger the planted upstream issue. Agent lights up the lineage graph, names the exact model + deployment at risk, auto-creates the guarding assertion, and posts the impact report - all visible in the DataHub UI.
- **Risks:** The provided datapacks may need ML entities added - pre-seed a small MLflow-style model + feature graph via the SDK (a few dozen MCPs) as part of setup. Smart/anomaly assertions are Cloud-only; use open-spec assertions + your own drift check.

---

**#2 - MigrationCopilot: PR-ready code + impact analysis before you break prod** *(Category b: Metadata-Aware Code Generation)*
- **Problem:** Engineers change a column/type or migrate a platform and "deploy and see what breaks." Blast radius is invisible until Slack lights up.
- **How it uses DataHub (read + write):** Reads real schemas + column-level lineage for a proposed change; computes the exact downstream dashboards/models/pipelines + their owners; **generates PR-ready artifacts** (updated dbt models, an Airflow/Dagster DAG patch, and a migration script) grounded in the true schema; writes back new/updated **lineage edges** and a **data contract** for the changed dataset, and stamps a structured-property "migration-reviewed" flag. Ships sample generated artifacts in `examples/`.
- **Domain framing:** Finance regulatory reporting ("changing this field alters a BCBS 239 lineage path") or any team doing schema migrations.
- **Open-source contribution:** A **`datahub-migration-impact` Skill** and/or a GitHub Action that comments blast-radius on PRs (extending the spirit of DataHub's existing `dbt-impact-action`).
- **Demo angle:** Developer proposes a column rename; agent replies with the 14 downstream assets + owners, a ready-to-merge dbt/DAG diff, and writes the new contract to DataHub.
- **Risks:** Code-gen quality is easy to fake and hard to make truly "PR-ready" - invest in one clean, verifiable end-to-end path rather than broad coverage. Medium competition.

---

**#3 - PHIGuard / PII-Governance-at-Scale agent** *(Category a or d; healthcare framing)*
- **Problem:** Sensitive data (PHI/PII) sprawls across schemas; proving where it lives and enforcing classification is manual and audit-driven (HIPAA, GDPR/CCPA).
- **How it uses DataHub (read + write):** Scans datasets, detects PII/PHI columns (regex + LLM classification), then **writes** glossary-term tags + PII structured properties, propagates classification downstream along column-level lineage, and generates an on-demand **compliance coverage report** as a DataHub document ("where PHI lives, gaps in classification"). This composes the existing `datahub-enrich` skill's pattern but adds lineage propagation + audit reporting.
- **Domain framing:** Healthcare - the ~55k-record synthetic healthcare datapack with planted issues is tailor-made; patient-safety + HIPAA narrative.
- **Open-source contribution:** A **`datahub-pii-propagate` Skill** that extends `datahub-enrich` with lineage-aware classification propagation.
- **Demo angle:** "Tag every column containing PHI across the catalog, propagate downstream, and give me an auditor-ready coverage report" - executed live, written back to DataHub.
- **Risks:** Closest to an existing shipped capability (`datahub-enrich` already tags PII). Differentiate hard via **downstream propagation + audit report generation**, or Originality score suffers. This is why it ranks below #1-#2.

---

**#4 - IncidentRootCause: multi-agent data-incident triage loop** *(Category a: Agents That Do Real Work)*
- **Problem:** When a dashboard shows wrong numbers, root-causing across tools takes hours/days of Slack archaeology.
- **How it uses DataHub (read + write):** A small **multi-agent** team - a Detector (watches assertions), an Investigator (walks column-level lineage upstream to the offending source column + last-modified event), and a Scribe (writes an incident annotation, root-cause document, and a new guarding assertion back to DataHub). Uses `get_queries` to inspect the actual SQL in the failing path.
- **Open-source contribution:** A **`datahub-incident-triage` Skill** bundle.
- **Demo angle:** Planted nyc-taxi freshness issue → agents converge on the stale stage → post root cause + fix in minutes.
- **Risks:** **Highest overlap with the Nebius winner "DataHub Agent On Call."** Judges have seen this; you must out-execute a known-good bar. Solid, but not the differentiated bet.

---

**#5 - CostGuard: lineage-aware unused-asset & spend optimizer** *(Category d: Open/Wildcard)*
- **Problem:** Teams over-provision because they can't safely tell what's unused. DataHub-driven deprecation has produced documented results: per DataHub's 2026 IDC Business Value Study, customers reported saving 20-25% on data storage costs (~$250,000-$300,000/year), and DPG Media cut monthly Snowflake costs 25% using DataHub metadata tests + impact analysis.
- **How it uses DataHub (read + write):** Reads usage stats + lineage to find zero-downstream-consumer assets, verifies safety via impact analysis, and **writes** deprecation flags + a "safe-to-delete" structured property + a savings report document.
- **Open-source contribution:** A **`datahub-deprecation-advisor` Skill**.
- **Risks:** Less visually dramatic; usage-stats fidelity in the demo datapacks may be limited. Good "genuine value" story, weaker demo punch.

---

**#6 - ContractForge: agent-authored data contracts from observed behavior** *(Category b/d)*
- **Problem:** Data contracts are valuable but nobody writes them.
- **How it uses DataHub (read + write):** Infers reasonable schema/freshness/volume expectations from profiles + query history and **writes** YAML `DataContract` entities + backing assertions to DataHub, with human-approval gating.
- **Open-source contribution:** A **`datahub-contract-author` Skill**.
- **Risks:** Overlaps conceptually with assertions/quality; needs a crisp "observe → propose → write contract" loop to feel novel.

---

## Recommendations

**Primary pick: #1 Janus (Production ML Agents), with a healthcare *and* finance demo toggle.** It maximizes all five criteria at once, sits in the least-crowded category, excites the Pinterest and Cloudflight judges, exploits DataHub capabilities (ML lineage) that competitors will ignore, and has a natural open-source artifact (the `datahub-ml-guard` Skill). If ML entity seeding proves heavier than expected by end of Week 1, **fall back to #2 MigrationCopilot** (same write-back philosophy, no ML-graph seeding required).

**Staged 4-week execution plan (2 people):**
- **Week 1 - Foundation & de-risk.** Stand up local DataHub Quickstart; load a datapack; get MCP server + Agent Context Kit talking to your agent (Claude/LangGraph). Seed a minimal ML graph (model group, model, feature table, training run, deployment, lineage to source tables) via the SDK. **Kill-criterion:** by end of week you can programmatically read ML lineage AND write one assertion + one structured property back. If ML seeding stalls, pivot to #2.
- **Week 2 - Core loop.** Implement detect → traverse-blast-radius → flag-model → provision-assertion → write-impact-report. Get one clean end-to-end path working on a single planted issue.
- **Week 3 - Polish + contribution.** Package the `datahub-ml-guard` Skill (SKILL.md + references + scripts), test it in Claude Code/Cursor, and open the PR to `datahub-project/datahub-skills` (and optionally an RFC). Add the finance/healthcare framing toggle. Harden setup into one script.
- **Week 4 - Submission craft.** Record and edit the ≤3-min video; write the README + `examples/` artifacts; dry-run the judge setup on a clean machine; buffer for bugs. Submit 24h early.

**Benchmarks that change the plan:** If by end of Week 2 the write-back loop isn't reliably mutating the graph, cut scope to a single assertion type + single report rather than adding features. If the open-source PR isn't review-ready by mid-Week 3, ship it as a well-documented standalone skill repo linked from the README (still counts as a contribution) rather than blocking on upstream merge.

**Submission-quality tactics (maximize that criterion):**
- **Video:** ≤3 min, cold-open on the *pain* (money/safety), one uninterrupted live loop, close on the DataHub UI showing the write-back. No slideware.
- **README:** one-command setup (`bash quickstart.sh` that boots DataHub + loads datapack + seeds ML graph + runs the agent), a labeled architecture diagram, an explicit "How this uses DataHub (reads AND writes)" section mapping each action to an API, and a "What we did NOT rebuild" note to pre-empt the Originality rubric.
- **`examples/` folder:** committed sample artifacts (generated impact reports, created assertion YAML, the Skill files) so judges see output without running anything.
- **Contribution callout:** link the merged/open PR prominently to bank the bonus.

---

## Caveats
- **Hackathon-specific facts are unverified.** The exact prizes, weighted criteria, category wording, submission rules, and dataset record counts/planted-issue details come from your brief; the live Devpost page was not indexed at research time. Re-read the official rules on day one and adjust category choice/scope accordingly.
- **Cloud-only features.** Smart/anomaly-detection assertions, Monitoring Rules, and some observability UI are DataHub *Cloud* capabilities. Build the hackathon project on open-source DataHub Core primitives (open assertions YAML, SDK writes, your own detection) and disclose any Cloud dependencies.
- **ML entities may need seeding.** The demo datapacks are warehouse/BI-centric; you will likely add ML lineage entities yourself via the SDK. Budget Week 1 for this and treat it as a kill-criterion.
- **Don't over-scope multi-agent.** Multi-agent framing impresses, but a reliable single loop that demonstrably writes to the graph beats a flaky agent swarm. Add agents only after the core loop is bulletproof.
- **Originality risk on quality/PII/incident concepts.** `datahub-enrich`, `datahub-quality`, and the Nebius "Agent On Call" winner already cover adjacent ground; differentiate via lineage propagation, ML-blast-radius, or audit-report generation, and state explicitly what you extended vs. reused.
