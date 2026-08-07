<!--
Short by design. Everything here is a question a reviewer would otherwise have
to ask, and the live-DataHub one exists because six modules once landed with
unit tests against FakeGraph and nothing else: a fake written by the same people
as the code cannot fail the way a server does.
-->

## What this changes

<!-- One or two lines. -->

## Why

<!-- The reasoning. If it changes a design choice, say which and what it replaces. -->

## Documentation

<!-- Which docs/ page this changes, or "none". A command or flag change also
     changes site/index.html and README.md in this same commit. -->

## Evidence

- [ ] `pytest` (offline) passes
- [ ] Ran against a live DataHub, or: this touches no read or write path
      (`pytest -m integration`, or the command and what it did)
- [ ] Benchmarked, or: this adds or changes no detector
      (`python -m benchmarks.run_bench --out benchmarks/RESULTS.md`)
- [ ] Every new behaviour has a test that was confirmed red before the fix
      (CONTRIBUTING.md)
