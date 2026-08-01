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
