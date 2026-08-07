# Running it for real

How Janus is deployed and released: the live public demo, container and cluster
deployment, and how a version reaches PyPI.

Command and flag reference is on
[docs.ahmedxsaad.me](https://docs.ahmedxsaad.me). This page is about the
infrastructure around the commands.

## The live demo

**<https://janus.ahmedxsaad.me>**

One virtual machine running the full stack: DataHub's own Quickstart plus
`janus watch` continuously, against a graph with the ML supply chain seeded and a
failure planted. What a visitor sees is a graph Janus is actively maintaining,
not a screenshot, and nothing needs to be installed to see it.

Once signed in, search `credit_risk_v3` for the model side (its `model-at-risk`
tag, `janus.trust_score` and `trust_band` properties, and the linked Model Impact
Report), or `loans_raw` for the data side (the open incident and the guarding
freshness assertion).

### How it is built

`deploy/azure/cloud-init.yaml` provisions it from cold: Docker, the repository,
the full Quickstart stack, the seeder, the scenario, and
`deploy/azure/janus-watch.service` as a systemd unit. `deploy/azure/Caddyfile.template`
terminates HTTPS on a custom domain.

The whole thing has been provisioned from scratch and verified end to end rather
than assumed from a UI loading: cold boot to a real incident raised, checked over
SSH.

### The security decision that matters there

A DataHub Quickstart ships with **metadata service authentication disabled**. On a
public VM that would mean an unauthenticated write API on the internet.

The demo therefore keeps GMS off the internet at **two independent layers**: a
dedicated network security group that opens the frontend port and never port 8080,
and a host firewall that does the same. Two layers rather than one, because either
alone is a single misconfiguration away from exposing a writable metadata service.
SSH is scoped to the provisioning machine, not to the world. The frontend's default
credentials are changed as an explicit post-provision step.

### Two failures worth knowing about

Both were found by running it, not by reading it, and both are fixed in the
committed configuration:

- **Cloud-init wrote files before the user it wrote them for existed**, which
  cascaded into a failed clone and a VM that came up empty. The file writes moved
  into the ordered run stage.
- **OpenSearch was OOM-killed and stayed dead for six hours** while GMS and the
  frontend kept answering health checks, silently breaking search and browse the
  whole time. There was no restart policy. Every container now sets
  `restart: unless-stopped`, so a crash self-heals in seconds. That is a
  mitigation, not a capacity fix: 8 GiB shared across three JVMs, MySQL and a
  watch process runs tight, and swap was doubled ahead of judging for the same
  reason.

### Cost control

Compute is only billed while the VM runs. Provisioning, testing, then deallocating
until shortly before it is needed is the same VM for a fraction of the runtime; the
disk and public IP bill either way. Rates differ by region and subscription type,
so check the portal rather than trusting a number from anywhere else, and confirm
the price shown is a regular rate and not a Spot bid. Spot is wrong for this
workload regardless of price: an eviction is an unannounced outage on a demo that
has to stay reachable.

## Docker

```bash
datahub docker quickstart              # once: builds DataHub's own stack
docker compose run --rm janus-seed
docker compose run --rm janus scan --table loans_raw
docker compose up janus-mcp            # long-running, stdio
```

`docker-compose.yml` **adds Janus to the network `datahub docker quickstart`
already creates** rather than reimplementing DataHub's own multi-container stack
(GMS, MySQL, Kafka, OpenSearch, frontend) inside this repository.

Two details that are deliberate:

- **The compose project is named explicitly**, not left to the directory-name
  default. DataHub's own Quickstart compose defaults to the same project name, and
  sharing it would make an ordinary `docker compose down --remove-orphans` here
  treat the entire Quickstart as orphaned containers of this project and stop it.
- **`docker compose up` with no service named starts nothing.** Every service needs
  a target, so `run --rm <service> ...` or `up <service>` are the only ways
  anything starts.

`Dockerfile` builds a non-root image pinned to the exact patch version this project
develops against, with all console scripts installed. A build argument bakes an LLM
provider in instead of installing one at runtime.

## Kubernetes

There is **one chart, `charts/janus-watch`, for exactly one entry point.** `scan`
and `gate` are one-shot, and belong in a Job or a CI step. The MCP server speaks
stdio to whatever launched it, not to a cluster. `watch` is the only entry point
meant to run forever.

```bash
helm install my-watch charts/janus-watch \
  --set image.repository=ghcr.io/ahmedxsaad/janus/janus \
  --set datahub.gmsUrl=http://datahub-gms.datahub.svc.cluster.local:8080 \
  --set watch.table=loans_raw
```

Required values fail `helm install` and `helm template` with a message naming the
missing one, rather than deploying a pod that crash-loops on a one-line mistake.

**`existingSecret` is the path meant for real use**: create the Secret however this
cluster already manages credentials and point the chart at it. The inline
`secrets.*` values are the quick-demo path.

The chart deliberately ships **no autoscaling, no probes and no Ingress**, because
none of them would check or serve anything real for this workload. An optional
CronJob runs `link --all` after your ingest, which is the scheduled half of keeping
the model-to-column join alive.

`.github/workflows/publish-image.yml` builds and pushes the image to GHCR on every
version tag, so the chart has somewhere real to pull from.

## Releases

Two distributions, on separate tag namespaces.

| Distribution | What it is | Tag |
|---|---|---|
| `janus-datahub` | The Python package and every console script | `v*.*.*` |
| `janus-argos` | The desktop window | `argos-v*` |

Both publish through GitHub Actions using **Trusted Publishing (OIDC)**, so no API
token is stored in this repository. Publishing requires a one-time pending-publisher
registration on PyPI naming the owner, repository, workflow file and environment
exactly; a mismatch is rejected at upload.

The release environment can require a reviewer, which is worth doing: a tag already
makes a release deliberate, but a yanked release still burns its version number
forever.

The Linux route for the window is a release attachment (`.deb`, `.AppImage`) rather
than a wheel.

One practical note: **do not check whether a project exists by loading its
`pypi.org/project/<name>/` page from a script.** PyPI answers automated requests
with a bot-challenge page that returns HTTP 200 regardless, which reads as "it
exists" when it does not. The `/simple/` index is the honest check.

## Packaging

Two distributions, nine optional extras, four console scripts.

| Extra | What it adds |
|---|---|
| `anthropic`, `openai`, `google` | One provider binding for the narrator. Install exactly the one you configure |
| `agent` | `langgraph`, for `scan --review`'s human-approval interrupt |
| `mcp` | The MCP runtime for `janus-mcp` |
| `pet` | The Argos window, on macOS and Windows |
| `feast` | The Feast declaration reader |
| `kafka` | The change-log consumer behind `watch --events` |
| `otel` | The three OTLP metrics |
| `dev` | Everything above that is a tool, plus pytest, ruff, mypy, mutmut, pre-commit |

**No provider is a core dependency**, because a scan falls back to deterministic
template prose when no model is configured, and that is the out-of-the-box path.
The `agent` extra needs only `langgraph`, not an umbrella `langchain`: the state
graph's nodes call Janus's own deterministic functions, so there is no tool-caller
in it.

**One extra is deliberately absent and says so in the file.** DataHub's own Agent
Context Kit would be a natural extra, but every release from 1.6.0.6 onward pins
`acryl-datahub==1.6.0.6` exactly, including 1.7.0, while this project pins
1.6.0.13. Pip answers `ResolutionImpossible` for the pair, so declaring the extra
would ship an install command that cannot succeed. Measured, reported upstream,
and the integration is written to the kit's public API so it starts working the
moment that pin loosens.

**The Python floor and ceiling are both reasoned.** 3.11 is a floor because 3.11
syntax is used throughout. The ceiling used to be `<3.12` on the strength of a
*warning* the DataHub CLI prints, and that made the package uninstallable on
every current distro Python: Ubuntu 24.04 ships 3.12, 25.04 ships 3.13, and
neither carries 3.11 by default, so `pip install janus-datahub` failed outright
for the ordinary user. Verified on 3.12: install, scan, gate, leakage detection
and the trust score all behave identically. Development targets 3.11, which is
what CI runs.

`janus-datahub` is the distribution name because the bare name `janus` is already
registered on PyPI by an unrelated package. PyPI names are global and are not
reclaimed because a package looks unused. The console scripts are unaffected:
`janus`, `janus-seed`, `janus-scenario` and `janus-mcp` install regardless.

## Continuous integration

Eight jobs on every push and pull request. Described in
[09-testing.md](09-testing.md).

A fifth workflow surface is the bundled **GitHub Action** (`action.yml`), which
wraps `gate` for somebody else's workflow. It takes a model or table, a policy, a
GMS URL and token, and reports three states to the rest of the workflow as
`outcome` (`clean`, `blocked`, `error`) alongside the boolean `blocked`. It also
writes the verdict to the run's own summary page.
