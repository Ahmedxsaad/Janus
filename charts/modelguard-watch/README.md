# modelguard-watch

Runs `modelguard watch` as a long-running Kubernetes Deployment: polls a table
and/or model on an interval and writes back the moment a new problem appears.

This chart exists for exactly one ModelGuard entry point. `scan` and `gate`
are one-shot (a Job, a CI step, an `kubectl run --rm`, not a standing
Deployment) and the MCP server speaks stdio to whatever process launches it,
not to a cluster. `watch` is the only one that is meant to run forever, so it
is the only one with a chart.

## Install

```bash
helm install my-watch . \
  --set image.repository=ghcr.io/ahmedxsaad/datahub/modelguard \
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
misconfiguration: `modelguard scan`/`watch` fall back to deterministic
template prose. `--no-llm` (`watch.noLlm: true`) makes that explicit; leaving
`watch.llmProvider` unset makes it implicit.

## What this chart deliberately does not do

- **No autoscaling, and exactly one replica.** `replicaCount` above 1 is
  refused by the template, and the update strategy is `Recreate` so a rollout
  never runs two pods either. This is a correctness limit, not a cost one: one
  `watch` per graph is the supported topology. Every model-level write is a
  read-merge-write of the whole `structuredProperties` aspect and DataHub has
  no conditional write, so two writers reaching one model overwrite each
  other's trust score or risk flag with no error on either side. The same
  applies to a `watch` daemon running next to somebody typing `modelguard
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
