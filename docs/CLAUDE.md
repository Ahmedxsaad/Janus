# CLAUDE.md - docs

Reference material, not code. Layers:
- hackathon-specs/: the official hackathon rules and context captured from
  Devpost (01-08 plus their README index). Facts, do not edit except to fix
  capture errors.
- plan/: the ModelGuard strategy, architecture, implementation plan, hardening
  plan, resources, and improvement proposals. This is the source of truth for
  what we build.
- decision-log.md: the running log of decisions (what, options, why, result).
- deploy/: operational runbooks (the Azure judge-facing demo VM, the PyPI
  release). Unlike plan/, these describe infrastructure that exists and is
  meant to be followed step by step, not a strategy. A runbook whose steps
  have not actually been run says so at the top: pypi-release.md does.

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
| 2026-07-23 | Claude (for Ahmed Saad) | Add deploy/: the Azure judge-facing demo VM runbook (D-057) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-073: the hardening checklist's four open items closed and its benchmark item corrected; the PyPI install instruction in 05-oss-delivery.md fixed to match reality (rule 1: the plan does not get to rot) |
| 2026-08-02 | Claude (for Ghassen Naouar) | plan/06-judge-review-and-improvements.md added: the review against the five judging criteria and the ten improvements chosen from it (D-076) |
| 2026-08-02 | Claude (for Ghassen Naouar) | deploy/pypi-release.md gains a pre-tag checklist, checked mechanically against the built wheel. The repository rename is its one open item, so the decision has a place that blocks a release rather than only a plan doc (D-083) |
| 2026-08-02 | Claude (for Ghassen Naouar) | plan/07-weaknesses-and-remedies.md added: an adversarial audit of the merged codebase, 18 findings with a remedy and a verification step each. Distinct from 06, which asked where the judging points are; this one asks where the project would break, mislead, or fail to be adopted |
| 2026-08-03 | Claude (for Ghassen Naouar) | plan/08-watchdog-mascot.md lands with the build it describes. Three of its claims were corrected by building them (sprite file layout, the terminal fallback, the dropped file), each corrected in place per rule 1 (D-098) |
