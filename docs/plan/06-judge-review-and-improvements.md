# Judge review, and the improvements to land before the PyPI tag

Written 2026-08-01, against `f5c0901` on `main`. The repository was read as a
judge reads it: the README first, then the code behind each claim, then the
tests and the benchmark that back the numbers. Everything below is either
verified in the tree or explicitly marked as an assumption.

State at review time: 402 offline tests pass in 14 seconds (42 integration
tests deselected), `python -m build --wheel` produces a wheel with the
structured-property YAML packaged correctly, and every README claim spot-checked
against source held up.

Two parts:

1. [Scorecard](#part-1-scorecard) - how this scores against the five published
   criteria, and where the points are actually being lost.
2. [Improvements](#part-2-improvements) - ten proposals, ranked, each with the
   criterion it moves, the smallest implementation that works, and its size.

**Status, 2026-08-02: all ten are implemented** (D-076 through D-084). Each
section below keeps its original argument, which is what makes the decisions
readable a month from now; what actually shipped, and what it cost, is in the
decision log. The one item deliberately left open is the repository rename
inside **I**, which is now a blocking line on the pre-tag checklist in
`docs/deploy/pypi-release.md` rather than an unlogged P1.

---

## Part 1: scorecard

Criteria are equally weighted and tie-break in the order listed
(`docs/hackathon-specs/05-judging.md`).

### 1. Use of DataHub - strong

Reading: column-level lineage through `paths` rather than `urn` (the distinction
most submissions get wrong), the `operation` aspect for freshness,
`schemaMetadata` and `editableSchemaMetadata`, glossary terms on both the
`schemaField` and the parent dataset, `ownership`, and ML entities
(`mlModel`, `mlFeature`, `mlModelDeployment`).

Writing back: incidents, tags, structured properties, Document entities,
assertion entities with evaluation results, glossary terms, and ML feature
links. That is the "goes beyond reading metadata" the criterion asks for, and
it is the deepest write surface of anything likely to be submitted.

Where points are still on the table:

- **Governance signals are read for nothing.** DataHub's classification surface
  (PII tags, sensitivity terms, deprecation, domains) is exactly the graph
  context a judge from DataHub thinks about all day, and no detector consumes
  it. See improvement **B**, which is the single highest-value item here.
- **No use of DataHub's own agent surfaces.** The criterion names the MCP
  Server, Agent Context Kit, DataHub Skills and Analytics Agent by name.
  Janus ships its *own* MCP server and a skill, and contributes an
  `mcp_ext` tool, which is arguably better. But it never *composes* the official
  `mcp-server-datahub`, so a judge reading the criterion literally cannot tick
  that box. Improvement **F** is the cheap version of ticking it.
- **Deprecation is a free signal nobody reads.** A model training on a dataset
  DataHub already marks deprecated is a one-aspect check (**B2**).

### 2. Technical execution - very strong, one honest hole

402 offline tests, a marked integration suite that runs against a live
Quickstart, a benchmark that scores detectors against a real graph rather than
fixtures, a sweep across the SLA boundary that would catch a `>` / `>=`
off-by-one, CI running pre-commit plus the offline suite, a Dockerfile, a
compose file that composes the Quickstart instead of rebuilding it, a Helm
chart, and a GitHub Action. The mutation-testing discipline (D-016) is rarer
than the test count.

The hole is named in `benchmarks/RESULTS.md` already: **no scale test**.
`scan --all-models` runs a full independent scan per model, so a catalog with
500 models is an unmeasured number of graph round trips. A judge whose day job
is a real catalog (a Data Architect, a Pinterest EM) will ask exactly this.
Improvement **E** closes it with a measurement rather than an optimization, which
is the honest order.

Second, smaller: `P2-5` in `04-improvements.md` (structured logging with
`run_id`) is still open. `pipeline.py` emits one `key=value` line per scan,
which is most of the value; JSON behind an env flag is the rest (**H**).

### 3. Originality - strong, but the original part is buried

Leakage detection, drift detection and freshness monitoring each exist
elsewhere. What does not exist elsewhere is **the join**: `janus link`
writes the model-to-column edge that no DataHub ingestion source produces, and
every detector is then a walk across a boundary nothing else can cross. The
benchmark's per-feature table (1.00 / 1.00 vs table-level lineage's 0.25 / 1.00,
and "still alerting after the fix: 2 features") is the proof, and it is the
strongest single artifact in the repository.

That story appears on README line ~200, under a heading called "Use it on your
own project". A judge who stops at the demo section never sees it. Improvement
**J** is free and moves the tie-break-critical criterion 3.

The other originality gap is that `link` is **manual**. Every model needs a
human to name its feature table, its label column, and its join keys. That is a
per-model onboarding tax, and it caps how original the idea can look: a judge
who runs `inventory` on their own DataHub sees "not checked" everywhere and has
no cheap way past it. Improvement **A** is the answer.

### 4. Real-world usefulness - strong, with one adoption cliff

`janus inventory` reporting "not checked" instead of "healthy" is the most
professionally credible thing in the repository, and `examples/real-project/`
(validated on a real dbt + MLflow + postgres stack rather than assumed) is what
separates this from a demo. The gate's three exit codes, with "could not reach a
verdict" distinct from "policy violated", is the kind of detail that only comes
from having been burned.

The cliff is the same one as above: between `pip install` and a first useful
finding there is a manual `link` per model. **A** removes it. **G** (the
importable Python API) removes the shell-out from the training script, which is
where `link` actually belongs.

Secondary: a finding lands in DataHub and stops there. A platform team wants it
where they already look. **D** is the ten-line version of that (a GitHub job
summary), deliberately not a Slack integration.

### 5. Submission quality - strong, with one thing judges see first

The README is genuinely excellent: it states what is not measured, what a check
cannot do and why, and what each dependency choice cost. `examples/` has real
generated artifacts. The security section is a threat model, not a disclaimer.

Two issues:

- **The repository is still named `DataHub`.** `P1-1` has been open since the
  first improvements doc. A repo named after the sponsor's own product is the
  first thing a judge sees, and it reads as a fork of DataHub. **This is not
  free to fix any more**, and the reason is worth recording: the PyPI Trusted
  Publisher registered in `docs/deploy/pypi-release.md` matches on
  `Repository name: DataHub`. A rename breaks publishing until the pending
  publisher is edited to match, and GitHub's redirect does not help, because
  the OIDC claim carries the new name. Decide deliberately: see **I**.
- **The README is 392 lines with 22 relative links.** On the PyPI project page
  every one of those 22 resolves against `pypi.org` and 404s, because
  `readme = "README.md"` ships this file verbatim as the long description. That
  is what the first `pip install janus-datahub` visitor sees. **I** fixes it.

### Bonus: open-source contribution - strong

The skill, the `mcp_ext` tool with its RFC, and sixteen reproducible feedback
items with repros and workarounds. Nothing needed here except actually filing
them, which `docs/plan/05-oss-delivery.md` covers.

### Summary of where the points are

| Criterion | Verdict | The one thing that moves it |
|---|---|---|
| Use of DataHub | Strong | **B**: read the governance graph, not just the structural one |
| Technical execution | Very strong | **E**: measure scale, honestly |
| Originality | Strong, buried | **A** (inferred link) and **J** (lead with the join) |
| Real-world usefulness | Strong, one cliff | **A**, then **G** |
| Submission quality | Strong, one own-goal | **I** (PyPI-facing README, and the rename decision) |

---

## Part 2: improvements

Ranked by (judge points moved) / (lines of code). Each carries the smallest
implementation that works; a bigger one is a decision, not a default.

### A. `janus link --infer`: propose the join instead of demanding it

**Moves:** originality, real-world usefulness. **Size:** ~150 lines plus tests.

Today a user must know and type the feature table, the label column, and every
join key. Almost all of that is already in the graph: DataHub's mlflow source
records the training run and the datasets it read, and the feature table's
`schemaMetadata` lists the columns.

`--infer` reads those, proposes a complete `link` invocation, prints it as the
exact command a human would have typed, and writes only on confirmation (or
`--yes`). The heuristics stay deterministic and dumb on purpose:

- Feature table: the dataset upstream of the model's training run. More than
  one, and it asks rather than guesses.
- Label column: a column already carrying the configured label term, else a
  name match against a small configurable list (`label`, `target`, `y`,
  `churned`, `is_*`). No match means no guess: it says so and asks.
- Excluded columns: columns that are primary keys or that appear in a lineage
  join edge. Everything else is a feature.

The point is not that inference is always right. It is that a wrong proposal a
human reads and corrects costs seconds, where a blank `--features` flag costs an
afternoon of reading the catalog. Detection stays deterministic either way: this
writes the *declaration*, and the user confirms it.

Explicitly **not** an LLM feature, per root CLAUDE.md rule 4.

**Ships when:** `inventory` on a freshly ingested stack goes from "not checked"
to a proposed command per model, and an integration test asserts the inferred
proposal matches the hand-written one for `examples/real-project/`.

### B. The sensitive-feature detector: a model trained on data it should not see

**Moves:** use of DataHub (the biggest single gain), real-world usefulness.
**Size:** ~120 lines plus tests, because it reuses the leakage walk almost whole.

Target leakage asks: does this feature descend from the label? The same
traversal, with a different term set, answers a question every regulated team
has and no lineage tool answers today: **does this feature descend from a column
somebody classified as PII, restricted, or otherwise not permitted in a model?**

The machinery already exists. `detect/leakage.py` has `_LabelIndex` (reading
glossary terms from both `editableSchemaMetadata` and the `schemaField`'s own
aspect, cached per dataset) and `leak_path` (walking a column's upstream cone
and returning the shortest proving chain). A `_SensitiveIndex` that also reads
`globalTags` and matches a configured set of term and tag URNs drops into the
same walk.

The finding it produces is one nothing else in the ecosystem can produce:

> `credit_risk_v3` feature `applicant_income_band` derives, three hops upstream,
> from `pii.applicants.ssn`, tagged `PII` by `<owner>`. The model serves live
> traffic. Derivation chain: `ssn -> ssn_hash -> income_band`.

Config, all-or-nothing per root CLAUDE.md rule 6c: `JANUS_SENSITIVE_TERMS`
and `JANUS_SENSITIVE_TAGS`. Unset means the detector does not run, and
`coverage.py` reports it as not evaluated with the reason, exactly like the
other three.

**B2, a footnote-sized sibling:** a model whose input dataset carries DataHub's
`deprecation` aspect. One `get_aspect` call, one finding type, no traversal. Add
it in the same PR or not at all.

**Ships when:** the benchmark scores it like the other three (`inject.py` plants
a tagged column upstream of a feature and a clean control), and it appears in
`RESULTS.md` with measured precision and recall.

### C. Trust as a trend, not a snapshot

**Moves:** use of DataHub, real-world usefulness. **Size:** ~80 lines.

`janus.trust_score` is a structured property, overwritten every scan. The
value of a trust score is its *direction*: 82 means nothing, 82 after 95 last
Tuesday means somebody shipped something.

Lazy version, and it should stay the lazy version: append a row
(`run_id`, UTC timestamp, score, band, the deductions that fired) to the Model
Impact Report Document that `writeback/documents.py` already maintains, and cap
the table at the last N runs. The Document is already read-before-write and
already idempotent per run.

What this deliberately is **not**: a custom timeseries aspect. That needs a
DataHub model change, which belongs in the RFC lane (`mcp_ext/`), not here.

**Ships when:** three scans with different findings produce three rows in one
report, a fourth rerun of an unchanged graph adds none, and the reader can see
which deduction changed.

### D. Put the gate's verdict where the reviewer is looking

**Moves:** submission quality, real-world usefulness. **Size:** ~15 lines.

`janus gate` answers in an exit code. In a pull request that means a red X
and a click into the log. GitHub Actions already exposes the answer: if
`GITHUB_STEP_SUMMARY` is set, anything written to that path renders as markdown
on the job page. No token, no API call, no permissions block, no dependency.

The gate already builds a findings table for the console. Write the same
markdown to that path when the variable is present. Judges who try the bundled
action see the verdict, the findings, and the trust score on the run page
without opening a log.

`--format json` on `scan` and `gate` is the same paragraph of work and makes
both scriptable. Do both or neither.

**Ships when:** a CI run of the action on a leaking model shows the findings
table on the job summary page, and a unit test asserts the file is written only
when the variable is set.

### E. Measure scale, do not optimize it

**Moves:** technical execution. **Size:** ~60 lines in `benchmarks/`.

`RESULTS.md` says "no scale test" and that honesty is worth more than a fake
number, but the number itself is worth more still. `scan --all-models` performs
one full scan per model; nobody has measured what that costs.

Add a `--replicas N` flag to the seeder (the graph spec is already generated,
so this is a loop, not a new seeder), then a benchmark trial that reports, for
N in something like 1, 10, 50: wall clock for `scan --all-models`, graph calls
issued, and per-model p50/p95. Publish it in `RESULTS.md` whatever it says.

If it turns out slow, the fix is a shared per-run cache of dataset aspects,
which `_LabelIndex` already demonstrates the pattern for. Do not write that
cache before the measurement says it is needed.

**Ships when:** `RESULTS.md` has a scaling table and its "not measured" list no
longer contains scale.

### F. Read the graph through DataHub's own MCP server, once

**Moves:** use of DataHub. **Size:** documentation plus one worked example,
or ~40 lines if wired.

Criterion 1 lists the MCP Server, Agent Context Kit, DataHub Skills and
Analytics Agent explicitly. Janus ships its own MCP server and a skill and
contributes a tool to `mcp-server-datahub`, which is a stronger position than
merely consuming them, but nothing in the repository *composes* the official
surfaces.

The cheap, honest version: a documented worked example in `skill/` showing the
`datahub-ml-guard` skill driving both servers side by side, the official one for
open-ended catalog questions and `janus-mcp` for the deterministic checks,
with a paragraph on why detection is not exposed as a question an LLM answers.
That paragraph is itself a differentiator, and it makes the composition
visible without adding a dependency.

Do not fold `mcp-server-datahub` in as a runtime dependency for a criterion
tick. That would be complexity bought with points.

### G. An importable API, because this is about to be a PyPI package

**Moves:** real-world usefulness. **Size:** ~40 lines, mostly re-export.

`import janus` currently gives a version string and a docstring. Once
`pip install janus-datahub` is real, the natural first thing a user tries
is to call it from the script that trains the model, which is exactly where
`link` belongs. Shelling out to a CLI from inside a training script is a
worse interface than a function call, and it is what the README currently tells
people to do.

The minimum that is worth having, and nothing beyond it:

```python
from janus import link_model, scan_model

link_model(model="churn_model", features="analytics.customer_features",
           label_column="churned", exclude=["customer_id"])
report = scan_model(model="churn_model", dry_run=True)
```

Two functions wrapping code paths the CLI already calls, with the connection
handled the same way the CLI handles it. No new abstraction layer, no client
class, no builder. Public means documented in the README and covered by a test
that imports from `janus` and not from a submodule, so the surface is
pinned and the internals stay free to move.

**Ships when:** a test imports only from the top-level package and runs a full
link-then-scan against the integration graph.

### H. JSON logs behind one env flag (closes P2-5)

**Moves:** technical execution. **Size:** ~25 lines.

`pipeline.py` already emits one `key=value` line per run carrying `run_id`,
phase timings and counts, which is most of the value. What is missing is a
machine-parseable form for anyone running `watch` under a log pipeline.

`JANUS_LOG_FORMAT=json` selects a `logging.Formatter` subclass that
`json.dumps` the record and its extras. Stdlib only, no `structlog`. Default
stays the human-readable line, because the default reader is a human at a
terminal. Then mark P2-5 done in `04-improvements.md`.

### I. Ship a README PyPI can render, and settle the rename

**Moves:** submission quality. **Size:** an hour, plus one decision.

Two separable things, both due before the tag.

**The long description.** `readme = "README.md"` ships this file as the PyPI
long description, where all 22 relative links resolve against `pypi.org` and
404. Two options:

1. Rewrite the 22 links as absolute `https://github.com/<owner>/<repo>/blob/main/...`
   URLs. One file changes; GitHub renders absolute links identically. Cheapest,
   and recommended.
2. Add a short `README-pypi.md` and point `readme` at it. A second document that
   will drift from the first. Choose this only if the full README is judged too
   long for a package page, which it arguably is at 392 lines.

**The rename (P1-1).** Renaming `DataHub` to `janus` is worth real points
on the criterion judges see first, and it is no longer free:

- The PyPI Trusted Publisher is registered against `Repository name: DataHub`.
  It must be edited on PyPI in the same sitting, or the first release fails
  OIDC verification.
- `action.yml` consumers (`uses: Ahmedxsaad/janus@main`) survive on GitHub's
  redirect, but the README example should be updated in the same commit.
- The Helm chart's default image repository and `publish-image.yml`'s GHCR path
  both contain the repo name.

Recommendation: rename, and do it **before** the first tag, in one commit that
touches all four places, with the PyPI publisher edited first. Renaming after
publishing is strictly worse. If the team would rather not, log that as a
decision with the reason, because an unlogged open P1 reads as an oversight.

**Pre-tag release checklist**, verified mechanically today except where noted:

- [x] `python -m build --wheel` succeeds; `writeback/props/*.yaml` is in the wheel
- [x] All four console scripts are declared in `entry_points.txt`
- [x] `requires-python` admits 3.11 through 3.13
- [ ] README renders on PyPI with working links (this item)
- [ ] Repo rename decided, and the Trusted Publisher matches whatever is decided
- [ ] `pip install` of the built wheel into a **throwaway** venv, then
      `janus --help` and `janus inventory` against a live GMS
      (`docs/deploy/pypi-release.md` warns why the dev venv would lie)
- [ ] `__version__` in `janus/__init__.py` matches `pyproject.toml`
      (both `0.1.0` today; nothing enforces they stay equal - a two-line test
      would, and is worth writing while touching this)

### J. Lead with the join

**Moves:** originality, submission quality. **Size:** a README edit.

The single most defensible claim in this project is measured, and it sits at
line ~200 under a heading about other people's projects. Promote the
per-feature comparison table (1.00 / 1.00 against table-level lineage's
0.25 / 1.00, with 2 features still alerting after the fix) to just under the
live-demo section, with one sentence naming what nobody else can do: **join the
warehouse graph to the ML graph at column granularity, and prove it changes the
answer**.

No new content. Moving 15 lines up 180 lines.

---

## Suggested order

Ordered so each step is shippable alone and nothing blocks on a decision that
has not been made.

| Wave | Items | Why this order |
|---|---|---|
| 1 | **J**, **D**, **H** | Hours each, no design decisions, all visible to a judge |
| 2 | **B** (+B2) | Biggest criterion-1 gain, reuses machinery that already exists and is tested |
| 3 | **A** | Biggest usefulness gain, the most design surface, wants B's config landed first |
| 4 | **E**, **C** | Measurement and trend; C may change once E says what a scan costs |
| 5 | **G**, **I** | The package-facing work, last, so the README documents what actually shipped |
| 6 | **F** | Documentation-shaped; can land any time, needs no code |

Wave 1 alone is a day. Waves 1-3 are the ones that change a score.

## Change log

| Date | Author | Change |
|---|---|---|
| 2026-08-01 | Claude (for Ghassen Naouar) | Initial review against `f5c0901`: scorecard on the five criteria, ten ranked improvements, pre-tag release checklist |
| 2026-08-02 | Claude (for Ghassen Naouar) | All ten implemented (D-076 to D-084). The proposals are kept as written, since the argument is the part worth rereading; the release checklist moves to docs/deploy/pypi-release.md, where it blocks a tag |
