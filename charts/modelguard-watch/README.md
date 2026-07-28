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

- **No autoscaling, no more than one replica by default.** `watch` holds no
  state a second replica could shard (findings ride an in-process holder, not
  a checkpoint store); N replicas watching the same target just poll DataHub N
  times for one answer. Writes are idempotent, so it is wasteful, not unsafe,
  but there is no reason to default to it.
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
