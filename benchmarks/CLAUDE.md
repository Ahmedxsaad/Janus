# CLAUDE.md - benchmarks

ModelGuard-Bench (docs/plan/03-production-hardening.md section A): detectors
are measured, not asserted. Shipped: inject.py (the labelled trial matrix),
metrics.py (the pure scoring arithmetic), run_bench.py (the live harness and
the RESULTS.md renderer), baselines.py (the approaches without column-level
lineage), RESULTS.md (generated). Not built: Jenga corruption injection, the
scale test, golden/ (D-047, D-050).

Run it with a Quickstart up and the graph seeded:

    modelguard-seed
    python -m benchmarks.run_bench --out benchmarks/RESULTS.md

## Local rules

1. Ground truth is deterministic: synthetic leakage/drift/freshness planted by
   modelguard.seed.scenarios, fixed lags, fixed order. Same run, same numbers.
   Trials call the shipped detectors; never reimplement detection here.
2. Report per detector: precision, recall, F1, false-positive rate, MTTD,
   blast-radius recall, idempotency (duplicate incidents after N reruns = 0).
   Targets live in the hardening doc; do not quietly lower them.
3. Baselines run the same scenarios and the same ground truth. Shipped:
   table-level lineage, and quality-checks-with-no-lineage (D-050). They are
   implementations of an *approach*, not of a product: no Great Expectations,
   Deequ, Evidently or NannyML process is run, and RESULTS.md says so rather
   than letting a reader assume those tools were benchmarked.
4. RESULTS.md must be fully reproducible from run_bench.py on the seeded
   graph; never hand-edit numbers. Prose in the renderer is fine, a number
   typed into the template is not.
5. Golden reports in golden/ are updated only deliberately, with the diff
   explained in the commit message.
6. Measure against a live DataHub, never against a fixture graph. A detector
   scored on our own fakes measures the fakes, and the number would not
   survive a judge asking what it was run against.
7. Wait for the graph to show a planted state before asking a detector
   anything, and never wait for the detector to give the expected answer:
   that manufactures perfect recall. A trial whose precondition never lands
   is an error, reported separately, not a miss.
8. A perfect score is a claim about the trials, not about the detector. Before
   trusting a green column, break the detector it grades and confirm the
   column moves (tests/CLAUDE.md rule 6, applied to the benchmark).
9. A baseline is written to be *fair*, not to lose. Hand it every fact
   ModelGuard gets (the same label index, the same source-column resolution)
   and let it differ in one respect only. Test that it genuinely detects
   before testing that it over-reports: a baseline that finds nothing turns
   the comparison into a fabrication no green suite would catch.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: determinism, metrics, baseline rules |
| 2026-07-22 | Claude (for Ahmed Saad) | ModelGuard-Bench core lands: inject.py, metrics.py, run_bench.py, generated RESULTS.md. Add rules 6-8 (live graph not fixtures, precondition never the expected answer, mutate before trusting a perfect score). Jenga, baselines, scale test and golden/ remain unbuilt (D-047) |
| 2026-07-22 | Claude (for Ahmed Saad) | baselines.py lands: table-level lineage and no-lineage approaches scored on the same graph and ground truth, per feature. Add rule 9 (a baseline is written to be fair, not to lose) and revise rule 3 (D-050) |
