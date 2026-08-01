# ModelGuard

The missing CI for your ML supply chain. ModelGuard is an agent that sits on
the warehouse-to-ML boundary that DataHub uniquely spans: it reads end-to-end
column-level lineage and ML metadata to catch silent data-to-model failures
(target leakage, upstream blast radius, training/serving schema drift), and
writes incidents, model trust scores, impact reports, and guarding assertions
back into the DataHub graph.

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

## Documentation

| Doc | What it answers |
|---|---|
| [docs/plan/01-strategy-modelguard.md](docs/plan/01-strategy-modelguard.md) | Why this project, what it solves |
| [docs/plan/architecture.md](docs/plan/architecture.md) | How it works: layers, flows, diagrams |
| [docs/plan/02-implementation-plan.md](docs/plan/02-implementation-plan.md) | The build: phases, APIs, schedule |
| [docs/plan/03-production-hardening.md](docs/plan/03-production-hardening.md) | Benchmark, scaling, security model |
| [docs/plan/04-improvements.md](docs/plan/04-improvements.md) | Proposed improvements, pending decisions |
| [docs/plan/06-judge-review-and-improvements.md](docs/plan/06-judge-review-and-improvements.md) | Review against the judging criteria, and what to land before the PyPI tag |
| [docs/decision-log.md](docs/decision-log.md) | Decisions made, options, why, results |
| [docs/hackathon-specs/](docs/hackathon-specs/) | Official hackathon rules and requirements |

## Repository layout

```
modelguard/    Python package: seed/, detect/, writeback/, agent/
skill/         OSS contribution: the datahub-ml-guard skill
mcp_ext/       OSS contribution (stretch): MCP incident mutation tool
examples/      Sample generated artifacts for judges
benchmarks/    ModelGuard-Bench: injection, metrics, measured RESULTS.md
tests/         pytest unit and integration tests
docs/          Plan, decision log, hackathon specs
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
[examples/real-project/](examples/real-project/).

`modelguard link` is that join, and it is one call from the script that trains
the model:

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
[feedback #14](docs/most-valuable-feedback.md)); the arguments are recorded on the
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

Already have a glossary term for labels? Point `MODELGUARD_LABEL_TERM_URN` at it
in `.env` and the detector honors yours instead of creating one.

## Block a bad model before it merges

Everything above is after the fact: it audits a graph that already holds the mistake.
`modelguard gate` is the preventive half, for a pull request. It runs the same
detectors, judges them against a policy, and answers in an exit code, so a leaking or
untrustworthy model fails the build rather than shipping.

```bash
modelguard gate --model credit_risk_v3 --block-at-or-above high   # exit 1 if it leaks
modelguard gate --model credit_risk_v3 --min-trust 80             # exit 1 if trust < 80
```

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

## Run it without a Python install

```bash
datahub docker quickstart              # once: builds DataHub's own stack
docker compose run --rm modelguard-seed
docker compose run --rm modelguard scan --table loans_raw
docker compose run --rm modelguard gate --model credit_risk_v3 --block-at-or-above high
docker compose up modelguard-mcp       # long-running, stdio
```

[`docker-compose.yml`](docker-compose.yml) adds ModelGuard to the Docker network
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
this project and stop it. [`Dockerfile`](Dockerfile) builds a non-root image (pinned
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
placeholder. See [`charts/modelguard-watch/README.md`](charts/modelguard-watch/README.md)
for secret handling (`existingSecret` is the path meant for real use) and what
the chart deliberately leaves out (autoscaling, probes that would check nothing
real, an Ingress nothing needs).

## More sample output

Sample outputs, generated by a real run, are in [examples/](examples/).
To verify the whole loop against a live DataHub: `pytest -m integration`.

## Is it any good?

Measured, not asserted. [ModelGuard-Bench](benchmarks/RESULTS.md) scores the detectors
against a live DataHub (never against fixtures, which would only measure the fixtures):

```bash
modelguard-seed
python -m benchmarks.run_bench          # writes benchmarks/RESULTS.md
```

The freshness sweep walks the lag across the SLA boundary rather than only planting the
obvious 30-hour failure, because that is where a detector actually goes wrong: changing
one comparison from `>` to `>=` is caught by the trial sitting exactly on the SLA, and
scores a clean 1.00 under the demo scenario alone.

### Why column-level lineage, measured

The claim everywhere else in these docs is that only cross-boundary, *column-level*
lineage both roots a failure to the exact upstream column and names the model at risk.
Here it is a number. The same graph, the same ground truth, scored per **feature**,
because every approach can tell that a leaking model leaks; the question that separates
them is *which* of its features leaks, which is what somebody has to go and fix.

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
was run. [RESULTS.md](benchmarks/RESULTS.md) says so, and states what is still not
measured: no scale test, and no scoring of narrative quality.

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
  properties, documents, and assertion aspects. Give it a token you are willing
  to rotate, and rotate it.

Detection is measured, not asserted, and each detector implements a published
result rather than a heuristic somebody liked:

| What | Source |
|---|---|
| Target leakage as illegitimate information about the target, found by inspecting how a feature was constructed | Kaufman, Rosset and Perlich, *Leakage in Data Mining: Formulation, Detection, and Avoidance* (KDD 2011 / ACM TKDD 2012) |
| Undeclared consumers: a table acquiring model consumers its owners never agreed to serve | Sculley et al., *Hidden Technical Debt in Machine Learning Systems* (NeurIPS 2015) |
| A schema fixed at training time, against which serving data is continuously validated | Breck, Polyzotis, Roy, Whang and Zinkevich, *Data Validation for Machine Learning* (MLSys 2019) |
| The prompt-injection and sensitive-disclosure threat model | OWASP Top 10 for LLM Applications (2025), LLM01 and LLM06 |

Full reading list with what each one changed here:
[docs/plan/resources.md](docs/plan/resources.md).

## OSS contributions

Built alongside ModelGuard and offered back to the DataHub ecosystem:

| Contribution | What it is |
|---|---|
| [skill/datahub-ml-guard/](skill/datahub-ml-guard/) | The `datahub-ml-guard` skill: traces model features back to source columns to catch leakage, drift, and blast radius, and guides the write-back. Unlike the several ML-reliability skills already submitted to the registry, it is a thin wrapper around a real, tested, deterministic detection engine (this repo), not an LLM asked to eyeball a lineage graph. Destined for [datahub-project/datahub-skills](https://github.com/datahub-project/datahub-skills). |
| [mcp_ext/raise_incident_tool.py](mcp_ext/raise_incident_tool.py) | A thin `raise_incident` mutation tool for [acryldata/mcp-server-datahub](https://github.com/acryldata/mcp-server-datahub), which today has no incident-write tool. Ships with [an RFC](mcp_ext/RFC-ml-incidents.md) for first-class ML incidents. |
| [docs/most-valuable-feedback.md](docs/most-valuable-feedback.md) | Fourteen concrete, reproducible bugs and doc gaps found while building, each with a repro and a workaround. |

## Contributing

Team conventions (commit format, code rules, formatting rules) live in
[CLAUDE.md](CLAUDE.md). Each directory has its own CLAUDE.md with local rules.
License: [Apache 2.0](LICENSE).
