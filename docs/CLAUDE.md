# CLAUDE.md - docs

Reference material, not code. Layers:
- hackathon-specs/: the official hackathon rules and context captured from
  Devpost (01-08 plus their README index). Facts, do not edit except to fix
  capture errors.
- plan/: the Janus strategy, architecture, implementation plan, hardening
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
| 2026-08-03 | Claude (for Ghassen Naouar) | plan/08 revised again after the redesign and the live run: the state table is twelve rows and every one names its source, the character is 24x24, and both live-GMS [confirm] items are closed by having run them (D-099) |
| 2026-08-04 | Claude (for Ghassen Naouar) | plan/09-depth-axes.md added: generalizability, evaluation, observability, explainability and governance, each with the filter that admits it and a ranked order. Distinct from 07, which asks where the product breaks; this one asks where it goes deeper, and records what is deliberately not built (D-106) |
| 2026-08-04 | Claude (for Ghassen Naouar) | plan/10-depth-implementation.md added: 09's build order as 21 numbered tasks across eight phases, with the standing definition of done every task inherits from the repo's rules, the phase gates, and the map from 07's open F-numbers to the tasks that close them (D-107) |
| 2026-08-04 | Claude (for Ghassen Naouar) | plan/09 section 3.1 and plan/10 T-04 corrected in place per rule 1 by building them: a dataProcessInstance's inputs and outputs accept dataset and mlModel only, models.py needed no change, and F4's cross-cutting line now says which half T-04 closes and which half stays open (D-111) |
| 2026-08-04 | Claude (for Ghassen Naouar) | plan/09 sections 1.1 and 1.3 and plan/10 phase 3 corrected in place per rule 1 by building them: the dbt adapter needs no dependency, exclusions are the complement of a declaration against a real schema and not the adapter's to return, Feast can declare a label after all, and the degraded mode's disclosure quotes its precision against the question that precision answers (D-112, D-113) |
| 2026-08-05 | Claude (for Ghassen Naouar) | plan/09 sections 1.1 and 3.2 and plan/10 phase 7 corrected in place per rule 1 by building them: sklearn's `get_feature_names_out()` does not give the mapping and retains the label column's name nowhere, so T-21 is struck through rather than deleted; guard coverage excludes freshness, because a table check and five model checks divide two different denominators. 07-weaknesses-and-remedies.md closes F11 with its decision id and states which of its four remedies stays open and why (D-126, D-131, D-132) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Package and brand identifiers renamed repo-wide: paths, imports, and prose all match the current name and distribution name (D-136) |
