# Design decisions

The choices that shaped the product, each with what else was considered and why
this one won. Written for a reader who was not in the room.

They are grouped by what they are about, not by date. Several were forced by
something that turned out not to be true of DataHub, and those are the most
useful ones to read.

## What the language model is allowed to do

**The model writes prose and only prose.** Detection is deterministic Python.
Nothing the model emits may reach a dedup key, a severity, a URN or an enum.

*Considered:* letting a capable model read the lineage graph and judge whether a
feature leaks, which is what most reliability agents do.
*Why not:* three things become impossible at once. The detectors stop being
measurable, because the same input can produce a different answer. Prompt
injection through a table description becomes able to invent or suppress a
finding. And the tool stops working for anyone without an API key. The
deterministic version gives up nothing that matters: a graph traversal is exactly
the kind of question a program answers better than a model does.

**No vendor is named outside one module.** Provider, model and key come from the
environment together, all three or none, and `janus/llm.py` is the only module
allowed to import a vendor SDK.

*Considered:* defaulting to one provider so the tool works out of the box.
*Why not:* a default provider means a silent call billed to whatever key happens
to be in the ambient environment. Missing means missing, and it fails naming the
variable.

**The narrator never raises.** An unconfigured model, an uninstalled binding, a
network error, a rate limit, an empty or over-long reply all fall back to the
deterministic template and record which source produced the text.

## How writes are keyed

**An incident's dedup key is `(resource_urn, incident_type, title)`, and
`run_id` is deliberately not in it.**

*Considered:* including `run_id`, which is the obvious reading of "make every
write traceable".
*Why not:* `run_id` changes every run, so every scan would raise a fresh copy of
the same finding. Provenance and identity are different jobs. The `run_id` is
stamped into the body instead, and since every scan also emits a
`dataProcessInstance`, it resolves to something a reader can open.

**Existing incidents are found by traversing the `IncidentOn` relationship
inbound, never by reading `incidentsSummary`.**

*Why:* GMS does not write `incidentsSummary`. A dedup based on it silently finds
nothing and duplicates on every scan. This was found by having it happen.

**Reconciliation keys on the finding type, not on `(resource, incident_type)`.**

*Why:* leakage and a sensitive source both raise a `FIELD` incident on the same
column. A flatter key leaves a fixed leak open forever.

## What DataHub would not let us do

**Incidents attach to the data asset, and model risk is carried as structured
properties on the model.**

*Wanted:* an incident on the `mlModel`, which is the entity a reliability
incident is actually about.
*Reality:* `incidentInfo.entities` accepts dataset, chart, dashboard, dataFlow,
dataJob and schemaField. GMS answers 500 for a model. Filed upstream as an RFC,
see [14-oss-contributions.md](14-oss-contributions.md).

**Guarding assertions are rendered as portable open-assertions YAML, validated by
parsing it back through DataHub's own `AssertionsConfigSpec`, with
`assertionInfo` emitted directly.**

*Wanted:* DataHub's smart assertions and scheduled evaluation.
*Reality:* both are DataHub Cloud only, as is `DataHubClient.assertions`, which
imports the Cloud package. Detection is therefore Janus's own, and the Cloud
boundary is disclosed rather than hidden. Judges reward candour and penalize
hidden Cloud dependencies.

**Freshness is read from the `operation` aspect through
`get_latest_timeseries_value`.**

*Why:* `operation` is a timeseries aspect and `get_aspect` raises a `TypeError`
for it. Not obvious from the documentation.

**Column-level lineage is read from the returned paths, never from a result's
`urn`.**

*Why:* a column-level query returns the parent *dataset* as the result URN. Reading
the URN names the wrong thing entirely. Later, the flattened path list had to be
split back into the paths GMS returned as well, because two derivations through
one upstream table were rendering as a single impossible chain.

**Model discovery goes through one module that turns off DataHub's
latest-version-only search filter.**

*Why:* search hides non-latest versions of a versioned model. A model Janus cannot
see is one whose link never replays and whose incident never closes, so this is not
optional.

**A tag on a model goes through read-merge-emit on `globalTags`.**

*Why:* there is no `mlModel` patch builder in `datahub.specific`, and a blind write
is an upsert of the whole list, so it would drop tags somebody else applied.

## The join, which is the whole product

**`janus link` writes the model-to-column join, because no DataHub ingestion
source produces it.**

This was learned by running the product against a real dbt plus MLflow plus
postgres stack rather than against the seeded demo graph. MLflow gives a model
with no features; dbt gives column-level lineage between tables; nothing joins
them. Without the join, every column-level detector reports "not evaluated" on a
real catalog, which the demo graph had been hiding.

The same exercise produced `coverage.py`, `inventory` and the rule that a check
which could not run is never rendered as healthy.

**`link --infer` proposes the join and a human confirms it.**

*Considered:* inferring and writing it directly.
*Why not:* a wrong label column makes every leakage verdict wrong in both
directions. `--infer` tries four routes in descending order of confidence, says
which one answered and whether each line is a fact or a guess, and writes nothing
until you answer. Where nothing in the graph names a label at all, it refuses to
invent one.

Exclusions come only from the warehouse's own key declarations, never from column
names that look like identifiers: `customer_id` is usually a join key and
`score_id` is usually a feature, and no rule over names tells them apart.

**`link --from feast|dbt` imports the join from a declaration your team already
maintains.**

*Why:* a Feast repo and a dbt semantic model already say which column each feature
is read from, and they know things a name match cannot. The case that proves it is
a feature named `tenure` read from a column named `tenure_months`. The readers are
offline and read-only: they parse a file on disk and never connect to Feast, to
dbt or to a warehouse.

Where a declaration names a column the table does not have, the import stops
rather than linking the rest. A half-declared model is one whose unchecked columns
nobody would ever hear about.

**`watch --events` re-applies, catalog-wide, any link an ingest drops.**

*Why:* DataHub's MLflow source upserts the whole `mlModelProperties` aspect, which
drops the features `link` attached. From that moment the column-level checks have
nothing to walk and report "not evaluated" on a model that was fully checked
yesterday. Nothing errors. This is the adoption cliff, and no poll of one target
could ever see it.

*Considered:* DataHub's own `datahub-actions` framework.
*Why not:* it would put a second configuration surface next to `env.py` for the
same records. Polling stays the default and needs no broker.

Only links a human already confirmed are ever replayed: an inferred join looks
identical to a confirmed one in the graph, and replaying a guess would make every
detector downstream confident about the wrong columns.

## Saying what cannot be known

**A model nothing has linked gets the table-level answer, labelled as weaker.**

*Considered:* staying silent, which is what a positive-evidence rule implies.
*Why not:* silence reads as health. The degraded mode answers at table
granularity, is gated so it stays silent whenever a column-level detector can
answer, never outranks a column-level finding, and is excluded from the trust
score because a maybe must not move a number people compare over time. Every one
of its findings quotes the precision the benchmark measured for table-level
reasoning, against the question that precision answers.

**The classification detectors have no default taxonomy and report themselves not
evaluated when unset.**

*Why:* a guessed classification URN either matches nothing or matches a term that
means something else in your catalog. A false incident about a compliance exposure
is the worst kind to be wrong about.

**The generated documents refuse to certify anything.** The evidence pack's first
heading is "This is not a compliance certification" and its second is "What this
pack could NOT establish", deliberately at the front rather than as a closing
caveat, because a gap at the end of a long document is a gap nobody reads.
Anything the catalog does not record prints as *not recorded in the catalog*
rather than being quietly dropped.

**The NIST AI RMF crosswalk is a mapping, not a conformity claim,** and says so in
its own first paragraph. Which subcategory an artifact is evidence *for* is a fact
about the artifact; whether the subcategory is *satisfied* is a judgement about a
whole process, and nothing that reads a metadata graph is in a position to make
it. The table is generated from the detector registry, so a check cannot be added
without appearing in it.

## The trust score, and what it admits about itself

**The score is a rollup of a scan's findings, written only for models a finding
named**, and it leads with its deductions, each naming the triggering finding.

**The band caps at `watch` when the worst finding is critical or high**, whatever
the point total. Points alone let a live leaking model read healthy at exactly the
70 floor while `gate` correctly blocked it. Found by testing the product rather
than by reading it.

**Every history entry carries a scoring version.** A release that adds a detector
changes what every previously scored model would now score. Without the version, a
trend that drops because of a release looks exactly like a trend that drops because
somebody shipped a bug.

**The weights are labelled a preference ordering, not a calibrated model,**
wherever the number is shown. `gate --min-trust` prints that caution itself.
A composite score with unjustified weights looks far more rigorous than it is.

## Evidence

**The benchmark measures a live DataHub, never fixtures**, and the freshness sweep
walks the SLA boundary rather than only planting the obvious failure. A detector
scored on its own fakes measures the fakes. The sweep is what makes the number mean
anything: a `>` to `>=` off-by-one scores a clean 1.00 under the demo scenario
alone.

**A baseline is written to be fair, not to lose.** Each is handed every fact Janus
gets and differs in one respect, and each is tested to genuinely detect before it
is tested to over-report. A baseline that finds nothing turns the comparison into a
fabrication no green suite would catch.

**Detectors are also scored on a graph this project did not build**, the dbt plus
MLflow stack in `examples/real-project/`, where the leak lives in a dbt model and
the derivation comes from DataHub's SQL parser.

**Every finding's counterfactual is verified by performing it** against the graph
and asking the detector again, rather than being printed as advice.

**The suite is mutation tested**, because a green suite proves nothing until a
fault kills it. The report groups every survivor under a root cause with a verdict
rather than stopping at a score.

## Operational shape

**`gate` answers in three exit codes, and the third one is the point:** `0`
shippable, `1` policy violated, `2` could not reach a verdict. A gate that reported
"I could not connect" as a policy violation would teach a team to wave through
every red build. The bundled GitHub Action carries the same distinction as an
`outcome` output alongside the boolean `blocked`, so a step cannot comment "this
model is unsafe" on a pull request because DataHub happened to be unreachable.

**The MCP server is read-only, on any flag.** The model driving an MCP client is
outside this project's control, so it gets to ask what is wrong and never to fix
it. It is meant to run beside DataHub's own `mcp-server-datahub`, which answers
what the catalog contains; Janus answers the three questions that have to be
reproducible.

**Docker composes onto the network `datahub docker quickstart` already creates**
rather than reimplementing DataHub's own multi-container stack. The compose project
is named explicitly, because DataHub's own Quickstart defaults to the same name and
sharing it would let an ordinary `docker compose down --remove-orphans` here stop
the entire Quickstart.

**There is one Helm chart, for `watch`, not a chart per command.** `scan` and
`gate` are one-shot and the MCP server speaks stdio to whatever launched it.
`watch` is the only entry point meant to run forever.

**Structured logging, three OTLP metrics, and no traces.** The scan numbers are
assembled once and rendered twice, as a human line and as structured fields, so a
metric and a log line cannot disagree about a scan. A team that wants spans across
the SDK's HTTP calls installs `opentelemetry-instrumentation-requests` and gets
them, which is better than shipping a second, worse copy of it.

**The agent is an entity in the graph it guards.** Every scan emits a
`dataProcessInstance` naming what it read and what it wrote. A dry run emits
nothing.

## Packaging and naming

**The distribution is `janus-datahub`**, because the exact name `janus` was already
taken on PyPI by an unrelated package. The console scripts are still `janus`,
`janus-mcp` and the rest: a distribution name and its entry points are independent.
The desktop binary is `janus-argos` rather than `argos`, which is too generic to
claim on somebody's PATH.

**Dependencies moved off exact `==` pins.** Exact pins fought the resolver and made
the published package effectively uninstallable alongside anything else.

**Python's floor is 3.11, and the ceiling is verified rather than assumed.** A
`<3.12` ceiling based only on a CLI warning made the package uninstallable on
every current distro Python; widened after 3.12 was verified to behave
identically. Development and CI still target 3.11 exactly. See
[12-operations.md](12-operations.md).

## The desktop watchdog

Argos exists, and its design decisions are in [11-argos.md](11-argos.md). The one worth
repeating here: **the renderer applies no thresholds.** A trust band arrives on the
event because a detector decided it, and re-deriving it in the window would let the
window disagree with the catalog it is reporting on.
