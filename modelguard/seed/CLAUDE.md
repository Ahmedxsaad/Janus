# CLAUDE.md - seed

Builds the ML graph the datapacks lack: model group, model, features, training
run, deployment, plus column-level lineage from real datapack tables into the
feature table. This is the Week 1 kill-criterion (docs/plan/02-implementation-plan.md
section 3): if seeding stalls, we pivot to the fallback.

## Local rules

1. Seeding is idempotent and deterministic: fixed URNs, fixed values, fixed
   scenario seeds. Running it twice converges to the same graph.
2. Mirror the official DataHub AI/ML tutorial scripts exactly for feature,
   feature table, and deployment creation; the exact SDK module paths are
   [confirm] and must be verified against the installed acryl-datahub first.
3. Column-level lineage is Dataset to Dataset only. Feature-to-dataset lineage
   goes through the ML sources aspect, never add_lineage.
4. scenarios.py plants failures for the demo and benchmark; keep each scenario
   labeled, reversible, and shared with benchmarks/inject.py.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: seeding rules and Week 1 gate context |
