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
| 2026-08-01 | Claude (for Ghassen Naouar) | tests/writeback/test_link.py covers what `link` declares: the excluded column is no feature but stays in the training snapshot (drop it and drift reports it as newly appeared), and the label declaration follows the label's own lineage. Both mutation-checked per rule 6 |
| 2026-08-02 | Claude (for Ghassen Naouar) | tests/test_render.py, tests/test_logs.py and tests/detect/test_governance.py land, each mutation-checked per rule 6. The unconfigured-detector test asserts on the lineage calls issued, not on the empty result: with an empty index the walk returns the same answer either way, so asserting the result tested nothing (D-077, D-078, D-079) |
| 2026-08-02 | Claude (for Ghassen Naouar) | Three integration modules land for the write-shaped modules that had none: the sensitive source (the only proof a live GMS serves globalTags off a schemaField), the trust history (a MULTIPLE structured property, written and read back, with a rerun that must add nothing), and link --infer against the seeded graph. Run against a live Quickstart: 52 integration tests pass, and the first run caught a real defect the whole offline suite passed through, a sensitive-source scan crashing while writing its own impact report (D-093, D-096, F8) |
| 2026-08-03 | Claude (for Ghassen Naouar) | tests/test_argos.py and tests/test_companion.py land, 44 tests mutation-checked per rule 6. The art is tested like code: every frame's geometry and palette, and that no frame paints red, since red is state and pre-red art would render a healthy graph as a failing one (D-098) |
| 2026-08-03 | Claude (for Ghassen Naouar) | tests/test_site.py checks the three joints where site/ names something living elsewhere: the poses and frames it animates, the glyphs its bubbles need, and that it still reads the one copy of the art. Mutation-checked per rule 6 (D-104) |
| 2026-08-03 | Claude (for Ghassen Naouar) | The art tests now also check that every frame the window names exists and every frame in the file is reachable, and that the trust band is sent rather than recomputed in the renderer. That last one is a regression test for a bug a live run found, not one the offline suite could have (D-099) |
| 2026-08-04 | Claude (for Ghassen Naouar) | conftest gains make_trust_score, so a test says what it means (a name-to-points mapping) and never invents the cause prose it is not asserting on. tests/test_config.py fingerprints the scoring contract: changing a weight, a band boundary, or the contributing finding set fails until SCORING_VERSION is bumped (D-108, T-01) |
