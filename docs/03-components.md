# Everything in the box

A complete inventory: every module, command, artifact and deliverable in this
repository, with one line saying what it is for. Nothing that was built is
missing from this page.

The pages that follow explain the interesting parts in depth. This one exists so
a reader can see the whole surface at once and know what to look at.

## By the numbers

| | Count |
|---|---|
| Python modules in the `janus` package | 57, across 6 subpackages |
| Finding types (detectors that raise one) | 7, plus a coverage reporter and a catalog-wide fold |
| CLI commands | 12 |
| Console entry points | `janus`, `janus-seed`, `janus-scenario`, `janus-mcp` |
| Distributions published | 2 (`janus-datahub`, `janus-argos`) |
| Optional extras | 10 (9 features, plus `dev`) |
| DataHub aspects written | 16 kinds, plus the `Document` entity |
| Structured properties defined | 11, plus one custom property on `mlFeature` |
| Generated document types | 4 |
| Benchmark modules | 9 |
| Test modules | 80, holding 1,053 tests (982 offline, 71 integration) |
| CI jobs | 8 |
| Planted, reversible failure scenarios | 10 |

## The command line (`janus/cli.py`)

Twelve commands. Every one that runs detection shares the same core in
`agent/pipeline.py`. Full flag reference:
[docs.ahmedxsaad.me](https://docs.ahmedxsaad.me/).

| Command | What it does | Writes? |
|---|---|---|
| `scan` | Audit a table, a model, or the whole catalog; explain and write back | Yes |
| `watch` | Poll (or consume the change log) and act the moment a finding appears | Yes |
| `gate` | Judge a dry-run scan against a policy and answer in an exit code | No, unless `--write` |
| `link` | Declare which columns a model trained on, and which is its label | Yes |
| `inventory` | Every model in the graph, and what can and cannot be checked on it | No |
| `coverage` | The same sweep as one catalog figure, with a trend | Only with `--write` |
| `finops` | Tables that exist only to feed models nothing uses | No |
| `companion` | Sweep the assets one owner owns, and show them on the desktop | No |
| `crosswalk` | The map from each detector to the NIST AI RMF subcategory it evidences | No |
| `model-card` | A model card generated from the catalog | Only with `--write` |
| `evidence-pack` | The EU AI Act Article 10 evidence pack | Only with `--write` |
| `feature-card` | One Data Card per feature | Only with `--write` |

Three more console scripts: `janus-seed` builds the demo ML graph,
`janus-scenario` plants and reverts failures, `janus-mcp` serves the read-only
MCP tools over stdio.

Two more entry points that are not a shell: `janus/api.py` exposes `link_model`
and `scan_model` for a training script, and `janus/mcp_server.py` exposes three
read-only tools to an MCP client.

## The package, module by module

### Detection, `janus/detect/` (pure: no LLM, no writes)

| Module | What it answers |
|---|---|
| `blast_radius.py` | This table is stale. Which live models score on it? |
| `leakage.py` | Which feature descends from the label the model predicts? |
| `schema_drift.py` | Which training-time columns changed since? |
| `governance.py` | Sensitive source, deprecated input, and proxy candidate |
| `degraded.py` | What is knowable about a model nothing has linked |
| `coverage.py` | Which checks could not run on this model, and what is missing |
| `guard_coverage.py` | The same, folded into one catalog figure with a next step |
| `trust_score.py` | The findings rolled into 0-100 and a band, with deductions |
| `column_marks.py` | The shared upstream column walk both mark detectors reuse |
| `graph_reads.py` | The shared model, deployment and ownership reads |

Explained in [04-detectors.md](04-detectors.md).

### Write-back, `janus/writeback/` (fixed, parameterized, idempotent)

| Module | What it writes |
|---|---|
| `incidents.py` | `raiseIncident` / `updateIncidentStatus`, deduplicated |
| `properties.py` | The structured property definitions and their values |
| `labels.py` | Tags, by read-merge-emit |
| `terms.py` | Glossary terms, by read-merge-emit |
| `documents.py` | The Model Impact Report as a knowledge document |
| `model_documents.py` | The model card and the EU AI Act evidence pack |
| `feature_documents.py` | One Data Card per feature |
| `assertions.py` | The guarding assertion, its entity, and its measured run event |
| `contract.py` | The input schema as an ODCS v3.1.0 data contract |
| `link.py` | The model-to-column join, and the arguments that produced it |
| `link_infer.py` | The proposal for that join, read out of the graph |
| `trust_history.py` | One capped trend entry per scored model |
| `coverage_history.py` | One capped trend entry per catalog sweep |
| `process_instance.py` | The scan itself, as an entity in the graph it guards |
| `props/janus_props.yaml` | The 11 property definitions, as data rather than code |

Explained in [06-writeback.md](06-writeback.md).

### Orchestration and reasoning, `janus/agent/`

| Module | What it is |
|---|---|
| `pipeline.py` | `run_scan`: the detect, reason, write core every trigger shares |
| `graph.py` | The LangGraph `StateGraph` with a real `interrupt()` behind `scan --review` |
| `narrate.py` | The only place a language model runs, and it writes prose only |
| `context_kit.py` | Organizational context read through DataHub's Agent Context Kit |

### Adapters, `janus/adapters/` (offline, read-only)

| Module | What it reads |
|---|---|
| `feast.py` | A Feast repo's feature views, field mappings, label view and feature service |
| `dbt.py` | A dbt semantic model's entities, dimensions and measures from `manifest.json` |

Explained in [05-the-link.md](05-the-link.md).

### The desktop watchdog, `janus/argos/`

| Module | What it is |
|---|---|
| `protocol.py` | The wire: one event shape out, one command shape in |
| `events.py` | Turning a detector's output into what the dog does |
| `window.py` | Owning the window child process and its two pipes |
| `terminal.py` | The fallback when there is no window |
| `handler.py` | Driving the four mid-scan states off the log channel |
| `producer.py` | The four things every producer needs, in one object |

Explained in [11-argos.md](11-argos.md).

### Seeding, `janus/seed/` (never imported by production code)

| Module | What it is |
|---|---|
| `graph_spec.py` | The single source of truth for every seeded URN and value |
| `seed_ml_graph.py` | Builds the ML supply chain DataHub's datapacks lack |
| `scenarios.py` | Ten planted, reversible failures shared with the benchmark |

The ten scenarios: stale source, schema drift, target leakage, a second leak
path, a label reachable only through a common ancestor, a proxy attribute, a
label lookalike (a confusable negative), a sensitive source, a deprecated input,
and a de-linked model (an ingest having dropped the join). Each plants and
reverts, so a run can prove both directions.

### Cross-cutting, `janus/`

| Module | What it is |
|---|---|
| `env.py` | The only module that reads `os.environ` or loads `.env` |
| `config.py` | Thresholds, hop caps and score weights, with documented defaults |
| `client.py` | The only factory for a DataHub connection |
| `llm.py` | The only module allowed to import a vendor SDK |
| `models.py` | Every typed finding, remedy, score and report that crosses a layer |
| `discovery.py` | Model lookup that sees the versions DataHub's search hides |
| `gate.py` | The CI policy, as a pure function of a report |
| `render.py` | JSON output, the CI job summary, and the NIST AI RMF crosswalk |
| `logs.py` | One measurement, two renderings: logfmt for a human, JSON for a pipeline |
| `telemetry.py` | The same numbers as three OTLP metrics, behind an extra |
| `mcl.py` | The `MetadataChangeLog` consumer behind `watch --events` |
| `reconcile.py` | Re-applying a link an ingestion run just dropped |
| `lifecycle.py` | How long findings stay open, read back out of the graph |
| `finops.py` | The pipelines that exist only to feed a model nobody uses |
| `companion.py` | The general DataHub companion: what is wrong with what you own |
| `api.py` | The two functions a training script may pin to |
| `mcp_server.py` | Three read-only tools for an MCP client |
| `cli.py` | The twelve commands |

## What Janus writes into DataHub

Twelve kinds of write. Full detail in [06-writeback.md](06-writeback.md).

Incidents · tags · glossary terms · structured properties · knowledge documents ·
assertion entities · assertion run events · `mlFeature` and `mlPrimaryKey`
entities · `mlModelProperties` feature links · training-run schema snapshots ·
`dataFlow` and `dataJob` entities for the agent itself · one
`dataProcessInstance` per scan.

### The eleven structured properties

| Property | On | What it holds |
|---|---|---|
| `janus.trust_score` | `mlModel` | 0 to 100 |
| `janus.trust_band` | `mlModel` | healthy, watch, at-risk |
| `janus.risk_flags` | `mlModel` | One value per finding type that deducted |
| `janus.run_id` | `mlModel` | The run that last wrote here |
| `janus.trust_history` | `mlModel` | Capped trend, oldest first |
| `janus.scoring_version` | `mlModel` | Which scoring function produced the score |
| `janus.open_leak_columns` | `mlModel` | Columns with an open leakage incident |
| `janus.coverage_history` | `dataFlow` | Capped catalog-coverage trend |
| `janus.feature_table` | `mlModel` | What `link` was told |
| `janus.label_column` | `mlModel` | What `link` was told |
| `janus.excluded_columns` | `mlModel` | What `link` was told |

The last three are the reason a link survives an ingest: structured properties are
a separate aspect DataHub's MLflow source does not touch.

A twelfth value, `janus.source_column`, is a **custom property** on each
`mlFeature` rather than a structured property. It holds the exact warehouse
column a feature was computed from, and it is where every column-level walk
starts: DataHub's column-level lineage is dataset-to-dataset only, and the ML
sources aspect that connects a feature to a dataset is dataset-granular, so the
column itself has nowhere else to live.

## What Janus generates

Nine artifacts, all read entirely out of the catalog rather than hand-maintained:
four document types (the first four rows), three portable standards-based
formats, and two machine-readable output formats for a pipeline that is not
Janus. Explained in [07-reports.md](07-reports.md).

| Artifact | Command | Standard it follows |
|---|---|---|
| Model Impact Report | `scan` | A blameless postmortem, one per finding |
| Model card | `model-card` | Mitchell et al., FAT* 2019 |
| Evidence pack | `evidence-pack` | Regulation (EU) 2024/1689, Articles 10 and 12 |
| Data Card | `feature-card` | Pushkarna et al., FAccT 2022 |
| Guarding assertion | `scan --assertion-out` | DataHub's Open Assertions Spec |
| Input data contract | `scan --contract-out` | ODCS v3.1.0 (Linux Foundation Bitol) |
| NIST AI RMF crosswalk | `crosswalk` | NIST AI RMF 1.0 Playbook |
| Scan report as JSON | `scan --format json` | This project's own, documented shape |
| CI job summary | `gate` in Actions | GitHub's `GITHUB_STEP_SUMMARY` |

## The benchmark, `benchmarks/`

| Module | What it measures |
|---|---|
| `inject.py` | The labelled trials, built from the shipped scenarios |
| `metrics.py` | The scoring arithmetic, pure and offline-testable |
| `run_bench.py` | The live harness and the RESULTS.md renderer |
| `baselines.py` | Table-level lineage and no-lineage quality checks, on the same graph |
| `counterfactuals.py` | Applies each remedy to the graph and re-asks the detector |
| `faithfulness.py` | Whether the prose quotes only figures the narrator was shown |
| `ingested.py` | The detectors on a graph this project did not build |
| `scale.py` | What a whole-catalog sweep actually costs |
| `mutation_report.py` | The mutation score, with a verdict for every survivor |

Explained in [08-evaluation.md](08-evaluation.md); the numbers are in
[benchmarks/RESULTS.md](../benchmarks/RESULTS.md).

## Tests, `tests/`

1,053 tests across 80 modules: 982 offline and 71 marked `integration` against a
live DataHub. The
layout mirrors the package. Explained in [09-testing.md](09-testing.md).

## Deployment and packaging

| Path | What it is |
|---|---|
| `Dockerfile` | A non-root image, pinned to an exact patch Python, all four scripts |
| `docker-compose.yml` | Janus onto the network DataHub's own Quickstart creates |
| `charts/janus-watch/` | The Helm chart: `watch` as a Deployment, plus a `link --all` CronJob |
| `deploy/azure/` | Cloud-init, a systemd unit and a Caddy template for the public demo |
| `action.yml` | The bundled GitHub Action wrapping `gate` |
| `.github/workflows/` | Four workflows: CI, PyPI, the container image, the Argos binaries |

Explained in [12-operations.md](12-operations.md).

## The desktop application, `argos/`

A Tauri v2 binary. `src/main.rs` owns pixels and nothing else; `ui/argos.js` is
the state machine; `ui/walk.js` is the blast-radius overlay; `ui/sprites.js` is
the shared pixel drawing; `ui/sprites/make_sprites.py` authors the 24 frames;
`icons/make_icon.py` renders both icon formats from the same art.

## The documentation page, `site/`

A single self-contained HTML page with no fetch and no build step, published at
[docs.ahmedxsaad.me](https://docs.ahmedxsaad.me/). Its art is generated, not
authored in place: `art/make_pixels.py` bundles the sprite art the window reads,
`art/make_ornaments.py` draws the Roman ornaments, `art/make_diagrams.py` draws
the explanatory diagrams into the page, and `argos-guide.js` walks the dog down
it. `assets/make_demo.py` renders the README's animation from that same sprite
file, so there is exactly one copy of the art in the repository.

## Contributions to DataHub, `skill/` and `mcp_ext/`

The `datahub-ml-guard` skill (with three reference documents and four scripts),
a `raise_incident` tool for DataHub's MCP server, an RFC for first-class ML
incidents, and sixteen reproducible bug reports. Explained in
[14-oss-contributions.md](14-oss-contributions.md).

## Worked examples, `examples/`

Sample generated artifacts a judge can read without running anything: an impact
report, an incident payload, a guarding assertion YAML, an ODCS contract, and a
Feast repo. Plus `real-project/`: a complete postgres, dbt, scikit-learn and
MLflow stack with DataHub ingestion recipes, which is both a validation target
and a benchmark target.
