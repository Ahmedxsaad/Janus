# CLAUDE.md - docs

Reference material, not code. Layers:
- hackathon-specs/: the official hackathon rules and context captured from
  Devpost (01-08 plus their README index). Facts, do not edit except to fix
  capture errors.
- plan/: the ModelGuard strategy, architecture, implementation plan, hardening
  plan, resources, and improvement proposals. This is the source of truth for
  what we build.
- decision-log.md: the running log of decisions (what, options, why, result).

## Local rules

1. When plan and reality diverge (an SDK symbol differs, a feature is
   Cloud-only), update the plan doc and add a decision-log entry; never let
   the plan silently rot.
2. Keep the numbered naming scheme (01-, 02-, ...) when adding plan docs.
3. more.md and less.md are superseded early research kept for history; do not
   cite them, cite plan/ docs instead.
4. Diagrams are PlantUML source inside the markdown; keep them conceptual (no
   file names in diagrams). plantuml is not installed on every machine; check
   before trying to render.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: doc layers and maintenance rules |
