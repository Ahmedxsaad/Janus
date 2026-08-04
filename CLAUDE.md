# CLAUDE.md - ModelGuard (root guide)

ModelGuard is a data-to-model reliability agent built on DataHub for the
"Build with DataHub: The Agent Hackathon" (deadline: Aug 10, 2026, 5:00pm ET).
It reads column-level and ML lineage to catch silent data-to-model failures and
writes incidents, trust scores, reports, and guarding assertions back into the graph.

Source of truth for what we build:
- docs/plan/01-strategy-modelguard.md (why), docs/plan/architecture.md (how),
  docs/plan/02-implementation-plan.md (build steps), docs/plan/03-production-hardening.md
  (benchmark, scaling, security), docs/plan/resources.md (literature).
- Read the CLAUDE.md inside a directory before working there. Local rules live locally.

## Repository map

| Path | What it is |
|---|---|
| modelguard/ | The Python package: seed/, detect/, writeback/, agent/, adapters/, argos/, client, CLI |
| argos/ | The desktop window: a Tauri v2 binary and its text sprite art, driven by the event stream modelguard/argos/ writes to its stdin |
| skill/ | OSS contribution: the datahub-ml-guard skill |
| mcp_ext/ | OSS contribution (stretch): MCP raise_incident mutation tool |
| examples/ | Sample generated artifacts for judges, and real-project/, the live stack the product is validated and benchmarked against |
| benchmarks/ | ModelGuard-Bench: injection, metrics, baselines, RESULTS.md |
| tests/ | pytest unit and integration tests |
| docs/ | hackathon-specs/ (official rules), plan/, decision-log.md |
| charts/ | Helm chart for `modelguard watch`, the one long-running entry point |
| deploy/ | Cloud-init and systemd for the Azure judge-facing demo VM |
| assets/ | The README's animation, generated from the sprite art by `assets/make_demo.py` |
| site/ | The documentation and landing page for the shipped product, with Argos reading the same sprite art the window does |

## Workflow rules

1. Before starting any non-trivial task, ask clarifying questions first and
   propose 2-3 concrete options with tradeoffs so the user can choose.
   Never assume scope, never silently expand it.
2. Be concise. Optimize token and context usage: read only the files the task
   needs, do not restate what the user already knows, keep docs short.
3. Before using any CLI tool, verify it is installed (command -v <tool>).
   This is a Linux machine. If a tool is missing, say so and propose the
   install command; do not assume and do not auto-install.
4. Record every significant decision in docs/decision-log.md: date, decision,
   options considered, why, result. If in doubt whether it is significant, log it.
5. Several people work on this repo. Never put personal or machine-specific
   values (absolute paths, tokens, usernames, editor config) in tracked files.
   Personal settings belong in .env and .claude/settings.local.json (both git-ignored).

## Code rules

1. Clean and modular: single-responsibility modules, small functions, explicit
   names, type hints on every public function, docstrings on every module,
   class, and function.
2. Comments explain intent and the why in detail wherever the code is not
   self-evident. No comments that restate the line below them.
3. Never commit empty functions, pass placeholders, TODO stubs, or dead code.
   Code lands only when it is implemented and tested.
4. Design law: detection is deterministic Python; the LLM only explains,
   ranks, and drafts text. It never decides whether a finding exists and
   never composes raw GraphQL (docs/plan/architecture.md section 2).
5. Every DataHub write is idempotent, keyed by (resourceUrn, finding_type,
   run_id), with read-before-write. Reruns must never duplicate.
6. Configuration enters the process in exactly one module: modelguard/env.py.
   It is the only place that calls load_dotenv and the only place that touches
   os.environ. Two tests enforce this; do not weaken them.
   a. Anything that identifies a system, an account, or a vendor gets NO default
      and NO fallback: server URLs, tokens, API keys, LLM provider names, model
      ids. A fallback is a machine-specific value in tracked code. It turns a
      missing .env into a silent connection to the wrong place, or a silent call
      to the wrong vendor billed to whatever key is in the ambient environment.
      Missing means missing, and it fails loudly, naming the variable.
   b. Algorithm parameters (thresholds, hop caps, score weights) are not
      identity. They may keep a documented default in modelguard/config.py.
   c. A group of related settings is all-or-nothing: set every one or none.
      A half-configured feature fails loudly, it never downgrades in silence.
   d. Secrets never appear in a log line, an exception message, a repr, or a CLI
      flag. Carry them as pydantic SecretStr. Text that came back from someone
      else's SDK goes through env.scrub() before it reaches a log.
   e. .env and .env.example carry the identical key set, in the same order.
      Copying .env.example to .env must produce a working run. Add a key to one,
      add it to the other with an empty value and a comment.
7. Verify every SDK symbol against the installed package before using it
   (pip show <pkg>, then introspect). Plan snippets marked [confirm] are
   unverified; never trust a doc snippet over the installed signature.
8. The agent is provider-agnostic. Never import a vendor's SDK outside
   modelguard/llm.py, and never name a vendor's model anywhere else.

## Formatting rules (strict, apply everywhere)

- No em dashes anywhere: not in code, docs, comments, commit messages, or
  replies. Use a hyphen, comma, colon, or parentheses instead.
- No emojis anywhere, ever. Use text markers like [verified] or [confirm].
- Everything in English.

## Git rules

- Commit messages follow Conventional Commits: type(scope): summary
  - Types: feat, fix, docs, test, chore, refactor, bench
  - Scope: the directory or module touched (seed, detect, writeback, agent,
    skill, bench, docs). Omit scope only for repo-wide changes.
  - Summary: imperative, lowercase, no trailing period, at most 60 characters.
- One logical change per commit. Commit regularly; never mix scaffolding,
  features, and docs in a single commit.
- Keep the repo clean at all times: no build artifacts, caches, .env, or
  personal files in git. Check git status before and after every commit.
- Branch names: type/short-topic (example: feat/leakage-detector).
  Never commit directly to main.
- Never add AI attribution to commits: no "Co-Authored-By: Claude" trailer,
  no "Generated with Claude Code" lines, in commit messages or PR bodies.
  Commits are authored by the person running the session only. Enforced for
  the whole team via the attribution setting in .claude/settings.json.
- Do not push or open PRs unless the user asks.

## CLAUDE.md maintenance

- Every CLAUDE.md in this repo ends with a Change Log table. Whenever you edit
  a CLAUDE.md, append a row: date, author, what changed. If unsure of the
  date, run: date +%F
- Keep every CLAUDE.md short. A rule that applies repo-wide belongs here and
  only here; subdirectory files hold only local rules.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: project map, workflow, code, formatting, git, and maintenance rules |
| 2026-07-08 | Claude (for Ahmed Saad) | Git rules: forbid AI attribution (co-author trailers, generated-with lines) in commits and PRs |
| 2026-07-08 | Claude (for Ahmed Saad) | Note that the no-attribution rule is enforced via .claude/settings.json |
| 2026-07-10 | Claude (for Ghassen Naouar) | Code rule 6 rewritten: env.py is the sole config entry point, no fallbacks for identity values, all-or-nothing groups, secret hygiene, .env/.env.example parity. Add rule 8: provider-agnostic LLM |
| 2026-07-23 | Claude (for Ahmed Saad) | Add charts/ to the repository map: the modelguard-watch Helm chart (D-056) |
| 2026-08-01 | Claude (for Ghassen Naouar) | examples/ now also holds real-project/, the dbt + MLflow + postgres stack ModelGuard was validated against as an ordinary user would (D-074) |
| 2026-08-03 | Claude (for Ghassen Naouar) | assets/ joins the repository map: the README's animation, generated from the same sprite file the window and the icon read (D-103) |
| 2026-08-03 | Claude (for Ghassen Naouar) | site/ joins the repository map: the static documentation page for the shipped product (D-104) |
| 2026-08-03 | Claude (for Ghassen Naouar) | argos/ joins the repository map: the Tauri v2 desktop window (Argos) and its sprite art, driven by the JSON event stream modelguard/argos/ produces (D-098) |
| 2026-08-04 | Claude (for Ghassen Naouar) | modelguard/adapters/ joins the repository map: read-only, offline readers that import the model-to-column join out of a Feast repo or a dbt semantic model, behind `link --from` (D-112, T-05/T-06) |
| 2026-08-04 | Claude (for Ghassen Naouar) | examples/real-project/ is a benchmark target as well as a validation stack: the detectors are scored on the graph its own ingestion builds, in its own RESULTS.md section (D-115, T-14) |
