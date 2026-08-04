# ModelGuard

<p align="center">
  <img src="assets/argos.gif" width="200" height="200"
       alt="Argos, the ModelGuard watchdog: patrolling, walking his beat, sniffing out a lineage traversal, barking with a red collar at a finding, writing it back, wagging when it clears, then asleep." />
</p>

The missing CI for your ML supply chain. ModelGuard is an agent that sits on
the warehouse-to-ML boundary that DataHub uniquely spans: it reads end-to-end
column-level lineage and ML metadata to catch silent data-to-model failures
(target leakage, upstream blast radius, training/serving schema drift), and
writes incidents, model trust scores, impact reports, and guarding assertions
back into the DataHub graph. Every scan is itself catalogued there, as a
dataProcessInstance naming what it read and what it wrote, so the agent is
subject to the same lineage it guards.

Built for "Build with DataHub: The Agent Hackathon" (Devpost, deadline
Aug 10, 2026). Category: Production ML Agents.

Status: the core loop works end to end. A stale upstream table is detected, its
blast radius traversed into the live model that consumes it, and an incident,
a tag, risk properties, a guarding assertion with its measured result, and a
Model Impact Report are all written back to DataHub. Reruns never duplicate.
Target leakage, training/serving schema drift, and the model trust score have
landed, as has the human-approval agent (`scan --review`) and a polling `watch`.

## Live demo

**<https://modelguard.ahmedxsaad.me>**

A real DataHub instance with the ML supply chain seeded, a failure planted, and
`modelguard watch` running against it continuously, so what you are looking at
is a graph ModelGuard is actively maintaining rather than a screenshot. Sign-in
credentials are in the Devpost submission's testing instructions (they are
deliberately not in this repository, since anything committed here stays in the
git history forever).

Once signed in, search `credit_risk_v3` for the model side (its
`model-at-risk` tag, `modelguard.trust_score` and `trust_band` properties, and
the linked Model Impact Report), or `loans_raw` for the data side (the open
incident and the guarding freshness assertion). Nothing needs to be installed
to see any of it.

Prefer to run it yourself? [Try it](#try-it) below is the same loop against a
local Quickstart, and it is the path the rest of this README documents.

## The one thing nothing else does, measured

DataHub holds two graphs that no other catalog holds together: column-level
lineage across the warehouse, and ML metadata for the models. Nothing joins
them, so a model is not connected to a single column and a data failure cannot
be traced to the model it breaks. **ModelGuard writes that join** (`modelguard
link`) and then reads across it, which is what makes every detector below
possible.

That is a claim, so here it is as a number. The same graph, the same ground
truth, three ways of reading it, scored per **feature**: every approach can tell
that a leaking model leaks, and the question that separates them is *which* of
its features leaks, which is what somebody has to go and fix.

| Approach | Precision | Recall | Still alerting after the fix |
|---|---|---|---|
| ModelGuard (column-level lineage) | 1.00 | 1.00 | 0 features |
| Table-level lineage | 0.25 | 1.00 | 2 features |
| Table quality checks, no lineage | - | 0.00 | 0 features |

Note the middle row's **perfect recall**: table-level lineage does catch the leak. It
just cannot say which of the two features carries it, because both descend from the same
labelled table. And having never seen the column edge, it cannot see the column edge
being removed either, so it keeps alerting on a graph somebody has already fixed. That
last column is what gets a reliability tool switched off.

These are implementations of an *approach*, handed ModelGuard's own label index so
nothing is won by starting better informed; no Great Expectations or Evidently process
was run. [RESULTS.md](https://github.com/Ahmedxsaad/DataHub/blob/main/benchmarks/RESULTS.md) says so, and states what is still not
measured, alongside a scale table for a whole-catalog sweep.

## Documentation

Everything a user of the package needs is also a page: `site/` is a static
documentation and landing page covering install, every command, the Python API,
the MCP server and Argos, with the dog walking the reader down it. It reads the
same sprite art the window does, so serve the repository root rather than
opening the file:

```bash
python -m http.server        # then open http://localhost:8000/site/
```

The plan and the reasoning behind the product live here:

| Doc | What it answers |
|---|---|
| [docs/plan/01-strategy-modelguard.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/01-strategy-modelguard.md) | Why this project, what it solves |
| [docs/plan/architecture.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/architecture.md) | How it works: layers, flows, diagrams |
| [docs/plan/02-implementation-plan.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/02-implementation-plan.md) | The build: phases, APIs, schedule |
| [docs/plan/03-production-hardening.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/03-production-hardening.md) | Benchmark, scaling, security model |
| [docs/plan/04-improvements.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/04-improvements.md) | Proposed improvements, pending decisions |
| [docs/plan/06-judge-review-and-improvements.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/06-judge-review-and-improvements.md) | Review against the judging criteria, and what to land before the PyPI tag |
| [docs/plan/07-weaknesses-and-remedies.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/07-weaknesses-and-remedies.md) | An adversarial audit: 18 known weaknesses, each with a proposed fix |
| [docs/decision-log.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/decision-log.md) | Decisions made, options, why, results |
| [docs/hackathon-specs/](https://github.com/Ahmedxsaad/DataHub/tree/main/docs/hackathon-specs) | Official hackathon rules and requirements |

## Repository layout

```
modelguard/    Python package: seed/, detect/, writeback/, agent/, argos/
argos/         Argos, the desktop window: a Tauri binary and its text sprite art
skill/         OSS contribution: the datahub-ml-guard skill
mcp_ext/       OSS contribution (stretch): MCP incident mutation tool
examples/      Sample generated artifacts for judges
benchmarks/    ModelGuard-Bench: injection, metrics, measured RESULTS.md
tests/         pytest unit and integration tests
docs/          Plan, decision log, hackathon specs
assets/        The animation at the top of this file, generated from the sprite art
site/          The documentation and landing page for the shipped product
```

## Prerequisites

- Linux, Python 3.11 exactly, Docker (about 2 CPUs / 8 GB free for the Quickstart)
- No credentials are required for a local Quickstart: it ships with metadata
  service authentication disabled. See .env.example for when you need a token.
- No LLM key is required either, and no particular vendor. ModelGuard uses a
  model only to word the incident description and the report's assessment;
  without one, deterministic template prose is written instead. Detection never
  depends on the LLM. Pick your provider in .env (anthropic, openai, or google)
  and install its binding: pip install -e ".[openai]"

## Try it

```bash
pip install -e ".[dev]"           # add ".[anthropic]", ".[openai]" or ".[google]"
cp .env.example .env              # DATAHUB_GMS_URL=http://localhost:8080
datahub docker quickstart         # UI at http://localhost:9002 (datahub/datahub)

modelguard-seed                   # build the ML supply chain the datapacks lack
modelguard-scenario --lag-hours 30   # a source table silently stops refreshing
modelguard scan --table loans_raw    # detect, explain, write back
```

Not developing on ModelGuard itself, just want the CLI against your own DataHub?
Until the first PyPI release is cut it is `pip install -e .` from a clone; from
the release on, `pip install modelguard-datahub` (add `[agent]` for
`scan --review`, `[mcp]` for `modelguard-mcp`). The distribution is named
`-datahub`, since the exact name `modelguard` was already taken on PyPI by an
unrelated package; the commands you run are still `modelguard`,
`modelguard gate`, `modelguard-mcp`.

The scan names the live model at risk, then writes the incident, the
`model-at-risk` tag, the risk properties, the guarding assertion, and the impact
report into DataHub. Run it twice: nothing duplicates.

Every finding also carries its counterfactual: the changes that would make it
stop existing, each one sufficient on its own. Cut the derivation, drop the
feature, or withdraw the declaration the finding rests on. Where a feature
reaches a label by more than one path it says so, and names every edge, because
cutting one of two fixes nothing. These are not advice: the benchmark applies
them to the graph and checks the finding clears.

One caveat if you paste these as a block rather than typing them: DataHub indexes
a freshness change asynchronously, about three seconds locally, so a scan run in
that window still reports the state from before. The scenario command says so when
it returns. Give it a moment, or rerun the scan.

Then recover and rescan:

```bash
modelguard scan --table loans_raw --dry-run   # detect and explain, write nothing
modelguard-scenario --revert                  # the table refreshes
modelguard scan --table loans_raw             # no finding, no writes
```

## Use it on your own project

The Quickstart above builds a demo graph where every link a detector needs is
already in place. Your DataHub is not that graph, so start by asking what
ModelGuard can already see:

```bash
modelguard inventory        # every model, and what can and cannot be checked
```

Expect most models to come back "not checked", and that is the honest answer
rather than a failure. DataHub's mlflow source records a model and its training
run; its dbt, Spark and warehouse sources record excellent column-level lineage
between tables. **Nothing joins the two**, so out of the box a model is not
connected to a single column, and a detector that walks from a feature to its
source column has nowhere to start. Verified on a real stack, not assumed: see
[examples/real-project/](https://github.com/Ahmedxsaad/DataHub/tree/main/examples/real-project).

`modelguard link` is that join. Before typing it out, ask ModelGuard to work it
out for you:

```bash
modelguard link --model churn_model --infer
```

It works the training table out from whatever the graph does hold, trying four
routes in descending order of confidence and telling you which one answered:
the inputs the training run recorded (`dataProcessInstanceInput`), a run
parameter naming a table (where DataHub's mlflow source puts MLflow params), a
dataset the catalog already declares upstream of the model, and failing all of
those, a shortlist of nearby tables for you to pick from. Then it reads that
table's schema and proposes the exact command a person would have typed, with
one line per decision saying which aspect it came from and whether it is a fact
or a guess:

```
Inferred from the graph:
  feature table: the only input recorded on churn_model's training run(s), from dataProcessInstanceInput
  label column: churned matches a known label name (MODELGUARD_LABEL_COLUMN_NAMES). This one is a guess: check it
  excluded columns: customer_id, from the schema's own key declarations (primaryKeys, isPartOfKey, isPartitioningKey) and the label itself

Proposed:
modelguard link \
  --model churn_model \
  --features analytics.customer_features \
  --label-column churned \
  --exclude customer_id

Declare this? [Y/n]
```

There is no LLM in that, and nothing is written until you answer. A column that
already carries the label term was *declared* rather than guessed, and the
proposal says so, including when the declaration is on a column the feature
table descends from, which is where a warehouse usually keeps its labels; where nothing in the graph names a label at all, it refuses to
invent one and asks for `--label-column`, because a wrong label makes every
leakage verdict wrong in both directions. Exclusions come only from the
warehouse's own key declarations, never from column names that look like
identifiers: `customer_id` is usually a join key and `score_id` is usually a
feature, and no rule over names tells them apart.

A plain mlflow ingest often carries none of the first three: it produces a model
whose training run records no inputs at all (verified live, D-074). `--infer`
then says so, names what would fix it, and lists the nearest tables by name
instead of refusing:

```
Inferred from the graph:
  feature table: NOT FOUND. churn_model's training run records no inputs and no dataset
    parameter, which is the usual state after an mlflow ingest, and nothing in the catalog
    declares a dataset upstream of it. Pass --features <table>, or log the training table as
    an MLflow run parameter (modelguard_features=...) and re-ingest so this can be read
    rather than guessed

Nearest tables, for you to choose:
  1. analytics.churn_features
  2. analytics.churn_labels
```

One line in the training script makes the next ingest self-describing, and it is
the same line that keeps the link alive (see below):

```python
mlflow.log_param("modelguard_features", "analytics.customer_features")
```

Prefer to type it, or the graph is too quiet to infer from? It is one call from
the script that trains the model:

```bash
modelguard link \
  --model churn_model \
  --features analytics.customer_features \
  --label-table analytics.customer_labels \
  --label-column churned \
  --exclude customer_id
```

That declares the model's features (one per column, each carrying the exact
source column it came from), marks the label column with the glossary term the
leakage detector reads, and captures the input schema as the baseline drift is
measured against. Then `modelguard scan --model churn_model` works the way the
demo does, on your data.

Run `link` again after each ingestion of the model. DataHub's mlflow source
upserts the whole `mlModelProperties` aspect and drops the features (reported as
[feedback #14](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/most-valuable-feedback.md)); the arguments are recorded on the
model in an aspect ingestion does not touch, so the replay needs no arguments at
all, and one command covers every model at once:

```bash
datahub ingest -c mlflow.yml     # your existing pipeline, unchanged
modelguard link --all            # put back what it dropped, for every linked model
modelguard scan --all-models     # audit the whole catalog
```

A model nobody has linked is skipped rather than guessed at, so `--all` is safe
to run on a schedule.

### What each check needs, and what it says when it lacks it

A scan never reports something healthy that it could not measure. It names the
check, the missing metadata, and how to supply it:

| Check | Needs | Who normally writes it |
|---|---|---|
| Freshness + blast radius | the `operation` aspect on the table | dbt, Airflow, Spark, or the SDK's `report_operation` |
| Target leakage | features with source columns, plus a column carrying the label term | `modelguard link` |
| Schema drift | a training-time schema snapshot on the training run | `modelguard link` |
| Sensitive source | features with source columns, plus `MODELGUARD_SENSITIVE_TAG_URNS` or `..._TERM_URNS` | your classifier, or a human in the UI |
| Deprecated input | the model's training run, and the `deprecation` aspect | the table's own owners |

Already have a glossary term for labels? Point `MODELGUARD_LABEL_TERM_URN` at it
in `.env` and the detector honors yours instead of creating one.

### The two checks that read the governance graph

The first three checks ask whether a model's data is *correct*. The last two ask
something the organization has already answered elsewhere in DataHub, and that
nothing today joins back to the model.

**Sensitive source.** Somebody classified a column as PII, PHI, or restricted.
Three joins downstream, a feature derives from it, and a live model trains on
that feature. Nothing is broken and the model works; what is wrong is what it was
allowed to see, and the derivation is far enough upstream that neither team would
notice. It is the leakage walk with a different mark, so it produces the same
auditable proof:

> `credit_risk_v3` feature `applicant_income` derives, through
> `applicant_income <- income`, from `loans_raw.income`, classified
> `modelguard.sensitive`.

Point it at your own taxonomy, comma-separated, either surface or both:

```bash
MODELGUARD_SENSITIVE_TAG_URNS=urn:li:tag:PII,urn:li:tag:Confidential
MODELGUARD_SENSITIVE_TERM_URNS=urn:li:glossaryTerm:Classification.Restricted
```

There is deliberately no default. A guessed classification URN either matches
nothing or matches a term that means something else in your catalog, and a false
incident about a compliance exposure is the worst kind to be wrong about. Leave
both empty and every scan reports the check as **not evaluated**, never as clean.

**Deprecated input.** A table's owners marked it deprecated, with a note and
sometimes a decommission date. They have no way to know a model depends on it,
and the model's team has no way to know the flag was set. This needs no
configuration: `deprecation` is DataHub's own aspect with one meaning everywhere.
It is never more than `medium` severity, because it is a deadline rather than a
defect.

Both are reversible scenarios, so you can watch them fire and clear:

```bash
modelguard-scenario --scenario sensitive-source
modelguard scan --model credit_risk_v3
modelguard-scenario --scenario sensitive-source --revert
```

## Block a bad model before it merges

Everything above is after the fact: it audits a graph that already holds the mistake.
`modelguard gate` is the preventive half, for a pull request. It runs the same
detectors, judges them against a policy, and answers in an exit code, so a leaking or
untrustworthy model fails the build rather than shipping.

```bash
modelguard gate --model credit_risk_v3 --block-at-or-above high   # exit 1 if it leaks
modelguard gate --model credit_risk_v3 --min-trust 80             # exit 1 if trust < 80
```

Prefer `--block-at-or-above`. A severity is a thing a detector decided; the trust
score is a weighted sum whose weights are a stated preference ordering, not a
calibrated model, so a team that sets `--min-trust 80` has calibrated nothing
against a scale with no units. `--min-trust` used on its own prints a one-line
caution saying exactly that. It still works, and it is the blunter of the two.

Three exit codes, and the third is the point: `0` shippable, `1` the policy was
violated, `2` the gate could not reach a verdict (DataHub unreachable, bad config).
A gate that reported "I could not connect" as a policy violation would teach a team to
wave through every red build, so a setup failure never masquerades as a finding. It
writes nothing by default, because a gate runs on every push and one incident per run
would fill the graph with findings about branches that never merged.

In a workflow, via the bundled action:

```yaml
- uses: Ahmedxsaad/DataHub@main
  with:
    model: credit_risk_v3
    block-at-or-above: high
    gms-url: ${{ secrets.DATAHUB_GMS_URL }}
    gms-token: ${{ secrets.DATAHUB_GMS_TOKEN }}
```

The verdict lands on the run's own summary page, not just in the log: findings,
severities, trust scores, and the checks that could not run, as a table the
reviewer sees without opening anything. That needs no input and no token, because
GitHub already gives every step a `GITHUB_STEP_SUMMARY` file to append markdown
to; outside Actions the variable is unset and nothing is written.

Routing findings somewhere ModelGuard does not know about? Both `scan` and `gate`
take `--format json` and put the whole report (evidence, models at risk, trust
deductions, each finding's counterfactual, the gate's violations) on stdout as
one parseable document, with
progress lines moved to stderr so the stream stays clean.

## Call it from your training script

The command line is the main interface, but there is one place ModelGuard belongs
inside your code: the script that trains the model. That is the only moment when
the feature table, the label column, and the training-time schema are all known,
and shelling out to a CLI from inside it is a worse interface than a function
call.

```python
import mlflow

from modelguard import link_model, scan_model

FEATURE_TABLE = "analytics.customer_features"

# Logged as a run parameter as well as declared: the parameter survives into
# DataHub through the ordinary mlflow ingest, which is what lets `link --infer`
# read the table next time instead of guessing at it.
mlflow.log_param("modelguard_features", FEATURE_TABLE)

link_model(
    model="churn_model",
    features=FEATURE_TABLE,
    label_column="churned",
    exclude=["customer_id"],
)

report = scan_model(model="churn_model", dry_run=True)
if not report.clean:
    raise SystemExit(f"{len(report.writes)} finding(s) before this model ships")
```

This is the durable place for the link, and the reason is worth stating plainly:
an ingest drops it (see above), so a link declared once decays on a schedule you
do not control. Declared here, it is re-declared by the same run that produces
the model, so the next training run repairs it whether or not anybody noticed.
For the models that are not retrained nightly, schedule `modelguard link --all`
after your ingest: the [`modelguard-watch` chart](charts/modelguard-watch)
ships that as a CronJob (`link.enabled=true`). And when neither has happened,
a scan says so specifically ("carries a recorded modelguard link but declares
no features") rather than reporting a model it cannot see as healthy.

Two functions and their result types, and deliberately no more: those names are
the supported surface a script may pin to. They are thin wrappers over exactly
the functions `modelguard link` and `modelguard scan` call, so a finding found
here is found identically at the command line. Both read `.env` the same way the
CLI does; pass `conn=` to reuse one connection across many models. Everything
else in the package is importable and documented, but its shape is free to
change, so import a submodule knowingly when you need to go deeper.

## Ask it, don't type it

```bash
pip install -e ".[mcp]"
modelguard-mcp   # serves check_leakage, check_freshness, check_gate over stdio
```

Point an MCP client (Claude Desktop or similar) at the installed `modelguard-mcp`
command and ask "is credit_risk_v3 leaking?" in plain language. All three tools are
read-only, enforced at registration (`readOnlyHint: true`) and by calling every scan
in dry-run with no way to turn that off: the model on the other end of an MCP client
is not ModelGuard's own narrator, it is outside this project's control entirely, so
it gets to ask what is wrong and nothing more.

It is meant to run *beside* DataHub's own
[`mcp-server-datahub`](https://github.com/acryldata/mcp-server-datahub), not instead
of it. That server answers what the catalog contains: search, lineage, schemas,
ownership, the open-ended questions where a model's job is to explore. ModelGuard
answers the three that have to be reproducible, with the column chain as evidence
and no LLM anywhere in the decision. Configuring both, and the argument for keeping
detection deterministic rather than asking a capable model to eyeball a lineage
graph, is in
[skill/datahub-ml-guard/references/mcp-composition.md](https://github.com/Ahmedxsaad/DataHub/blob/main/skill/datahub-ml-guard/references/mcp-composition.md).

## Watch it from the corner of your eye

```bash
pip install "modelguard-datahub[pet]"     # macOS and Windows; Linux: the .deb or
                                          # .AppImage on the GitHub release
modelguard watch --table loans_raw --pet  # ModelGuard's own findings
modelguard companion                      # everything wrong with the assets you own
```

Argos is a 32x32 pixel watchdog that sits on your desktop and shows what the graph
is doing. It patrols while a poll finds nothing, sniffs while a lineage walk is in
flight, barks with a red collar the moment a finding lands, and turns into a
translucent ghost when it cannot reach DataHub, because a cheerful pet on a
disconnected watch is the lie that gets ambient status displays switched off. Nothing
it does is on a timer: every state is an event a detector actually produced.

Double-click a finding and it walks the blast radius across the screen, one hop per
graph hop, with the column name floating over each jump. That is the column-level
traversal the benchmark above measures, rendered as motion instead of a paragraph.

`modelguard companion` is the half that is not about ModelGuard at all: it runs no
detector, and sweeps the assets one owner owns for open incidents, failing assertion
runs and deprecations. DataHub has no desktop presence today, and that is the gap it
fills. Design and protocol:
[docs/plan/08-watchdog-mascot.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/08-watchdog-mascot.md).

With no window binary installed, both commands report one line per change in the
terminal instead, which is also what runs over SSH.

## Run it without a Python install

```bash
datahub docker quickstart              # once: builds DataHub's own stack
docker compose run --rm modelguard-seed
docker compose run --rm modelguard scan --table loans_raw
docker compose run --rm modelguard gate --model credit_risk_v3 --block-at-or-above high
docker compose up modelguard-mcp       # long-running, stdio
```

[`docker-compose.yml`](https://github.com/Ahmedxsaad/DataHub/blob/main/docker-compose.yml) adds ModelGuard to the Docker network
`datahub docker quickstart` already creates, rather than reimplementing DataHub's own
multi-container stack (GMS, MySQL, Kafka, OpenSearch, frontend) inside this repo:
composing what is already shipped, not rebuilding it. `docker compose up` with no
service named starts nothing: every service needs a `--table`/`--model` naming an
actual target, so `run --rm <service> ...` or `up <service>` (named explicitly) are
the only ways anything starts.

The project is named `modelguard` explicitly in that file, not left to compose's
directory-name default: DataHub's own Quickstart compose defaults to the same
project name (`datahub`), and sharing it would make an ordinary `docker compose
down --remove-orphans` here treat the entire Quickstart as orphaned containers of
this project and stop it. [`Dockerfile`](https://github.com/Ahmedxsaad/DataHub/blob/main/Dockerfile) builds a non-root image (pinned
to the exact patch version this project develops against, `python:3.11.14-slim`)
with all four console scripts installed; `docker build --build-arg
MODELGUARD_EXTRAS=agent,mcp,anthropic` (or `openai`/`google`) bakes an LLM provider
in instead of installing it at runtime.

## Run watch on a cluster

`scan` and `gate` are one-shot; the MCP server speaks stdio to whatever launched
it. `watch` is the only entry point meant to run forever, so it is the only one
with a Helm chart:

```bash
helm install my-watch charts/modelguard-watch \
  --set image.repository=ghcr.io/ahmedxsaad/datahub/modelguard \
  --set datahub.gmsUrl=http://datahub-gms.datahub.svc.cluster.local:8080 \
  --set watch.table=loans_raw
```

`.github/workflows/publish-image.yml` builds and pushes that image to GHCR on
every version tag, so the chart has somewhere real to pull from rather than a
placeholder. See [`charts/modelguard-watch/README.md`](https://github.com/Ahmedxsaad/DataHub/blob/main/charts/modelguard-watch/README.md)
for secret handling (`existingSecret` is the path meant for real use) and what
the chart deliberately leaves out (autoscaling, probes that would check nothing
real, an Ingress nothing needs).

## More sample output

Sample outputs, generated by a real run, are in [examples/](https://github.com/Ahmedxsaad/DataHub/tree/main/examples).
To verify the whole loop against a live DataHub: `pytest -m integration`.

## Is it any good?

Measured, not asserted. [ModelGuard-Bench](https://github.com/Ahmedxsaad/DataHub/blob/main/benchmarks/RESULTS.md) scores the detectors
against a live DataHub (never against fixtures, which would only measure the fixtures):

```bash
modelguard-seed
python -m benchmarks.run_bench          # writes benchmarks/RESULTS.md
```

The freshness sweep walks the lag across the SLA boundary rather than only planting the
obvious 30-hour failure, because that is where a detector actually goes wrong: changing
one comparison from `>` to `>=` is caught by the trial sitting exactly on the SLA, and
scores a clean 1.00 under the demo scenario alone.

The per-feature comparison against table-level lineage is
[above](#the-one-thing-nothing-else-does-measured); the full numbers, and what is
still not measured, are in [RESULTS.md](https://github.com/Ahmedxsaad/DataHub/blob/main/benchmarks/RESULTS.md).

## Security and privacy

**No row-level data ever leaves DataHub, and none of it reaches the LLM.**
ModelGuard reads the metadata graph and nothing else: aspects DataHub already
holds, among them the `operation` aspect for freshness, `schemaMetadata` for
drift, glossary terms for labels, and lineage for the paths between them. It
never connects to the warehouse and never issues a query against a table, so
there is no path by which a row, a PII column value, or a PHI record can reach a
model provider. What an LLM is shown is the fact block
printed in every incident: URNs, column names, hop counts, and the numbers a
detector measured. You can read exactly what was sent, because it is what was
written back.

The rest of the security model, in the order it matters:

- **The LLM decides nothing.** Detection is deterministic Python. The model
  explains, ranks, and drafts prose; nothing it emits reaches a dedup key, a
  severity, a URN, or an enum, and it never composes a mutation. A scan runs
  end to end with no LLM configured at all, and detection is byte-identical
  either way.
- **Prompt injection is contained, not assumed away** (OWASP LLM01). Catalog
  text is metadata anybody can edit, so it is wrapped as delimited untrusted
  data and delimiter lookalikes are stripped before wrapping. Even a successful
  injection cannot invent a finding: it is downstream of the detectors.
- **Writes are fixed and parameterized.** `writeback/` exposes a closed set of
  mutations with validated arguments (URNs must resolve, incident types are
  checked against `IncidentTypeClass`, scores are clamped). There is no code
  path that sends a GraphQL string the caller supplied.
- **Writes are idempotent and reversible in place.** Every write is keyed on
  `(resource_urn, incident_type, title)` with read-before-write, stamped with a
  `modelguard.run_id` for provenance. The benchmark reads the graph back after a
  rerun and measures the duplicates created: 0.
- **Writes are gated on a human.** `scan --review` pauses after detection and
  writes only what you approve. `gate` reads and does not write unless you pass
  `--write`, because it runs on every push and one incident per run would fill
  the graph with findings about code that never merged. The MCP tools cannot
  write at all, on any flag: the model driving an MCP client is outside this
  project's control, so it gets to ask what is wrong, never to fix it. `watch`
  auto-approves because it is unattended by definition.
- **The token stays a secret.** It enters the process in one module
  (`modelguard/env.py`), lives only in `.env` (git-ignored), and is never
  logged, echoed, or put in an exception message. Errors name the *variable*,
  never its value. Text that came back from somebody else's SDK is scrubbed of
  it before it reaches a console or a CI log, and Typer's locals-in-traceback
  rendering is pinned off because those frames hold the token.
- **Least privilege, honestly.** DataHub OSS personal access tokens are not
  scoped per operation, so ModelGuard cannot claim a narrowed token. What it can
  say is what it touches: incidents, tags, glossary terms, structured
  properties, documents, assertion aspects, and the dataFlow, dataJob and
  dataProcessInstance entities it records its own runs as. Give it a token you
  are willing to rotate, and rotate it.

Detection is measured, not asserted, and each detector implements a published
result rather than a heuristic somebody liked:

| What | Source |
|---|---|
| Target leakage as illegitimate information about the target, found by inspecting how a feature was constructed | Kaufman, Rosset and Perlich, *Leakage in Data Mining: Formulation, Detection, and Avoidance* (KDD 2011 / ACM TKDD 2012) |
| Undeclared consumers: a table acquiring model consumers its owners never agreed to serve | Sculley et al., *Hidden Technical Debt in Machine Learning Systems* (NeurIPS 2015) |
| A schema fixed at training time, against which serving data is continuously validated | Breck, Polyzotis, Roy, Whang and Zinkevich, *Data Validation for Machine Learning* (MLSys 2019) |
| The prompt-injection and sensitive-disclosure threat model | OWASP Top 10 for LLM Applications (2025), LLM01 and LLM06 |

Full reading list with what each one changed here:
[docs/plan/resources.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/plan/resources.md).

## Show a governance function where this fits

```bash
modelguard crosswalk        # markdown on stdout, connects to nothing
```

One row per detector, mapping it to the NIST AI RMF subcategory its output is
evidence for, with the subcategory text quoted from the AI RMF 1.0 Playbook
rather than paraphrased. The table is generated from the detector registry, so a
check cannot be added to ModelGuard without appearing in it.

It is a mapping and not a conformity claim, and it says so in its own first
paragraph. Which subcategory an artifact is evidence *for* is a fact about the
artifact; whether the subcategory is *satisfied* is a judgement about your whole
process, and nothing that reads a metadata graph is in a position to make it.

## OSS contributions

Built alongside ModelGuard and offered back to the DataHub ecosystem:

| Contribution | What it is |
|---|---|
| [skill/datahub-ml-guard/](https://github.com/Ahmedxsaad/DataHub/tree/main/skill/datahub-ml-guard) | The `datahub-ml-guard` skill: traces model features back to source columns to catch leakage, drift, and blast radius, and guides the write-back. Unlike the several ML-reliability skills already submitted to the registry, it is a thin wrapper around a real, tested, deterministic detection engine (this repo), not an LLM asked to eyeball a lineage graph. Destined for [datahub-project/datahub-skills](https://github.com/datahub-project/datahub-skills). |
| [mcp_ext/raise_incident_tool.py](https://github.com/Ahmedxsaad/DataHub/blob/main/mcp_ext/raise_incident_tool.py) | A thin `raise_incident` mutation tool for [acryldata/mcp-server-datahub](https://github.com/acryldata/mcp-server-datahub), which today has no incident-write tool. Ships with [an RFC](https://github.com/Ahmedxsaad/DataHub/blob/main/mcp_ext/RFC-ml-incidents.md) for first-class ML incidents. |
| [docs/most-valuable-feedback.md](https://github.com/Ahmedxsaad/DataHub/blob/main/docs/most-valuable-feedback.md) | Fourteen concrete, reproducible bugs and doc gaps found while building, each with a repro and a workaround. |

## Contributing

Team conventions (commit format, code rules, formatting rules) live in
[CLAUDE.md](https://github.com/Ahmedxsaad/DataHub/blob/main/CLAUDE.md). Each directory has its own CLAUDE.md with local rules.
License: [Apache 2.0](https://github.com/Ahmedxsaad/DataHub/blob/main/LICENSE).
