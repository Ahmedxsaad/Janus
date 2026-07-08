# CLAUDE.md - writeback

Idempotent DataHub mutations: incidents, structured properties, labels,
documents, assertions. Verified GraphQL and API shapes live in
docs/plan/02-implementation-plan.md section 6; use those exact forms.

## Local rules

1. Fixed, parameterized functions only. The LLM selects a function and passes
   arguments; it never composes GraphQL. Validate every argument before the
   write: URNs must resolve, enums checked against the allowed set, numbers clamped.
2. Idempotency is mandatory: dedup key (resourceUrn, finding_type, run_id),
   read-before-write, skip if an open incident with the same key exists.
3. Human approval happens in agent/, not here. Functions in this package assume
   the write was already approved.
4. Incident types allowed: OPERATIONAL, FRESHNESS, VOLUME, COLUMN, SQL,
   DATA_SCHEMA, CUSTOM.
5. Smart/anomaly assertions are DataHub Cloud only. We emit open-assertions
   YAML plus the assertion entity, and we disclose that boundary in any doc.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: parameterized writes, idempotency, Cloud boundary |
