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
3. The skill wraps the same detection logic as modelguard/; do not fork logic,
   call the same scripts or document the same workflow.
4. Even if the upstream merge is slow, this folder must stand alone: a judge
   reading only skill/ should understand and be able to run it.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: upstream mirroring and standalone rules |
| 2026-07-21 | Claude (for Ghassen Naouar) | datahub-ml-guard skill lands: SKILL.md + scripts/ (thin CLI wrappers) + references/. Mirrors the datahub-enrich format (D-041) |
| 2026-07-22 | Claude (for Ahmed Saad) | Drop the "first ML skill" claim (false: several overlapping skills already open upstream); state the real differentiator, a tested deterministic engine behind it (D-043) |
