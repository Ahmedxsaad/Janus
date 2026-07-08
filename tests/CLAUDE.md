# CLAUDE.md - tests

pytest suite (docs/plan/02-implementation-plan.md section 9).

## Local rules

1. Unit tests are offline: detectors run against fixture graphs, no network,
   no live DataHub. A known-leakage fixture must flag exactly the seeded
   feature; a clean fixture must flag nothing.
2. Integration tests assume a local DataHub Quickstart plus the seeded graph;
   mark them (pytest -m integration) so unit runs stay fast, and skip cleanly
   when DataHub is unreachable.
3. Idempotency is a test: run scan twice, assert exactly one incident per finding.
4. LLM-dependent behavior is not unit-tested; detection is LLM-free by design,
   test that instead. Generated-text quality belongs to the benchmark, not here.
5. Mirror the package layout: tests/detect/test_leakage.py tests
   modelguard/detect/leakage.py.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: offline units, marked integration, idempotency test |
