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
