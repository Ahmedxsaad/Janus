# The reports and generated documents

Everything Janus produces for a reader rather than for the graph: the catalog
sweeps, the governance artifacts, and the machine-readable output formats.

All of them are read-only by default. Asking for documentation never raises an
incident as a side effect, and the sweeps write nothing unless you say so.

## Catalog sweeps

### `janus inventory`

Every model in the graph, and per model, what can and cannot be checked on it.
This is the first command to run against a catalog that has never seen Janus, and
its honest answer on a fresh one is "not checked" for most models. That is
[the link](05-the-link.md) missing, not a detector failing.

Discovery goes through `janus/discovery.py` rather than through search, and that
matters: DataHub versions entities, so registering a second version of an MLflow
model produces a **second** `mlModel` and stamps the first one's
`versionProperties.isLatest` to false. GMS then drops every non-latest version
out of search results, while the entity stays perfectly alive: not soft-deleted,
still carrying its aspects, still carrying whatever Janus wrote to it. A model
Janus cannot see is one whose link never replays and whose incident never closes,
so turning that filter off is not optional.

### `janus coverage`

The same sweep folded into the number a platform lead reports upward:

```
Guard coverage: 34% of models have a checkable leakage path (up from 11%),
8% have a training schema snapshot.
```

`detect/guard_coverage.py` is a pure fold over the per-model gaps. It reads
nothing itself, which is what keeps `detect/` from importing the pipeline to get
a sweep. It also names **the single next declaration that would unblock the most
checks**, which reframes the adoption cliff from an embarrassment into a roadmap.

Three things it is careful about:

- **It measures how much has been declared, not how healthy the models are.** A
  catalog at 8% is one where Janus mostly cannot tell you either way, and the
  report says so.
- **The denominator is printed beside every percentage.** 100% of two models is
  not the same claim as 100% of two hundred.
- **Freshness is deliberately excluded from the figure.** It is a check asked of
  a *table*, and folding one table check and five model checks into one
  percentage divides by two different denominators and calls the result one
  number. A test asserts the model-check tuple equals what a model with nothing
  set up actually produces, so a seventh detector cannot open a silent sixth row.

`--write` appends one capped entry to the trend, so the next sweep has a
direction to compare against.

### `janus finops`

The one command whose reader is a budget holder rather than an engineer, and the
only one that suggests deleting something:

> These 6 tables exist only to feed churn_model_v2, which has no live deployment
> and nothing has touched in 90 days.

It is a **report and not a detector**, deliberately. Nothing is broken. A model
nobody deployed is not a defect, it is a decision somebody may not have noticed
they made, and raising an incident about it would put a cost question into the
queue where correctness failures live. So there is no finding type, no incident,
no severity and no trust deduction.

Two guards make it safe to act on:

- **A table is listed only when *every* downstream model is unused.** One live
  consumer and it is not a saving, it is a table somebody needs.
- **A model with no recorded date is reported as *undated*, never as unused.**
  DataHub records what somebody's ingestion put there. Calling an unstamped model
  abandoned, in a report whose whole purpose is to suggest deletion, would be the
  single most expensive mistake this module could make. In a report like this, an
  absence is not evidence.

It also reads the model set through `discovery.py`, for the sharpest version of
the reason above: a hidden model version is exactly the live consumer that would
make this report recommend deleting a table something still reads.

### Incident lifecycle

`janus/lifecycle.py` measures how long Janus's own findings stay open: mean and
median time to resolution, per finding type. A leak caught in eleven minutes and
a leak caught in eleven days are the same detection and completely different
outcomes, and this is the number that says whether the tool changed anything.

**Nothing new is recorded to make it possible.** `incidentInfo.created.time` is
when the incident was raised and `incidentInfo.status.lastUpdated.time` is when
it moved to RESOLVED, both stamped by GMS. An incident whose two stamps disagree
with the order of events would produce a negative duration, so it is reported as
unusable rather than clamped to zero: a floor of zero would silently flatter the
mean.

## Governance artifacts

Three documents, all read entirely out of the catalog, all printing by default
and writing only with `--write`.

The reason they are generated rather than maintained is the whole point: a
hand-written model card is accurate until the model next changes, and most of
them are already wrong. These are regenerated from the graph, so they are current
by construction and empty exactly where the catalog is. **Anything DataHub does
not record prints as *not recorded in the catalog* rather than being quietly
dropped**, so a gap is visible in the document instead of reading as an absence
of a problem.

### `janus model-card`

A model card in the sense Mitchell et al. (FAT* 2019) proposed one: intended use
where somebody declared it, which columns each feature was actually computed
from, the reported training metrics, the trust score with its waterfall, the
findings open against the model, and the checks that could not run.

### `janus evidence-pack`

Maps the same graph to Regulation (EU) 2024/1689 Article 10 (training-data
provenance, governance, examination for bias) and Article 12 (record-keeping),
**by paragraph number**, so a reader can check the mapping rather than trust it.

Its first heading is **"This is not a compliance certification"** and its second
is **"What this pack could NOT establish"**. Deliberately the first section rather
than a closing caveat, because a gap at the end of a long document is a gap
nobody reads. It states, for instance, that freshness at training time is
unknowable from this graph (Janus measures freshness *now*, which is a different
claim and not a substitute), and that whether anyone examined the data for bias
is an activity no catalog records.

A generated document that implied conformity would be worse than no document at
all.

### `janus feature-card`

A **Data Card** in the sense Pushkarna, Zaldivar and Kjartansson (FAccT 2022)
proposed one, but for a single feature. It answers the question every data
scientist asks and nothing today answers: *where does this feature actually come
from?*

Per feature: where it is computed from hop by hop, every other derivation the
walk found, each table that chain crosses and how current it is, whether the
chain reaches a column classified as restricted or as a protected attribute,
whether its type has moved since training, and, when a finding names it, the
changes that would clear it.

Taken per model because that is what somebody has in hand; `--feature` filters
within it. Its freshness figures say out loud that they are measured *now* and
not at training time, for the same reason the evidence pack refuses to substitute
one for the other: a provenance claim has to say when it was true.

The paper's argument that documentation a system produces outlives documentation
a person promises to write is why this exists, and its known-limitations field is
why the card renders what it could **not** establish as a section of its own.

### `janus crosswalk`

One row per detector, mapping it to the NIST AI RMF subcategory its output is
evidence for, with the subcategory text **quoted verbatim from the AI RMF 1.0
Playbook** rather than paraphrased. Prints markdown on stdout and connects to
nothing: it is a fact about the detectors, not about a catalog, so it runs before
a token exists.

It is a mapping and not a conformity claim, and it says so in its own first
paragraph. Which subcategory an artifact is evidence *for* is a fact about the
artifact; whether the subcategory is *satisfied* is a judgement about a whole
process, and nothing that reads a metadata graph is in a position to make it.

The table is generated from the detector registry keyed by `FindingType`, so a
check cannot be added to Janus without appearing in it, and a test enforces that.

## Machine-readable output

### `--format json`

Both `scan` and `gate` put the whole report on stdout as one parseable document:
evidence, models at risk, trust deductions, each finding's counterfactual, the
gate's violations. Progress lines move to stderr so the stream stays clean.

This exists because a team routing findings somewhere Janus does not know about
(a ticket system, a dashboard, their own policy engine) should not have to parse
coloured console text. The JSON shape is treated as a public interface.

### The CI job summary

`gate` running inside GitHub Actions appends a markdown table to the file named
by `GITHUB_STEP_SUMMARY`: findings, severities, trust scores, and the checks that
could not run, rendered on the run's own page. No token, no API call, no
permission block, because GitHub already gives every step that file. Outside
Actions the variable is unset and nothing is written.

The alternative is an exit code, which in CI means a red cross and a click into a
log.

Both renderings live in `janus/render.py` and are **pure functions** of a scan
report, for the same reason the gate is pure: they can be exercised offline
against a hand-built report, and they hold no judgement of their own.

## The gate's three exit codes

`janus gate` is the preventive half of the product, and the third exit code is
the point:

| Code | Meaning |
|---|---|
| `0` | Shippable |
| `1` | The policy was violated |
| `2` | The gate could not reach a verdict (DataHub unreachable, bad config) |

A gate that reported "I could not connect" as a policy violation would teach a
team to wave through every red build, so a setup failure never masquerades as a
finding.

The bundled GitHub Action carries the same distinction to the rest of the
workflow as an `outcome` output (`clean`, `blocked`, `error`) alongside the
boolean `blocked`. It matters more there: a step gated on `blocked == 'true'`
must not comment "this model is unsafe" on a pull request because DataHub
happened to be unreachable, so `blocked` stays false when the gate could not
tell.

Two policies, and one is better than the other. `--block-at-or-above` acts on a
severity a detector decided. `--min-trust` acts on a weighted sum whose weights
are a stated preference ordering, so a team that sets `--min-trust 80` has
calibrated nothing against a scale with no units. `--min-trust` used on its own
prints exactly that caution. It still works, and it is the blunter of the two.

The gate reads and does not write by default because it runs on every push to
every branch of every pull request, most of which are never merged and many of
which are the same commit retried. One incident per run would fill the governance
graph with findings about code that does not exist.

## The desktop and terminal surfaces

`janus watch --pet` and `janus companion` drive the Argos window, described in
[11-argos.md](11-argos.md). With no window binary installed, both print one line
per change in the terminal instead, which is also what runs over SSH.

## Structured logs and metrics

Every completed scan logs one line carrying what an SLO is built from: findings
raised, writes made, and how long detection itself took, kept separate from the
poll interval and from DataHub's own indexing because `watch` controls neither.

Two readers want two different things from that line: a human tailing the process
wants `scan complete run_id=... findings=1`, readable without a parser; a log
pipeline wants indexable fields. Both are served from **one source of truth**. A
caller logs its message and attaches the facts as a mapping; the text format
renders them as logfmt and `JANUS_LOG_FORMAT=json` emits them as object keys.
Nothing is written twice, so the two renderings cannot drift.

What never appears in a log line: aspect content, narrative prose, or a
credential. Identifiers, counts and durations only.

Set `JANUS_OTEL_ENDPOINT` and the same three numbers also go to an OTLP collector
as metrics. The exporter is a **logging handler reading the very fields the
pipeline already assembles**, exactly the way the Argos handler reads the phase
field, so a metric and a log line cannot disagree about a scan: one measurement,
two renderings.

Deliberately three instruments and no traces. A team that wants spans across the
DataHub SDK's HTTP calls installs `opentelemetry-instrumentation-requests` and
gets them, which is better than this project shipping a second, worse copy of
somebody else's product.
