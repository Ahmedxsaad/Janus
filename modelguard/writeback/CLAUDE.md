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
6. Smart/anomaly assertions are DataHub Cloud only. We emit open-assertions
   YAML plus the assertion entity, and we disclose that boundary in any doc.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: parameterized writes, idempotency, Cloud boundary |
| 2026-07-09 | Claude (for Ghassen Naouar) | Correct the dedup key (title, not run_id) and the incident types (FIELD, not COLUMN) |
| 2026-07-10 | Claude (for Ghassen Naouar) | Incidents cannot attach to mlModel; dedup via IncidentOn, never incidentsSummary |
