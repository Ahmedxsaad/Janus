# CLAUDE.md - detect

Deterministic detectors, one module per problem (docs/plan/architecture.md
section 5): leakage (P1), blast_radius (P2), schema_drift (P3), trust_score (P4).

## Local rules

1. Absolutely no LLM calls and no graph writes in this package. Detectors are
   pure functions of the graph; this is what makes them benchmarkable and
   prompt-injection resistant.
2. Every detector returns typed finding models, never raw dicts or free text.
3. Bound every traversal: hop cap (default 5), visited set, early exit when a
   live deployment is reached. Batch graph reads; no N+1 single fetches.
4. Every detector needs unit tests on fixture graphs: known positives are
   caught, clean graphs raise nothing (false-positive control).
5. Each detector cites its literature (Kaufman 2012 for leakage, Sculley 2015
   for blast radius, Breck 2019 for drift) in its module docstring.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: purity, typing, traversal, and test rules |
