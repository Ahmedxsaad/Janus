# Janus - OSS Contribution Delivery Steps

> The three Section 8 artifacts are built and verified in the repo (see
> `02-implementation-plan.md` §8 and D-041). This doc records the remaining steps to
> actually *deliver* each one upstream, and who does what. Route decision (D-042):
> full PRs to both upstream repos, plus the Devpost feedback survey.
>
> Deadline for all of it, including the survey: **Aug 10, 2026, 5:00pm ET**.

## What is already in the repo

| Artifact | Location |
|---|---|
| `datahub-ml-guard` skill | `skill/datahub-ml-guard/` (SKILL.md, scripts/, references/) |
| `raise_incident` MCP tool + RFC | `mcp_ext/raise_incident_tool.py`, `mcp_ext/RFC-ml-incidents.md` |
| Most Valuable Feedback survey | `docs/most-valuable-feedback.md` (16 findings) |

None are contributed yet. The steps below are the delivery, not the build.

## 8.1 Skill PR to datahub-project/datahub-skills

Confirmed process from their CONTRIBUTING: fork, install pre-commit hooks
(prettier + markdownlint-cli2 + ruff), format the PR title as Conventional Commits;
Release-Please handles versioning; no stated CLA/DCO.

1. Fork and clone `datahub-project/datahub-skills`; branch `feat/datahub-ml-guard`.
2. `pip install pre-commit && pre-commit install`.
3. Copy `skill/datahub-ml-guard/` -> `skills/datahub-ml-guard/` in the fork.
4. **The janus-dependency wrinkle (D-055, corrected in D-073).** Upstream
   skills wrap the stock `datahub` CLI; ours wraps the `janus` CLI, which is
   packaged for PyPI as `janus-datahub` (the exact name `janus` was
   already taken by an unrelated package; the installed commands are still
   `janus`, `janus-mcp`, etc., since the distribution name and the
   console-script names are independent). **The release is not cut yet**
   (docs/deploy/pypi-release.md), so SKILL.md's prerequisite names the working
   clone-and-`pip install -e .` path first and the one-line `pip install` from
   the release on. Do not submit the skill upstream ahead of that release: a
   prerequisite a reviewer cannot run is worse than a clone they can. The honest
   framing still holds: "an ML-reliability skill that drives the Janus
   package," not a stock-CLI skill.
5. **Match upstream conventions** by diffing against `skills/datahub-enrich/`:
   confirm the frontmatter field set (we mirror `name`, `description`,
   `user-invocable`, `allowed-tools`); check whether `min-cli-version` applies and
   whether `allowed-tools: Bash(janus *)` is accepted where peers use
   `Bash(datahub *)`. Check whether the `using-datahub/` routing table and/or the
   `.claude-plugin` manifest need a new entry for discoverability.
6. `pre-commit run --all-files`; fix any prettier/markdownlint/ruff findings.
7. Commit; open the PR titled `feat: add datahub-ml-guard ML-reliability skill`.

Fallback if upstream review stalls: the plan notes a well-documented standalone
skill linked from the README still counts. It already is (README OSS-contributions
section), so this PR is upside, not a gate.

## 8.2 Full code PR to acryldata/mcp-server-datahub

No CONTRIBUTING.md. Confirmed from the source: the server already registers
mutation tools inside `register_mutation_tools()`, gated by
`get_boolean_env_variable("TOOLS_IS_MUTATION_ENABLED")`, and it has no incident,
assertion, or lineage-write tool (our gap). GraphQL runs through a module-level
`execute_graphql()` in `mcp_server_datahub/graphql_helpers.py`.

1. Fork and clone; branch `feat/raise-incident-tool`. Read `pyproject.toml`,
   `.pre-commit-config.yaml`, and `tests/` for their lint/test conventions; check a
   recent PR for a CLA bot / DCO sign-off.
2. Port the pure logic into their layout (e.g. a new
   `src/mcp_server_datahub/incident_tools.py`), reusing the allowed-type and
   entity-type derivation from the installed metadata model as written in our
   standalone copy.
3. Register `raise_incident` **inside `register_mutation_tools()`** so it inherits
   the existing `TOOLS_IS_MUTATION_ENABLED` gate; drop the standalone tool's own
   per-call `_mutations_enabled()` check there (keep it only in our repo copy).
   Annotate `readOnlyHint: false`.
4. Call the module-level `execute_graphql()` from `.graphql_helpers` (this is the
   resolution of the `[confirm]` in our `register()`; do not use a `client.graph`
   getattr chain).
5. Add a test in their harness mirroring our `demo()` assertions: the gate, the
   mlModel rejection, and the exact payload shape.
6. Run their formatter/linter and tests; open the PR. Link `RFC-ml-incidents.md` in
   the body, or file it as a companion issue/discussion so the larger GMS
   metadata-model change (allowing incidents on an mlModel) gets its own thread.

## 8.3 Complete the Most Valuable Feedback survey (Devpost)

Not a PR. It is a Devpost online form completed during submission (the "$50, 10
winners" bonus). One submission per entrant, within the Feedback Period.

1. During Devpost submission, opt into the feedback section.
2. Submit the actionable content from `docs/most-valuable-feedback.md`: the 12
   findings, each with a repro and a workaround, which is exactly the "actionable
   comments DataHub can use to improve the SDKs or documentation" the rules ask for.
3. Optional strengthener: file the sharpest findings as individual GitHub issues on
   the relevant repos and link them from the survey:
   - the `FixedIntervalFreshnessAssertion` `total_seconds()` truncation (finding #8),
   - the `LineageResult.paths` column-granularity gotcha (finding #12),
   - incidents cannot attach to an mlModel / GMS 500 (finding #1, the RFC).

## Division of labor

- **Prep (can be done in-repo / in a fork without publishing):** copying files,
  wiring `register_mutation_tools`, resolving the `execute_graphql` call, running
  each repo's pre-commit and tests, drafting the port and its test, formatting.
- **Account-bound (the maintainer's action):** creating the forks and opening the
  upstream PRs under a GitHub identity, any CLA/DCO click-through, and the Devpost
  submission plus its feedback form.
