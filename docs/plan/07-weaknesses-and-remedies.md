# Weaknesses, and what to do about each

An adversarial read of the repository at `e73584f`, after the ten improvements of
`06-judge-review-and-improvements.md` merged. That document asked "where are the
judging points". This one asks the harder question: **where would this break, or
mislead, or fail to be adopted, and what exactly should be done about it.**

Every finding below was verified against the code, not inferred from the docs.
File and line references are to `e73584f`. Where a number is quoted (a prose
ratio, a test count) the command that produced it is given, so a reader can
disagree with the measurement rather than with an assertion.

Nothing here is a proposal to rewrite the project. The architecture is sound and
most of these are contained fixes. Three of them (F1, F2, F8) are the ones I
would not ship another release without.

## Summary

Severity is about consequence, not effort. **High** means it can produce a wrong
answer, lose data, or block adoption outright.

| # | Finding | Area | Severity | Effort |
|---|---|---|---|---|
| F1 | Lineage results are capped at 500 and truncation is silent | Correctness | **High** | S |
| F2 | Exact `==` pins make the published package uninstallable alongside anything | Packaging | **High** | S |
| F3 | Read-merge-write has no concurrency control, so writes are lost | Correctness | **High** | M |
| F4 | A mid-scan failure leaves the graph half-written, and says nothing | Correctness | Medium | M |
| F5 | No GMS server-version check | Robustness | Medium | S |
| F6 | The benchmark scores itself on its own graph against its own baselines | Evidence | **High** | L |
| F7 | Trust-score weights are invented, and the score gates builds | Evidence | Medium | M |
| F8 | Six new modules have zero integration or benchmark coverage | Evidence | **High** | M |
| F9 | CI tests one Python version; the package claims three | Packaging | Medium | S |
| F10 | `--infer` needs an aspect the validated stack does not produce | Adoption | **High** | M |
| F11 | `link` must be re-run per training run, with no automation hook | Adoption | **High** | L |
| F12 | `watch` hides the error message and never logs a failure | Operability | Medium | S |
| F13 | No LLM timeout, so a hanging provider stalls a daemon forever | Operability | Medium | S |
| F14 | `watch` runs a full scan twice on every change | Efficiency | Low | S |
| F15 | `--limit` slices after fetching the whole catalog | Efficiency | Medium | S |
| F16 | Reconciliation is N+1 across columns and incidents | Efficiency | Medium | M |
| F17 | 65% of the package is prose | Maintainability | Medium | L |
| F18 | `cli.py` is 1,545 lines and does four unrelated jobs | Maintainability | Low | M |

---

# Correctness

## F1. Lineage results are capped at 500, and truncation is silent

**Severity: High. Effort: S.**

### Evidence

Three call sites pass `count=config.lineage_result_cap` (default 500) and none
of them inspects how many results came back:

- `modelguard/detect/column_marks.py:203` (the leakage and sensitive-source walk)
- `modelguard/detect/blast_radius.py:145` (the downstream traversal)
- `modelguard/writeback/link.py:181` (the label's own lineage)

The SDK's signature confirms there is no continuation token to miss:

```
LineageClient.get_lineage(..., max_hops: int = 1, count: int = 500) -> List[LineageResult]
```

`count` is a hard cap on a plain list. When a column's cone has more than 500
results, the detector sees the first 500 and reports on those.

### Why it matters

This is the one failure mode the whole project exists to prevent, arriving
through the back door. A leaking column beyond the cap produces **no finding**,
and no finding is rendered to the user as a clean model. `coverage.py` exists
precisely so that silence is never read as health, and this path bypasses it: the
check *did* run, it just did not see everything.

It is worst exactly where it matters most. A 500-result cone means a wide, mature
warehouse, which is the catalog most likely to contain a leak nobody has noticed.

### Proposed fix

Detect saturation and refuse to claim completeness. In `column_marks.py`:

```python
@dataclass(frozen=True)
class WalkResult:
    """What a walk found, and whether it was allowed to see everything."""

    hit: tuple[str, str, tuple[str, ...]] | None
    truncated: bool
    """True when DataHub returned exactly the cap, so an unseen result may exist."""


def marked_ancestor(...) -> WalkResult:
    results = conn.client.lineage.get_lineage(..., count=config.lineage_result_cap)
    # Equality, not >=: the cap is a hard limit, so exactly-the-cap is the only
    # observable signature of "there may be more". One result short is complete.
    truncated = len(results) == config.lineage_result_cap
    ...
    return WalkResult(hit=..., truncated=truncated)
```

Then in the detectors: a truncated walk that found a hit still reports the
finding, because the evidence is real. A truncated walk that found **nothing**
must not be reported as clean. It becomes an `Unevaluated`:

```python
Unevaluated(
    check="target leakage",
    target_urn=model_urn,
    reason=(
        f"{feature_name}'s upstream cone returned the full "
        f"{config.lineage_result_cap}-result cap, so the traversal may not have "
        "seen every ancestor and a leak beyond the cap would be missed"
    ),
    remedy=(
        "Raise MODELGUARD_LINEAGE_RESULT_CAP, or narrow the scan to the model "
        "whose lineage is wide."
    ),
)
```

Same treatment for `blast_radius`: a truncated downstream traversal cannot claim
"no model consumes this table".

### How to verify

Unit test with a `FakeClient` returning exactly `cap` results and no match: the
scan must report the check as not evaluated, not as clean. Mutation-check by
removing the `truncated` guard and confirming the test goes red (tests/CLAUDE.md
rule 6).

### Cost of not doing it

A false negative that the product's own honesty machinery was built to make
impossible. Of everything in this document, this is the finding I would fix first.

---

## F2. Exact `==` pins make the published package effectively uninstallable

**Severity: High. Effort: S.**

### Evidence

`pyproject.toml`:

```toml
dependencies = [
    "acryl-datahub[datahub-rest]==1.6.0.13",
    "pydantic==2.13.4",
    "python-dotenv==1.2.2",
    "PyYAML==6.0.3",
    "rich==15.0.0",
    "typer==0.26.8",
]
```

The comment above it reads "Pins are exact." That is the correct policy for an
*application*. `modelguard-datahub` is now a **library** on PyPI, and the policy
inverts.

### Why it matters

`pydantic==2.13.4` alone will conflict with a large share of the Python data
ecosystem. Any environment that already holds pydantic 2.12 or 2.14 (FastAPI,
LangChain, dbt adapters, most ML tooling) either has ModelGuard force a change to
a shared dependency, or fails to resolve. The same applies to `rich`, which
`pip`, `poetry` and dozens of CLIs depend on.

The practical outcome is that `pip install modelguard-datahub` works in a fresh
venv and fails in the environment where somebody actually wants it: next to their
training code. That is a silent adoption killer, and it is invisible from the
maintainers' side because the development venv is the fresh one.

### Proposed fix

Floor and a compatible-release ceiling for everything except the SDK, whose API
surface is genuinely load-bearing here:

```toml
dependencies = [
    # The SDK is the one dependency whose exact behaviour this project verified
    # symbol by symbol (D-012). A minor bump can move an aspect class or a
    # mutation shape, so it gets a tested floor and a hard major ceiling rather
    # than a free range, and the ceiling is raised deliberately after a run of
    # `pytest -m integration` against the new version.
    "acryl-datahub[datahub-rest]>=1.6.0.13,<2",
    # Everything below is used through a stable, small surface: BaseModel and
    # SecretStr, a dotenv loader, safe_load, a Console, a Typer app. There is no
    # reason to force a resolver's hand on any of them.
    "pydantic>=2.7,<3",
    "python-dotenv>=1.0,<2",
    "PyYAML>=6.0,<7",
    "rich>=13.0,<16",
    "typer>=0.12,<1",
]
```

Reproducibility does not disappear; it moves to where it belongs. Add a
`requirements-dev.lock` (or `uv.lock`) that CI installs, so the *tested*
combination stays exact while the *published* contract stays wide:

```yaml
- name: Install the locked development environment
  run: pip install -r requirements-dev.lock -e ".[dev]"
```

### How to verify

A CI job that installs the wheel into an environment seeded with a deliberately
different pydantic patch and asserts the install succeeds and `modelguard --help`
runs:

```bash
pip install "pydantic==2.12.0"
pip install dist/*.whl
python -c "import modelguard; modelguard.scan_model"
```

That job is the regression test for this whole class of problem.

---

## F3. Read-merge-write has no concurrency control, so writes are lost

**Severity: High. Effort: M.**

### Evidence

`modelguard/writeback/properties.py:196-213`, `assign_properties`:

```python
existing = conn.graph.get_aspect(entity_urn, StructuredPropertiesClass)
kept = [a for a in (existing.properties if existing else []) if a.propertyUrn not in incoming]
merged = kept + [...]
conn.graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=entity_urn, aspect=StructuredPropertiesClass(properties=merged)))
```

Read, merge in Python, write the whole aspect. No version, no ETag, no
conditional write. `labels.add_tag` and `terms.add_term` follow the same pattern,
as writeback/CLAUDE.md rule 9 documents for tags.

### Why it matters

Two writers touching one model lose each other's work, last-write-wins on the
*entire* aspect rather than on the field each intended to change. That is not
hypothetical here:

- `watch` on a table and `watch` on a model, both reaching the same downstream
  model, which is the recommended production deployment.
- `scan --all-models` where two models share a stale upstream.
- A `watch` daemon and an engineer running `modelguard scan` by hand, which is
  the normal way somebody investigates what the daemon just reported.

The project already knows the symptom: tests/CLAUDE.md rule 2 records an
integration test failing because a concurrent watcher wrote the timeseries aspect
it was reading. That was diagnosed as a test-ordering hazard. It is the same
root cause, and on structured properties it silently drops a trust score or a
risk flag instead of failing a test.

Nothing in the README or the docs tells an operator not to run two watchers.

### Proposed fix

Three layers, in increasing cost. Do the first two now.

**1. Document and enforce the safe deployment.** One `watch` per graph is the
supported topology. Say so in `charts/modelguard-watch/README.md` and set the
Deployment's `replicas` to 1 with `strategy: Recreate`, so a rolling update never
runs two pods at once:

```yaml
spec:
  replicas: 1                # more than one watcher races on read-merge-write
  strategy:
    type: Recreate           # never two pods during a rollout
```

**2. Narrow the write.** The lost update is only harmful because the whole aspect
is rewritten. Batch every property this scan sets for one model into a single
`assign_properties` call instead of the current four separate calls (risk flags,
run id, trust score and band, trust history), which shrinks the window from four
round trips to one:

```python
def _persist_model_state(conn, model_urn, *, flags, run_id, score, history) -> None:
    """One read-merge-write per model per scan, not four."""
    assign_properties(conn, model_urn, {
        RISK_FLAGS: flags, RUN_ID: [run_id],
        TRUST_SCORE: [float(score.value)], TRUST_BAND: [str(score.band)],
        TRUST_HISTORY: [entry.render() for entry in history],
    })
```

**3. Detect the loss, if DataHub exposes a version.** `emit_mcp` accepts system
metadata; if the connected GMS returns an aspect version on read, carry it and
re-read after write to confirm the value landed. Investigate before promising it:
the OSS aspect API may not offer a compare-and-set, in which case say so plainly
in the docs rather than implying safety that is not there.

### How to verify

A test that interleaves two `assign_properties` calls against one entity with a
stale read in between, asserting the second does not erase the first's unrelated
property. It will pass today (different property names are preserved) and fail
for the same property, which is the honest boundary to document.

---

## F4. A mid-scan failure leaves the graph half-written and says nothing

**Severity: Medium. Effort: M.**

### Evidence

`run_scan` and `_write_back` in `modelguard/agent/pipeline.py` contain **no**
`try`/`except`. Verified:

```bash
grep -n 'try:\|except' modelguard/agent/pipeline.py   # no handler in either function
```

`_write_back` performs, in order: raise incident, upsert assertion, record
assertion result, add term, define properties, ensure tag, then per model add
tag, read-merge properties, publish document. Nine writes with no rollback and no
checkpoint.

### Why it matters

A GMS restart, an expiring token, or a transient 503 midway leaves an incident
raised with no tag on the model, no trust score, no impact report, and
reconciliation never reached. The next scan converges most of it (the writes are
idempotent, which is the saving grace), but two things do not self-heal:

- `_record_leak_columns` never ran, so the record that closes a deleted-column
  leak (D-069, D-074) is missing for that run.
- The user sees a stack trace and has no idea which half landed.

In `watch` the exception is caught by the poll loop and retried, so the daemon
survives. In `scan` it is an unhandled traceback.

### Proposed fix

Do not add transactions; DataHub has none and faking one would be worse. Add
**per-finding isolation and an honest report**:

```python
@dataclass(frozen=True)
class FindingWrites:
    ...
    error: str | None = None
    """Why this finding's write-back did not complete, if it did not. The scan
    continues to the next finding: one unwritable finding must not cost the
    other four, and a partial run that says which part failed is recoverable
    where a traceback is not."""


writes = []
for finding in findings:
    try:
        writes.append(_write_back(conn, finding, narrate(finding, llm), config, run_id, observed_at, trust_history))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        logger.warning("write-back failed %s", logfmt({"run_id": run_id, "finding": finding.title}), exc_info=True)
        writes.append(FindingWrites(finding=finding, narrative=..., error=safe_error(exc)))
```

Then: `ScanReport.partial` is true when any write carries an error; the CLI
prints those in red and **exits 1**; `gate` maps it to `EXIT_ERROR` (2), because
a scan that could not finish writing has not reached a verdict. The JSON
rendering gains the `error` field.

### How to verify

Unit test with a `FakeGraph` whose `execute_graphql` raises on the second
finding: the first finding's writes must be present, the second must carry an
error, and the report must be `partial`. Assert the exit code, since that is
what CI acts on.

---

## F5. No GMS server-version check

**Severity: Medium. Effort: S.**

### Evidence

`modelguard/client.py:127-138` calls `graph.test_connection()` and nothing else.
That proves the host answers; it says nothing about whether this GMS supports
what ModelGuard is about to do.

The project depends on server behaviour that is genuinely version-sensitive and
was verified against exactly one version. D-021 records that
`updateIncidentStatus` takes `IncidentStatusInput`, "introspected from a live GMS
1.5.0.6". D-022 records that Document entities are accepted on OSS. Structured
properties, the incident mutations, and the Document entity all landed in DataHub
at different times.

### Why it matters

A user on an older GMS gets a raw GraphQL validation error from the middle of a
write, after ModelGuard has already raised an incident. The message names a
DataHub type they have never heard of. Nothing points at the actual cause, which
is that their server is too old.

### Proposed fix

The SDK already provides exactly the primitive needed, which makes this smaller
than it looks. Introspected from the installed `acryl-datahub 1.6.0.13`:

```
DataHubGraph.server_config -> RestServiceConfig
RestServiceConfig: service_version, parsed_version, is_version_at_least,
                   supports_feature, is_datahub_cloud, server_type
```

So probe once at connect, warn rather than block, and name the minimum:

```python
#: The oldest GMS this project has been verified against (D-021 introspected the
#: incident mutations against 1.5.0.6, D-022 the Document entity). Not a hard
#: floor: an older server may serve every read path and fail only on a write, so
#: this warns rather than refuses. Refusing would lock out a user whose read-only
#: `inventory` would have worked perfectly well.
MINIMUM_VERIFIED_GMS = "1.5.0"


def server_warning(graph: DataHubGraph) -> str | None:
    """Return a warning when the server predates what this project verified.

    An unknown version is not evidence of an old one, so anything unreadable
    here is silence rather than a warning nobody can act on.
    """
    try:
        config = graph.server_config
        if config.is_version_at_least(MINIMUM_VERIFIED_GMS):
            return None
        return (
            f"DataHub at this endpoint reports {config.service_version}. ModelGuard "
            f"is verified against {MINIMUM_VERIFIED_GMS} and later; incident and "
            "structured-property writes may be rejected by this server."
        )
    except Exception:
        return None
```

Print it once in `_prepare`, and add it to `ScanReport.warnings` so it reaches
the JSON output and the CI job summary too.

`supports_feature` is worth investigating in the same pass: if it can answer for
structured properties or the Document entity directly, that is a better check
than a version comparison, because it asks the server what it can do rather than
inferring it from a number.

### How to verify

Unit test with a fake graph reporting an old version: the warning appears in
`report.warnings`. And a test that an unknown version produces no warning, since
guessing is worse than silence here.

---

# Evidence and measurement

## F6. The benchmark scores itself on its own graph against its own baselines

**Severity: High. Effort: L.**

### Evidence

`benchmarks/RESULTS.md` reports precision 1.00 and recall 1.00 for all three
original detectors. Every element of that measurement is authored by this
project:

- the graph, by `modelguard-seed`
- the failures, by `modelguard/seed/scenarios.py`
- the ground-truth labels, by `benchmarks/inject.py`
- the competing approaches, by `benchmarks/baselines.py`

benchmarks/CLAUDE.md rule 9 ("a baseline is written to be fair, not to lose") and
the RESULTS.md disclosure are honest about this, and the freshness sweep across
the SLA boundary is a genuine falsifiable test: it can fail, and a documented
mutation makes it fail.

Everything else cannot fail. The leakage comparison is a construction proof
presented in the shape of a measurement.

### Why it matters

A 1.00/1.00 table is the project's headline evidence and it is the claim a
sceptical reviewer will attack first. "You scored yourself on your own fixtures"
is unanswerable as things stand, and it puts the genuinely strong parts (the
sweep, the still-alerting-after-the-fix column) under the same shadow.

### Proposed fix

Three steps, in order of value per unit of work.

**1. Grade the disclosure by falsifiability, in the table itself.** Add a column
that says, per detector, whether a trial exists that could plausibly fail:

| Detector | Trials | Boundary trials | Could this table have failed? |
|---|---|---|---|
| Upstream freshness | 10 | 5 either side of the SLA | Yes: `>` to `>=` fails the 6h trial |
| Target leakage | 2 | 0 | No: presence/absence of one edge |

That single column converts a suspicious perfect score into a candid one, and it
costs an afternoon.

**2. Add boundary trials for the detectors that have none.** Leakage currently
tests "edge present" and "edge absent". The interesting cases are the ones a real
warehouse produces:

- a leak at exactly `leakage_max_hops` (must fire) and at `+1` (must not, and the
  scan must say the cap was reached, per F1)
- a feature and the label descending from a *common ancestor* rather than one
  from the other, which is not leakage and must not fire
- a column named like the label but not carrying the term
- a diamond: two paths to the label, where the shortest must be the one quoted

Each is a scenario plus a trial, and each one can genuinely go the wrong way.

**3. Score against a graph this project did not build.** The strongest possible
answer, and the most work. `examples/real-project/` already stands up a real dbt
plus MLflow plus postgres stack. Promote it: plant a leak in the dbt model,
ingest with DataHub's own sources, and score the detectors on the graph
*ingestion* produced rather than the graph the seeder wrote. That measures the
thing users actually have, and it would have caught F10 long ago.

### How to verify

The measure of success is that a reviewer can point at a trial and say "that one
could have gone the other way". If no trial in a detector's row can, the row is
documentation and should be labelled as such.

---

## F7. Trust-score weights are invented, and the score gates builds

**Severity: Medium. Effort: M.**

### Evidence

`modelguard/config.py`: 40 for an upstream failure, 20 leakage, 15 drift, 15
freshness lag, 10 missing owner, and now 15 sensitive source and 5 deprecated
input. The docstring is candid: "the plan's illustrative values (section 5.3)".

That number is written to `modelguard.trust_score` on the model, rendered as a
band, published in the impact report, and accepted by `modelguard gate
--min-trust 80` as a build-blocking policy input.

### Why it matters

A composite score with unjustified weights looks far more rigorous than it is.
The specific hazards:

- **The band boundaries are arbitrary too** (70 healthy, 40 watch). A model at 69
  and one at 71 are reported as different kinds of thing on no evidence.
- **`--min-trust` invites a false precision.** A team that sets 80 has calibrated
  nothing; they have picked a number against a scale whose units are undefined.
- **It is unstable across versions.** Adding the two governance detectors changed
  what every previously-scored model would now score, and nothing recorded that.
  The trust history added in D-081 will show a drop that is a release, not a
  regression.

### Proposed fix

Not "find the true weights", which do not exist. Make the score honest about
what it is.

**1. Version the scoring function and record it.** Add a `scoring_version` to the
config and stamp it into every history entry and the structured property:

```python
#: Bumped whenever a weight, a band boundary, or the set of contributing findings
#: changes. Recorded alongside every score, because a score is only comparable to
#: another score computed the same way, and D-079 changed the function under
#: models that had already been scored.
SCORING_VERSION = 2
```

Then the trust history renders a version change as a visible discontinuity rather
than a mysterious drop, and the impact report can say "scored under v2; earlier
entries used v1".

**2. Report the score as a band with its inputs, and de-emphasise the integer.**
The deductions list is the actionable part and it is already computed. Lead with
it. In the report: "at-risk: leakage, missing owner" before "42/100".

**3. Make `--min-trust` harder to misuse.** Recommend `--block-at-or-above` in
the docs as the primary policy, since a severity is a defined thing, and document
`--min-trust` as a blunt secondary control. Print a one-line warning when it is
used without `--block-at-or-above`.

**4. State the provenance where the number appears.** One sentence in the report:
"The weights are configuration, not a calibrated model. They express a stated
preference ordering (an unowned leaking model behind a live endpoint scores
zero), and the ordering is the claim, not the arithmetic."

### How to verify

A test that the scoring version is stamped on every history entry, and one that
a config change without a version bump fails a check in CI.

---

## F8. Six new modules have zero integration or benchmark coverage

**Severity: High. Effort: M.**

### Evidence

```bash
grep -rl 'sensitive\|deprecated\|link_infer\|trust_history\|render\|logs' tests/integration/
# no matches
```

The modules added in D-077 through D-084 (`detect/governance.py`,
`detect/column_marks.py`, `writeback/link_infer.py`,
`writeback/trust_history.py`, `render.py`, `logs.py`, `api.py`) are tested only
against `FakeGraph`. The governance detectors have benchmark trials defined in
`benchmarks/inject.py`, but the benchmark has not been run since they landed:
`RESULTS.md` is still dated 2026-08-01 and has no Scale section and no
governance rows.

benchmarks/CLAUDE.md rule 6 is unambiguous: "Measure against a live DataHub,
never against a fixture graph. A detector scored on our own fakes measures the
fakes."

### Why it matters

`FakeGraph` was written by the same people who wrote the detectors, against the
same mental model of DataHub. The failures it cannot reproduce are exactly the
ones that have bitten this project before, and every one of them is in the
decision log: `get_aspect` raising `TypeError` for a timeseries aspect (D-021),
`exists` returning False for every schemaField (writeback rule 5),
`incidentsSummary` never being written (D-018), the mlflow source dropping
`mlFeatures` (D-074).

The sensitive-source detector reads `globalTags` on a `schemaField`. Nothing has
confirmed a live GMS returns that aspect from that entity type. It is very likely
fine. "Very likely fine" is what the integration suite exists to replace.

### Proposed fix

**1. Run the benchmark.** It is one command against a seeded Quickstart and it
publishes the governance detectors' first real precision and recall, plus the
scale table:

```bash
modelguard-seed
python -m benchmarks.run_bench --out benchmarks/RESULTS.md
```

Until that runs, the two new detectors are unmeasured claims and `RESULTS.md`
misrepresents the project's own coverage.

**2. Add integration tests for the write-shaped ones.** `tests/integration/`
already has the pattern. The three worth adding:

- `test_sensitive_source.py`: plant the tag, scan, assert one incident on the
  feature's source column with the classification in its title; revert, rescan,
  assert resolved. This is the one that proves `globalTags` on a `schemaField`
  round-trips.
- `test_trust_history.py`: three scans with different findings, read the property
  back from GMS, assert three entries and that a rerun of the middle one adds
  nothing. Structured properties with MULTIPLE cardinality and 20 values have
  not been exercised against a real server.
- `test_link_infer.py`: run `link --infer --yes` against the seeded graph and
  assert the resulting link matches the hand-written one.

**3. Make the gap visible.** A CI job that fails when a module under
`modelguard/` has no corresponding integration or benchmark reference would keep
this from recurring, though a simpler discipline is a line in the PR template.

### How to verify

`RESULTS.md` carries rows for `Sensitive source (P5)` and `Deprecated input (P6)`
with real numbers, and its "Run at" date is after this document.

---

# Packaging and distribution

## F9. CI tests one Python version; the package claims three

**Severity: Medium. Effort: S.**

### Evidence

`.github/workflows/ci.yml` uses `python-version-file: .python-version` for every
job, which pins 3.11. `pyproject.toml` declares `requires-python = ">=3.11,<3.14"`
and classifiers for 3.11, 3.12 and 3.13.

D-074 records a manual check on 3.12.3, once, by hand. 3.13 has never been run.

### Why it matters

The package advertises support for two Python versions no automated check has
ever exercised, and 3.12 and 3.13 are what current distributions ship: Ubuntu
24.04 has 3.12, 25.04 has 3.13. The advertised versions are the ones most users
will actually be on, and the tested version is the one almost nobody has by
default.

The risk is concrete: this codebase uses `StrEnum`, `datetime.UTC`, `tomllib`,
`zip(strict=)`, and `dataclasses.replace` on frozen classes, plus `singledispatch`
registration by type annotation. Behaviour around dataclass and enum internals
has moved between 3.11 and 3.13.

### Proposed fix

Matrix the offline job only. The docker, helm and audit jobs stay single-version,
since they are not testing Python semantics:

```yaml
  test:
    name: lint, types, offline tests (py${{ matrix.python }})
    strategy:
      fail-fast: false      # a 3.13-only failure must not hide a 3.12-only one
      matrix:
        python: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
```

Keep `pre-commit` (ruff, mypy) on 3.11 alone: linting three times reports the
same findings three times, and mypy's `python_version` is pinned in config
anyway.

If a version fails and is not worth supporting, narrow `requires-python` and drop
the classifier. An advertised version is a promise.

### How to verify

Three green checks on the PR instead of one, and the classifier list matches the
matrix exactly.

---

# Adoption

## F10. `--infer` needs an aspect the validated stack does not produce

**Severity: High. Effort: M.**

### Evidence

`modelguard/writeback/link_infer.py:_feature_dataset` resolves the feature table
from `DataProcessInstanceInputClass` on the model's training runs, and raises
when there is none:

```python
if not inputs:
    raise InferenceError(
        f"{model_name}'s training run records no input datasets "
        "(dataProcessInstanceInput is empty), which is the usual state after "
        "an mlflow ingest. ..."
    )
```

The error message states the problem accurately, and D-074 documents it from the
live run: DataHub's mlflow source produces "an mlModel that knows nothing at all:
no features, no inputs on its training run, no link to a single column."

So `--infer` refuses on precisely the stack this project validated against.

### Why it matters

`--infer` was built (D-080) to remove the adoption cliff where every model needs
four hand-typed arguments. On the seeded demo graph it works, because the seeder
writes the run inputs. On a real mlflow ingest it declines. The cliff is intact
for the users it was meant to help, and the README's framing ("ask ModelGuard to
work it out for you") oversells what will happen on their catalog.

### Proposed fix

Add inference routes that work on what a real ingest *does* produce, in strict
descending order of confidence, each labelled with its own confidence in the
proposal.

**Route 2, MLflow run parameters.** DataHub's mlflow source carries the run's
tags and params into `DataProcessInstanceProperties.customProperties`. Teams
routinely log the training dataset there (`mlflow.log_param("dataset", ...)`, or
`mlflow.data` inputs). Read those keys, resolve any value that looks like a table
name against the catalog, and propose it as a **medium-confidence** match:

```python
#: Run-parameter keys that conventionally name the training dataset. A convention,
#: not a standard, so a hit is proposed and never assumed, and the reason line
#: says which key it came from.
_DATASET_PARAM_KEYS = ("dataset", "dataset_name", "training_data", "input_table", "table")
```

**Route 3, the model's own downstream lineage.** If anything in the catalog
declares dataset-to-model lineage (Spark and some ingestion sources do), read it.

**Route 4, ask instead of guessing.** When no route resolves, do not raise.
Return an incomplete proposal listing the *candidate* tables (datasets whose name
shares a token with the model's, capped and clearly labelled a shortlist) so the
user picks one instead of going to find it themselves:

```
Inferred from the graph:
  feature table: NOT FOUND. The training run records no inputs, which is the
    usual state after an mlflow ingest. Nearest by name, for you to choose:
      1. analytics.churn_features
      2. analytics.churn_labels
  Re-run with --features <table>, or log the dataset as an MLflow run parameter
  and re-ingest so this can be read rather than guessed.
```

That is the difference between a tool that refuses and a tool that helps.

**And fix the README.** State plainly that `--infer` works when the training run
records its inputs, and that a plain mlflow ingest often does not, with the
one-line `mlflow.log_param` that makes it work next time.

### How to verify

Re-run `examples/real-project/` end to end and confirm `link --infer` produces a
usable proposal on the graph DataHub's own ingestion built. That is the only test
that counts here, and it is the same test that should have caught this.

---

## F11. `link` must be re-run per training run, with no automation hook

**Severity: High. Effort: L.**

### Evidence

From `modelguard/writeback/link.py` and D-074: DataHub's mlflow source upserts
the whole `mlModelProperties` aspect and drops the features `link` attached, so
the link must be replayed after every ingestion. `link --all` exists for exactly
this and is the documented remedy.

Nothing runs it. There is no Airflow operator, no dbt hook, no mlflow plugin, no
scheduled job, and the Helm chart deploys only `watch`.

### Why it matters

This is the project's central adoption risk, and it is structural rather than a
bug. The value proposition requires a link that decays on a schedule the user
does not control. A team adopts ModelGuard, sees it work, and three weeks later
their nightly ingest has silently reverted every model to "not checked". The
scan will say so honestly (that is what `coverage.py` is for), which converts a
silent failure into a visible one, but the tool still stopped working and
somebody has to notice and go re-run a command.

### Proposed fix

**1. Ship the scheduled replay, since the chart already deploys a daemon.** Add a
`link --all` CronJob to `charts/modelguard-watch`, off by default, one values
block to enable:

```yaml
link:
  enabled: false            # opt-in: it writes, and it should be a deliberate choice
  schedule: "0 3 * * *"     # after a typical nightly ingest
```

This is the smallest change that makes the decay survivable, and it reuses a
command that already exists and is already tested.

**2. Document the two-line integration where the user already is.** In the
README's training-script section, next to `link_model`, show the pipeline shape
rather than only the call:

```python
# In the training script, after the model is registered. This is the durable
# place for it: the link is re-declared by the same run that creates the model,
# so an ingest that drops it is repaired on the next training run rather than by
# somebody remembering.
mlflow.log_param("modelguard_features", FEATURE_TABLE)   # also feeds --infer (F10)
link_model(model=..., features=FEATURE_TABLE, label_column=...)
```

**3. Report the decay as a first-class finding.** ModelGuard can detect its own
broken link: a model with a recorded `modelguard.feature_table` property but no
`mlFeatures` has been de-linked by an ingest. That is a positive-evidence check,
cheap, and it turns the failure into something `inventory` and `watch` surface:

```
Unevaluated(
    check="target leakage",
    reason="this model was linked on <date> but its features are gone, which is "
           "what an mlflow ingest does to mlModelProperties (D-074)",
    remedy="Replay it: modelguard link --all",
)
```

**4. The real fix is upstream.** `docs/most-valuable-feedback.md` already reports
the aspect-clobbering behaviour as feedback #14. An mlflow source that patched
`mlModelProperties` instead of upserting it would delete this entire problem for
every DataHub user, not just this project's. That is the contribution worth
pursuing, and it is a far better use of the OSS-contribution lane than another
skill.

### How to verify

Ingest, confirm the features are gone, run the CronJob's command, confirm the
scan is green again. And the new de-link check fires in between, which is what
makes the gap visible rather than silent.

---

# Operability

## F12. `watch` hides the error message and never logs a failure

**Severity: Medium. Effort: S.**

### Evidence

`modelguard/cli.py:1012-1016`:

```python
except Exception as exc:  # a daemon must survive SDK failures
    console.print(
        f"[yellow]watch poll failed ({type(exc).__name__}); "
        f"retrying in {backoff:.0f}s.[/yellow]"
    )
```

Only the exception's class name is printed. The message is discarded. And the
structured logging added in D-078 covers the success path only: `_log_scan` emits
one line per completed scan, and nothing emits on failure.

### Why it matters

An operator watching a daemon fail every five minutes sees
`watch poll failed (HTTPError); retrying in 300s` and has no way to tell an
expired token from a network partition from a GMS that is out of disk. The
information needed to fix it was in the exception and was thrown away.

`safe_error()` exists a few hundred lines up in the same file and scrubs the
token out of exactly this kind of message. It is simply not called here.

### Proposed fix

```python
except Exception as exc:  # noqa: BLE001 - a daemon must survive SDK failures
    # safe_error, not str(exc): an SDK failure can quote the request we handed
    # the token to, and this line lands in a log the whole team reads.
    logger.warning(
        "watch poll failed %s",
        logfmt({"error_type": type(exc).__name__, "backoff_s": round(backoff)}),
        extra={LOG_FIELDS: {"error_type": type(exc).__name__, "backoff_s": round(backoff)}},
        exc_info=True,
    )
    console.print(f"[yellow]watch poll failed: {safe_error(exc)}[/yellow]")
    console.print(f"[dim]retrying in {backoff:.0f}s[/dim]")
```

`exc_info=True` puts the traceback in the log where an operator can find it,
while the console keeps the one-line scrubbed summary. Both the JSON and text
formatters already handle `exc_info` (`JsonFormatter.format` renders it under
`exception`).

Also worth adding: after N consecutive failures, escalate the console message to
red and say plainly that the daemon is not working, since a yellow line every
five minutes is a line people stop seeing.

### How to verify

A test asserting that a raised `RuntimeError("token=abc... expired")` reaches the
console with the token scrubbed and the message intact. The existing
`test_an_exception_printed_to_the_console_carries_no_token` is the template.

---

## F13. No LLM timeout, so a hanging provider stalls a daemon forever

**Severity: Medium. Effort: S.**

### Evidence

`modelguard/llm.py:150-156` constructs the chat model with `model`, `api_key`,
`temperature` and `max_tokens`. No timeout, and no retry cap.

`narrate` wraps the call in `except Exception` and falls back to the template, so
an *error* is handled correctly. A provider that accepts the connection and never
responds raises nothing. The call blocks.

### Why it matters

In `scan` that is a hung terminal. In `watch` it is a daemon that stops polling
and stops reporting, while looking alive. The finding it was narrating is never
written, and the freshness incident it was about to raise never appears. A
reliability tool that silently stops because a third-party API is slow is the
specific irony worth avoiding.

### Proposed fix

Every LangChain chat binding accepts a timeout; verify the keyword per provider
against the installed packages (root CLAUDE.md rule 7) before writing this.

```python
#: Seconds to wait for the narrator before giving up and writing the template.
#: The prose is a nice-to-have and the finding is not: no incident should wait on
#: somebody else's API. Deliberately short, because this sits between a detected
#: failure and the incident that reports it.
LLM_TIMEOUT_SECONDS = 30.0

#: One attempt. LangChain's default retry behaviour would multiply the timeout by
#: the retry count, and a narrator that takes two minutes has already lost to the
#: template.
LLM_MAX_RETRIES = 0
```

Pass both at construction. Keep them in `config.py` (they are algorithm
parameters, not identity) so a slow self-hosted provider can raise them.

Then confirm the fallback records it: `Narrative.source` becomes `TEMPLATE` and
the scan logs one line saying the narrator timed out, so the degradation is
visible rather than mysterious prose changes.

### How to verify

A unit test with a fake chat model that sleeps past the timeout, asserting the
narrative falls back to the template and the scan completes. That test also pins
the guarantee the README makes: detection never depends on the LLM.

---

## F14. `watch` runs a full scan twice on every change

**Severity: Low. Effort: S.**

### Evidence

`modelguard/cli.py:_watch_once` runs `run_scan(..., dry_run=True)` to compute the
finding signature, and then, when the signature changed, runs `run_scan(...)`
again for real. Every detector, every lineage traversal, and every coverage check
runs twice per transition.

### Why it matters

It doubles the graph load at exactly the moment something is wrong, which is when
DataHub is most likely to be busy. It also opens a window: the two scans can
observe different graph states, so the report printed to the console can differ
from what was written.

This is deliberate and defensible (the dry run is what makes "no change, stay
quiet" possible) but it is not free and it is not documented as a cost.

### Proposed fix

The dry run is only needed to compute a signature, and a signature is a pure
function of the findings. Detect once, then decide:

```python
def _watch_once(...) -> frozenset[FindingSignature]:
    # One detection pass. The signature decides whether the write path runs, and
    # the same findings are what it writes, so the console and the graph cannot
    # disagree about what this poll saw.
    report = run_scan(conn, config, ..., dry_run=True)
    signature = _finding_signature(report)
    if signature == previous:
        return signature
    written = run_scan(conn, config, ...)   # still a second call today
```

Doing this properly means splitting `run_scan` into `detect` and `write_back(findings)`
so the caller can reuse one detection. `_detect` and `_write_back` already exist
as private functions; the work is exposing a supported seam and threading the
reconciliation, which currently lives inside `run_scan`.

Given the severity, this is worth doing only alongside F4, which touches the same
code. Otherwise leave it and document the cost in a `ponytail:` comment naming
the ceiling.

---

## F15. `--limit` slices after fetching the whole catalog

**Severity: Medium. Effort: S.**

### Evidence

`modelguard/cli.py:_model_urns`:

```python
urns = sorted(str(urn) for urn in conn.client.search.get_urns(filter=F.entity_type("mlModel")))
return tuple(urns[:limit] if limit is not None else urns)
```

`SearchClient.get_urns` returns an `Iterable[Urn]` with no count parameter. The
generator is fully consumed by `sorted()` before the slice.

### Why it matters

`modelguard inventory --limit 50` reads as "look at 50 models". On a catalog with
20,000 models it enumerates all 20,000, paging through search, and then discards
19,950. The flag that exists to bound the work does not bound the work.

`scan --all-models` has the same shape with no limit at all, and then runs a full
scan per model, which is what the new scale benchmark measures.

### Proposed fix

Stop consuming the iterator once the cap is reached. Sorting still requires a
full read, so drop the sort when a limit is given and say so:

```python
def _model_urns(conn: DataHubConnection, *, limit: int | None = None) -> tuple[str, ...]:
    """Return mlModel URNs, stopping the search once `limit` is reached.

    Sorted only when unlimited. A stable order needs every URN in hand, and
    reading the whole catalog to sort 50 of them is what this parameter exists
    to avoid; a capped run is explicitly a sample, not the first N by name.
    """
    found = conn.client.search.get_urns(filter=F.entity_type("mlModel"))
    if limit is None:
        return tuple(sorted(str(urn) for urn in found))
    return tuple(str(urn) for urn in islice(found, limit))
```

Then make `inventory` print `showing 50 of an unknown total (capped)` so the
sample is not mistaken for the catalog.

Add `--limit` to `scan --all-models` as well, for the same reason.

### How to verify

A `FakeSearch` that counts how many items were pulled from the generator:
`--limit 5` must pull 5, not all of them.

---

## F16. Reconciliation is N+1 across columns and incidents

**Severity: Medium. Effort: M.**

### Evidence

`_reconcile_stale_findings` in `pipeline.py`, per model scan:

- for each candidate source column (features plus recorded leak columns), call
  `_active_incidents_titled`, which calls `attached_incident_urns` (one
  `get_related_entities`) and then one `get_aspect` per attached incident;
- and now, since D-079, it does that **twice** per column, once for the leakage
  prefix and once for the sensitive prefix.

A model with 40 features and a handful of incidents per column is hundreds of
round trips per scan, and `scan --all-models` multiplies that by the catalog.

detect/CLAUDE.md rule 3 forbids exactly this ("Batch graph reads; no N+1 single
fetches"). The rule is enforced in the detectors and not in reconciliation.

### Why it matters

It is the dominant cost of the write path, and it grows with feature count, which
is the thing that grows fastest on a real model. The new scale benchmark measures
the *dry-run read* path and will not surface it.

### Proposed fix

**1. Fetch each column's incidents once, not twice.** The two prefix walks share
a resource:

```python
for source_column, known_feature_urn in sorted(candidates.items()):
    # One relationship read and one aspect read per incident, shared by both
    # column detectors, instead of the same reads repeated per prefix.
    active = _active_incidents(conn, source_column)   # -> list[tuple[urn, IncidentInfoClass]]
    for prefix, finding_type, on_resolve in _COLUMN_RECONCILERS:
        ...
```

**2. Batch the aspect reads.** `DataHubGraph.get_entities` takes a list of URNs
and returns their aspects in one call. Verified present on the installed SDK,
already used by `graph_reads.live_deployments:50`, and already implemented by
`FakeGraph`, so this needs no new fixture work. Collect every attached incident
URN across all candidate columns first, then fetch their `incidentInfo` in one
batch.

**3. Skip the walk entirely when nothing could be open.** The model records
`modelguard.open_leak_columns` (D-074). If that property is empty and this scan
raised no column finding, there is nothing to reconcile and the whole loop can be
skipped. That is the common case on a healthy catalog, which is most scans.

### How to verify

Extend the scale benchmark to measure the **write** path as well as the read
path, with the same `_CountingGraph`. The reads-per-model figure is the number
that must fall, and it is already the diagnostic the scale table reports.

---

# Maintainability

## F17. 65% of the package is prose

**Severity: Medium. Effort: L.**

### Evidence

Measured with `ast`, counting docstrings and comment lines against total lines
under `modelguard/`:

```
TOTAL modelguard/: 11,680 lines, 7,140 docstring, 466 comment => 65% prose
```

Worst offenders by share:

| File | Lines | Docstring | Prose |
|---|---|---|---|
| `agent/graph.py` | 346 | 258 | 78% |
| `models.py` | 776 | 573 | 74% |
| `writeback/assertions.py` | 278 | 201 | 74% |
| `writeback/link_infer.py` | 290 | 208 | 73% |
| `seed/scenarios.py` | 652 | 369 | 63% |

Plus 7,227 lines of markdown under `docs/`, of which the decision log alone is
about 2,600.

### Why it matters

To be clear about what is *not* wrong: the comment quality here is genuinely
excellent, and the class of comment that explains a trap (the `paths` versus
`urn` distinction, the assertion-source restamping, why a soft delete is not
enough) is the most valuable kind and should stay untouched.

The problem is volume and placement. Working in this codebase, finding the actual
logic means scrolling past essays. `models.py` is 776 lines to define what is
essentially eight dataclasses. A 40-line module docstring that restates the
design rationale is a maintenance liability: it duplicates the decision log, and
the two will drift, at which point a reader has two sources and no way to know
which is current.

There is also a signalling cost. Some passages read as written for a judge rather
than for the next maintainer, and a reviewer who notices that starts discounting
the parts that are load-bearing.

### Proposed fix

Not a mass deletion. A rule and one pass:

**1. Adopt a placement rule** in root CLAUDE.md:

> A docstring says what a caller needs to use the thing correctly: arguments,
> return, raises, and the one non-obvious constraint. A *comment* explains a trap
> at the line that contains it. Everything else, the options considered, the
> history, the rationale, belongs in the decision log, referenced by id.

**2. One pass over the five worst files**, moving history and rationale into the
decision log entry it already duplicates and leaving a `(D-0NN)` reference. Target
the module docstrings first: they are the largest and the most duplicative.

**3. Keep every trap comment.** If a comment prevents a specific bug, it stays,
regardless of length. The test for whether a passage is load-bearing: would
removing it let somebody reintroduce a bug the decision log records? If yes, keep.

**4. Trim the README to a landing page.** It is 538 lines and is now also the
PyPI long description. The top 120 lines (what it is, the measured claim, install,
one worked example) are what anyone reads. Move the rest to `docs/`.

Expected outcome: `modelguard/` around 45% prose, which is still high and
appropriate for this kind of code, without the essays.

---

## F18. `cli.py` is 1,545 lines and does four unrelated jobs

**Severity: Low. Effort: M.**

### Evidence

`modelguard/cli.py` holds: entity resolution (`resolve_table`, `resolve_model`,
`_model_urns`), console rendering (nine `_print_*` functions), the watch loop and
its state machine, the inference proposal printer, and five Typer commands with
their argument surfaces.

The five commands now carry 45 distinct options between them.

### Why it matters

Low severity because it works and it is tested. It matters because every new
feature lands here (the last five all did), the file grows monotonically, and the
guard clauses at the top of each command are where the one real bug of the last
release lived (the rich markup crash) precisely because nothing ever exercised
them.

### Proposed fix

Split along the seams that already exist, without changing behaviour:

```
modelguard/cli/__init__.py    the Typer app and command registration
modelguard/cli/resolve.py     resolve_table, resolve_model, _model_urns
modelguard/cli/display.py     the _print_* family, plus _print_proposal
modelguard/cli/watch.py       WatchState, _watch_once, _announce_watch_change
```

`render.py` already established that rendering is separable and pure. `display.py`
is the console counterpart, and pulling it out makes the printers testable
without a `CliRunner`.

Do this when the next command is added, not before: a refactor with no behaviour
change is a large diff to review for no user-visible gain, and the file is not
yet painful enough to justify it on its own.

---

# Suggested order

Grouped by what they unblock, not by severity alone.

| Wave | Items | Rationale |
|---|---|---|
| 1 | **F2**, **F9**, **F12**, **F13** | Hours each. F2 and F9 are release-blocking for a package that is now published; F12 and F13 make the daemon debuggable and unstallable. |
| 2 | **F1**, **F8** | The two that decide whether the tool's answers can be trusted: one closes a false-negative path, the other measures what has never been measured. |
| 3 | **F10**, **F11** | The adoption cliff, which no amount of correctness compensates for. F10 first, since F11's fix depends on the same MLflow parameter. |
| 4 | **F3**, **F4**, **F5** | Robustness. F4 and F14 touch the same code, so do them together. |
| 5 | **F6**, **F7** | The evidence story. Slower, and worth doing before the next external presentation rather than before the next release. |
| 6 | **F15**, **F16**, **F17**, **F18** | Efficiency and maintainability, continuous rather than scheduled. |

Waves 1 and 2 are about a week. They are the ones I would not ship another
release without.

## What this document does not cover

- **Security beyond what is already reviewed.** D-049 and D-073 covered prompt
  injection containment and token scrubbing, and both hold. The one open item is
  operational, not code: D-075 records a fine-grained clone token that outlived
  its job on the demo VM and can only be revoked by its owner.
- **The repository rename**, which is tracked on the pre-tag checklist in
  `docs/deploy/pypi-release.md` and is a decision, not a weakness.
- **Anything about the LLM's output quality**, which is deliberately unmeasured
  and correctly so: detection does not depend on it.

## Change log

| Date | Author | Change |
|---|---|---|
| 2026-08-02 | Claude (for Ghassen Naouar) | Initial audit against `e73584f`: 18 findings across correctness, evidence, packaging, adoption, operability and maintainability, each with a proposed remedy and a verification step |
