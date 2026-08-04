# CLAUDE.md - benchmarks

ModelGuard-Bench (docs/plan/03-production-hardening.md section A): detectors
are measured, not asserted. Shipped: inject.py (the labelled trial matrix),
metrics.py (the pure scoring arithmetic), run_bench.py (the live harness and
the RESULTS.md renderer), baselines.py (the approaches without column-level
lineage), counterfactuals.py (each finding's remedies, applied), scale.py (the
catalog sweep), ingested.py (the same detectors on a graph DataHub's own
ingestion built, from examples/real-project/), RESULTS.md (generated). Not
built: Jenga corruption injection, golden/ (D-047, D-050).

Run it with a Quickstart up and the graph seeded:

    modelguard-seed
    python -m benchmarks.run_bench --out benchmarks/RESULTS.md

## Local rules

1. Ground truth is deterministic: synthetic leakage/drift/freshness planted by
   modelguard.seed.scenarios, fixed lags, fixed order. Same run, same numbers.
   Trials call the shipped detectors; never reimplement detection here.
2. Report per detector: precision, recall, F1, false-positive rate, MTTD,
   blast-radius recall, idempotency (duplicate incidents after N reruns = 0).
   Targets live in the hardening doc; do not quietly lower them.
3. Baselines run the same scenarios and the same ground truth. Shipped:
   table-level lineage, and quality-checks-with-no-lineage (D-050). They are
   implementations of an *approach*, not of a product: no Great Expectations,
   Deequ, Evidently or NannyML process is run, and RESULTS.md says so rather
   than letting a reader assume those tools were benchmarked.
4. RESULTS.md must be fully reproducible from run_bench.py on the seeded
   graph; never hand-edit numbers. Prose in the renderer is fine, a number
   typed into the template is not.
5. Golden reports in golden/ are updated only deliberately, with the diff
   explained in the commit message.
6. Measure against a live DataHub, never against a fixture graph. A detector
   scored on our own fakes measures the fakes, and the number would not
   survive a judge asking what it was run against.
7. Wait for the graph to show a planted state before asking a detector
   anything, and never wait for the detector to give the expected answer:
   that manufactures perfect recall. A trial whose precondition never lands
   is an error, reported separately, not a miss.
8. A perfect score is a claim about the trials, not about the detector. Before
   trusting a green column, break the detector it grades and confirm the
   column moves (tests/CLAUDE.md rule 6, applied to the benchmark).
9. A baseline is written to be *fair*, not to lose. Hand it every fact
   ModelGuard gets (the same label index, the same source-column resolution)
   and let it differ in one respect only. Test that it genuinely detects
   before testing that it over-reports: a baseline that finds nothing turns
   the comparison into a fabrication no green suite would catch.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: determinism, metrics, baseline rules |
| 2026-07-22 | Claude (for Ahmed Saad) | ModelGuard-Bench core lands: inject.py, metrics.py, run_bench.py, generated RESULTS.md. Add rules 6-8 (live graph not fixtures, precondition never the expected answer, mutate before trusting a perfect score). Jenga, baselines, scale test and golden/ remain unbuilt (D-047) |
| 2026-07-22 | Claude (for Ahmed Saad) | baselines.py lands: table-level lineage and no-lineage approaches scored on the same graph and ground truth, per feature. Add rule 9 (a baseline is written to be fair, not to lose) and revise rule 3 (D-050) |
| 2026-07-22 | Claude (for Ahmed Saad) | Review pass: the table-level baseline now filters past the hop cap the detector filters (D-020), so it is not charged for false positives this harness caused; a precondition that never lands drops the comparison instead of discarding the whole run (D-051) |
| 2026-08-02 | Claude (for Ghassen Naouar) | inject.py gains a positive and a negative trial for each governance detector. The deprecation negative writes the aspect with deprecated=false, which is how DataHub records a withdrawn deprecation, so a detector treating the aspect's mere presence as the signal fails exactly there (D-079) |
| 2026-08-02 | Claude (for Ghassen Naouar) | scale.py lands and RESULTS.md's "no scale test" gap closes: N model replicas carrying the seeded model's features and run, swept dry-run at 1/10/50, reporting wall clock and graph reads counted at the connection. Replicas are hard-deleted afterwards. Nothing is scored against a target; rule 2's list is unchanged, and the disclosure section now follows what was actually run instead of carrying a caveat that stopped being true (D-082) |
| 2026-08-02 | Claude (for Ghassen Naouar) | Trials gain overrides/boundary/planted, and RESULTS.md gains two columns saying per detector whether the row could have failed. Leakage gets its first boundary trials by moving the hop cap and the label term rather than seeding new tables; a detector matching the label by column name instead of by declared term now fails one. The precondition waits on `graph_state`, never on `expected` (rule 7), since two of the new trials plant the leak and expect silence (D-094, F6) |
| 2026-08-04 | Claude (for Ghassen Naouar) | counterfactuals.py lands: each finding's suggested fixes are applied to the live graph and the detector asked again, with the ones no metadata write can perform named as unverified rather than counted as passes. Rule 8 applied to remediation: a remedy nobody performed is not a measurement (D-110, T-03) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The scale sweep runs last. Its fifty hard deletes left enough index churn behind them to time out the counterfactual measurement's wait for a refreshed table, so a remedy that had landed was reported as an error. Ordering, not a longer timeout: a longer one only makes every genuine error slower to report (D-110, T-03) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The degraded mode gets its own family, scored separately from the column-level detectors as rule 2 requires: two boundary trials that differ only in whether the model is linked, plus an applier for its declare-link remedy. run_bench also checks the precision the product quotes about that mode against the table-level baseline it measures, and prints both. Ordering, per the D-110 precedent: those two trials are the only ones that rewrite mlFeatures, the last edge of the blast-radius traversal measured next, so the family sits mid-matrix rather than the walk being taught to wait for its own answer (D-113, T-07) |
| 2026-08-04 | Claude (for Ahmed Saad) | mutation_report.py lands for T-08: `modelguard/detect/` mutation-tested with mutmut (1484 mutants, 0.77 score), every survivor grouped by function and verdicted (real gap or provably equivalent) rather than left as a bare count, rendered into RESULTS.md between marker comments. A survivor with no verdict raises instead of publishing silently, the same discipline rule 4 already applies to a hand-typed number (D-115) |
| 2026-08-04 | Claude (for Ahmed Saad) | inject.py gains two T-09 trials (common ancestor, label lookalike) and generalizes `_leakage_visible`/`_sensitive_visible` to check lineage reachability rather than only a tag or the flagship feature's own column; `_sensitive_visible` checking only tag presence, never reachability, was a pre-existing bug the fix surfaced. The two new trials are ordered lookalike-then-common-ancestor on purpose: nothing in the matrix reverts a trial's plant before the next one runs, and common-ancestor's own write happens to restore the baseline the reverse order would not. mutation_report.py's VERDICTS gains x__cap_reason and an updated x_marked_ancestor entry after T-08 re-ran (D-116, T-09) |
| 2026-08-04 | Claude (for Ahmed Saad) | faithfulness.py lands for T-10: generated prose checked against the facts its narrator was actually shown, since the prompt carries more than Finding.evidence and grounding on the mapping alone would report a correctly-quoted hop count as a hallucination. Rate reported beside the figure count, because prose quoting no number is faithful by this measure and says nothing. inject.py gains the four T-11 proxy trials, and counterfactuals.py an applier for the one proxy remedy a machine may perform: REVIEW deliberately has none (D-117, D-118) |
| 2026-08-04 | Claude (for Ahmed Saad) | Three registration gaps the first full run with a seventh detector exposed, each now tested: findings_for raised (correctly, rule 8's spirit: an unregistered detector must not be scored as one that never fires), _DETECTOR_LABELS silently rendered six rows for seven detectors, and measure_faithfulness narrated one finding out of seven families because it read whatever the matrix left behind. Faithfulness now plants each family's own positive trial and runs last, before restore_baseline, since it plants state (D-120) |
| 2026-08-04 | Claude (for Ghassen Naouar) | ingested.py lands: the detectors scored against the graph DataHub's own postgres, dbt and mlflow sources built from examples/real-project/, in its own RESULTS.md section and never merged with the seeded numbers. Ground truth is the dbt model on disk, so playing the README's fix flips it with no code change; rule 6's live-graph rule reaches its strongest form here, since not even the graph's shape was written by this project. A DataHub without that stack ingested gets a section saying so and how to fill it, not zeros (D-121, T-14) |
| 2026-08-05 | Claude (for Ahmed Saad) | mutation_report's `_splice` normalizes the whitespace on both sides of the section rather than reusing what it found, so writing the same section twice is a fixed point. It had added a blank line per run, and D-122 (which stopped run_bench deleting the section) is what finally let the two writers be compared and found it. The advisory job on main was red over one character; rule 4's never-hand-edit-a-number has a corollary here, that a generated file must regenerate to itself (D-124) |
| 2026-08-04 | Claude (for Ahmed Saad) | run_bench carries the mutation section across instead of deleting it. It rewrites RESULTS.md whole, and that section is written by a different command on a different schedule, so every benchmark run silently dropped it and its own CI job then reported the file as stale: a permanently red advisory job, which is what ci.yml warns teaches people to ignore red. Carried verbatim, since regenerating it needs a mutmut run this process does not do (D-122) |
