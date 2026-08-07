# How this is tested

1,053 tests, a mutation score for the detectors, and eight CI jobs on every push
and pull request. This page describes the discipline behind them, because a
reliability tool that is casually tested is making a claim it has not earned.

The benchmark is a different thing and has its own page:
[08-evaluation.md](08-evaluation.md) measures whether the detectors are *right*;
this page is about whether the code *works*.

## The rule everything else follows

**A green suite proves nothing until a fault kills it.**

Before tests for a behaviour land, that behaviour is broken on purpose and the
suite is confirmed to go red. This rule is not aspirational: it exists because a
deduplication that ignored the incident title once passed the entire suite. The
tests were green and the product raised a duplicate incident on every scan.

Its corollary: **assertions over values the test itself constructs are not
tests.** A test that builds a URN, passes it in, and asserts the URN comes back
has measured nothing. Assertions are on what the code actually sent to DataHub or
wrote to the graph.

## The two suites

### Offline: 982 tests

No network, no live DataHub. Detectors run against fixture graphs. Two
obligations for every detector: a known-positive fixture must flag **exactly** the
seeded entity, and a clean fixture must flag **nothing**. The second is the false
positive control and it is half the value.

```bash
pytest -m "not integration"
```

The layout mirrors the package, so `tests/detect/test_leakage.py` tests
`janus/detect/leakage.py`. Ninety test modules across `detect/`, `writeback/`,
`agent/`, `adapters/`, `seed/`, `benchmarks/` and the cross-cutting modules.

### Integration: 71 tests

Against a live DataHub Quickstart with the ML graph seeded. Marked so unit runs
stay fast, and skipped cleanly when DataHub is unreachable.

```bash
janus-seed
pytest -m integration
```

**This suite is not a formality, and there is direct evidence for that.** Six
modules once landed with unit tests against a fake graph and nothing else. The
first integration run found a defect the entire offline suite had passed through:
a sensitive-source scan raising `NotImplementedError` while writing its own
impact report, *after* the incident had already been written. A fake written by
the same people as the code cannot fail the way a server does.

One operational note that is written down precisely because it looks like a bug:
stop any `janus watch` pointed at the same graph first. The suite reads back the
*latest* value of timeseries aspects it just wrote, and an assertion run event is
an append, so a watcher scanning the same table concurrently makes its own event
the latest one and the assertion test fails on a lag it never measured. Observed
once on the demo VM: one failure in the first run, zero in an identical second.

A test that writes to the benchmark's graph owes it a restore. One integration
module blind-wrote a whole `mlModelProperties` aspect, which is exactly what the
write-back rules forbid the product from doing, and stripped `trainingJobs` and
deployments off the seeded model. Four benchmark trials then came back wrong on a
graph a test had damaged. Fixed twice over: the write is now read-merge-emit, and
the module re-seeds on teardown.

## What is deliberately not unit-tested

**Language-model-dependent behaviour.** Detection is LLM-free by design, so the
test is that it *is* free of one: a scan runs end to end with no provider
configured and produces byte-identical detection. Generated-text quality belongs
to the benchmark's faithfulness check, not here.

## Mutation testing

A suite that passes is not a suite that would catch a regression. `mutmut`
mutates `janus/detect/` and the report records how many mutants the suite killed.

Scoped to the detectors on purpose: detection is the claim this project makes,
and mutating the rest of the package would measure something that was never
claimed. Logging calls are excluded from mutation entirely, because a corrupted
log line is invisible to every consumer this project has.

**The report does not stop at a score.** `benchmarks/mutation_report.py` renders
the section from `mutmut results --all=true`, and every survivor is grouped under
a hand-written verdict saying whether it is a real gap or a provably equivalent
mutation, and what test would kill it. **A survivor with no verdict fails the
render loudly** rather than being silently dropped from the report. Mutation
testing can say a mutant survived; it can never say why that is acceptable.

The two recurring classes it found are ordinary and worth naming, because they
reproduce anywhere:

- A `continue` mutated to `break` inside a per-item loop survives whenever the
  fixture gives that loop exactly one item.
- A finding's own identifying field swapped for `None` survives whenever a trial
  checks that a finding exists without checking what it says.

Both are fixture poverty rather than logic bugs, and naming them is what turns a
score into a list of tests worth writing.

CI regenerates the section and fails advisorily if the committed one is stale, so
the published number cannot drift from the code.

## Documentation is tested too

Three joints between the code and its prose are asserted rather than remembered:

- **Every CLI command appears in `README.md`**, and separately in
  `site/index.html`. Both documents claim to cover every command, and both are
  promises a human keeps by remembering, which is how two commands once shipped
  appearing in neither. Nothing failed, the phase looked done, and the artifact
  most likely to matter to a governance reader was reachable only by reading
  `--help`.
- **The documentation page loads nothing from outside its own directory.** A
  `../` reference or a `fetch(` fails the test. Such a reference works locally,
  where the server root is the repository root, and 404s in production, where the
  page still renders and the missing thing is simply absent. That is how a page
  with no dog on it passed review and shipped.
- **The page's generated art matches its generator.** The test reruns the
  generator and compares, so a second copy of the sprite art cannot drift from
  the one the window reads.

## Continuous integration

`.github/workflows/ci.yml`, eight jobs on every push and pull request.

| Job | What it proves |
|---|---|
| lint, types, hygiene | Runs `pre-commit`, not its own list of linters |
| offline tests | The 982-test suite, on a matrix |
| mutation score | `mutmut` over `janus/detect/`, and the report is not stale |
| dependency audit | Advisory, reported rather than failed on |
| installs alongside a different pydantic | The regression test for exact pins |
| docker image builds | The image builds and smoke-runs |
| helm chart lints and renders | `helm lint` and `helm template` on the chart |
| deploy runbook files are valid | The cloud-init and unit files parse |

**The lint job runs `pre-commit` rather than its own separate invocations of ruff
and mypy.** That is deliberate: the local hooks and the enforced checks then
cannot drift, which is the failure mode of a CI file that lists its own tools.

The dependency audit is **advisory** and reports rather than fails, because a new
advisory against a transitive dependency should not block an unrelated pull
request at the moment it is published.

The **installs-alongside** job is the regression test for a real packaging bug:
exact `==` pins fought the resolver and made the published package effectively
uninstallable next to anything else. `janus-datahub` is a library on PyPI, so an
environment already holding pydantic or rich for something else (FastAPI,
LangChain, dbt adapters, most ML tooling) has to be able to install this beside
it. The job installs into such an environment and checks it works.

The DataHub SDK is the one dependency that stays exactly pinned rather than
ranged, and the reason is specific: widening it to `<2` was tried and reverted
after it let CI resolve four patches past the one version verified symbol by
symbol, and that patch changed the incident-entity-types schema enough to fail
tests written against the verified behaviour. A bump there is a decision made by
running the integration suite against the new version, never something a resolver
picks on its own.

## The pre-commit hooks

Installed once per clone with `pre-commit install`, and re-run by CI:

- `ruff` and `ruff format`
- `mypy` over `janus/` and `benchmarks/`
- `check-yaml`, excluding `charts/*/templates/`, which is Go template syntax
  rather than YAML. `helm lint` and `helm template` are this project's actual
  syntax check for those files and both run on every chart change, so the
  exclusion routes the check to the tool that understands the format rather than
  leaving a gap.
- `check-toml`, end-of-file, trailing whitespace, large files, merge conflicts
- `detect-private-key`, because `.env` holds tokens and must never be committed
- A local hook banning em dashes and emojis everywhere, since nothing else
  enforces the formatting rules

## Two invariants the tests enforce that are easy to lose

- **Configuration enters through `janus/env.py` and nowhere else.** Two tests
  assert it. Without them the rule decays the first time somebody reaches for
  `os.environ` in a hurry, and the failure it prevents is silent: a threshold set
  in `.env` being ignored because the scan read it before anything loaded the
  file.
- **The token never reaches a traceback.** A test covers the one boundary where
  this project hands a credential to somebody else's SDK, because that SDK's
  error text is not ours to assume is clean.
