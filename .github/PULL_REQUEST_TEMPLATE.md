<!--
Short by design. Everything here is a question a reviewer would otherwise have
to ask, and the last one exists because six modules once landed with unit tests
against FakeGraph and nothing else (F8): a fake written by the same people as
the code cannot fail the way a server does.
-->

## What this changes

<!-- One or two lines. The decision-log entry is where the reasoning goes. -->

## Decision log

<!-- The D-0NN entry this lands, or "none: no decision worth recording". -->

## Evidence

- [ ] `pytest` (offline) passes
- [ ] Ran against a live DataHub, or: this touches no read or write path
      (`pytest -m integration`, or the command and what it did)
- [ ] Benchmarked, or: this adds or changes no detector
      (`python -m benchmarks.run_bench --out benchmarks/RESULTS.md`)
- [ ] Every new behaviour has a test that was confirmed red before the fix
      (tests/CLAUDE.md rule 6)
