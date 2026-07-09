# ModelGuard

The missing CI for your ML supply chain. ModelGuard is an agent that sits on
the warehouse-to-ML boundary that DataHub uniquely spans: it reads end-to-end
column-level lineage and ML metadata to catch silent data-to-model failures
(target leakage, upstream blast radius, training/serving schema drift), and
writes incidents, model trust scores, impact reports, and guarding assertions
back into the DataHub graph.

Built for "Build with DataHub: The Agent Hackathon" (Devpost, deadline
Aug 10, 2026). Category: Production ML Agents.

Status: scaffold. Week 1 (foundation and de-risking) starts now; see the
implementation plan below for the schedule and the Week 1 gate.

## Documentation

| Doc | What it answers |
|---|---|
| [docs/plan/01-strategy-modelguard.md](docs/plan/01-strategy-modelguard.md) | Why this project, what it solves |
| [docs/plan/architecture.md](docs/plan/architecture.md) | How it works: layers, flows, diagrams |
| [docs/plan/02-implementation-plan.md](docs/plan/02-implementation-plan.md) | The build: phases, APIs, schedule |
| [docs/plan/03-production-hardening.md](docs/plan/03-production-hardening.md) | Benchmark, scaling, security model |
| [docs/plan/04-improvements.md](docs/plan/04-improvements.md) | Proposed improvements, pending decisions |
| [docs/decision-log.md](docs/decision-log.md) | Decisions made, options, why, results |
| [docs/hackathon-specs/](docs/hackathon-specs/) | Official hackathon rules and requirements |

## Repository layout

```
modelguard/    Python package: seed/, detect/, writeback/, agent/
skill/         OSS contribution: the datahub-ml-guard skill
mcp_ext/       OSS contribution (stretch): MCP incident mutation tool
examples/      Sample generated artifacts for judges
benchmarks/    ModelGuard-Bench: injection, metrics, baselines
tests/         pytest unit and integration tests
docs/          Plan, decision log, hackathon specs
```

## Prerequisites

- Linux, Python 3.10+, Docker (about 2 CPUs / 8 GB free for DataHub Quickstart)
- A DataHub personal access token and an Anthropic API key (see .env.example)

Setup instructions and a one-command quickstart land with the Week 2 core loop.

## Contributing

Team conventions (commit format, code rules, formatting rules) live in
[CLAUDE.md](CLAUDE.md). Each directory has its own CLAUDE.md with local rules.
License: [Apache 2.0](LICENSE).
