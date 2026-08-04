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
| 2026-07-16 | Claude (for Ghassen Naouar) | schema_drift (P3) and trust_score (P4) land. Drift diffs a training-time schema snapshot (on the run) against the current schemaMetadata, not a reconstructed timeline (D-036). trust_score is a pure weighted rollup of a scan's findings, still a pure function though of findings rather than the graph (D-037) |
| 2026-07-30 | Claude (for Ahmed Saad) | trust_score's band now caps at WATCH when the worst finding is CRITICAL or HIGH, regardless of point total: points alone let a live leaking model read healthy at exactly the 70 floor while gate correctly blocked it. blast_radius gains downstream_models and schema_drift gains schema_drift_candidate_resources, both pure reads exposing a detector's candidate resources independent of whether they currently trigger a finding, for pipeline.py's reconciliation to use (D-067) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-073: an unparseable `modelguard.source_column` is treated as absent rather than raising out of the scan (rule 5 applied to a malformed value, not only a missing one); `leak_path` returns the shortest match instead of the server's first, so the quoted derivation chain is stable; downstream datasets and features are deduplicated like the models already were |
| 2026-08-01 | Claude (for Ghassen Naouar) | coverage.py lands (D-074): rule 5's positive-evidence silence is correct in the detector and was being rendered to users as "healthy". Every check a scan asks for now reports whether it had the metadata to run, with the missing aspect named. It reads only, decides nothing, and skips any check that produced a finding |
| 2026-08-02 | Claude (for Ghassen Naouar) | column_marks.py extracts the upstream column walk and the two-aspect mark index leakage owned, because the sensitive-source detector asks the same question of the same graph with a different mark; duplicating the paths-not-urn traversal would duplicate the chance to get it wrong. governance.py lands both governance detectors: a feature descending from a classified column (configured, no default, reports itself unevaluated when unset) and a training input its owners deprecated (D-079) |
| 2026-08-02 | Claude (for Ghassen Naouar) | coverage.py tells a model nobody linked apart from one an ingest de-linked (a recorded modelguard.feature_table with no mlFeatures), naming the ingest and the replay command. This is the first import from writeback/ into detect/: `read_properties` and the property name, both pure reads. Layer purity is unchanged, detect still writes nothing (D-092, F11) |
| 2026-08-02 | Claude (for Ahmed Saad) | marked_ancestor returns a WalkResult (hit, truncated) instead of a bare tuple: a walk that hit the lineage result cap with nothing found cannot claim there is nothing to find, only that nothing was seen. blast_radius.py's downstream traversal gets the same flag on BlastRadius. coverage.py's leakage and sensitive-source gaps, and pipeline.py's blast-radius warning, both act on it (D-097, F1) |
| 2026-08-04 | Claude (for Ghassen Naouar) | trust_score returns typed Deductions (name, points, cause) worst first, instead of a name-to-points mapping. The cause is the triggering finding's own title, so rule 2's typed-findings rule now reaches the rollup: a number a reader acts on carries the evidence it came from (D-108, T-01) |
| 2026-08-04 | Claude (for Ghassen Naouar) | column_marks returns every marked ancestor a walk reached, not only the shortest, and splits the SDK's flattened path list back into the paths GMS returned. Two derivations through one upstream table were one match, and a chain truncated by index into the concatenation quoted the previous derivation's tail as part of the proof (D-110, T-03) |
| 2026-08-04 | Claude (for Ghassen Naouar) | degraded.py lands (D-113, T-07): the table-level mode for a model no link reaches, gated so it stays silent whenever a column-level detector can answer. Rule 5's positive-evidence rule holds at a weaker granularity, and every finding it returns carries the mode, its limit, and the mode's measured precision. trust_score skips it: a maybe must not move a number people compare over time |
| 2026-08-04 | Claude (for Ahmed Saad) | column_marks.WalkResult gains hop_capped, distinct from truncated: one means the walk may not have seen everything (raise the result cap), the other means it saw an ancestor and declined it on distance alone (raise the hop cap). coverage.py's leakage and sensitive-source gaps name whichever cap actually bound, or both, closing the silent half of F1 D-097 left open (D-116, T-09) |
| 2026-08-04 | Claude (for Ahmed Saad) | governance.py gains proxy_candidate_findings (D-117, T-11): a sixth detector looking for a fork rather than a chain, a feature and a classified protected attribute descending from one ancestor with neither descending from the other. Its own config group with no default, its own finding type, severity capped at MEDIUM and deliberately not escalating for a live model, and no contribution to the trust score: it raises a question for a human, and rule 5's positive-evidence rule cannot settle one. column_marks gains related_columns, the unmarked sibling of marked_ancestor, which answers what a column touches rather than what it descends from |
