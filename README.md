# Janus

<p align="center">
  <img src="assets/argos.gif" width="200" height="200"
       alt="Argos, the Janus watchdog: patrolling, walking his beat, sniffing out a lineage traversal, barking with a red collar at a finding, writing it back, wagging when it clears, then asleep." />
</p>

The missing CI for your ML supply chain. Janus is an agent that sits on
the warehouse-to-ML boundary that DataHub uniquely spans: it reads end-to-end
column-level lineage and ML metadata to catch silent data-to-model failures
(target leakage, upstream blast radius, training/serving schema drift), and
writes incidents, model trust scores, impact reports, and guarding assertions
back into the DataHub graph. Every scan is itself catalogued there, as a
dataProcessInstance naming what it read and what it wrote, so the agent is
subject to the same lineage it guards.

<p align="center">
  <b><a href="https://docs.ahmedxsaad.me/">Documentation</a></b> &nbsp;·&nbsp;
  <b><a href="https://janus.ahmedxsaad.me">Live demo</a></b> &nbsp;·&nbsp;
  <b><a href="docs/">Engineering docs</a></b> &nbsp;·&nbsp;
  <b><a href="benchmarks/RESULTS.md">Measured results</a></b>
</p>

Built for "Build with DataHub: The Agent Hackathon" (Devpost, deadline
Aug 10, 2026). Category: Production ML Agents.

Status: the core loop works end to end. A stale upstream table is detected, its
blast radius traversed into the live model that consumes it, and an incident,
a tag, risk properties, a guarding assertion with its measured result, and a
Model Impact Report are all written back to DataHub. Reruns never duplicate.
Target leakage, training/serving schema drift, and the model trust score have
landed, as has the human-approval agent (`scan --review`) and a polling `watch`.

## Contents

**Start here**
[The one thing nothing else does, measured](#the-one-thing-nothing-else-does-measured)
· [Try it](#try-it)
· [Documentation](#documentation)

**Use it**
[On your own project](#use-it-on-your-own-project)
· [Block a bad model before it merges](#block-a-bad-model-before-it-merges)
· [Call it from your training script](#call-it-from-your-training-script)
· [Ask it, don't type it](#ask-it-dont-type-it)
· [Watch it from the corner of your eye](#watch-it-from-the-corner-of-your-eye)

**Run it**
[Without a Python install](#run-it-without-a-python-install)
· [On a cluster](#run-watch-on-a-cluster)

**Trust it**
[Is it any good?](#is-it-any-good)
· [Security and privacy](#security-and-privacy)
· [Governance and paperwork](#show-a-governance-function-where-this-fits)
· [OSS contributions](#oss-contributions)

## Live demo

**<https://janus.ahmedxsaad.me>**

A real DataHub instance with the ML supply chain seeded, a failure planted, and
`janus watch` running against it continuously, so what you are looking at
is a graph Janus is actively maintaining rather than a screenshot. Sign-in
credentials are in the Devpost submission's testing instructions (they are
deliberately not in this repository, since anything committed here stays in the
git history forever).

Once signed in, search `credit_risk_v3` for the model side (its
`model-at-risk` tag, `janus.trust_score` and `trust_band` properties, and
the linked Model Impact Report), or `loans_raw` for the data side (the open
incident and the guarding freshness assertion). Nothing needs to be installed
to see any of it.

Prefer to run it yourself? [Try it](#try-it) below is the same loop against a
local Quickstart, and it is the path the rest of this README documents.

## The one thing nothing else does, measured

DataHub holds two graphs that no other catalog holds together: column-level
lineage across the warehouse, and ML metadata for the models. Nothing joins
them, so a model is not connected to a single column and a data failure cannot
be traced to the model it breaks. **Janus writes that join** (`janus
link`) and then reads across it, which is what makes every detector below
possible.

That is a claim, so here it is as a number. The same graph, the same ground
truth, three ways of reading it, scored per **feature**: every approach can tell
that a leaking model leaks, and the question that separates them is *which* of
its features leaks, which is what somebody has to go and fix.

| Approach | Precision | Recall | Still alerting after the fix |
|---|---|---|---|
| Janus (column-level lineage) | 1.00 | 1.00 | 0 features |
| Table-level lineage | 0.25 | 1.00 | 2 features |
| Table quality checks, no lineage | - | 0.00 | 0 features |

Note the middle row's **perfect recall**: table-level lineage does catch the leak. It
just cannot say which of the two features carries it, because both descend from the same
labelled table. And having never seen the column edge, it cannot see the column edge
being removed either, so it keeps alerting on a graph somebody has already fixed. That
last column is what gets a reliability tool switched off.

These are implementations of an *approach*, handed Janus's own label index so
nothing is won by starting better informed; no Great Expectations or Evidently process
was run. [RESULTS.md](benchmarks/RESULTS.md) says so, and states what is still not
measured, alongside a scale table for a whole-catalog sweep.

## Documentation

Three places, and each one owns what it holds. This README tells the whole story
once; the other two are where you go when you need more of one part of it.

### The reference manual: <https://docs.ahmedxsaad.me/>

**Every command, every flag, every configuration key**, plus install, the Python
API, the MCP server and Argos, with the dog walking the reader down the page.
This README never lists flags; it links here.

It is `site/`, self-contained (no fetch, no build step), so a clone reads the
identical page by opening the file directly:

```bash
xdg-open site/index.html      # or just double-click it
```

### The engineering explanation: [`docs/`](docs/)

How it is built and why. [`docs/README.md`](docs/README.md) is the index.

| Page | What it answers |
|---|---|
| [01-overview.md](docs/01-overview.md) | The problem, why DataHub is the only place to solve it, what Janus does about it |
| [02-architecture.md](docs/02-architecture.md) | The layers and their boundaries, execution modes, idempotency, discovery, errors |
| [03-components.md](docs/03-components.md) | **The complete inventory**: every module, command, artifact and deliverable |
| [04-detectors.md](docs/04-detectors.md) | The seven checks: what each needs, and what it says when it cannot run |
| [05-the-link.md](docs/05-the-link.md) | The join no ingestion source writes, four ways to declare it, and how it survives an ingest |
| [06-writeback.md](docs/06-writeback.md) | Everything Janus writes into DataHub, and the rules every write obeys |
| [07-reports.md](docs/07-reports.md) | The sweeps, the governance documents, the output formats, the gate's exit codes |
| [08-evaluation.md](docs/08-evaluation.md) | How the detectors are measured, and what the benchmark deliberately does not |
| [09-testing.md](docs/09-testing.md) | 1,053 tests, mutation testing, and the eight CI jobs |
| [10-security.md](docs/10-security.md) | The threat model: LLM containment, prompt injection, the write surface, secrets |
| [11-argos.md](docs/11-argos.md) | The desktop watchdog: twelve states, the event protocol, the generated art |
| [12-operations.md](docs/12-operations.md) | The live demo, Docker, Kubernetes, packaging, releases |
| [13-design-decisions.md](docs/13-design-decisions.md) | The choices that shaped it, each with the alternative and the reason |
| [14-oss-contributions.md](docs/14-oss-contributions.md) | The skill, the MCP tool and RFC, the feedback filed upstream |
| [15-references.md](docs/15-references.md) | The literature, each with what it changed in this codebase |
| [16-most-valuable-feedback.md](docs/16-most-valuable-feedback.md) | Sixteen reproducible DataHub bugs found while building |

Start with [03-components.md](docs/03-components.md) to see everything that was
built in one place.

### The numbers: [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md)

Generated by `python -m benchmarks.run_bench` against a live DataHub, never
hand-edited.

## Repository layout

```
janus/         Python package: seed/, detect/, writeback/, agent/, adapters/, argos/
argos/         Argos, the desktop window: a Tauri binary and its text sprite art
skill/         OSS contribution: the datahub-ml-guard skill
mcp_ext/       OSS contribution: MCP incident mutation tool and its RFC
examples/      Sample generated artifacts, and real-project/, a live validation stack
benchmarks/    Janus-Bench: injection, metrics, measured RESULTS.md
tests/         pytest unit and integration tests
docs/          The engineering explanation, indexed in docs/README.md
charts/        Helm chart for `janus watch`, the one long-running entry point
deploy/        Cloud-init and systemd for the judge-facing demo VM
assets/        The animation at the top of this file, generated from the sprite art
site/          The documentation page published at docs.ahmedxsaad.me
```

## Prerequisites

- Linux, Python 3.11-3.13 (development targets 3.11 exactly; 3.12 is verified to
  behave identically), Docker (about 2 CPUs / 8 GB free for the Quickstart)
- No credentials are required for a local Quickstart: it ships with metadata
  service authentication disabled. See .env.example for when you need a token.
- No LLM key is required either, and no particular vendor. Janus uses a
  model only to word the incident description and the report's assessment;
  without one, deterministic template prose is written instead. Detection never
  depends on the LLM. Pick your provider in .env (anthropic, openai, or google)
  and install its binding: pip install -e ".[openai]"

## Try it

```bash
pip install -e ".[dev]"           # add ".[anthropic]", ".[openai]" or ".[google]"
cp .env.example .env              # DATAHUB_GMS_URL=http://localhost:8080
datahub docker quickstart         # UI at http://localhost:9002 (datahub/datahub)

janus-seed                   # build the ML supply chain the datapacks lack
janus-scenario --lag-hours 30   # a source table silently stops refreshing
janus scan --table loans_raw    # detect, explain, write back
```

Not developing on Janus itself, just want the CLI against your own DataHub?
Until the first PyPI release is cut it is `pip install -e .` from a clone; from
the release on, `pip install janus-datahub` (add `[agent]` for
`scan --review`, `[mcp]` for `janus-mcp`). The distribution is named
`-datahub`, since the exact name `janus` was already taken on PyPI by an
unrelated package; the commands you run are still `janus`,
`janus gate`, `janus-mcp`.

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
janus scan --table loans_raw --dry-run   # detect and explain, write nothing
janus-scenario --revert                  # the table refreshes
janus scan --table loans_raw             # no finding, no writes
```

## Use it on your own project

The Quickstart above builds a demo graph where every link a detector needs is
already in place. Your DataHub is not that graph, so start by asking what
Janus can already see:

```bash
janus inventory        # every model, and what can and cannot be checked
janus coverage         # the same sweep as one catalog figure, with a trend
janus finops           # tables that exist only to feed models nothing uses
```

`inventory` answers per model; `coverage` folds the same sweep into the number a
platform lead reports upward ("34% of models have a checkable leakage path"),
names the single next declaration that would raise it most, and with `--write`
records the point so the next sweep has a direction to compare against. It
measures how much has been declared, not how healthy the models are: a catalog at
8% is one where Janus mostly cannot tell you either way.

`finops` is the one command here whose reader is a budget holder rather than an
engineer, and the only one that suggests deleting something: it lists the tables
whose every downstream model has no deployment in service and has gone untouched
past `JANUS_UNUSED_MODEL_DAYS`. One live consumer and a table is not listed,
because that is not a saving. A model whose catalog entry carries no date at all
is reported separately as undated and never as unused, since in a report like
this an absence is not evidence. It writes nothing and raises no incident:
nothing here is broken.

Expect most models to come back "not checked", and that is the honest answer
rather than a failure. DataHub's mlflow source records a model and its training
run; its dbt, Spark and warehouse sources record excellent column-level lineage
between tables. **Nothing joins the two**, so out of the box a model is not
connected to a single column, and a detector that walks from a feature to its
source column has nowhere to start. Verified on a real stack, not assumed: see
[examples/real-project/](examples/real-project).

`janus link` is that join. Before typing it out, ask Janus to work it
out for you:

```bash
janus link --model churn_model --infer
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
  label column: churned matches a known label name (JANUS_LABEL_COLUMN_NAMES). This one is a guess: check it
  excluded columns: customer_id, from the schema's own key declarations (primaryKeys, isPartOfKey, isPartitioningKey) and the label itself

Proposed:
janus link \
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
whose training run records no inputs at all (verified live). `--infer`
then says so, names what would fix it, and lists the nearest tables by name
instead of refusing:

```
Inferred from the graph:
  feature table: NOT FOUND. churn_model's training run records no inputs and no dataset
    parameter, which is the usual state after an mlflow ingest, and nothing in the catalog
    declares a dataset upstream of it. Pass --features <table>, or log the training table as
    an MLflow run parameter (janus_features=...) and re-ingest so this can be read
    rather than guessed

Nearest tables, for you to choose:
  1. analytics.churn_features
  2. analytics.churn_labels
```

One line in the training script makes the next ingest self-describing, and it is
the same line that keeps the link alive (see below):

```python
mlflow.log_param("janus_features", "analytics.customer_features")
```

### If your stack already declares the mapping, do not type it twice

A Feast repo and a dbt semantic model already say which column each feature is
read from, in a file your training pipeline reads and your team keeps correct.
`link` imports it instead of asking:

```bash
janus link --model churn_model --from feast --repo ./feature_repo
janus link --model churn_model --from dbt   --repo ./churn_analytics
```

The readers are offline and read-only: they parse the declaration on disk and
never connect to Feast, to dbt, or to a warehouse. Feast needs the package
(`pip install "janus-datahub[feast]"`); dbt needs nothing at all, because a
manifest is JSON, so this works against a `manifest.json` somebody sent you.

The output is the same proposal `--infer` prints, with the declaration each line
came from, and it writes nothing until you answer:

```
Read from the feast declaration:
  read 'churn_model_v1' from the Feast repo at ./feature_repo
  feature table: warehouse.analytics.customer_features, the batch source of 'customer_features'
  features: 3 declared, of which 1 name a warehouse column different from the feature (from the source's field_mapping)
  label: churned of warehouse.analytics.customer_labels, from label view 'churn_label'
  not features: customer_id, event_timestamp (entity join keys and event timestamps), so they are excluded from the link

Features 'churn_model_v1' declares:
  tenure <- tenure_months  (feature view 'customer_features')
  monthly_charges <- monthly_charges  (feature view 'customer_features')
  support_calls <- support_calls  (feature view 'customer_features')
```

That first line is the case a name match gets wrong: the feature is `tenure` and
the column is `tenure_months`, and only the declaration knows. Where a
declaration is silent it says so rather than guessing (a dbt semantic model
names no label, so `--label-column` stays yours to give), and where it names a
column the table does not have, the import stops instead of linking the rest:
a half-declared model is one whose unchecked columns nobody would ever hear
about. `--select` picks between several declarations in one repo.

Prefer to type it, or the graph is too quiet to infer from? It is one call from
the script that trains the model:

```bash
janus link \
  --model churn_model \
  --features analytics.customer_features \
  --label-table analytics.customer_labels \
  --label-column churned \
  --exclude customer_id
```

That declares the model's features (one per column, each carrying the exact
source column it came from), marks the label column with the glossary term the
leakage detector reads, and captures the input schema as the baseline drift is
measured against. Then `janus scan --model churn_model` works the way the
demo does, on your data.

Run `link` again after each ingestion of the model. DataHub's mlflow source
upserts the whole `mlModelProperties` aspect and drops the features (reported as
[feedback #14](docs/16-most-valuable-feedback.md)); the arguments are recorded on the
model in an aspect ingestion does not touch, so the replay needs no arguments at
all, and one command covers every model at once:

```bash
datahub ingest -c mlflow.yml     # your existing pipeline, unchanged
janus link --all            # put back what it dropped, for every linked model
janus scan --all-models     # audit the whole catalog
```

A model nobody has linked is skipped rather than guessed at, so `--all` is safe
to run on a schedule.

### What each check needs, and what it says when it lacks it

A scan never reports something healthy that it could not measure. It names the
check, the missing metadata, and how to supply it:

| Check | Needs | Who normally writes it |
|---|---|---|
| Freshness + blast radius | the `operation` aspect on the table | dbt, Airflow, Spark, or the SDK's `report_operation` |
| Target leakage | features with source columns, plus a column carrying the label term | `janus link` |
| Schema drift | a training-time schema snapshot on the training run | `janus link` |
| Sensitive source | features with source columns, plus `JANUS_SENSITIVE_TAG_URNS` or `..._TERM_URNS` | your classifier, or a human in the UI |
| Deprecated input | the model's training run, and the `deprecation` aspect | the table's own owners |
| Proxy candidate | features with source columns, plus `JANUS_PROTECTED_ATTRIBUTE_TAG_URNS` or `..._TERM_URNS` | your classifier |

Already have a glossary term for labels? Point `JANUS_LABEL_TERM_URN` at it
in `.env` and the detector honors yours instead of creating one.

What each detector actually looks for, the paper behind it, and the counterfactual
it carries: [docs/04-detectors.md](docs/04-detectors.md). Every configuration key:
[docs.ahmedxsaad.me](https://docs.ahmedxsaad.me/).

### Before you link anything: the table-level answer

Until a model is linked, none of the column-level checks can run on it. Rather
than only listing what it could not do, a scan says what it *can* see about the
tables that model is recorded as training on: whether one is past its freshness
SLA, marked deprecated by its owners, or holds a column your organization
classified. It is a distinct finding type, it never outranks a column-level
finding, and it says out loud what it cannot see:

> Checked at table level only (churn_model declares no features): the table this
> model trains on is past its freshness SLA. Which of the model's features carry
> the stale values is not knowable without a column-level link. Asked which
> feature carries it, table-level reasoning scores a measured precision of 0.25
> (benchmarks/RESULTS.md, table-level baseline), which is why this finding names
> the table and not a feature. Run `janus link` to get the column-level
> answer instead.

That 0.25 is measured, not asserted: it is the table-level baseline scored in
[benchmarks/RESULTS.md](benchmarks/RESULTS.md),
and the benchmark checks the figure the tool quotes against the one it measures
on every run.

### The three checks that read the governance graph

The first three checks ask whether a model's data is *correct*. The last three ask
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
> `janus.sensitive`.

Point it at your own taxonomy, comma-separated, either surface or both:

```bash
JANUS_SENSITIVE_TAG_URNS=urn:li:tag:PII,urn:li:tag:Confidential
JANUS_SENSITIVE_TERM_URNS=urn:li:glossaryTerm:Classification.Restricted
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

**Proxy candidate.** Does a feature share an ancestor with a column classified as
a protected attribute, without either descending from the other? This looks for a
fork rather than a chain, which is why it is a separate detector from leakage
rather than a variant of it. Severity is capped at `medium`, it never escalates
for a live model, and it contributes nothing to the trust score: it raises a
question a human has to settle, and a positive-evidence rule cannot settle one.

```bash
JANUS_PROTECTED_ATTRIBUTE_TAG_URNS=urn:li:tag:ProtectedAttribute
JANUS_PROTECTED_ATTRIBUTE_TERM_URNS=urn:li:glossaryTerm:Protected.Ethnicity
```

Sensitive source and deprecated input are reversible scenarios, so you can watch
them fire and clear:

```bash
janus-scenario --scenario sensitive-source
janus scan --model credit_risk_v3
janus-scenario --scenario sensitive-source --revert
```

## Block a bad model before it merges

Everything above is after the fact: it audits a graph that already holds the mistake.
`janus gate` is the preventive half, for a pull request. It runs the same
detectors, judges them against a policy, and answers in an exit code, so a leaking or
untrustworthy model fails the build rather than shipping.

```bash
janus gate --model credit_risk_v3 --block-at-or-above high   # exit 1 if it leaks
janus gate --model credit_risk_v3 --min-trust 80             # exit 1 if trust < 80
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
- uses: Ahmedxsaad/janus@main
  with:
    model: credit_risk_v3
    block-at-or-above: high
    gms-url: ${{ secrets.DATAHUB_GMS_URL }}
    gms-token: ${{ secrets.DATAHUB_GMS_TOKEN }}
```

The action reports the same three states to the rest of the workflow, as
`outcome` (`clean`, `blocked`, `error`) alongside the boolean `blocked`. The
distinction is the same one the exit codes make and it matters more here: a step
gated on `blocked == 'true'` must not comment "this model is unsafe" on a pull
request because DataHub happened to be unreachable, so `blocked` stays false when
the gate could not tell, and `outcome` is how you catch that case.

The verdict lands on the run's own summary page, not just in the log: findings,
severities, trust scores, and the checks that could not run, as a table the
reviewer sees without opening anything. That needs no input and no token, because
GitHub already gives every step a `GITHUB_STEP_SUMMARY` file to append markdown
to; outside Actions the variable is unset and nothing is written.

Routing findings somewhere Janus does not know about? Both `scan` and `gate`
take `--format json` and put the whole report (evidence, models at risk, trust
deductions, each finding's counterfactual, the gate's violations) on stdout as
one parseable document, with
progress lines moved to stderr so the stream stays clean.

## Call it from your training script

The command line is the main interface, but there is one place Janus belongs
inside your code: the script that trains the model. That is the only moment when
the feature table, the label column, and the training-time schema are all known,
and shelling out to a CLI from inside it is a worse interface than a function
call.

```python
import mlflow

from janus import link_model, scan_model

FEATURE_TABLE = "analytics.customer_features"

# Logged as a run parameter as well as declared: the parameter survives into
# DataHub through the ordinary mlflow ingest, which is what lets `link --infer`
# read the table next time instead of guessing at it.
mlflow.log_param("janus_features", FEATURE_TABLE)

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
For the models that are not retrained nightly, schedule `janus link --all`
after your ingest: the [`janus-watch` chart](charts/janus-watch)
ships that as a CronJob (`link.enabled=true`). And when neither has happened,
a scan says so specifically ("carries a recorded janus link but declares
no features") rather than reporting a model it cannot see as healthy.

Two functions, their result types, and the two errors they raise
(`LinkError`, and `TableResolutionError` for a name matching no dataset or more
than one, which is what a relation named the way the warehouse names it usually
does): those names are the supported surface a script may pin to. They are thin wrappers over exactly
the functions `janus link` and `janus scan` call, so a finding found
here is found identically at the command line. Both read `.env` the same way the
CLI does; pass `conn=` to reuse one connection across many models. Everything
else in the package is importable and documented, but its shape is free to
change, so import a submodule knowingly when you need to go deeper.

## Ask it, don't type it

```bash
pip install -e ".[mcp]"
janus-mcp   # serves check_leakage, check_freshness, check_gate over stdio
```

Point an MCP client (Claude Desktop or similar) at the installed `janus-mcp`
command and ask "is credit_risk_v3 leaking?" in plain language. All three tools are
read-only, enforced at registration (`readOnlyHint: true`) and by calling every scan
in dry-run with no way to turn that off: the model on the other end of an MCP client
is not Janus's own narrator, it is outside this project's control entirely, so
it gets to ask what is wrong and nothing more.

It is meant to run *beside* DataHub's own
[`mcp-server-datahub`](https://github.com/acryldata/mcp-server-datahub), not instead
of it. That server answers what the catalog contains: search, lineage, schemas,
ownership, the open-ended questions where a model's job is to explore. Janus
answers the three that have to be reproducible, with the column chain as evidence
and no LLM anywhere in the decision. Configuring both, and the argument for keeping
detection deterministic rather than asking a capable model to eyeball a lineage
graph, is in
[skill/datahub-ml-guard/references/mcp-composition.md](skill/datahub-ml-guard/references/mcp-composition.md).

## Watch it from the corner of your eye

```bash
pip install "janus-datahub[pet]"     # macOS and Windows; Linux: the .deb or
                                          # .AppImage on the GitHub release
janus watch --table loans_raw --pet  # Janus's own findings
janus companion                      # everything wrong with the assets you own
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

`janus companion` is the half that is not about Janus at all: it runs no
detector, and sweeps the assets one owner owns for open incidents, failing assertion
runs and deprecations. DataHub has no desktop presence today, and that is the gap it
fills. Design, the twelve states and the event protocol: [docs/11-argos.md](docs/11-argos.md).

With no window binary installed, both commands report one line per change in the
terminal instead, which is also what runs over SSH.

## Run it without a Python install

```bash
datahub docker quickstart              # once: builds DataHub's own stack
docker compose run --rm janus-seed
docker compose run --rm janus scan --table loans_raw
docker compose run --rm janus gate --model credit_risk_v3 --block-at-or-above high
docker compose up janus-mcp       # long-running, stdio
```

[`docker-compose.yml`](docker-compose.yml) adds Janus to the Docker network
`datahub docker quickstart` already creates, rather than reimplementing DataHub's own
multi-container stack (GMS, MySQL, Kafka, OpenSearch, frontend) inside this repo:
composing what is already shipped, not rebuilding it. `docker compose up` with no
service named starts nothing: every service needs a `--table`/`--model` naming an
actual target, so `run --rm <service> ...` or `up <service>` (named explicitly) are
the only ways anything starts.

The project is named `janus` explicitly in that file, not left to compose's
directory-name default: DataHub's own Quickstart compose defaults to the same
project name (`datahub`), and sharing it would make an ordinary `docker compose
down --remove-orphans` here treat the entire Quickstart as orphaned containers of
this project and stop it. [`Dockerfile`](Dockerfile) builds a non-root image (pinned
to the exact patch version this project develops against, `python:3.11.14-slim`)
with all four console scripts installed; `docker build --build-arg
JANUS_EXTRAS=agent,mcp,anthropic` (or `openai`/`google`) bakes an LLM provider
in instead of installing it at runtime.

## Run watch on a cluster

`scan` and `gate` are one-shot; the MCP server speaks stdio to whatever launched
it. `watch` is the only entry point meant to run forever, so it is the only one
with a Helm chart:

```bash
helm install my-watch charts/janus-watch \
  --set image.repository=ghcr.io/ahmedxsaad/janus/janus \
  --set datahub.gmsUrl=http://datahub-gms.datahub.svc.cluster.local:8080 \
  --set watch.table=loans_raw
```

`.github/workflows/publish-image.yml` builds and pushes that image to GHCR on
every version tag, so the chart has somewhere real to pull from rather than a
placeholder. See [`charts/janus-watch/README.md`](charts/janus-watch/README.md)
for secret handling (`existingSecret` is the path meant for real use) and what
the chart deliberately leaves out (autoscaling, probes that would check nothing
real, an Ingress nothing needs).

Every completed scan already logs what an SLO is built from: findings raised,
writes made, and how long detection itself took, kept separate from the poll
interval and from DataHub's own indexing because `watch` controls neither
(`JANUS_LOG_FORMAT=json` ships those same lines to a log pipeline). Set
`JANUS_OTEL_ENDPOINT` and the three numbers also go to an OTLP collector as
metrics:

```bash
pip install "janus-datahub[otel]"
JANUS_OTEL_ENDPOINT=http://localhost:4318/v1/metrics janus watch --table loans_raw
```

Three instruments, no traces, and nothing imported when the variable is unset. A
team that wants spans across the DataHub SDK's HTTP calls installs
`opentelemetry-instrumentation-requests` and gets them, which is better than this
project shipping a second, worse copy of it.

### React to the graph instead of a timer

`watch --events` consumes DataHub's own `MetadataChangeLog` instead of polling.
It does one thing polling structurally cannot: it re-applies, **catalog-wide**,
any `janus link` an ingestion run drops.

That failure is the adoption cliff, and it is silent. DataHub's mlflow source
upserts the whole `mlModelProperties` aspect on every ingest, which drops the
`mlFeatures` that `link` attached. From that moment the leakage, sensitive-source
and proxy checks have nothing to walk, and each reports "not evaluated" on a
model that was fully checked yesterday. Nothing errors. `link --all` fixes it and
requires somebody to remember.

```bash
pip install "janus-datahub[kafka]"
janus watch --events --model credit_risk_v3
```

Verified the way the plan asks: a model ingested twice through DataHub's own
mlflow source, with the features surviving the second ingest and no human
touching anything. It replays only what a human already confirmed, so a model
nobody linked is left alone: an inferred join looks identical to a confirmed one
in the graph and would make every detector downstream confident about the wrong
columns.

Polling remains the default and needs no broker.

## More sample output

Sample outputs, generated by a real run, are in [examples/](examples).
To verify the whole loop against a live DataHub: `pytest -m integration`.

## Is it any good?

Measured, not asserted. [Janus-Bench](benchmarks/RESULTS.md) scores the detectors
against a live DataHub (never against fixtures, which would only measure the fixtures):

```bash
janus-seed
python -m benchmarks.run_bench          # writes benchmarks/RESULTS.md
```

The freshness sweep walks the lag across the SLA boundary rather than only planting the
obvious 30-hour failure, because that is where a detector actually goes wrong: changing
one comparison from `>` to `>=` is caught by the trial sitting exactly on the SLA, and
scores a clean 1.00 under the demo scenario alone.

It also scores the detectors on a graph this project did not build. Stand up
[examples/real-project/](examples/real-project)
(a postgres warehouse, a dbt project, a scikit-learn script, an MLflow registry,
ingested by DataHub's own sources) and the same command adds a section for it:
the leak lives in the dbt model rather than in a seeding call, and the derivation
the finding quotes comes from DataHub's SQL parser. Seven features are scored
there, one of them leaking.

The per-feature comparison against table-level lineage is
[above](#the-one-thing-nothing-else-does-measured); the full numbers, and what is
still not measured, are in [RESULTS.md](benchmarks/RESULTS.md). How those numbers
are produced, why the baselines are written to be fair, and what the benchmark
deliberately cannot see: [docs/08-evaluation.md](docs/08-evaluation.md).

## Security and privacy

**No row-level data ever leaves DataHub, and none of it reaches the LLM.**
Janus reads the metadata graph and nothing else: aspects DataHub already
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
  `janus.run_id` for provenance. The benchmark reads the graph back after a
  rerun and measures the duplicates created: 0.
- **Writes are gated on a human.** `scan --review` pauses after detection and
  writes only what you approve. `gate` reads and does not write unless you pass
  `--write`, because it runs on every push and one incident per run would fill
  the graph with findings about code that never merged. The MCP tools cannot
  write at all, on any flag: the model driving an MCP client is outside this
  project's control, so it gets to ask what is wrong, never to fix it. `watch`
  auto-approves because it is unattended by definition.
- **The token stays a secret.** It enters the process in one module
  (`janus/env.py`), lives only in `.env` (git-ignored), and is never
  logged, echoed, or put in an exception message. Errors name the *variable*,
  never its value. Text that came back from somebody else's SDK is scrubbed of
  it before it reaches a console or a CI log, and Typer's locals-in-traceback
  rendering is pinned off because those frames hold the token.
- **Least privilege, honestly.** DataHub OSS personal access tokens are not
  scoped per operation, so Janus cannot claim a narrowed token. What it can
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

Full reading list, with what each one changed here:
[docs/15-references.md](docs/15-references.md). The complete threat model is
[docs/10-security.md](docs/10-security.md); what it cannot tell you is
[docs/08-evaluation.md](docs/08-evaluation.md).

## Show a governance function where this fits

```bash
janus crosswalk        # markdown on stdout, connects to nothing
```

One row per detector, mapping it to the NIST AI RMF subcategory its output is
evidence for, with the subcategory text quoted from the AI RMF 1.0 Playbook
rather than paraphrased. The table is generated from the detector registry, so a
check cannot be added to Janus without appearing in it.

It is a mapping and not a conformity claim, and it says so in its own first
paragraph. Which subcategory an artifact is evidence *for* is a fact about the
artifact; whether the subcategory is *satisfied* is a judgement about your whole
process, and nothing that reads a metadata graph is in a position to make it.

## Generate the paperwork instead of maintaining it

```bash
janus model-card    --model credit_risk_v3   # prints; --write publishes it to DataHub
janus evidence-pack --model credit_risk_v3   # EU AI Act Article 10
janus feature-card  --model credit_risk_v3   # one Data Card per feature
```

Three documents, all read entirely out of the catalog. A model card in the sense
Mitchell et al. (FAT* 2019) proposed one: intended use, which columns each
feature was actually computed from, the reported training metrics, the trust
score with its waterfall, the findings open against the model, and the checks
that could not run. An evidence pack that maps the same graph to Regulation (EU)
2024/1689 Article 10 and Article 12, by paragraph number, so a reader can check
the mapping rather than trust it.

Neither is maintained by hand, which is the whole point: a hand-written model
card is accurate until the model next changes, and most of them are already
wrong. These are regenerated from the graph, so they are current by construction
and empty where the catalog is. Anything DataHub does not record prints as *not
recorded in the catalog* rather than being quietly dropped, so a gap is visible
in the document instead of reading as an absence of a problem.

The evidence pack's first heading is **This is not a compliance certification**,
and its second is **What this pack could NOT establish**: deliberately the first
section rather than a closing caveat, because a gap at the end of a long
document is a gap nobody reads. It states, for instance, that freshness at
training time is unknowable from this graph (Janus measures freshness
*now*, which is a different claim and not a substitute), and that whether anyone
examined the data for bias is an activity no catalog records. A generated
document that implied conformity would be worse than no document at all.

`feature-card` is the third, a **Data Card** in the sense Pushkarna, Zaldivar and
Kjartansson (FAccT 2022) proposed one, but for a single feature: where it is
computed from hop by hop, every other derivation the walk found, each table that
chain crosses and how current it is, whether the chain reaches a column
classified as restricted or as a protected attribute, whether its type has moved
since training, and, when a finding names it, the changes that would clear it.
Taken per model because that is what somebody has in hand; `--feature` filters
within it. Its freshness figures say out loud that they are measured *now* and
not at training time, for the same reason the evidence pack refuses to
substitute one for the other.

All three print by default and write nothing. `--write` publishes the document,
keyed on the model or the feature alone, so regenerating replaces it rather than
leaving a second copy behind. Either way the underlying scan is read-only:
asking for documentation never raises an incident as a side effect.

## OSS contributions

Built alongside Janus and offered back to the DataHub ecosystem:

| Contribution | What it is |
|---|---|
| [skill/datahub-ml-guard/](skill/datahub-ml-guard) | The `datahub-ml-guard` skill: traces model features back to source columns to catch leakage, drift, and blast radius, and guides the write-back. Unlike the several ML-reliability skills already submitted to the registry, it is a thin wrapper around a real, tested, deterministic detection engine (this repo), not an LLM asked to eyeball a lineage graph. Destined for [datahub-project/datahub-skills](https://github.com/datahub-project/datahub-skills). |
| [mcp_ext/raise_incident_tool.py](mcp_ext/raise_incident_tool.py) | A thin `raise_incident` mutation tool for [acryldata/mcp-server-datahub](https://github.com/acryldata/mcp-server-datahub), which today has no incident-write tool. Ships with [an RFC](mcp_ext/RFC-ml-incidents.md) for first-class ML incidents. |
| [docs/16-most-valuable-feedback.md](docs/16-most-valuable-feedback.md) | Sixteen concrete, reproducible bugs and doc gaps found while building, each with a repro and a workaround. |

What each one fills, and its upstream status:
[docs/14-oss-contributions.md](docs/14-oss-contributions.md).

## Contributing

Conventions (setup, commit format, code rules, testing rules, formatting rules)
live in [CONTRIBUTING.md](CONTRIBUTING.md). Read
[docs/02-architecture.md](docs/02-architecture.md) for the layer boundaries before
changing anything, and [docs/13-design-decisions.md](docs/13-design-decisions.md) for
the choices those boundaries encode.

License: [Apache 2.0](LICENSE).
