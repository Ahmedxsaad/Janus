# CLAUDE.md - tests

pytest suite (docs/plan/02-implementation-plan.md section 9).

## Local rules

1. Unit tests are offline: detectors run against fixture graphs, no network,
   no live DataHub. A known-leakage fixture must flag exactly the seeded
   feature; a clean fixture must flag nothing.
2. Integration tests assume a local DataHub Quickstart plus the seeded graph;
   mark them (pytest -m integration) so unit runs stay fast, and skip cleanly
   when DataHub is unreachable. Stop any `modelguard watch` pointed at the same
   graph first. The suite reads back the *latest* value of timeseries aspects it
   just wrote, and an assertion run event is an append, so a watcher scanning
   the same table concurrently makes its own event the latest one and the
   assertion test fails on a lag it never measured. Observed on the judge VM
   (D-073): one failure in the first run, zero in the identical second. Nothing
   is wrong with the product when this happens, which is exactly why it is
   written down: an intermittent red here will otherwise be chased as a bug.
3. Idempotency is a test: run scan twice, assert exactly one incident per finding.
4. LLM-dependent behavior is not unit-tested; detection is LLM-free by design,
   test that instead. Generated-text quality belongs to the benchmark, not here.
5. Mirror the package layout: tests/detect/test_leakage.py tests
   modelguard/detect/leakage.py.
6. A green suite proves nothing until a fault kills it. Before landing tests for
   a behavior, break that behavior on purpose and confirm the suite goes red.
   Assertions over values the test itself constructs (fixed URNs, constants) are
   not tests; assert on what the code sent to DataHub or wrote to the graph.
   This rule exists because a dedup that ignored the incident title once passed
   the whole suite (D-016).

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: offline units, marked integration, idempotency test |
| 2026-07-10 | Claude (for Ghassen Naouar) | Add rule 6: mutation-check tests, never assert on self-constructed constants |
| 2026-07-22 | Claude (for Ahmed Saad) | tests/benchmarks/ covers the bench's scoring arithmetic and its ground-truth labels offline; both mutation-checked, and rule 6 applied to the benchmark itself by breaking a detector and confirming RESULTS.md moves (D-047) |
| 2026-07-22 | Claude (for Ahmed Saad) | A test pass adds tests/integration/test_scenario_convergence.py (scenarios converge under cycling, interleaving and re-seeding) and tests/benchmarks/test_report.py (an unscoreable trial is excluded and disclosed). Both cover failure modes every existing test passed through (D-048) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-073's seven regression tests, each confirmed red against the pre-fix code per rule 6: document-id collision, malformed source column, leak-path ordering, downstream dedup, evidence-detail dispatch, token scrubbing, and watch's first-poll message |
| 2026-08-01 | Claude (for Ghassen Naouar) | Rule 2 gains its missing precondition: stop `modelguard watch` on the same graph before an integration run, or a concurrent scan's timeseries append becomes the "latest" event the assertion test reads (D-073) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-074 adds tests/detect/test_coverage.py (a check that could not run is never reported as clean) and a reconciliation test for a leak fixed by deleting the column, both mutation-checked per rule 6 |
