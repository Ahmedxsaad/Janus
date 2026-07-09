# Decision Log

Running log of project decisions. Newest first. Every significant decision gets
an entry: what we decided, the options considered, why, and the result.

Entry template:

```
## D-NNN: <short title> (YYYY-MM-DD)
- Decided by:
- Decision:
- Options considered:
- Why:
- Result:
```

---

## D-014: Seed the warehouse tables instead of depending on a datapack (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: seed_ml_graph.py creates loans_raw and customer_features with
  explicit schemas, using the same URNs the showcase-ecommerce datapack would
  use, rather than assuming the datapack is loaded.
- Options considered: (a) require `datahub datapack load showcase-ecommerce`
  first and seed only ML entities on top, (b) create both warehouse tables
  ourselves at the datapack's URNs, (c) invent our own URNs.
- Why: Column-level lineage needs schemaField URNs, which need a schema. Option
  (b) is a no-op enrichment when the datapack is present and still works when it
  is not, so the gate and the judge's path never depend on datapack contents we
  cannot verify offline. Option (c) would forfeit the "lineage into a real
  warehouse table" story.
- Result: The seeder is self-contained. Loading the datapack remains optional
  realism for the demo, not a prerequisite for the gate.

## D-013: Dedup incidents on (resource, type, title), not on run_id (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: The incident dedup key is (resourceUrn, incident_type, title) over
  the resource's active incidents. run_id is stamped into the description as
  provenance and is deliberately excluded from the key.
- Options considered: (a) the literal key from writeback/CLAUDE.md rule 2,
  (resourceUrn, finding_type, run_id), (b) drop run_id from the key,
  (c) emit incidents on a deterministic URN derived from a hash of the finding.
- Why: run_id changes every run by definition, so (a) makes every scan raise a
  fresh duplicate, contradicting the plan's own idempotency test in section 9
  ("run scan twice, exactly one incident per finding"). (c) is more strictly
  idempotent but bypasses the raiseIncident mutation the plan and the demo rely
  on. (b) keeps the mutation and satisfies the test.
- Result: Implemented and unit-tested. writeback/CLAUDE.md rule 2 corrected.

## D-012: Correct the plan's verified SDK symbols against 1.6.0.13 (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: Trust the installed package over the plan. Four symbols the plan
  marked [verified] are wrong for acryl-datahub 1.6.0.13:
  MLModel.add_group (use the model_group constructor argument),
  client.create_training_run and client.add_input_datasets_to_run (do not exist;
  emit a DataProcessInstance with mlTrainingRunProperties and
  dataProcessInstanceInput), client._emit_mcps (use client.entities.upsert or
  graph.emit_mcps). There are no SDK entity classes for MLFeature,
  MLPrimaryKey, MLFeatureTable, or MLModelDeployment; those are aspect MCPs.
  The incident type COLUMN does not exist; the column-scoped type is FIELD.
  MLFeatureProperties.sources declares entityTypes [dataset], so a feature
  cannot point at a column; the exact column is carried in customProperties.
- Options considered: none. Root CLAUDE.md rule 7 already mandates verifying
  every SDK symbol against the installed package.
- Why: Building on the plan's snippets would have failed at the first write, and
  the leakage detector's whole design assumed column-granular feature sources.
- Result: 02-implementation-plan.md sections 3, 5.1, 6.1, and 13 corrected, and
  writeback/CLAUDE.md rule 4 corrected. Code cites the verified signatures.

## D-011: Pin Python to 3.11 (2026-07-09)
- Decided by: Claude (for Ghassen Naouar), per improvement P1-3
- Decision: .python-version pins 3.11; pyproject requires >=3.11,<3.12.
- Options considered: (a) 3.12, which the acryl-datahub classifiers advertise,
  (b) 3.11, which the acryl-datahub CLI asks for at runtime, (c) leave unpinned.
- Why: On 3.12 the CLI prints "Python versions above 3.11 are not actively
  tested with yet. Please use Python 3.11 for now." A runtime warning from the
  package itself outranks its own classifier metadata.
- Result: Warning gone on 3.11.12. This is the drift P1-3 predicted.

## D-010: Adopt improvements P1-2, P1-3, P1-4; defer P2-3, P2-4, P2-5 (2026-07-09)
- Decided by: Ghassen Naouar
- Decision: Adopt pyproject.toml (P1-2), the Python pin (P1-3), and
  ruff/mypy/pre-commit (P1-4) before Phase 0 code lands. The shared pydantic
  models (P2-3), the central config module (P2-4), and structured logging
  (P2-5) stay open proposals. P1-1 (repo rename) and P2-1 (CI) not yet decided.
- Options considered: (a) Phase 0 exactly as the plan writes it, ignoring
  04-improvements, (b) foundation plus Phase 0, (c) foundation only.
- Why: 04-improvements argues migrating before any code lands is free and later
  is churn. The deferred three describe contracts between layers that do not
  exist yet: Phase 0 produces no detector findings, no tunable thresholds, and
  no multi-node run to correlate.
- Result: Foundation and Phase 0 landed together on feat/phase-0-de-risker.
  Revisit P2-3 and P2-4 when the first detector lands in Phase 1.

## D-009: Make the Week 1 gate an executable integration test (2026-07-09)
- Decided by: Claude (for Ghassen Naouar)
- Decision: The kill-criterion lives in tests/integration/test_week1_gate.py,
  run with `pytest -m integration`, rather than staying prose in the plan.
- Options considered: (a) leave it prose and verify by eye in the UI, (b) a
  standalone gate script printing PASS or FAIL (improvement P2-2), (c) a marked
  pytest module.
- Why: The pivot decision must not rest on wishful thinking. (c) reuses the
  existing runner and the skip-when-unreachable convention from tests/CLAUDE.md
  rule 2, and doubles as the judge's smoke test, so it beats a second bespoke
  entry point.
- Result: Nine integration tests cover both halves of the gate plus idempotency.
  scenarios.py deliberately not written: the Week 1 schedule does not call for
  it and no detector consumes it yet, so it would be dead code.

## D-008: Move hackathon specs into docs/hackathon-specs/ (2026-07-08)
- Decided by: Ahmed Saad
- Decision: The eight captured Devpost spec files (01 to 08) plus their README
  index live in docs/hackathon-specs/.
- Options considered: none, direct request.
- Why: docs/ was mixing official hackathon reference with our own plan,
  research, and logs; separating them keeps docs/ navigable.
- Result: Moved 2026-07-08; docs/CLAUDE.md and root CLAUDE.md updated to match.

## D-007: Scaffold branch based on the docs branch (2026-07-08)
- Decided by: Claude (for Ahmed Saad)
- Decision: Create chore/project-scaffold off docs/hackathon-plan-documents,
  not off main.
- Options considered: (a) branch off main, (b) branch off the docs branch,
  (c) commit the scaffold directly onto the docs branch.
- Why: The scaffold references the plan docs, which only exist on the docs
  branch; committing scaffold onto a docs-named branch would mix concerns.
- Result: Merge order is docs/hackathon-plan-documents first, then
  chore/project-scaffold.

## D-006: One CLAUDE.md per part, global rules only at the root (2026-07-08)
- Decided by: Ahmed Saad (requested), shaped by Claude
- Decision: A root CLAUDE.md holds all repo-wide rules; each directory gets a
  short local CLAUDE.md; every CLAUDE.md ends with a Change Log table.
- Options considered: (a) one big root file only, (b) root plus per-directory
  files with duplicated rules, (c) root plus short local files, no duplication.
- Why: Claude Code loads nested CLAUDE.md files only when working in that
  directory, so short local files optimize token usage; duplication rots.
- Result: 12 CLAUDE.md files created; duplication forbidden by the root file.

## D-005: Strip em dashes and emojis from all existing docs (2026-07-08)
- Decided by: Ahmed Saad (rule), applied by Claude
- Decision: Team rule is no em dashes and no emojis anywhere. Applied
  retroactively to docs/: em dashes become hyphens; semantic markers become
  text tags ([verified] for the checkmark, [confirm] for the warning sign,
  [paper]/[book]/[tool]/[standard]/[security] for the legend icons).
  Also renamed "less .md" (filename contained a space) to less.md.
- Options considered: (a) apply the rule to new content only, (b) full
  retroactive cleanup.
- Why: The user marked this rule as very important and universal; leaving
  hundreds of violations in tracked docs would contradict it.
- Result: Cleanup committed separately so the mechanical diff is easy to review.

## D-004: Conventional Commits, max 60-char subject (2026-07-08)
- Decided by: Ahmed Saad (requirements), format chosen by Claude
- Decision: type(scope): summary, imperative, lowercase, no period, max 60
  chars; one logical change per commit; branches named type/short-topic.
- Options considered: (a) Conventional Commits, (b) free-form prefixed
  messages, (c) gitmoji (rejected outright: emoji ban).
- Why: Conventional Commits is the de facto standard, is tooling-friendly,
  and matches the user's ask for a clear structure with short names.
- Result: Documented in root CLAUDE.md git rules.

## D-003: No stub code in the scaffold (2026-07-08)
- Decided by: Ahmed Saad (rule), applied by Claude
- Decision: The scaffold contains directories, documented __init__.py files,
  and config; zero function stubs. Files like cli.py, client.py, or detector
  modules are created only when actually implemented and tested.
- Options considered: (a) full stub tree with pass placeholders matching the
  plan layout, (b) docstring-only packages, code lands with implementation.
- Why: The team rule forbids empty functions and pass placeholders; stubs
  also mislead readers about what exists.
- Result: Package structure exists and imports cleanly; planned modules are
  named in each package docstring and CLAUDE.md instead.

## D-002: Adopt the plan's repo layout at the existing repo root (2026-07-08)
- Decided by: Claude (for Ahmed Saad), per the plan
- Decision: Use the layout from docs/plan/02-implementation-plan.md section 2,
  placed directly at this repo's root (modelguard/ package plus skill/,
  mcp_ext/, examples/, benchmarks/, tests/ as siblings).
- Options considered: (a) nested modelguard/ project folder inside the repo,
  (b) plan layout at the repo root, (c) src/ layout.
- Why: The repo root is already the project; nesting adds a pointless level.
  src/ layout is a real alternative but deviates from the plan; raised in
  docs/plan/04-improvements.md instead of decided unilaterally.
- Result: Structure created 2026-07-08. Note: the repo is named DataHub while
  the project is ModelGuard; renaming is proposed in 04-improvements.md.

## D-001: Build ModelGuard, category 3 (2026-07-08)
- Decided by: Ahmed Saad
- Decision: Go with the plan folder: ModelGuard, Production ML Agents
  (category 3), with MigrationCopilot as the documented Week 1 fallback.
- Options considered: See docs/plan/01-strategy-modelguard.md (category
  analysis) and docs/more.md / docs/less.md (earlier candidate ideas).
- Why: Verified least-crowded category with the highest differentiation and
  maximal write-back surface; full argument in the strategy doc.
- Result: This scaffold. Week 1 gate: read column-level ML lineage plus write
  one incident and one structured property, or pivot.
