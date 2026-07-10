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
