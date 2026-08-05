# janus-watch

Runs `janus watch` as a long-running Kubernetes Deployment: polls a table
and/or model on an interval and writes back the moment a new problem appears.

This chart exists for exactly one Janus entry point. `scan` and `gate`
are one-shot (a Job, a CI step, an `kubectl run --rm`, not a standing
Deployment) and the MCP server speaks stdio to whatever process launches it,
not to a cluster. `watch` is the only one that is meant to run forever, so it
is the only one with a chart.

## Install

```bash
helm install my-watch . \
  --set image.repository=ghcr.io/ahmedxsaad/datahub/janus \
  --set image.tag=0.1.0 \
  --set datahub.gmsUrl=http://datahub-gms.datahub.svc.cluster.local:8080 \
  --set watch.table=loans_raw \
  --set-string secrets.gmsToken=<token, if metadata-service auth is enabled>
```

`watch.table` and/or `watch.model`, and `datahub.gmsUrl`, and `image.repository`
are required: `helm install`/`helm template` fails with a clear message naming
which one is missing, rather than deploying a pod that crash-loops on a
one-line config mistake.

## Secrets

`existingSecret` is the path meant for real use: create the Secret yourself
however this cluster already manages credentials (`kubectl create secret`,
sealed-secrets, External Secrets Operator) and point the chart at it:

```yaml
existingSecret: my-datahub-creds
existingSecretGmsTokenKey: gms-token     # defaults shown
existingSecretLlmApiKeyKey: llm-api-key
```

`secrets.gmsToken` / `secrets.llmApiKey` are the quick-demo path: the chart
creates its own Secret from whatever you pass, and those values land in
`helm get values` and this release's stored history in plain text. Fine
against a local Quickstart, wrong for anything else.

No LLM key at all is a supported, correct configuration, not a
misconfiguration: `janus scan`/`watch` fall back to deterministic
template prose. `--no-llm` (`watch.noLlm: true`) makes that explicit; leaving
`watch.llmProvider` unset makes it implicit.

## Keeping the link alive

```yaml
link:
  enabled: true            # off by default: it writes
  schedule: "0 3 * * *"    # after the nightly ingest
```

That adds a CronJob running `janus link --all` beside the watcher, and on
any cluster with an ingestion pipeline it is the difference between a watcher
that keeps working and one that quietly has nothing left to check.

The reason it is needed at all: DataHub's mlflow source upserts the whole
`mlModelProperties` aspect and drops the features `janus link` attached
(D-074). Every ingest therefore un-links every model, `scan` starts reporting
"not evaluated: this model carries a recorded janus link but declares no
features", and somebody has to notice. `link --all` replays only what each
model already records, and skips any model nobody has linked, so it writes
nothing a human did not previously confirm.

Schedule it away from the watcher's busy window if you can. The two are
separate writers on the same models, and the same read-merge-write limit below
applies to them; the properties they each write are different, and a scan
recomputes anything lost, so the worst case converges rather than corrupts.

## What this chart deliberately does not do

- **No autoscaling, and exactly one replica.** `replicaCount` above 1 is
  refused by the template, and the update strategy is `Recreate` so a rollout
  never runs two pods either. This is a correctness limit, not a cost one: one
  `watch` per graph is the supported topology. Every model-level write is a
  read-merge-write of the whole `structuredProperties` aspect and DataHub has
  no conditional write, so two writers reaching one model overwrite each
  other's trust score or risk flag with no error on either side. The same
  applies to a `watch` daemon running next to somebody typing `janus
  scan` by hand: the writes are idempotent per property, but a concurrent
  reader-writer pair on the same model can still drop the other's value. To
  watch two targets, install this chart twice.
- **No liveness/readiness probe.** `watch` is a foreground CLI loop with no
  HTTP or TCP port to ask, and the container's PID 1 is that process directly
  (the image's own `ENTRYPOINT`). Kubernetes' default exit-triggers-restart
  behaviour already does the one thing a probe here would do; a fabricated
  exec probe that does not check anything the code exposes would be worse
  than none.
- **No Ingress, no Service.** `watch` calls out to DataHub; nothing calls in.

## Values

See [`values.yaml`](values.yaml); every field carries a comment explaining
what it does and, where it matters, why the default is what it is.
