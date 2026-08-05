# CLAUDE.md - skill

OSS contribution: datahub-ml-guard. Several ML-reliability skills are already
open PRs against datahub-skills (drift, trust-score, leakage, RCA); ours is
differentiated by wrapping a real, tested, deterministic detection engine
(this repo) rather than an LLM reasoning over lineage from a prompt file
(D-043). Built here, then PRed to datahub-project/datahub-skills.

## Local rules

1. Mirror the upstream repo format exactly: skills/<name>/SKILL.md with YAML
   frontmatter (name, description), plus references/ and scripts/. Copy the
   structure of skills/datahub-enrich before writing anything.
2. Follow the upstream CONTRIBUTING.md (their commit conventions and release
   process), not ours, for anything destined for the PR.
3. The skill wraps the same detection logic as janus/; do not fork logic,
   call the same scripts or document the same workflow.
4. Even if the upstream merge is slow, this folder must stand alone: a judge
   reading only skill/ should understand and be able to run it.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: upstream mirroring and standalone rules |
| 2026-07-21 | Claude (for Ghassen Naouar) | datahub-ml-guard skill lands: SKILL.md + scripts/ (thin CLI wrappers) + references/. Mirrors the datahub-enrich format (D-041) |
| 2026-07-22 | Claude (for Ahmed Saad) | Drop the "first ML skill" claim (false: several overlapping skills already open upstream); state the real differentiator, a tested deterministic engine behind it (D-043) |
| 2026-07-23 | Claude (for Ahmed Saad) | Prerequisite changes from "clone the Janus repo, pip install -e ." to "pip install janus-datahub", now that the package is on PyPI. Closes the janus-dependency wrinkle docs/plan/05-oss-delivery.md flagged as an expected reviewer question (D-055) |
| 2026-08-01 | Claude (for Ghassen Naouar) | The row above was wrong: the package is not on PyPI, the release is deferred (D-072). The prerequisite names the clone-and-install path that works today and the `pip install` from the release on, so nobody follows an instruction that 404s (D-073) |
| 2026-08-02 | Claude (for Ghassen Naouar) | references/mcp-composition.md: running janus-mcp beside DataHub's own mcp-server-datahub, which question belongs to which, and why detection stays deterministic rather than becoming something a model judges. Documentation only, no runtime dependency on the other server (D-084) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Package and brand identifiers renamed repo-wide: paths, imports, and prose all match the current name and distribution name (D-136) |
