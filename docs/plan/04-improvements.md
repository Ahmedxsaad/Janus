# Proposed Improvements

Improvements Claude proposes on top of the existing plan (01-03). Each item is
a proposal, not a decision: pick the ones you want, then log the choice in
docs/decision-log.md. Priority: P1 = do before Week 1 coding starts,
P2 = do during Weeks 1-2, P3 = nice to have.

Status as of 2026-07-22 (D-010, updated after a docs audit found P2-3/P2-4 had
landed unlogged):
- Adopted and done: P1-2 (pyproject), P1-3 (Python pin, landed as 3.11 not 3.12),
  P1-4 (ruff, mypy, pre-commit), P2-3 (janus/models.py is the shared
  pydantic contract every layer reads/writes), P2-4 (janus/config.py plus
  the janus/env.py single-entry-point rule).
- Adopted in spirit: P2-2, as a marked integration test rather than a separate
  script (D-009).
- Adopted and done, later: P2-1 (GitHub Actions CI on every push and pull
  request, 2026-07-22, D-051). It runs pre-commit rather than its own list of
  ruff and mypy invocations, so the local hooks and the enforced checks cannot
  drift, plus the offline test suite and an advisory dependency audit.
- Still open: P1-1, P3-1 through P3-4.
- Adopted and done, later: P2-5 (structured logging with run_id, 2026-08-01).
  run_id already threaded through every write and dedup key (D-013,
  janus/CLAUDE.md rule 4); janus/logs.py adds the JSON logger behind
  `JANUS_LOG_FORMAT=json`, and `_log_scan` now assembles its facts once and
  renders them twice (logfmt in the message, structured fields on the record) so
  the human line and the indexed fields cannot drift.

## P1-1. Rename the repository from DataHub to janus
The repo is named DataHub but the project is Janus. Judges land on the
repo page first; a repo named after the sponsor's product is confusing and
weakens Submission Quality. The About section must also show the Apache 2.0
license and a one-line pitch. Rename early, before links spread.
Effort: minutes.

## P1-2. pyproject.toml instead of requirements.txt
One file for packaging, pinned dependencies, and tool config (pytest, ruff,
mypy), plus a console entry point so `janus scan` works after
`pip install -e .` instead of `python -m`. The plan's requirements.txt was
kept for now to match the plan; migrating before any code lands is free,
migrating later is churn.
Effort: under an hour.

## P1-3. Pin the project Python version
This machine runs Python 3.14.6. The plan requires 3.10+, but acryl-datahub
and datahub-agent-context may not yet support 3.14. Pick one version the SDK
verifiably supports (check upstream classifiers, likely 3.11 or 3.12), pin it
in a .python-version file, and have everyone use a venv on that version.
Prevents "works on my machine" across the team.
Effort: minutes to decide, saves days of drift.

## P1-4. Lint, format, and type-check from the first line of code
ruff (lint + format) and mypy, configured in pyproject, wired into pre-commit.
The team's clean-code rules are then enforced by tools, not by review comments.
Multiple people are committing; without a formatter the diffs fill with style noise.
Effort: under an hour, once.

## P2-1. GitHub Actions CI on every PR
Run ruff, mypy, and the offline unit tests on each push and PR. Free for
public repos. Judges also see green checks on the repo, which supports the
production-grade story. Add the integration suite later behind a manual
trigger since it needs a live DataHub.
Effort: one small workflow file.

## P2-2. Make the Week 1 kill-criterion an executable gate
The plan defines the gate (read column-level ML lineage, write one incident
and one structured property) as prose. Make it a script or marked test
(tests, integration, gate) that prints PASS or FAIL. Removes wishful thinking
from the pivot decision and later doubles as the judge's smoke test.
Effort: half a day in Week 1, alongside the seeder.

## P2-3. Shared pydantic models as the layer contract
Define Finding, AtRiskModel, DriftFinding, TrustScore, and RunContext in a
single models module before writing detectors. detect/ produces them,
agent/ ranks them, writeback/ consumes them, benchmarks/ scores them. Locking
the contract first lets teammates build layers in parallel without collisions.
Effort: an hour up front.

## P2-4. Central config module
One pydantic-settings config object: env vars, hop caps, thresholds, trust
score weights (the plan itself says weights must be configurable). No other
module reads os.environ directly. Prevents scattered constants that the
benchmark then cannot sweep.
Effort: an hour.

## P2-5. Structured logging with run_id from day 1
The hardening doc plans OpenTelemetry and Prometheus for Week 4, but plain
structured JSON logs with run_id correlation should exist from the first
detector run; retrofitting correlation into existing code is painful. OTel
can still land in Week 4 on top.
Effort: small if done first.

## P3-1. Thin CONTRIBUTING.md pointing at CLAUDE.md
Non-Claude contributors browsing GitHub will not read CLAUDE.md. A ten-line
CONTRIBUTING.md (commit format, branch names, no personal files, see
CLAUDE.md for the rest) puts the same rules where GitHub surfaces them.
Effort: minutes.

## P3-2. Archive the superseded research notes
docs/more.md and docs/less.md are early research whose open questions the
strategy doc has since resolved. Move them to docs/research/ (or prefix them
ARCHIVED-) so nobody cites stale caveats. The docs CLAUDE.md already forbids
citing them; moving them makes it structural.
Effort: minutes.

## P3-3. Makefile (or justfile) for the common loops
make setup / make test / make lint / make scan, each verifying its tools
exist before running (command -v) and failing with a clear install hint.
Encodes the "check the tool is installed" rule so it happens even when a
human runs things by hand. Note: make exists on this machine; just would be
an extra install for every teammate, so plain make is the safer default.
Effort: under an hour.

## P3-4. Add quickstart.sh only when it can be real
The plan puts quickstart.sh at the repo root from the start. Deliberately not
scaffolded: a script that cannot work yet (the datahub CLI is not even
installed) violates the no-placeholder rule. Build it in Week 2 as a thin
wrapper over the same code paths the integration tests use, so the judge path
and the CI path cannot diverge.
Effort: comes naturally with Week 2.
