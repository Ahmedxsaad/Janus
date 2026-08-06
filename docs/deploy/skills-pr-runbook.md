# Runbook: submit `datahub-ml-guard` to datahub-project/datahub-skills

Status, 2026-08-05: prepared, not submitted. Everything below is ready to run.
The one blocking decision is in section 1; the rest is mechanical.

The companion PR to `acryldata/mcp-server-datahub` (the `raise_incident`
mutation tool) is already open as
[PR #188](https://github.com/acryldata/mcp-server-datahub/pull/188) and needs
nothing from this runbook.

## 1. The blocker: the CLI the skill invokes must be installable

The skill's frontmatter declares `allowed-tools: Bash(janus *)`, and every
script under `scripts/` shells out to the `janus` CLI. A reviewer who
installs the skill and runs it gets nothing unless that CLI is on PATH.

`pip install janus-datahub` still returns 404: the first release has not
been cut (see `pypi-release.md`, whose pre-tag checklist governs it). Verify
before submitting:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/simple/janus-datahub/
# 200 = released, submit. 404 = not released, pick an option below.
```

Options, in order of preference:

1. **Cut the PyPI release first** (preferred). The pending publisher is already
   configured and `.github/workflows/publish-pypi.yml` fires on a version tag.
   Work `pypi-release.md`'s checklist, tag, confirm the 200 above, then submit
   this PR unchanged.
2. **Submit with a git install instruction.** Change the prerequisite line in
   `SKILL.md` and `README.md` to
   `pip install "git+https://github.com/Ahmedxsaad/janus.git"` and say the
   PyPI name is coming. Honest, and it works today, but it reads as unfinished
   to a reviewer.

Do not submit claiming a PyPI package that 404s. That is the one version of this
PR that wastes a maintainer's time.

## 2. What the PR contains

Upstream precedent, from the two skill-adding commits in that repo:

- `53e1e74` (MFE skills) touched only `skills/<name>/**`.
- `7115481` (data-quality) also touched `README.md`, `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json` and added a `commands/<name>.md`.

This PR follows the fuller shape, minus the two `.claude-plugin` description
strings (those summarise the whole plugin; adding one skill does not change what
the plugin is, and editing them invites a merge conflict with any other skill PR
in flight). Files:

```
skills/datahub-ml-guard/SKILL.md
skills/datahub-ml-guard/README.md
skills/datahub-ml-guard/references/detectors.md
skills/datahub-ml-guard/references/datahub-write-surface.md
skills/datahub-ml-guard/references/mcp-composition.md
skills/datahub-ml-guard/scripts/check_blast_radius.sh
skills/datahub-ml-guard/scripts/check_leakage.sh
skills/datahub-ml-guard/scripts/guard.sh
skills/datahub-ml-guard/scripts/seed_demo.sh
commands/ml-guard.md
README.md                                   (one row in the skills table)
```

`scripts/` is not unprecedented upstream: `datahub-connector-pr-review` ships one.

## 3. Launch steps

Run these under the GitHub identity that should own the PR.

```bash
# 1. Fork and clone
gh repo fork datahub-project/datahub-skills --clone=true
cd datahub-skills
git checkout -b feat/datahub-ml-guard

# 2. Copy the skill in (adjust the path to your Janus checkout)
MG=~/Applications/Datahub/DataHub
mkdir -p skills/datahub-ml-guard
cp -r $MG/skill/datahub-ml-guard/. skills/datahub-ml-guard/
cp $MG/docs/deploy/skills-pr-assets/ml-guard.md commands/ml-guard.md

# 3. Add the row to the skills table in README.md by hand, next to
#    datahub-quality. One line, same column shape as its neighbours.

# 4. The repo's own hooks, which CI re-runs
pip install pre-commit && pre-commit install
pre-commit run --all-files

# 5. Commit. The PR title becomes the squash commit, and CI lints it,
#    so it must be a Conventional Commit.
git add -A
git commit -m "feat: add datahub-ml-guard skill"
git push -u origin feat/datahub-ml-guard

# 6. Open it with the body in section 4
gh pr create --repo datahub-project/datahub-skills \
  --base main --title "feat: add datahub-ml-guard skill" --body-file <body.md>
```

## 4. PR body

Keep the first paragraph doing the work: the maintainers of that repo have seen
several ML-reliability skill proposals, and the thing that separates this one is
that a real, tested, deterministic engine sits behind it.

---

**What this adds**

`datahub-ml-guard`, a skill for protecting production ML models by reading the
join between two graphs DataHub already holds and nothing currently connects:
column-level lineage across the warehouse, and ML metadata for the models.

Out of the box a model is not connected to a single column, so a data failure
cannot be traced to the model it breaks. This skill operates that join and reads
across it to answer three questions that are otherwise guesswork: which models a
stale table puts at risk, which feature of a model leaks its target, and whether
a model's input schema has drifted since it was trained.

**Why it is not another prompt over a lineage graph**

Detection is deterministic Python (the `janus` package), and the skill is
the operator's guide to it. A leakage verdict is a graph traversal that carries
the column chain as evidence, not a judgement, so it is the same answer twice and
it survives "how do you know". The language model explains and ranks; it never
decides whether a finding exists and never composes a mutation.

That property is what makes the detectors measurable, and they are measured. On
the same graph and the same ground truth, scored per feature, column-level
lineage reaches precision 1.00 / recall 1.00 where table-level lineage reaches
0.25 / 1.00: table-level can tell you the model leaks but not which of its
features carries it, which is the part somebody has to go and fix.

**Writes back, not just reads**

`--review` gates every write on a human. What it writes is an incident on the
dataset or column at fault, a `model-at-risk` tag and trust score on the model, a
guarding freshness assertion with its measured result, and a Model Impact Report
document. Every write is idempotent and keyed, so rerunning never duplicates.

**Composes with the MCP server rather than competing with it**

`references/mcp-composition.md` is a worked example of running this alongside
`mcp-server-datahub`: the general server answers what the catalog contains, this
answers what a model's data is doing wrong. Nothing here imports or requires it,
and both work alone.

**Testing**

The engine behind the skill has a benchmark that scores every detector against a
live DataHub (never fixtures), including against a graph the project did not
build: a dbt + MLflow + postgres stack ingested by DataHub's own sources.

---

## 5. After it is open

- Expect a question about the `janus` dependency. The honest answer is
  section 1: the skill is a thin operator's guide over a separately installed,
  tested engine, which is the same relationship `datahub-connector-pr-review`
  has with its scripts.
- If review stalls past the hackathon deadline, the skill still counts as a
  contribution: it is public, documented, and linked from the project README.
  Do not withdraw it to make a deadline.
