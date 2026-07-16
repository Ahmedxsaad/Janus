# CLAUDE.md - detect

Deterministic detectors, one module per problem (docs/plan/architecture.md
section 5): leakage (P1), blast_radius (P2), schema_drift (P3), trust_score (P4).

## Local rules

1. Absolutely no LLM calls and no graph writes in this package. Detectors are
   pure functions of the graph; this is what makes them benchmarkable and
   prompt-injection resistant.
2. Every detector returns typed finding models, never raw dicts or free text.
3. Bound every traversal: hop cap (config.max_hops, default 3), visited set.
   Batch graph reads; no N+1 single fetches. DataHub returns entities beyond
   max_hops once it exceeds 2 (full-graph search), so filter on the hop count
   rather than trusting the server (D-020).
4. Take an entity's type from its URN, never from LineageResult.type, which is
   a display string.
5. A detector fires only on positive evidence. A missing aspect means "unknown",
   not "failing": a table that never reported an operation is not stale, and a
   deployment with no properties aspect is not live.
6. Freshness reads the operation aspect, which is a timeseries aspect:
   graph.get_latest_timeseries_value(urn, OperationClass, {}). get_aspect raises
   a TypeError for it (D-021).
7. Every detector needs unit tests on fixture graphs: known positives are
   caught, clean graphs raise nothing (false-positive control).
8. Each detector cites its literature (Kaufman 2012 for leakage, Sculley 2015
   for blast radius, Breck 2019 for drift) in its module docstring.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: purity, typing, traversal, and test rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | blast_radius lands: hop-cap filtering, URN-derived entity types, positive-evidence rule, timeseries freshness read |
| 2026-07-13 | Claude (for Ahmed Saad) | leakage lands: label declared via glossary term (union of two aspects), upstream traversal reads LineagePath, never LineageResult.urn, which is the dataset for a column query (D-031). Shared model/deployment reads factored into graph_reads.py |
