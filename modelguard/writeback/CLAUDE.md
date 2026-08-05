# CLAUDE.md - writeback

Idempotent DataHub mutations: incidents, structured properties, labels,
documents, assertions. Verified GraphQL and API shapes live in
docs/plan/02-implementation-plan.md section 6; use those exact forms.

## Local rules

1. Fixed, parameterized functions only. The LLM selects a function and passes
   arguments; it never composes GraphQL. Validate every argument before the
   write: URNs must resolve, enums checked against the allowed set, numbers clamped.
2. Idempotency is mandatory: dedup key (resourceUrn, finding_type, title),
   read-before-write, skip if an open incident with the same key exists.
   run_id is stamped on the write as provenance but is never part of the key:
   it changes every run, so keying on it would duplicate every finding (D-013).
   Find existing incidents by traversing the IncidentOn relationship inbound.
   Never read incidentsSummary: GMS does not write it, so that dedup silently
   finds nothing and duplicates on every scan (D-018).
3. Human approval happens in agent/, not here. Functions in this package assume
   the write was already approved.
4. Incident types allowed: OPERATIONAL, FRESHNESS, VOLUME, FIELD, SQL,
   DATA_SCHEMA, CUSTOM. There is no COLUMN type; the column-scoped one is
   FIELD. Read the allowed set from IncidentTypeClass, never hardcode it.
5. Incidents attach only to dataset, chart, dashboard, dataFlow, dataJob, and
   schemaField. Never to an mlModel: GMS answers 500. Findings go on the data
   asset; model risk goes on the model as structured properties (D-017).
   graph.exists() is always False for a schemaField; resolve a column through
   its parent dataset's schemaMetadata.
6. Smart/anomaly assertions and scheduled evaluation are DataHub Cloud only, and
   DataHubClient.assertions is Cloud-only too (it imports acryl_datahub_cloud).
   We render open-assertions YAML, validate it by parsing it back through
   DataHub's AssertionsConfigSpec, and emit assertionInfo ourselves. Never call
   get_assertion_info_aspect(): it restamps source.created with now and the
   aspect stops converging (D-025). Disclose the Cloud boundary in any doc.
7. An assertion run event reports what a detector actually measured on that run,
   never a fabricated pass or fail, and nativeResults names the source of the
   number. A fresh table writes SUCCESS.
8. An aspect with a timestampMillis field is a timeseries aspect: emitting it
   appends an event. That is right for run events, and it means "undo" is a
   newer event, not a delete.
9. There is no mlModel patch builder in datahub.specific, so tags on a model go
   through read-merge-emit on globalTags. Never blind-write the aspect: it is an
   upsert of the whole list and would drop tags somebody else applied.
10. Never write a value no detector computed. Phase 1 wrote only risk_flags and
    run_id; since P4 landed (D-037), trust_score and trust_band are also written,
    because a detector now computes them. The rule stands: the number comes from
    a finding's evidence or a detector, never from an LLM or thin air.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: parameterized writes, idempotency, Cloud boundary |
| 2026-07-09 | Claude (for Ghassen Naouar) | Correct the dedup key (title, not run_id) and the incident types (FIELD, not COLUMN) |
| 2026-07-10 | Claude (for Ghassen Naouar) | Incidents cannot attach to mlModel; dedup via IncidentOn, never incidentsSummary |
| 2026-07-10 | Claude (for Ghassen Naouar) | Phase 1: labels, assertions, documents land; Cloud-only assertions client, no source restamping, no mlModel patch builder, no fabricated values |
| 2026-07-13 | Claude (for Ahmed Saad) | terms.py lands: glossary-term read-merge-emit, same pattern as labels.py. A leakage finding writes a FIELD incident on the column, never on the model, same as freshness |
| 2026-07-16 | Claude (for Ghassen Naouar) | P3/P4: a schema-drift finding writes a DATA_SCHEMA incident on the drifted dataset (documents.py renders its report); trust_score + the new trust_band property are written per model (rule 10 updated, D-036, D-037) |
| 2026-07-16 | Claude (for Ghassen Naouar) | contract.py lands (section 6.5): a pure renderer emitting a model's inputs as an ODCS v3.1.0 YAML (schema from schemaMetadata, freshness SLA from config), validated with datacontract-cli. Reads the graph, writes a file, never mutates the graph (D-038) |
| 2026-07-22 | Claude (for Ahmed Saad) | _active_incident_urns becomes public attached_incident_urns, and is renamed because it never filtered to active ones; the benchmark reads it rather than forking the IncidentOn traversal D-018 records (D-047) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-073: the impact-report document id folds in the finding type. The resource URN alone does not separate the detectors, so a table that was both stale and drifted had its second report overwrite its first. Migration note in D-073: pre-fix documents are orphaned, not converted |
| 2026-08-01 | Claude (for Ghassen Naouar) | link.py lands (D-074): the model-to-column join no ingestion source writes (mlFeatures with their source columns, the label term propagated up the label's own lineage, the training-time schema on the run), idempotent by read-merge-emit like the rest of this package. Its arguments and the columns a model currently leaks through are recorded as structured properties, an aspect ingestion does not overwrite, which is what lets a link be replayed and what closes D-069's stale-incident gap |
| 2026-08-02 | Claude (for Ghassen Naouar) | link_infer.py lands beside link.py: a read-only proposer that works a link out of the graph (the training run's recorded inputs, a declared label term or a configured name, and the schema's own key declarations) and renders the exact command for a human to confirm. It writes nothing and guesses nothing it does not label as a guess; where no label is declared or named it returns an incomplete proposal rather than inventing one (D-080) |
| 2026-08-02 | Claude (for Ghassen Naouar) | trust_history.py lands: one capped entry per scan in modelguard.trust_history, keyed on run_id so a rerun replaces its own row. Projected before the per-finding writes and handed to publish_impact_report, so the report's trend and the graph describe the same run rather than being read twice and disagreeing. A structured property and not a timeseries aspect, because a new timeseries aspect is a change to DataHub's own model and belongs in the RFC lane (D-081) |
| 2026-08-02 | Claude (for Ghassen Naouar) | link_infer.py resolves the feature table four ways (run inputs, an MLflow run parameter, dataset-to-model lineage, then a shortlist to choose from) and returns an incomplete proposal with candidates instead of raising, because refusing left --infer declining on the exact stack it was built for (D-091, F10). The three `link` property names move to properties.py, the registry the rest already live in, aliased in link.py. assign_properties documents the read-merge-write ceiling it cannot fix: safe in sequence, lossy in parallel, one writer per graph (D-090, F3) |
| 2026-08-02 | Claude (for Ghassen Naouar) | link_infer.py finds a label declared upstream of the feature table, not only one in it, by reusing the leakage detector's own marked-ancestor walk. The label usually lives in its own mart, so the previous search made every proposal incomplete on exactly the graph this project seeds (D-095) |
| 2026-08-02 | Claude (for Ghassen Naouar) | documents.py registers the two governance findings in both singledispatch tables. D-079 wired them into narrate.py and not here, so a sensitive-source scan crashed writing its report after the incident had already landed. Rule: a new finding type is not shipped until every dispatch table it passes through is registered, and one offline test now renders a report for every concrete type (D-096) |
| 2026-08-04 | Claude (for Ghassen Naouar) | A trust history entry gains a sixth field, the scoring version, and parse_entry still reads the five-field form an older release wrote rather than dropping it: a graph scored before versioning is exactly where the discontinuity is worth showing. modelguard.scoring_version joins the property registry, and the impact report gains a waterfall section above the trend (D-108, T-01) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The impact report gains a How to clear this section inside the body, beside the proof rather than after the trend. Substituted before the narrative, so the narrative-last property that keeps LLM prose out of a template substitution survives a second placeholder (D-110, T-03) |
| 2026-08-04 | Claude (for Ghassen Naouar) | process_instance.py lands: every scan is a dataProcessInstance under the agent's own dataJob and dataFlow, keyed by the run_id, with a FAILURE run event when a scan dies rather than silence. Three things a live GMS corrected: a run's inputs and outputs may name only dataset and mlModel (a column reports as its parent dataset, everything else is dropped from the aspect and stays reachable from the asset it hangs off), the SDK's flow and job helpers always emit an empty globalTags and ownership which rule 9 forbids sending, and the run events are hand-built so messageId can come from the run_id (D-111, T-04) |
| 2026-08-04 | Claude (for Ghassen Naouar) | link_infer gains declared_proposal: an adapter's declaration joined against the linked table's real schema, returning the same LinkProposal --infer returns, so both routes share every step after the confirmation. A declared column the table does not have raises rather than being filtered, because linking the intersection reports success over columns nothing will ever check (D-112, T-05/T-06). documents.py registers the table-level finding in both dispatch tables, per the D-096 rule (D-113, T-07) |
| 2026-08-04 | Claude (for Ahmed Saad) | model_documents.py lands for T-12 and T-13: a model card and an EU AI Act Article 10 evidence pack, one gather() reading the graph and two pure renderers over it so the two artifacts cannot disagree about one model. Rule 10's never-write-a-value-no-detector-computed extends here into never *imply* one: the pack's first heading denies being a certification, its second is what it could not establish, and anything absent from the graph is marked not recorded rather than omitted (D-119) |
| 2026-08-05 | Claude (for Ghassen Naouar) | coverage_history.py lands (D-126, T-15): the guard-coverage trend, appended the way trust_history appends a score, but carried on ModelGuard's own dataFlow rather than on a guarded asset. A catalog-level figure belongs to no model or dataset, and inventing a synthetic entity to hold one would put a made-up asset in somebody's catalog. That a dataFlow accepts a structured property is verified against a live GMS, not assumed |
| 2026-08-05 | Claude (for Ghassen Naouar) | feature_documents.py lands for T-19 (D-130): a Data Card per feature, same gather-then-pure-render shape as model_documents.py so the two cannot disagree about one feature. Rule 10's never-imply-a-value-no-detector-computed reaches two places here: the freshness table states out loud that its numbers are measured now and not at training time, which is the substitution the evidence pack already refuses, and the training-time type is read from the snapshot entry for this column's own table rather than a flattening of every input, which is D-070's collision arriving one level down |
| 2026-08-05 | Claude (for Ghassen Naouar) | link.py's recorded_link gains a second caller (D-132, T-20): modelguard/reconcile.py replays it off a change-log event rather than off `link --all`. Rule 1's validate-every-argument holds without a change, because the arguments are the ones a human confirmed once; a LinkError is caught and returned as a refusal rather than raised, since this runs inside a daemon's event loop and one model whose feature table was deleted must not stop the loop protecting the others |
