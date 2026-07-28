# CLAUDE.md - charts

One Helm chart, `modelguard-watch`, for the one ModelGuard entry point that is
actually meant to run forever. `scan` and `gate` are one-shot; the MCP server
speaks stdio to whatever launched it, not to a cluster. Only `watch` is a
standing workload, so it is the only one with a chart.

## Local rules

1. A required value gets no default and a `fail()` guard in the template that
   needs it, not a silent fallback. `helm install`/`helm template` must name
   the missing value and stop, the same rule modelguard/env.py enforces at the
   application layer, applied here at the deployment layer (watch.table/model,
   datahub.gmsUrl, image.repository).
2. `existingSecret` is the default path documented and recommended; the
   chart's own Secret creation (`secrets.*` in values.yaml) is the quick-demo
   path and the README says so, because those values land in `helm get values`
   and the release's stored history in plain text.
3. No probe that does not check something the code actually exposes. `watch`
   is a foreground CLI loop with no HTTP or TCP port; a fabricated exec probe
   would be worse than none. Kubernetes' own exit-triggers-restart behaviour
   already does the one thing a probe here would do.
4. Every template change gets `helm lint --strict` and two `helm template`
   renders before it is trusted: one with no values (must fail on the
   required-value guards), one with a realistic set (must produce exactly the
   resources this chart is meant to create). CI runs both on every push
   (`.github/workflows/ci.yml`, job `helm`); do the same locally first.
5. `check-yaml` (pre-commit) cannot parse `templates/*.yaml`: Helm's `{{ }}`
   syntax is not YAML on its own. Excluded there on purpose
   (`.pre-commit-config.yaml`); `helm lint`/`helm template` are the real check
   for these files, not a hook that was silently skipped.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-23 | Claude (for Ahmed Saad) | Initial version: modelguard-watch chart (Deployment, Secret, ServiceAccount), existingSecret as the recommended path, required-value guards, no fabricated probes (D-056) |
