# Decision Log

Running log of project decisions. Newest first. Every significant decision gets
an entry: what we decided, the options considered, why, and the result.

Entry template:

```
## D-NNN: <short title> (YYYY-MM-DD)
- Decided by:
- Decision:
- Options considered:
- Why:
- Result:
```

---

## D-047: ModelGuard-Bench measures a live graph, and the sweep is what makes it mean anything (2026-07-22)
- Decided by: Ahmed Saad (chose the bench as the next build, and the core scope),
  built by Claude
- Decision: `benchmarks/` ships three modules and a generated report.
  `inject.py` holds the labelled trial matrix, `metrics.py` the pure scoring
  arithmetic, `run_bench.py` the live harness and the RESULTS.md renderer.
  Three choices carry the design:
  (a) **Trials run against a live DataHub**, never against fixture graphs.
  (b) **The freshness sweep walks the SLA boundary** (0.5h to 72h against a 6h
  SLA, including 5.5, 6.0 and 6.5) rather than only planting the 30h demo lag.
  (c) **A trial waits for the graph to show the state it planted, never for the
  detector to give the expected answer**, and a precondition that never lands is
  reported as an unscoreable error rather than counted as a miss.
- Options considered: for (a), scoring against the existing in-memory doubles,
  which would have run in milliseconds and needed no Docker; rejected because a
  detector scored on our own fakes measures the fakes, and the first question a
  judge asks about a benchmark is what it ran against. For (b), reusing the demo
  scenario alone; rejected as untestable in the sense that matters, see below.
  For (c), polling until the expected finding appeared, which is the obvious way
  to handle async indexing and manufactures perfect recall by construction.
- Why: a benchmark's own credibility has to be demonstrable, so the same rule
  tests live under (tests/CLAUDE.md rule 6: a green suite proves nothing until a
  fault kills it) was applied to the benchmark itself. Changing
  `FreshnessSignal.is_stale` from `>` to `>=`, a one-character off-by-one, was
  caught by the trial sitting exactly on the SLA: freshness precision fell 1.00
  to 0.83 and the false-positive rate rose 0.00 to 0.20, past its 0.05 target.
  Under the demo scenario alone that same bug scores a clean 1.00 and ships. The
  scoring arithmetic and the ground-truth labels were mutation-checked the same
  way, six mutations, each killing the offline suite.
- Result: 14 trials, all correct on the run committed as `benchmarks/RESULTS.md`:
  precision, recall and F1 of 1.00 per detector, false-positive rate 0.00,
  blast-radius recall 1/1 naming the live deployment, 0 duplicate incidents on
  rerun, trust score and band both written. Detector calls median 0.12s;
  DataHub's index convergence, reported separately so its latency is not blamed
  on ModelGuard, median 2.85s. 34 new offline tests (304 total).
  `modelguard/seed/scenarios.py` gained `plant/revert_leakage`, which the
  flagship detector had no negative control without (the seeder always plants
  the leak, D-032); it sets the fine-grained lineage outright because
  `add_lineage` patches additively and cannot undo an edge.
  `_active_incident_urns` became public `attached_incident_urns`, and was
  renamed because it never filtered to active ones.
- One bug was introduced and caught in the same session, and it is the reason
  the Week 1 gate is worth keeping green. The leakage scenario first stamped
  `transformOperation` on the leaking edge to satisfy seed/CLAUDE.md rule 5's
  "every scenario declares itself". That field is part of what GMS keys a
  fine-grained edge on, so the marked edge and the seeder's unmarked one are two
  *different* edges: the next `modelguard-seed` added its own alongside and the
  column lineage grew to five. Every unit test still passed, the benchmark still
  scored 1.00, and `test_seeding_twice_leaves_the_graph_byte_for_byte_identical`
  failed. The marker was removed rather than worked around: the leak is the
  seeded baseline, not an anomaly planted on top of it, so there was no
  planted-versus-real ambiguity for a marker to resolve. An offline twin of that
  assertion now guards it, since the integration suite needs a live Quickstart
  and will not run on every change.
- Not built, and RESULTS.md says so in its own words rather than leaving it to
  be inferred: Jenga corruption injection, the Great Expectations / Evidently /
  naive-lineage baseline comparison, the 10k/100k scale test, `golden/`
  regression reports, and any scoring of narrative quality.

## D-046: Close the rest of the docs audit: improvements status, skill/CLAUDE.md, strategy-doc annotations (2026-07-22)
- Decided by: Ahmed Saad (requested the audit), fix applied by Claude
- Decision: (1) docs/plan/04-improvements.md's status block, unedited since
  2026-07-09, said P2-3 (shared pydantic models) and P2-4 (central config
  module) were "still open"; both landed (modelguard/models.py,
  modelguard/config.py + env.py) and are now marked adopted. (2) skill/CLAUDE.md
  carried the same unqualified "first ML skill in the registry" claim already
  corrected elsewhere under D-043; corrected here too. (3)
  01-strategy-modelguard.md's two rationale-table rows asserting the "first ML
  skill" gap are historical decision rationale, not live status, so they were
  annotated in place (what was verified, then, and that it no longer holds,
  citing D-043) rather than rewritten. (4) examples/CLAUDE.md called its four
  artifacts "Planned"; all four have existed since 2026-07-13/16, reworded to
  "generated and committed."
- Options considered: for the strategy doc, (a) delete the outdated rows, (b)
  leave them, (c) annotate in place; (c) chosen, since a strategy doc's
  rationale table is a record of why a decision was made and deleting it loses
  that history, but leaving it unqualified misleads a reader in 2026-07-22 into
  thinking the gap still holds.
- Why: this is the same class of problem as D-045, docs that quietly stopped
  matching reality. Caught by re-reading every CLAUDE.md and plan doc in full
  (not just their Change Log tails) against the actual code and the current
  upstream PR queue, at the user's request after the D-044 stash-recovery work
  surfaced how easily this repo's docs drift once nobody is re-reading them
  end to end.
- Result: 04-improvements.md, skill/CLAUDE.md, 01-strategy-modelguard.md, and
  examples/CLAUDE.md corrected; all four CLAUDE.md edits carry a Change Log row.
  No other CLAUDE.md or plan doc in the repo was found to diverge from the code
  on this pass (root, modelguard/ and its five subpackages, tests/, benchmarks/,
  mcp_ext/, docs/ were all read in full and checked against the actual files
  and directory contents they describe).

## D-045: Correct the plan docs' watch description from Kafka-first to polling-shipped (2026-07-22)
- Decided by: Ahmed Saad (requested the docs audit), fix applied by Claude
- Decision: architecture.md, 01-strategy-modelguard.md, and the E-checklist in
  03-production-hardening.md described `watch` as consuming DataHub's
  `MetadataChangeLog` via the Actions framework (Kafka), with polling as a
  fallback. Corrected all three to state what actually shipped: `watch` is a
  polling loop only (`cli.py watch`, D-039), and the MCL/Kafka consumer is the
  documented, unbuilt upgrade path, not a fallback behind a built primary.
- Options considered: (a) leave the plan docs as originally written since
  02-implementation-plan.md already carries the correct D-039/D-040 landed
  notes, (b) propagate the correction to every plan doc that makes the same
  claim.
- Why: docs/CLAUDE.md rule 1 requires updating the plan doc and logging a
  decision the moment plan and reality diverge, specifically so it does not
  silently rot. The implementation plan had the correct note; architecture.md
  (the repo's own "how it works" source of truth), the strategy doc, and the
  production-hardening checklist did not, and a judge or contributor reading
  any of the other three would have believed an Actions/Kafka consumer exists.
- Result: architecture.md section 5 (component catalog), section 8 (execution
  modes table), and section 10 ("production" posture) now state the polling
  reality with the Kafka path marked as not built. 01-strategy-modelguard.md's
  scaling bullet corrected the same way. 03-production-hardening.md's
  checklist item is now checked, since the polling-fallback branch of its own
  "event-driven or a documented polling fallback" criterion is satisfied.

## D-044: Agent instructions use linked compatibility files (2026-07-17)
- Decided by: Repository maintainers
- Decision: Every directory that contains a `CLAUDE.md` also contains an
  `AGENTS.md` relative symlink to it.
- Options considered: (a) duplicate each instruction file, (b) use relative
  symlinks, (c) maintain only the Claude-specific filename.
- Why: `AGENTS.md` is recognized by Codex and other agent tooling, while a
  relative symlink keeps one source of truth and works after the repository is
  moved or cloned.
- Result: Claude and `AGENTS.md`-aware tools read identical repository and
  directory-specific instructions without synchronization work. Built
  2026-07-17 on a branch that went stale before merging; landed directly on
  main 2026-07-22 after a docs audit found the symlinks missing (12 of 12
  `CLAUDE.md` directories: root, benchmarks, docs, examples, mcp_ext,
  modelguard, modelguard/agent, modelguard/detect, modelguard/seed,
  modelguard/writeback, skill, tests).

## D-043: Drop the "first ML-reliability skill" claim after checking the upstream queue (2026-07-21)
- Decided by: Ahmed Saad (requested the review), fix applied by Claude
- Decision: Remove the "first ML-reliability skill for the DataHub skills registry"
  wording from README.md and the "(primary - first ML skill in the registry)"
  header in docs/plan/02-implementation-plan.md section 8.1. Replace with a claim
  that is actually true and actually differentiating: `datahub-ml-guard` wraps a
  real, tested, deterministic detection engine, not an LLM asked to eyeball a
  lineage graph.
- Options considered: (a) keep the "first" framing, (b) drop it with no
  replacement, (c) drop it and state the real differentiator; (c) chosen.
- Why: a review of datahub-project/datahub-skills open PRs (done as part of this
  review, 2026-07-21) found roughly seven overlapping ML-reliability skills
  already submitted (drift, trust-score, leakage, blast-radius, silent-failure
  RCA), several predating this branch by up to two weeks (#29 2026-07-08, #31
  2026-07-09, #33 and #34 2026-07-11). The "first" claim was false and would have
  read as a hackathon-crowd, unverified assertion. Diffing every one of those PRs'
  file lists showed all of them ship SKILL.md plus reference/template markdown
  only, no backing detection code, tests, or benchmark: the actual gap
  `datahub-ml-guard` fills is determinism and verifiability, which is true,
  checkable, and does not depend on being first.
- Result: README.md and 02-implementation-plan.md corrected before the PR (#8)
  was approved and merged. No other "first"/"primary gap" language found
  elsewhere in the branch.

## D-042: OSS contribution delivery route (2026-07-21)
- Decided by: Ghassen Naouar, applied by Claude
- Decision: Deliver all three Section 8 contributions the maximal way. (1) The
  skill goes as a full PR to datahub-project/datahub-skills. (2) The MCP tool goes
  as a full code PR to acryldata/mcp-server-datahub (register inside
  `register_mutation_tools()`, use the module-level `execute_graphql()` helper),
  with the RFC linked or filed as a companion issue. (3) The Most Valuable Feedback
  survey is submitted through the Devpost feedback form, not a PR. The concrete
  steps and division of labor are recorded in docs/plan/05-oss-delivery.md.
- Options considered: For the skill, (a) standalone linked repo only (the plan says
  it still counts), (b) also a full upstream PR; (b) chosen. For the MCP tool,
  (a) file the RFC as an issue only, (b) a full code PR plus the RFC; (b) chosen.
- Why: The upstream PRs are stronger contribution evidence than standalone
  artifacts. The MCP server already has a mutation-gating pattern
  (`TOOLS_IS_MUTATION_ENABLED`) and a GraphQL helper to plug into, so the code PR is
  a bounded change, not a rewrite. The survey mechanism is fixed by the rules.
- Result: docs/plan/05-oss-delivery.md records the steps. No upstream work started
  yet (the maintainer opens the forks/PRs and completes the Devpost form); the
  built artifacts and this delivery doc are committed to feat/oss-contribution.

## D-041: Section 8 OSS contributions ship (2026-07-21)
- Decided by: Ghassen Naouar, applied by Claude
- Decision: Deliver all three points of plan section 8. (1) The `datahub-ml-guard`
  skill lands under `skill/datahub-ml-guard/` (SKILL.md + scripts/ + references/),
  mirroring the upstream datahub-enrich format. Its `scripts/` are thin bash
  wrappers that shell out to the `modelguard` CLI (`modelguard-seed`,
  `modelguard scan --table/--model`), not a fork of detection logic. (2) The MCP
  contribution ships as both a thin `mcp_ext/raise_incident_tool.py` (wrapping the
  same `raiseIncident` GraphQL mutation as writeback/incidents.py, gated by
  `TOOLS_IS_MUTATION_ENABLED`, with an offline self-check) and `RFC-ml-incidents.md`.
  (3) The Most Valuable Feedback survey is assembled into `docs/most-valuable-feedback.md`
  from the 12 findings in plan section 8.3.
- Options considered: For the skill scripts, (a) thin CLI wrappers, (b) standalone
  Python importing modelguard, (c) embedded logic; (a) chosen (satisfies
  skill/CLAUDE.md rule 3, no logic fork). For the MCP tool, (a) RFC only, (b) thin
  tool file plus RFC; (b) chosen (a runnable artifact plus the metadata-model RFC
  the mlModel-incident gap actually needs).
- Why: The skill and the feedback survey are the primary and cheapest bonus points;
  the MCP tool is a stretch but the mutation already exists in writeback/, so a thin
  wrapper is small. Shelling to the CLI keeps one detection implementation.
- Result: `skill/datahub-ml-guard/` (7 files), `mcp_ext/raise_incident_tool.py`
  (self-check green) + `RFC-ml-incidents.md`, `docs/most-valuable-feedback.md`, and a
  README OSS-contributions section. Benchmarks and quickstart.sh remain for section 9.

## D-040: Reconcile watcher recovery and require explicit agent approval (2026-07-17)
- Decided by: Codex, requested by the repository maintainer
- Decision: A watch recovery resolves the active incident and removes only the
  recovered ModelGuard risk metadata, preserving unrelated tags, terms, and flags.
  The public LangGraph API requires an approval callback unless the caller passes
  `auto_approve=True` explicitly. Polling failures retry with bounded exponential
  backoff, and the process-local checkpointer is documented as synchronous rather
  than durable.
- Options considered: (a) leave recovery as console output, (b) clear all model
  metadata, (c) reconcile only the finding types and assets present in the prior
  typed report; (c) chosen. For approval, (a) default auto-approval, (b) default
  denial requiring explicit approval, and (c) a separate explicit auto-approve
  flag were considered; (c) preserves the demo path without granting library
  callers an implicit write capability.
- Why: An at-risk incident, tag, and trust score that survive a healthy poll are
  operationally false and can drive unsafe decisions. Implicit writes violate the
  least-agency boundary. A watcher that exits on one transient GMS error is not an
  always-on monitor. ModelGuard's current CLI is synchronous, so claiming durable
  replay from MemorySaver was misleading.
- Result: `watch` now reconciles incident status, risk flags, tags, leakage terms,
  and trust state; retries failures up to a bounded delay; and the agent API is
  approval-safe by default. New unit tests cover recovery and omitted approval.

## D-039: Section 7 lands as a LangGraph StateGraph over the existing pipeline, opt-in and dependency-light (2026-07-16)
- Decided by: Ghassen Naouar (chose scope: agent + watch), design by Claude
- Decision: `agent/graph.py` runs the same `detect -> reason -> [approval] ->
  write_back` order the pipeline already runs, but as a compiled `StateGraph` whose
  nodes delegate to the pipeline's own deterministic functions (`_detect`,
  `_write_back`, `_persist_trust`, `_trust_scores`) and to `narrate`. The one new
  capability is a real `interrupt()` human-approval gate: `run_agent` pauses after
  reasoning, hands the caller a preview, and writes only what is approved.
  `scan --review` (or `--auto-approve` for the recorded demo) opts into it; the
  default `scan` and `watch` keep using `run_scan`. `watch` is a polling loop that
  shares the pipeline core and acts on finding-set transitions (a new problem or a
  recovery), auto-approving because it is unattended.
- Options considered: (a) StateGraph over the existing pipeline nodes, opt-in via
  --review (chosen); (b) make the agent the default write path (rejected: forces the
  optional `agent` extra on the out-of-the-box `modelguard scan`, which must run on
  core deps with no LLM); (c) the plan's original `agent/tools.py` + `AgentExecutor`
  with the Agent Context Kit toolset and umbrella `langchain` (rejected: an LLM
  tool-caller that could decide to write contradicts the design law that detection
  is deterministic and the LLM only narrates); (d) event-driven `watch` on the
  DataHub Actions/Kafka framework (deferred: the plan flags Kafka timing as a demo
  risk, so polling ships and Actions is the documented upgrade path).
- Why: The pipeline already delivers the loop, so Section 7's value is the approval
  interrupt and replayability, not new detection. Reusing the pipeline nodes keeps a
  single write path and means the swap "touches nothing else" as the agent CLAUDE.md
  intended. Keeping langgraph an optional, lazily-imported extra preserves the
  judge's light out-of-the-box path. Dropping the umbrella `langchain` and
  `datahub-agent-context` from the extra follows from the StateGraph design: nothing
  imports them.
- Result: `agent/graph.py` (`run_agent`, `build_scan_graph`), `scan --review` /
  `--auto-approve`, and the `watch` command land on branch feat/langgraph-agent-watch.
  langgraph pinned to 1.2.9 in the `agent` extra. The checkpointer is the in-memory
  MemorySaver, and the findings/reports ride in an in-process holder rather than the
  checkpointed state, so no ModelGuard dataclass is msgpack-serialized (which
  langgraph warns will be blocked in a future release). 9 new unit tests: 4 on the
  approval gate (the preview is shown before any write; approve writes, decline and
  clean write nothing) and 5 on watch's transition logic. 266 unit tests green.

## D-038: The input data contract is an ODCS v3.1.0 artifact rendered from a model's inputs, not a graph write (2026-07-16)
- Decided by: Ghassen Naouar (chose scope and validation), design by Claude
- Decision: Section 6.5 lands as `writeback/contract.py`, a pure renderer that
  reads a model's training-run input datasets and their current `schemaMetadata`
  and emits an Open Data Contract Standard v3.1.0 YAML: one ODCS schema object per
  input table (columns as `physicalType` verbatim, `logicalType` mapped where
  unambiguous and omitted otherwise, `required` from the field's `nullable` flag)
  and one `slaProperties` freshness entry per table carrying the SLA ModelGuard
  guards. The CLI exposes it as `modelguard scan --model <m> --contract-out <path>`;
  it writes the file to disk, not the graph, and renders even on a clean or dry-run
  scan because a contract describes the model's boundary, not a finding. No volume
  or distribution expectation is emitted: ModelGuard measures none, and fabricating
  one would break writeback rule 10.
- Options considered: (a) render to an examples/ artifact and validate with
  datacontract-cli (chosen); (b) also write the contract back to DataHub as a graph
  entity; (c) reconstruct volume/quality expectations to fill more ODCS fields.
- Why: The plan frames the ODCS contract as a standards-based artifact for judges
  (section 6.5, examples/), so a renderer plus a CLI flag is the whole job; a graph
  write is a separate, larger scope. datacontract-cli ships the ODCS 3.1.0 JSON
  Schema, so `datacontract lint <file>` is a real, reproducible validation, not a
  hand-check. Emitting only schema + freshness keeps every value traceable to a
  fact DataHub holds or a config the guarding assertion already uses.
- Result: `writeback/contract.py` (10 unit tests), the `--contract-out` flag, and
  `examples/input-data-contract.odcs.yaml` generated from a real seeded scan and
  linted green against ODCS 3.1.0. datacontract-cli is a dev/validation tool, not a
  runtime dependency; `modelguard/` never imports it.

## D-037: The trust score is a rollup of a scan's findings, written only for models it found something about (2026-07-16)
- Decided by: Ghassen Naouar (chose the aggregation model), design by Claude
- Decision: P4 (`detect/trust_score.py`) starts at 100 and subtracts fixed
  weights for the risks a scan actually found about a model: upstream failure
  (40), leakage (20), schema drift (15), freshness lag scaled by lag/SLA (15),
  missing owner (10). The weights and the band thresholds (healthy >= 70, watch
  >= 40, else at-risk) live in `config.py` as documented defaults, no env
  plumbing. The score and band are written as `modelguard.trust_score` (number)
  and `modelguard.trust_band` (string) structured properties on the mlModel, in
  a pass after every per-finding write so the read-merge preserves the risk
  flags already set.
- Options considered: (a) score every scanned model, writing 100 for a clean
  one; (b) score only models a finding named, so a clean model is never written;
  (c) re-traverse the graph inside the trust detector to fill in dimensions the
  scan did not check (e.g. freshness for a `--model` scan).
- Why: (a) breaks the invariant that a clean scan writes nothing, and would
  stamp 100 on a model the scan barely assessed. (c) doubles the detection work
  and blurs which evidence the score rests on. (b) keeps the score honest about
  its own evidence: it aggregates exactly the findings this scan produced, so a
  `--table --model` scan of the seeded demo (stale source + leakage + drift +
  unowned) scores the live model 0, while a `--model`-only scan of the same
  model scores 55 because freshness was not checked. The trade-off is that the
  score reflects the scan's scope, which is documented on the detector.
- Result: `TrustScore`/`TrustBand` in models.py; `trust_inputs_from_findings`
  reduces findings to inputs, `trust_score` applies the weights; the pipeline's
  `_trust_scores`/`_persist_trust` run the pass. `modelguard.trust_band` added to
  the props YAML. 7 unit tests plus pipeline coverage; the phase 2 drift/trust
  integration gate asserts a score lands on the live model.

## D-036: Training-serving schema drift diffs a snapshot captured at training time, not a reconstructed timeline (2026-07-16)
- Decided by: Ghassen Naouar (chose the snapshot over the timeline), design by Claude
- Decision: P3 (`detect/schema_drift.py`) reads a schema fingerprint captured on
  the training run at seed/training time (a JSON map of input dataset URN to
  `field_path -> native_type`, in the run's `customProperties` under
  `modelguard.training_schema`) and diffs it against the input dataset's current
  `schemaMetadata`. Added, removed, and retyped columns each become a
  `SchemaChange`; a drifted input raises a `DATA_SCHEMA` incident on the dataset.
- Options considered: (a) reconstruct the training-time schema from DataHub's
  Timeline / Schema-History API as the plan (section 5.2) originally specified;
  (b) walk the versioned `schemaMetadata` aspect backward to the version whose
  `lastModified` predates the training run; (c) snapshot the schema on the
  training run and diff the current schema against it.
- Why: (a) and (b) both reconstruct "as-of training" from catalog history, which
  is fragile (versions compact, ingestion lags training, `lastModified` stamps
  are unreliable) and needs version-history support added to the test fake. (c)
  is exactly how TFX/TFDV guard against training-serving skew (Breck et al.
  2019): freeze a schema at training and validate serving data against it. It is
  deterministic, robust, testable against the existing fake, and arguably more
  correct than trusting a catalog's version history. It diverges from the plan's
  Timeline wording, so the plan and this log are updated together
  (docs/CLAUDE.md rule 1). The fingerprint is keyed by input dataset URN, not a
  flat field map, so a run with several inputs diffs each against the schema that
  input actually had, never another input's.
- Result: `graph_spec.training_schema_fingerprint` + `TRAINING_SCHEMA_PROPERTY`;
  the seeder writes it on the training run; `scenarios.plant_schema_drift` /
  `revert_schema_drift` mutate the feature table's live schema (one retype, one
  drop, one add), deliberately leaving the leakage columns untouched so the two
  Phase 2 scenarios coexist. `SchemaDriftFinding` carries the changes; narrate.py
  and documents.py dispatch on it, citing Breck 2019. The structured-property
  detail the plan named (`drifted_fields`, `training_run_urn`) is carried in the
  incident description and the impact report rather than as extra structured
  properties: the `input-schema-drift` risk flag already makes the model
  filterable, and the report holds the full column list. 8 detector unit tests
  (including a false-positive control and a malformed-snapshot guard), scenario,
  narrate, document, model, and pipeline tests, plus the drift/trust integration
  gate.

## D-035: A deep review before the phase 2 PR found and fixed a same-model, two-finding overwrite (2026-07-13)
- Decided by: Ahmed Saad (requested the review), fixes applied by Claude
- Decision: An 8-angle review (correctness, removed-behavior, cross-file,
  reuse, simplification, efficiency, altitude, conventions) ran against the
  leakage detector diff before opening the PR. Every finding it produced with a
  concrete failure scenario was fixed, not just logged, and each fix got a
  regression test that fails on the reverted code (mutation-checked per
  tests/CLAUDE.md rule 6).
- Options considered: (a) open the PR as-is and fix findings in follow-ups,
  (b) fix everything the review surfaced before opening the PR.
- Why: Two of the findings were directly reachable through the flagship demo
  command itself, `modelguard scan --table loans_raw --model credit_risk_v3`,
  because `credit_risk_v3` is simultaneously downstream of `loans_raw`'s blast
  radius and independently leaking its own label. Shipping a demo command that
  silently corrupts its own output would have been worse than the delay of
  fixing it first.
- Result: Six real defects fixed, verified against a live Quickstart:
  1. `assign_properties` replaces a structured property's value outright, and
     `_write_back` ran it once per finding; two findings on one model in a
     single scan (the case above) had the second overwrite the first's
     `risk_flags`. Fixed with read-then-union at the call site in
     `agent/pipeline.py`, which required teaching `FakeGraph.emit_mcp` to
     actually update its aspect store (it previously only recorded the call),
     since a real GMS applies a versioned aspect to its primary store
     synchronously and the fake needed to match that to test the fix at all.
  2. `_document_id` was keyed on the model alone; the same dual-finding case
     had the second `publish_impact_report` call silently overwrite the
     first's document. Fixed by folding a hash of the finding's own
     `resource_urn` into the id, so two distinct findings on one model land on
     two distinct, individually convergent documents.
  3. `leak_path`'s "skip my own starting column" guard also skipped checking
     whether that column *was itself* the label, so a feature aliased directly
     from the label with zero transformation, the most direct form of leakage,
     went undetected. Fixed with an explicit zero-hop check before the
     traversal.
  4. `column_path` was built from a `LineageResult`'s whole path rather than
     truncated at the matched label, so a path continuing past the label to a
     more distant ancestor was quoted as part of the proof. Fixed by
     truncating at the matched index.
  5. `run_scan`'s non-dry-run path fell back to the rendered-but-unwritten
     assertion YAML when no write in the batch had one, so `--assertion-out`
     could describe a check for a table that was never found stale in a
     dual-target scan. Fixed by falling back to empty instead.
  6. `_system_prompt` used a hand-rolled isinstance check with a silent `else`,
     unlike its three `singledispatch` siblings in the same file, which raise
     on an unregistered type. Converted to `singledispatch` so a future third
     finding type fails loudly instead of silently getting the wrong brief.
- Not fixed, logged instead: three call sites (`agent/pipeline.py`'s
  `_write_back`, `cli.py`'s `_print_finding`, and `narrate.py` before this fix)
  discriminate `Finding` subtypes via `isinstance` while `documents.py` and the
  rest of `narrate.py` use `singledispatch`, and the `Finding` ABC itself is a
  third mechanism for the same problem. Converting everything to one pattern
  now would be premature for two concrete subclasses; revisit when the third
  detector (schema drift) lands and the actual shape of the problem is known.
  `leakage_max_hops` also has no `MODELGUARD_*` env override unlike the other
  three `ScanConfig` thresholds: fixed, since it was a one-line gap against an
  explicit existing rule (modelguard/CLAUDE.md rule 3), not a design question.

## D-034: Phase 1 merged to main; Phase 2 branches from a clean base (2026-07-13)
- Decided by: Ahmed Saad
- Decision: feat/phase-1-core-loop, gated and passing since D-028, merged to main
  as PR #3. feat/phase-2-leakage branches from main, not from the old branch.
- Options considered: (a) stack Phase 2 on the unmerged branch, (b) merge first.
- Why: A week of gated work sitting unmerged blocks every later branch from
  starting clean, and the repo's own git rules expect one logical change per
  branch merged in sequence.
- Result: main is at the Phase 1 gate. 201 unit tests and 25 integration tests
  reproduced on a second machine, confirming the gate is not laptop-specific.

## D-033: Finding becomes an abstract base, one subclass per detector (2026-07-13)
- Decided by: Ahmed Saad (chose the ABC over plain fields), design by Claude
- Decision: Finding is an ABC declaring finding_type, resource_urn, incident_type,
  severity, title, evidence, and models_at_risk. FreshnessFinding wraps a
  BlastRadius; LeakageFinding wraps a LeakingFeature. narrate.py and
  documents.py dispatch on the concrete type via functools.singledispatch.
- Options considered: (a) plain title: str and evidence: Mapping fields any
  caller could set, (b) an ABC with abstract properties per subclass.
- Why: (a) drops the guarantee that a title is a pure function of graph facts,
  which is the whole invariant D-027 exists to protect: any caller could pass
  any string as a title and the dedup key would stop meaning anything. (b) keeps
  that guarantee at the type level and costs nothing extra once a third detector
  (schema drift) lands, since it subclasses the same contract.
- Result: ModelAtRisk split into ModelRef (identity, liveness, ownership; shared
  by every detector) and ModelAtRisk (adds hops and features_at_risk; freshness
  only). ScanReport.writes is a tuple of FindingWrites, so one scan can now run
  both detectors and report on both targets independently.

## D-032: A label is a glossary term, read from two aspects and unioned (2026-07-13)
- Decided by: Ahmed Saad (chose the glossary term over a structured property or
  config value), verified against a live GMS by Claude
- Decision: A column is a model's label when it carries the
  urn:li:glossaryTerm:modelguard.label term, checked two ways: the term aspect
  directly on the schemaField (what ModelGuard and the seeder write), and
  editableSchemaMetadata on the parent dataset (what the DataHub UI writes when
  a human tags a column by hand). Both were emitted and read back against a live
  Quickstart before this was decided.
- Options considered: (a) a structured property on the dataset naming the label
  column, (b) a glossary term on the column, checked on both routes, (c) a
  MODELGUARD_LABEL_COLUMN config value.
- Why: (c) is a property of one scan's config, not of the data, and does not
  scale past one model. (a) works but is invisible in the UI's own vocabulary.
  (b) is what a data team already reaches for, and reading both write paths
  means a human declaring a label in the UI, touching no ModelGuard config,
  makes leakage detection start working on their model.
- Result: modelguard/writeback/terms.py (ensure_term, add_term, read_terms),
  read-merge-emit like labels.py. modelguard/seed/seed_ml_graph.py declares the
  seeded label. config.py holds the term URN with a default, because it is a
  name, not a credential (D-029's distinction).

## D-031: Column-level lineage returns the dataset in urn; the column is in paths (2026-07-13)
- Decided by: Claude (for Ahmed Saad), verified against a live GMS before any
  detector code was written
- Decision: detect/leakage.py reads LineageResult.paths, a list of LineagePath
  with a schemaField urn and a column_name, and never compares
  LineageResult.urn against a label column.
- Options considered: none; this is a measured fact about the installed SDK,
  not a design choice.
- Why: get_lineage(source_column=..., direction="upstream") on the seeded graph
  returns LineageResult.urn == loans_raw, the table, even though the query was
  column-scoped. A detector that compared urn against the label column's
  schemaField URN would find nothing on a graph that leaks, and would report it
  clean: a silent false negative on the exact failure this detector exists to
  catch. The column identity survives only in paths.
- Result: tests/detect/test_leakage.py::test_the_detector_reads_paths_and_not_the_result_urn
  reproduces the exact shape and would fail if the bug were reintroduced;
  confirmed by mutation-testing the detector (reverting to a urn comparison
  kills 10 of 14 tests). Worth a Most Valuable Feedback entry: LineageResult.urn
  for a column-level query is the dataset, and this is not documented.

## D-030: The LLM is provider-agnostic (2026-07-10)
- Decided by: Ghassen Naouar
- Decision: ModelGuard names no vendor. `MODELGUARD_LLM_PROVIDER` selects one of
  anthropic, openai, or google; `MODELGUARD_LLM_MODEL` is the provider's model id
  verbatim; `MODELGUARD_LLM_API_KEY` is the credential. `modelguard/llm.py` is the
  only module allowed to import a vendor SDK or name a vendor's model, and it is
  the only place a new provider is added. `--llm-provider` and `--llm-model`
  override the first two; the key is deliberately not a flag, because a credential
  in argv lands in the shell history and the process table.
- Options considered: (a) hardcode Claude as the plan proposed, (b) a provider
  registry with lazy imports, (c) `langchain.chat_models.init_chat_model`.
- Why: (a) makes a vendor choice on the reader's behalf and bakes a model id into
  tracked code. (c) would pull the whole `langchain` package in as a hard
  dependency for a two-line dispatch. (b) keeps each binding an optional extra
  (`pip install -e ".[openai]"`) and fails with an actionable message when the
  package is absent.
- Result: All three chat classes were introspected before the registry was
  written: `ChatAnthropic`, `ChatOpenAI`, and `ChatGoogleGenerativeAI` accept the
  same four keyword arguments (`model`, `api_key`, `temperature`, `max_tokens`)
  even though their underlying field names all differ, so one uniform call reaches
  every vendor. A missing binding degrades to template prose rather than failing
  the scan. `agent/narrate.py` now reads no environment and knows no vendor: it is
  handed an `LLMConfig` or None.

## D-029: One module reads the environment, and identity values have no defaults (2026-07-10)
- Decided by: Ghassen Naouar (rule), implemented by Claude
- Decision: `modelguard/env.py` is the single entry point for configuration. It is
  the only module that calls `load_dotenv` and the only one that touches
  `os.environ`. Values that identify a system, an account, or a vendor (server
  URLs, tokens, API keys, provider names, model ids) get no default and no
  fallback. Algorithm parameters (a 6 hour SLA, a 3 hop cap) keep documented
  defaults in `config.py`: they are reproducible on every machine and identify
  nothing. Related settings are all-or-nothing. Secrets never reach a log line, an
  exception message, a repr, or a CLI flag. Now root CLAUDE.md code rule 6.
- Options considered: (a) let each module read what it needs, (b) centralize in
  env.py, (c) centralize and additionally forbid defaults for identity values.
- Why: this was not hypothetical. `load_dotenv` ran only inside
  `client.connect()`, and `modelguard scan` builds its `ScanConfig` *before* it
  connects, so `MODELGUARD_FRESHNESS_SLA_HOURS=99` in `.env` was silently ignored
  and the built-in 6 hour default was used instead. Whether a configured value was
  honored depended on whether something had already opened a DataHub connection.
  Configuration that depends on call order is configuration that lies. Separately,
  `narrate.py` had hardcoded `DEFAULT_LLM_MODEL = "claude-opus-4-8"` and read
  `ANTHROPIC_API_KEY` directly: a vendor decision and a machine-specific value
  compiled into tracked code, the exact thing D-015 forbade for the server URL.
- Result: Fixed and verified. Four unit tests enforce the rule rather than trusting
  anyone to remember it: no module but `env.py` may read `os.environ`, none but
  `env.py` may load `.env`, no module may name a vendor key variable, and
  `env.scrub()` strips a credential out of any third-party exception text before it
  is logged. A provider SDK that echoes the failing request, key included, into its
  exception message can no longer put that key in our logs. `.env` and
  `.env.example` now carry an identical key set, so copying the example produces a
  working run; the retired `ANTHROPIC_API_KEY` migrates to
  `MODELGUARD_LLM_API_KEY`.

## D-028: Phase 1 gate PASSED; the core loop is closed (2026-07-10)
- Decided by: Claude (for Ghassen Naouar), per the plan's section 4.3
- Decision: Phase 1 (Problem 2, end to end) is complete. `modelguard scan
  --table loans_raw` detects the planted stale load, traverses the blast radius
  into the live model, and writes back an incident, a tag, structured
  properties, a guarding assertion plus its measured result, and a Model Impact
  Report document. `tests/integration/test_phase1_loop.py` is that criterion,
  executable: 14 tests, passing, and repeatable back to back.
- Options considered: (a) declare the loop done from a manual UI inspection,
  (b) make the criterion an executable, hermetic integration test.
- Why: The same reason the Week 1 gate was executable (D-016). The gate resolves
  any incident an earlier run left open before scanning, so it exercises the
  create path rather than silently reusing an old incident, and so it passes
  twice in a row against a dirty graph.
- Result: Verified on a live OSS Quickstart. Both directions hold: the planted
  failure is caught at CRITICAL, and a reverted table produces a clean scan that
  writes nothing. Phase 2 (leakage, schema drift, trust score) may start.

## D-027: The LLM writes prose, never the incident title (2026-07-10)
- Decided by: Ghassen Naouar (chose LLM prose in Phase 1), design by Claude
- Decision: `agent/narrate.py` drafts the incident description and the report's
  assessment with Claude at temperature 0. The incident **title** stays a pure
  function of the failing table's name, with no lag, no timestamp, and no model
  output in it. Every number in the incident body and the report comes from the
  finding's `evidence` mapping, rendered by a deterministic `fact_block`; the
  narrative is appended after the facts, never in place of them.
- Options considered: (a) deterministic templates only, (b) LLM prose with a
  deterministic title, (c) LLM prose everywhere including the title.
- Why: (c) is unsound, not merely risky. The incident dedup key is
  `(resource_urn, type, title)` (D-013), so a reworded title on a rerun raises a
  duplicate incident on every scan. (b) buys better prose without touching the
  key. Facts stay deterministic so an incident is fully trustworthy even when
  the narrative degraded to the template.
- Result: `narrate()` never raises. A missing `ANTHROPIC_API_KEY`, a network
  error, a rate limit, an empty reply, or a reply over 1200 characters all fall
  back to the deterministic template and record `source=template`. `scan` and
  the whole unit suite therefore run offline and with no API key, which is the
  judge's out-of-the-box path. Graph metadata reaches the model only inside a
  delimited `<evidence>` block the system prompt names as untrusted data
  (OWASP LLM01, agent/CLAUDE.md rule 3).

## D-026: Emit the assertion entity and a real evaluation result (2026-07-10)
- Decided by: Ghassen Naouar (chose YAML + entity + run event), design by Claude
- Decision: The guarding assertion is written three ways: as open-assertions
  YAML in `examples/`, as an `assertionInfo` aspect so it appears on the
  dataset's Quality tab, and as an `assertionRunEvent` carrying the freshness
  result ModelGuard actually computed during that scan.
- Options considered: (a) YAML artifact only, (b) YAML plus the assertion
  entity, (c) YAML plus entity plus a run event.
- Why: (c) is the strongest demo and, importantly, it is honest here. The
  assertion's declared type is `DATASET_CHANGE`, and the detector's freshness
  measurement reads the dataset's `operation` aspect, which is exactly what
  "the dataset changed" means. The declared check and the executed check are the
  same check, so the result is measured, not fabricated. A fresh table writes
  SUCCESS. `nativeResults` records `evaluated_from` so nobody mistakes it for a
  warehouse query, and the report repeats the caveat.
- Result: `DataHubClient.assertions` turned out to be **DataHub Cloud only**: it
  imports `acryl_datahub_cloud` and raises `SdkUsageError` on OSS. The OSS path
  is to render the YAML, validate it by parsing it back through DataHub's own
  `AssertionsConfigSpec`, and emit the aspects directly. Validating through
  DataHub's parser means the committed artifact and the graph entity cannot
  drift. Scheduled evaluation and anomaly detection remain Cloud features, and
  every report says so.

## D-025: Do not restamp the assertion source on every run (2026-07-10)
- Decided by: Claude (for Ghassen Naouar)
- Decision: `upsert_guarding_assertion` calls `get_assertion_info()` and sets
  `AssertionSource` itself, reading `source.created` back from any existing
  assertion instead of restamping it.
- Options considered: (a) call `get_assertion_info_aspect()`, the obvious API,
  (b) call `get_assertion_info()` and own the source stamp.
- Why: `get_assertion_info_aspect()` runs `_ensure_source_created`, which calls
  `make_assertion_source()` and stamps the current time. The aspect would then
  differ on every scan, so a rerun would rewrite it forever and the graph would
  never converge. Idempotency is not optional (root CLAUDE.md code rule 5).
- Result: The assertion URN is a guid over `(entity, type, id_raw)`, so it is
  stable per table, and the aspect is now byte-identical across reruns. The
  source type is `INFERRED`: ModelGuard derived this check from an observed
  failure rather than a human authoring it.

## D-024: Refuse a freshness SLA of a day or more (2026-07-10)
- Decided by: Claude (for Ghassen Naouar)
- Decision: `build_assertion` raises when `sla_hours >= 24` instead of emitting
  the assertion.
- Options considered: (a) emit whatever the caller asks for, (b) refuse the
  range where the SDK is wrong, (c) hand-build the aspect and bypass DataHub's
  entity model.
- Why: `FixedIntervalFreshnessAssertion.get_assertion_info` builds its schedule
  from `timedelta.seconds` rather than `timedelta.total_seconds()`. A lookback
  of 30 hours therefore emits an assertion of 6 hours
  (`timedelta(hours=30).seconds == 21600`), silently. Emitting a wrong assertion
  is worse than emitting none, and (c) would give up the validation that keeps
  the YAML artifact and the graph entity in step.
- Result: Guarded, with a unit test on both sides of the boundary and an
  integration assertion that 6 hours arrives as 21600 seconds. Added to the
  Most Valuable Feedback list (plan section 8.3) as a reproducible upstream bug.

## D-023: updateIncidentStatus takes IncidentStatusInput (2026-07-10)
- Decided by: Claude (for Ghassen Naouar)
- Decision: The `updateIncidentStatus` mutation declares
  `$input: IncidentStatusInput!`, not `UpdateIncidentStatusInput!`.
- Options considered: None. The plan's snippet and DataHub's mutation docs both
  name a type the schema does not have.
- Why: GMS 1.5.0.6 answers `Validation error (VariableTypeMismatch)`. Confirmed
  by introspecting `Mutation.updateIncidentStatus` on the live server.
- Result: Fixed in `writeback/incidents.py`. The bug shipped in Phase 0 and was
  invisible because nothing called `resolve_incident`, and the unit test drove a
  fake graph that cannot validate a schema. The Phase 1 integration gate calls
  it for real, which is what caught it. A reminder that a fake-backed unit test
  cannot verify a wire contract.

## D-022: Impact reports are Document entities, not institutionalMemory links (2026-07-10)
- Decided by: Ghassen Naouar (chose "try Document, fall back"), probe by Claude
- Decision: The Model Impact Report is written as a first-class
  `datahub.sdk.document.Document`, linked to the model through `related_assets`.
  No fallback path is shipped.
- Options considered: (a) Document entity with an `institutionalMemory`
  fallback, (b) `institutionalMemory` link only, (c) a markdown file only.
- Why: The plan assumed the report could only be written through the MCP
  server's `save_document` write tool. The installed SDK has a real `Document`
  entity, and a probe against the local OSS Quickstart accepted it. Since the
  entity works, the fallback would be code that never executes, which the repo
  rules forbid (root CLAUDE.md code rule 3).
- Result: The report is a searchable graph entity with a stable id derived from
  the model, so reruns update one document rather than accumulating one per
  scan. If a future GMS rejects the entity, the fallback lands then, with a test.

## D-021: Freshness is read from the operation aspect (2026-07-10)
- Decided by: Claude (for Ghassen Naouar)
- Decision: The blast-radius detector measures staleness from the dataset's
  `operation` aspect (`lastUpdatedTimestamp`), and `seed/scenarios.py` plants
  the failure by emitting that aspect with a backdated value.
- Options considered: (a) read a failing assertion result, (b) read the
  `operation` aspect, (c) profile the table and compare row counts.
- Why: (b) is DataHub's own record of when a dataset last changed, it needs no
  warehouse connection, and it makes the guarding assertion's `DATASET_CHANGE`
  type describe exactly what we measured (D-026). (a) is circular in Phase 1:
  the assertion is the thing we are writing.
- Result: `operation` is a **timeseries** aspect. It must be read with
  `graph.get_latest_timeseries_value(urn, OperationClass, {})`; `get_aspect`
  raises a TypeError for it. Emitting it appends an event rather than replacing
  one, so reverting the scenario means emitting a newer event announcing a
  refresh, which is what a recovered pipeline would do anyway.

## D-020: Downstream lineage crosses into ML entities (2026-07-10)
- Decided by: Claude (for Ghassen Naouar), verified against a live GMS
- Decision: The blast-radius detector uses a single
  `client.lineage.get_lineage(direction="downstream")` call to span the whole
  supply chain, and reads deployments from `mlModelProperties` separately.
- Options considered: (a) one lineage call, (b) a lineage call for the dataset
  cone plus a manual relationship bridge (`DerivedFrom`, `Consumes`) into ML
  entities, in case lineage stopped at the dataset boundary.
- Why: The plan flagged this as unconfirmed and D-019 implied the boundary was
  real. It is not. `MLFeatureProperties.sources` (`DerivedFrom`) and
  `MLModelProperties.mlFeatures` (`Consumes`) both declare `isLineage: true`, so
  the traversal reaches `loans_raw -> customer_features` (hop 1) `-> mlFeature`
  (hop 2) `-> mlModel` (hop 3) in one call. The bridge in (b) is unnecessary.
- Result: Two behaviors to know. `MLModelProperties.deployments` (`DeployedTo`)
  is **not** a lineage edge, so deployments come from the aspect, and that is
  what decides severity. And once `max_hops` exceeds 2, DataHub switches to a
  full-graph search and returns entities **beyond** the cap (a model group came
  back at hop 4 for a cap of 3), so the detector filters on `hops` rather than
  trusting the server. `LineageResult.type` is a display string; the entity type
  is taken from the URN, which is authoritative.

## D-019: Week 1 gate PASSED; no pivot to MigrationCopilot (2026-07-10)
- Decided by: Claude (for Ghassen Naouar), per the plan's kill-criterion
- Decision: ModelGuard clears the Week 1 gate. The project continues. The
  MigrationCopilot fallback is not triggered.
- Evidence, against DataHub Quickstart v1.5.0.6 with acryl-datahub 1.6.0.13:
  (a) READ: `get_lineage(source_column="prior_default_flag", direction="upstream")`
      resolves one hop to loans_raw, the table holding the label column; the
      model resolves to its two features, its training run, and its live
      deployment.
  (b) WRITE: three structured properties land on the mlModel
      (trust_score=62.0, risk_flags=[target-leakage], run_id), and a FIELD
      incident lands on the leaking column.
  (c) IDEMPOTENT: the gate ran three times in three separate processes; the
      stable-title finding still has exactly one incident, and a second seed
      leaves every aspect byte-for-byte identical.
- Result: 11 integration tests, 57 unit tests, ruff and mypy strict clean.
  Phase 0 is complete. Phase 1 (blast-radius loop, scenarios.py) is unblocked.

## D-018: Dedup incidents via the IncidentOn relationship (2026-07-10)
- Decided by: Claude (for Ghassen Naouar)
- Decision: Read a resource's incidents by traversing the `IncidentOn`
  relationship inbound (`graph.get_related_entities`), then filter on each
  incident's own status. Do not read the resource's `incidentsSummary` aspect.
- Options considered: (a) `incidentsSummary` on the resource, as the plan and
  the aspect model imply, (b) search incidents filtered by their `entities`
  field, (c) the `IncidentOn` relationship index.
- Why: (a) silently returns nothing. On a Quickstart GMS the `incidentsSummary`
  aspect is never written, not for a dataset and not for a schemaField; the
  entity carries only its key aspect. A summary-based dedup therefore finds no
  existing incident and duplicates every finding on every scan. This actually
  happened: a gate run left two identical active incidents on one column.
  (b) fails with a GraphQL non-null violation from GMS
  ("field ... declared as a non null type, but the code ... wrongly returned").
  (c) is populated and correct.
- Result: Idempotency verified across three consecutive gate runs. Two GMS bugs
  worth reporting in the Most Valuable Feedback survey: the unwritten
  `incidentsSummary`, and the incident search non-null violation.

## D-017: Incidents attach to data, not to models (2026-07-10)
- Decided by: Claude (for Ghassen Naouar), forced by the metadata model
- Decision: A finding becomes an incident on the `dataset` or `schemaField` it
  concerns. Model-level risk is expressed with structured properties on the
  mlModel. `raise_incident` validates the target's entity type up front.
- Options considered: (a) raise the incident on the mlModel as the plan says,
  (b) raise it on the offending dataset or column and carry model risk as
  structured properties, (c) create a proxy dataset per model.
- Why: (a) is impossible. `incidentInfo.entities` declares
  `entityTypes: [dataset, chart, dashboard, dataFlow, dataJob, schemaField]`,
  and GMS rejects an mlModel URN with a 500 and a Java stack trace. The plan
  assumed the model was the target throughout sections 4.2, 5.1, and 5.3.
  (c) invents entities to work around the model rather than following it.
  (b) is also the better design: an incident is about a broken data asset,
  while a trust score is a property of a model. A leakage finding on
  `customer_features.prior_default_flag` points precisely at the leaking column.
- Result: `INCIDENT_ENTITY_TYPES` is derived from the aspect schema so it cannot
  drift. Column targets are resolved through the parent dataset's
  `schemaMetadata`, because `graph.exists()` is False for every schemaField;
  that check also catches a misspelled column, which `exists` never would.

## D-016: Mutation-test the suite rather than trust green checkmarks (2026-07-10)
- Decided by: Ghassen Naouar (requested), applied by Claude
- Decision: Before accepting a test suite, inject deliberate faults into the code
  under test and confirm the suite goes red. Faults tried: feature sources made
  column-granular, the source_column bridge dropped, incident dedup made
  title-blind, the property merge made destructive, and the GMS URL given a
  silent fallback.
- Options considered: (a) trust that a passing suite implies coverage, (b) add a
  mutation-testing dependency such as mutmut, (c) hand-inject a fault per
  behavior the tests claim to protect.
- Why: (a) is exactly what failed here. Four of five injected faults were caught,
  but "incident dedup ignores the title" passed the whole suite: the only
  distinct-finding test varied the incident type, so a type-only dedup satisfied
  it. That is the bug that would silently swallow a second leakage finding on
  one model. (b) is worth doing later; (c) costs minutes and found the gap now.
- Result: Added a same-type, different-title dedup test. The mutant now dies.
  The integration test test_seeding_twice_converges was also rewritten: it
  compared a SeedResult built from constants, so it passed even if the seeder
  wrote nothing. It now diffs real aspects (mlFeatures, upstreams, schema
  fields, fine-grained edges) before and after a second seed.

## D-015: No default values for environment configuration (2026-07-10)
- Decided by: Ghassen Naouar
- Decision: client.py applies no fallback for any environment variable.
  DATAHUB_GMS_URL is required and raises DataHubConnectionError when unset or
  blank. The previous DEFAULT_GMS_URL = "http://localhost:8080" was removed. A
  unit test asserts no module-level string in client.py starts with http.
- Options considered: (a) keep the Quickstart URL as a convenience default,
  (b) require every variable, documenting values only in .env.example.
- Why: A hardcoded fallback is a machine-specific value living in tracked code,
  which the repo rules forbid. It also converts a missing .env from a loud
  failure into a silent connection to whatever happens to listen on that port.
- Result: .env is now genuinely required. .env.example documents each variable,
  including how to mint a DATAHUB_GMS_TOKEN and the fact that the OSS Quickstart
  runs with authentication disabled so the token may be left blank.

## D-014: Seed the warehouse tables instead of depending on a datapack (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: seed_ml_graph.py creates loans_raw and customer_features with
  explicit schemas, using the same URNs the showcase-ecommerce datapack would
  use, rather than assuming the datapack is loaded.
- Options considered: (a) require `datahub datapack load showcase-ecommerce`
  first and seed only ML entities on top, (b) create both warehouse tables
  ourselves at the datapack's URNs, (c) invent our own URNs.
- Why: Column-level lineage needs schemaField URNs, which need a schema. Option
  (b) is a no-op enrichment when the datapack is present and still works when it
  is not, so the gate and the judge's path never depend on datapack contents we
  cannot verify offline. Option (c) would forfeit the "lineage into a real
  warehouse table" story.
- Result: The seeder is self-contained. Loading the datapack remains optional
  realism for the demo, not a prerequisite for the gate.

## D-013: Dedup incidents on (resource, type, title), not on run_id (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: The incident dedup key is (resourceUrn, incident_type, title) over
  the resource's active incidents. run_id is stamped into the description as
  provenance and is deliberately excluded from the key.
- Options considered: (a) the literal key from writeback/CLAUDE.md rule 2,
  (resourceUrn, finding_type, run_id), (b) drop run_id from the key,
  (c) emit incidents on a deterministic URN derived from a hash of the finding.
- Why: run_id changes every run by definition, so (a) makes every scan raise a
  fresh duplicate, contradicting the plan's own idempotency test in section 9
  ("run scan twice, exactly one incident per finding"). (c) is more strictly
  idempotent but bypasses the raiseIncident mutation the plan and the demo rely
  on. (b) keeps the mutation and satisfies the test.
- Result: Implemented and unit-tested. writeback/CLAUDE.md rule 2 corrected.

## D-012: Correct the plan's verified SDK symbols against 1.6.0.13 (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: Trust the installed package over the plan. Four symbols the plan
  marked [verified] are wrong for acryl-datahub 1.6.0.13:
  MLModel.add_group (use the model_group constructor argument),
  client.create_training_run and client.add_input_datasets_to_run (do not exist;
  emit a DataProcessInstance with mlTrainingRunProperties and
  dataProcessInstanceInput), client._emit_mcps (use client.entities.upsert or
  graph.emit_mcps). There are no SDK entity classes for MLFeature,
  MLPrimaryKey, MLFeatureTable, or MLModelDeployment; those are aspect MCPs.
  The incident type COLUMN does not exist; the column-scoped type is FIELD.
  MLFeatureProperties.sources declares entityTypes [dataset], so a feature
  cannot point at a column; the exact column is carried in customProperties.
- Options considered: none. Root CLAUDE.md rule 7 already mandates verifying
  every SDK symbol against the installed package.
- Why: Building on the plan's snippets would have failed at the first write, and
  the leakage detector's whole design assumed column-granular feature sources.
- Result: 02-implementation-plan.md sections 3, 5.1, 6.1, and 13 corrected, and
  writeback/CLAUDE.md rule 4 corrected. Code cites the verified signatures.

## D-011: Pin Python to 3.11 (2026-07-09)
- Decided by: Claude (for Ghassen Naouar), per improvement P1-3
- Decision: .python-version pins 3.11; pyproject requires >=3.11,<3.12.
- Options considered: (a) 3.12, which the acryl-datahub classifiers advertise,
  (b) 3.11, which the acryl-datahub CLI asks for at runtime, (c) leave unpinned.
- Why: On 3.12 the CLI prints "Python versions above 3.11 are not actively
  tested with yet. Please use Python 3.11 for now." A runtime warning from the
  package itself outranks its own classifier metadata.
- Result: Warning gone on 3.11.12. This is the drift P1-3 predicted.

## D-010: Adopt improvements P1-2, P1-3, P1-4; defer P2-3, P2-4, P2-5 (2026-07-09)
- Decided by: Ghassen Naouar
- Decision: Adopt pyproject.toml (P1-2), the Python pin (P1-3), and
  ruff/mypy/pre-commit (P1-4) before Phase 0 code lands. The shared pydantic
  models (P2-3), the central config module (P2-4), and structured logging
  (P2-5) stay open proposals. P1-1 (repo rename) and P2-1 (CI) not yet decided.
- Options considered: (a) Phase 0 exactly as the plan writes it, ignoring
  04-improvements, (b) foundation plus Phase 0, (c) foundation only.
- Why: 04-improvements argues migrating before any code lands is free and later
  is churn. The deferred three describe contracts between layers that do not
  exist yet: Phase 0 produces no detector findings, no tunable thresholds, and
  no multi-node run to correlate.
- Result: Foundation and Phase 0 landed together on feat/phase-0-de-risker.
  Revisit P2-3 and P2-4 when the first detector lands in Phase 1.

## D-009: Make the Week 1 gate an executable integration test (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: The kill-criterion lives in tests/integration/test_week1_gate.py,
  run with `pytest -m integration`, rather than staying prose in the plan.
- Options considered: (a) leave it prose and verify by eye in the UI, (b) a
  standalone gate script printing PASS or FAIL (improvement P2-2), (c) a marked
  pytest module.
- Why: The pivot decision must not rest on wishful thinking. (c) reuses the
  existing runner and the skip-when-unreachable convention from tests/CLAUDE.md
  rule 2, and doubles as the judge's smoke test, so it beats a second bespoke
  entry point.
- Result: Nine integration tests cover both halves of the gate plus idempotency.
  scenarios.py deliberately not written: the Week 1 schedule does not call for
  it and no detector consumes it yet, so it would be dead code.

## D-008: Move hackathon specs into docs/hackathon-specs/ (2026-07-08)
- Decided by: Ahmed Saad
- Decision: The eight captured Devpost spec files (01 to 08) plus their README
  index live in docs/hackathon-specs/.
- Options considered: none, direct request.
- Why: docs/ was mixing official hackathon reference with our own plan,
  research, and logs; separating them keeps docs/ navigable.
- Result: Moved 2026-07-08; docs/CLAUDE.md and root CLAUDE.md updated to match.

## D-007: Scaffold branch based on the docs branch (2026-07-08)
- Decided by: Claude (for Ahmed Saad)
- Decision: Create chore/project-scaffold off docs/hackathon-plan-documents,
  not off main.
- Options considered: (a) branch off main, (b) branch off the docs branch,
  (c) commit the scaffold directly onto the docs branch.
- Why: The scaffold references the plan docs, which only exist on the docs
  branch; committing scaffold onto a docs-named branch would mix concerns.
- Result: Merge order is docs/hackathon-plan-documents first, then
  chore/project-scaffold.

## D-006: One CLAUDE.md per part, global rules only at the root (2026-07-08)
- Decided by: Ahmed Saad (requested), shaped by Claude
- Decision: A root CLAUDE.md holds all repo-wide rules; each directory gets a
  short local CLAUDE.md; every CLAUDE.md ends with a Change Log table.
- Options considered: (a) one big root file only, (b) root plus per-directory
  files with duplicated rules, (c) root plus short local files, no duplication.
- Why: Claude Code loads nested CLAUDE.md files only when working in that
  directory, so short local files optimize token usage; duplication rots.
- Result: 12 CLAUDE.md files created; duplication forbidden by the root file.

## D-005: Strip em dashes and emojis from all existing docs (2026-07-08)
- Decided by: Ahmed Saad (rule), applied by Claude
- Decision: Team rule is no em dashes and no emojis anywhere. Applied
  retroactively to docs/: em dashes become hyphens; semantic markers become
  text tags ([verified] for the checkmark, [confirm] for the warning sign,
  [paper]/[book]/[tool]/[standard]/[security] for the legend icons).
  Also renamed "less .md" (filename contained a space) to less.md.
- Options considered: (a) apply the rule to new content only, (b) full
  retroactive cleanup.
- Why: The user marked this rule as very important and universal; leaving
  hundreds of violations in tracked docs would contradict it.
- Result: Cleanup committed separately so the mechanical diff is easy to review.

## D-004: Conventional Commits, max 60-char subject (2026-07-08)
- Decided by: Ahmed Saad (requirements), format chosen by Claude
- Decision: type(scope): summary, imperative, lowercase, no period, max 60
  chars; one logical change per commit; branches named type/short-topic.
- Options considered: (a) Conventional Commits, (b) free-form prefixed
  messages, (c) gitmoji (rejected outright: emoji ban).
- Why: Conventional Commits is the de facto standard, is tooling-friendly,
  and matches the user's ask for a clear structure with short names.
- Result: Documented in root CLAUDE.md git rules.

## D-003: No stub code in the scaffold (2026-07-08)
- Decided by: Ahmed Saad (rule), applied by Claude
- Decision: The scaffold contains directories, documented __init__.py files,
  and config; zero function stubs. Files like cli.py, client.py, or detector
  modules are created only when actually implemented and tested.
- Options considered: (a) full stub tree with pass placeholders matching the
  plan layout, (b) docstring-only packages, code lands with implementation.
- Why: The team rule forbids empty functions and pass placeholders; stubs
  also mislead readers about what exists.
- Result: Package structure exists and imports cleanly; planned modules are
  named in each package docstring and CLAUDE.md instead.

## D-002: Adopt the plan's repo layout at the existing repo root (2026-07-08)
- Decided by: Claude (for Ahmed Saad), per the plan
- Decision: Use the layout from docs/plan/02-implementation-plan.md section 2,
  placed directly at this repo's root (modelguard/ package plus skill/,
  mcp_ext/, examples/, benchmarks/, tests/ as siblings).
- Options considered: (a) nested modelguard/ project folder inside the repo,
  (b) plan layout at the repo root, (c) src/ layout.
- Why: The repo root is already the project; nesting adds a pointless level.
  src/ layout is a real alternative but deviates from the plan; raised in
  docs/plan/04-improvements.md instead of decided unilaterally.
- Result: Structure created 2026-07-08. Note: the repo is named DataHub while
  the project is ModelGuard; renaming is proposed in 04-improvements.md.

## D-001: Build ModelGuard, category 3 (2026-07-08)
- Decided by: Ahmed Saad
- Decision: Go with the plan folder: ModelGuard, Production ML Agents
  (category 3), with MigrationCopilot as the documented Week 1 fallback.
- Options considered: See docs/plan/01-strategy-modelguard.md (category
  analysis) and docs/more.md / docs/less.md (earlier candidate ideas).
- Why: Verified least-crowded category with the highest differentiation and
  maximal write-back surface; full argument in the strategy doc.
- Result: This scaffold. Week 1 gate: read column-level ML lineage plus write
  one incident and one structured property, or pivot.
