# CLAUDE.md - benchmarks

ModelGuard-Bench (docs/plan/03-production-hardening.md section A): detectors
are measured, not asserted. Planned pieces: inject.py (Jenga-based corruption
plus leakage/drift injection), run_bench.py (metrics and baselines), golden/
(golden impact reports for regression diffing), RESULTS.md.

## Local rules

1. Ground truth is deterministic: planted-issue datapacks, Jenga injections,
   and synthetic leakage/drift with fixed seeds. Same run, same numbers.
2. Report per detector: precision, recall, F1, false-positive rate, MTTD,
   blast-radius recall, idempotency (duplicate incidents after N reruns = 0).
   Targets live in the hardening doc; do not quietly lower them.
3. Baselines run the same scenarios: Great Expectations (no lineage),
   Evidently (drift only, after the fact), naive table-level lineage.
4. RESULTS.md must be fully reproducible from run_bench.py on the seeded
   graph; never hand-edit numbers.
5. Golden reports in golden/ are updated only deliberately, with the diff
   explained in the commit message.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: determinism, metrics, baseline rules |
