# CLAUDE.md - seed

Builds the ML graph the datapacks lack: model group, model, features, training
run, deployment, plus column-level lineage between the warehouse tables it also
creates. This is the Week 1 kill-criterion (docs/plan/02-implementation-plan.md
section 3): if seeding stalls, we pivot to the fallback.

The seeder creates the two warehouse tables itself, at the URNs the
showcase-ecommerce datapack uses. Loading the datapack is therefore optional
realism, not a prerequisite (D-014).

## Local rules

1. Seeding is idempotent and deterministic: fixed URNs, fixed values, fixed
   scenario seeds. Running it twice converges to the same graph.
2. There are no SDK entity classes for MLFeature, MLPrimaryKey, MLFeatureTable,
   or MLModelDeployment in acryl-datahub 1.6.0.13; emit their aspects as MCPs.
   Only MLModel and MLModelGroup have classes (D-012).
3. Column-level lineage is Dataset to Dataset only. Feature-to-dataset lineage
   goes through the ML sources aspect, never add_lineage. That aspect is
   dataset-granular, so a feature's exact column lives in customProperties
   under modelguard.source_column; detectors start their traversal there.
4. graph_spec.py is the single source of truth for every seeded URN and value.
   Nothing hardcodes a URN string; tests assert the spec is self-consistent.
5. scenarios.py plants failures for the demo and benchmark; keep each scenario
   labeled, reversible, and shared with benchmarks/inject.py. Every scenario
   stamps modelguard.scenario into the aspect's customProperties so a reader can
   tell a planted failure from a real one.
6. Scenarios take the current instant as an argument so tests can fix it.
   The stale-source scenario backdates the operation aspect's
   lastUpdatedTimestamp; because operation is a timeseries aspect, reverting
   emits a newer event announcing a refresh rather than deleting anything.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: seeding rules and Week 1 gate context |
| 2026-07-09 | Claude (for Ghassen Naouar) | Record the missing SDK ML entity classes, the dataset-granular sources aspect, and graph_spec as source of truth |
| 2026-07-10 | Claude (for Ghassen Naouar) | scenarios.py lands: labeled, reversible, injectable clock |
| 2026-07-13 | Claude (for Ahmed Saad) | seed_ml_graph declares the label: a glossary term on default_status, the column the leakage detector hunts for (D-032) |
| 2026-07-16 | Claude (for Ghassen Naouar) | The training run carries a schema fingerprint (customProperties, keyed by input dataset URN) the drift detector diffs against (D-036). scenarios adds plant/revert_schema_drift, mutating the feature table's live schema and leaving the leakage columns untouched so both Phase 2 scenarios coexist |
| 2026-07-22 | Claude (for Ahmed Saad) | scenarios adds plant/revert_leakage: the flagship detector's negative control, which the seeder's always-planted leak (D-032) never allowed. Sets fine-grained lineage outright, because add_lineage patches additively and cannot undo an edge (D-047) |
| 2026-08-02 | Claude (for Ghassen Naouar) | scenarios adds plant/revert for both governance failures: a tag on the source column a model feature derives from, and a deprecation on the model's training input. The sensitive scenario classifies `income` rather than the more obviously sensitive `applicant_id`, because only `income` is upstream of an actual model feature (D-079) |
| 2026-08-04 | Claude (for Ghassen Naouar) | scenarios adds plant/revert_second_leak_path: a backfilled copy of the label column, declared a label, feeding the same feature by a second derivation. It exists so the counterfactual can be asked the one question it can get wrong, whether cutting one path of two is a fix (D-110, T-03) |
| 2026-08-04 | Claude (for Ghassen Naouar) | scenarios adds plant/revert_delinked_model: the model's mlFeatures taken away and given back, which is not a fault somebody plants but what every mlflow ingest does (D-074). It is the only graph state where the degraded mode is allowed to speak, so it is what the T-07 trials are measured on (D-113) |
