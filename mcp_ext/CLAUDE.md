# CLAUDE.md - mcp_ext

OSS contribution, stretch goal: a raise_incident / create_assertion mutation
tool for acryldata/mcp-server-datahub (v0.6.0 has no incident, assertion, or
lineage-write tools; gap verified July 2026).

## Local rules

1. This is a stretch. Do not start it before the core loop (Problem 2) is
   bulletproof end to end.
2. Follow the upstream repo's tool conventions: annotate mutations with
   readOnlyHint: false and gate them behind TOOLS_IS_MUTATION_ENABLED.
3. If a code PR is too large for the remaining time, file it as an RFC for a
   first-class ML incident workflow instead; that also counts as contribution.
4. Keep the tool thin: it wraps the same GraphQL mutation used in
   modelguard/writeback/incidents, no duplicated logic.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: scope, gating, RFC fallback |
| 2026-07-21 | Claude (for Ghassen Naouar) | raise_incident_tool.py lands: thin raiseIncident wrapper, gated by TOOLS_IS_MUTATION_ENABLED, offline self-check. RFC-ml-incidents.md files the mlModel-incident gap (D-041) |
