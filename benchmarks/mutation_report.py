"""Render T-08's mutation-score section of RESULTS.md from mutmut's own output.

Detection is the claim this project makes, so this only ever scopes
`modelguard/detect/` (pyproject.toml's `[tool.mutmut]` `only_mutate`). Counts
and survivor names come from `mutmut results --all=true`, mutmut's own
documented CLI (its `export-cicd-stats` gives aggregate counts only, no
per-mutant detail, so it cannot drive the survivor table below).

What is hand-written is the verdict text in VERDICTS: mutation testing can say
a mutant survived, never why that is acceptable or what test would kill it.
A survivor with no entry here fails the render loudly rather than being
silently dropped from the report (10-depth-implementation.md T-08: "a
survivor list of zero with no explanation is not publishable").
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

START_MARKER = "<!-- MUTATION:START -->"
END_MARKER = "<!-- MUTATION:END -->"

_NAME_RE = re.compile(r"^\s*(modelguard\.detect\.[a-zA-Z0-9_.ǁ]+__mutmut_\d+):\s*(\w[\w ]*)\s*$")
_PREFIX_RE = re.compile(r"^(modelguard\.detect\.[a-zA-Z0-9_.ǁ]+)__mutmut_\d+$")


@dataclass(frozen=True)
class Verdict:
    """One root cause, covering every survivor whose qualified name starts with `prefix`.

    `prefix` is mutmut's dotted module path, e.g.
    "modelguard.detect.degraded.x_training_tables".
    """

    prefix: str
    kind: str  # "gap" or "equivalent"
    note: str


# Ordered by file, matching modelguard/detect/'s own listing. A survivor's
# prefix must start with exactly one of these, checked by _verify_coverage.
VERDICTS: tuple[Verdict, ...] = (
    # --- blast_radius.py ---------------------------------------------------
    Verdict(
        "modelguard.detect.blast_radius.x__downstream_traversal",
        "gap",
        "Two things this function does are unobserved: the min-hops-per-model "
        "reduction (`model_hops.get(r.urn, ...)` mutated to `.get(None, ...)` "
        "survives) and the final sort (`key=ModelAtRisk.sort_key` dropped "
        "survives, which would crash on two unsortable items if it ran). Both "
        "need two entries to distinguish correct from broken; every seeded "
        "scenario reaches this table through at most one model at one hop "
        "count. A trial with the same model reachable at two hop counts, and "
        "one with two at-risk models, would kill both.",
    ),
    Verdict(
        "modelguard.detect.blast_radius.x__model_at_risk",
        "gap",
        "`model_ref(conn, model_urn, properties=properties)` mutated to pass "
        "`None`/drop the kwarg survives: nothing asserts the built `ModelAtRisk` "
        "carries the model's own display name rather than a fallback.",
    ),
    Verdict(
        "modelguard.detect.blast_radius.x__now_ms",
        "gap",
        "`time.time() * 1000` mutated to `/ 1000` or `* 1001` survives: no test "
        "pins this helper's arithmetic directly, and nothing downstream "
        "compares an absolute timestamp closely enough to notice a "
        "thousand-fold or 0.1% error. Low real-world severity (staleness "
        "comparisons are relative), but a two-line direct test is cheap.",
    ),
    Verdict(
        "modelguard.detect.blast_radius.x_blast_radius",
        "gap",
        "The traversal's own source argument (`failing_table_urn`) swapped for "
        "`None` survives: no fixture asserts which URN `_downstream_traversal` "
        "was actually called with, only that its canned return value flows "
        "through (tests/CLAUDE.md rule 6, applied to a caller rather than a "
        "graph write).",
    ),
    Verdict(
        "modelguard.detect.blast_radius.x_downstream_models",
        "gap",
        "Same as x_blast_radius: the source URN argument to "
        "`_downstream_traversal` is swapped for `None` and nothing notices.",
    ),
    Verdict(
        "modelguard.detect.blast_radius.x_freshness_signal",
        "gap",
        "The timeseries query's filter argument (`{}`) swapped for `None` "
        "survives: the fixture's canned response does not depend on it.",
    ),
    # --- column_marks.py -----------------------------------------------------
    Verdict(
        "modelguard.detect.column_marks.x_marked_ancestor",
        "gap",
        "T-09 (D-115) killed two of the original six survivors here: the "
        "boundary trial this group already named (the `>` in `result.hops "
        "> config.leakage_max_hops` mutated to `>=`), and, once a second "
        "test ordered its hop-capped fixture result before the real hit "
        "instead of after, the `continue` in that same branch mutated to "
        "`break` -- a `break` there would have skipped the hit entirely, "
        "which is a stronger kill than the single-item-fixture blind spot "
        "T-08 named this pattern for elsewhere. Four remain, all the same "
        "class: `get_lineage`'s own `max_hops`/`count` keyword arguments "
        "swap for `None` or drop entirely and survive, because the fixture "
        "answers a column-level query with a canned result regardless of "
        "which arguments reached it. Live-GMS-only to close: the server "
        "does its own hop-cap search past two hops per D-020, so only a "
        "real GMS call could tell a wrong argument from a right one here.",
    ),
    Verdict(
        "modelguard.detect.column_marks.xǁColumnMarkIndexǁ_editable_marks",
        "gap",
        "`next((...), None)` mutated to `next((...),)` survives: dropping the "
        "fallback turns 'no matching term' from a graceful `None` into an "
        "unhandled `StopIteration`. It only survives because no fixture "
        "exercises the *no match* branch for this lookup; that branch needs "
        "its own trial, not because the two forms behave the same.",
    ),
    Verdict(
        "modelguard.detect.column_marks.xǁColumnMarkIndexǁmarker",
        "gap",
        "Same `next(..., None)` pattern as _editable_marks, same reason.",
    ),
    Verdict(
        "modelguard.detect.column_marks.xǁColumnMarkIndexǁ_marks_in",
        "equivalent",
        "`cached = self._by_dataset.get(dataset_urn)` mutated to `cached = "
        "None` unconditionally defeats the memoization (every call re-reads "
        "the aspect instead of hitting the cache), but the *returned* dict is "
        "identical either way. Equivalent for every currently observed "
        "behaviour; it would stop being equivalent the moment a test starts "
        "counting graph reads, which none currently do for this path.",
    ),
    # --- coverage.py ---------------------------------------------------------
    Verdict(
        "modelguard.detect.coverage.x__deprecated_input_gap",
        "gap",
        "Prose-content mutations (case, XX-wrapping) in the `reason`/`remedy` "
        "strings this Unevaluated carries are the majority of this group's "
        "survivors; exact wording is not a contract the offline suite pins, "
        "by the same design principle tests/CLAUDE.md rule 4 states for "
        "generated text. The minority worth a trial: `target_urn=model_urn` "
        "swapped for `None`, unasserted.",
    ),
    Verdict(
        "modelguard.detect.coverage.x__drift_gap",
        "gap",
        "Same split as x__deprecated_input_gap: mostly prose content, plus an "
        "unasserted `target_urn`.",
    ),
    Verdict(
        "modelguard.detect.coverage.x__freshness_gap",
        "gap",
        "Same pattern: prose content plus an unasserted `target_urn`.",
    ),
    Verdict(
        "modelguard.detect.coverage.x__leakage_gap",
        "gap",
        "Same pattern: prose content plus an unasserted `target_urn`.",
    ),
    Verdict(
        "modelguard.detect.coverage.x__sensitive_gap",
        "gap",
        "Same pattern and the largest group: prose content plus an unasserted `target_urn`.",
    ),
    Verdict(
        "modelguard.detect.coverage.x_coverage_gaps",
        "gap",
        "The `needs_leakage or needs_drift or needs_sensitive or "
        "needs_deprecation` guard survives every single `or`-to-`and` swap "
        "tried on it, and a gap function's own model_urn argument swaps for "
        "`None` too: no trial has exactly one `needs_*` flag true while the "
        "others are false, so the four-way `or` and a four-way `and` answer "
        "identically on every fixture used today.",
    ),
    Verdict(
        "modelguard.detect.coverage.x__cap_reason",
        "gap",
        "T-09's own helper (D-115), landed after the run T-08's verdicts were "
        "written against. Two patterns: prose content in the reason/remedy "
        "sentences it builds, the same class as the `_gap` functions above; "
        "and the feature counts themselves are unasserted (`sum(1 ...)` "
        "mutated to `sum(2 ...)` survives), along with the joiner and "
        "capitalization of the assembled remedy string. Every test that "
        "reaches this function checks which knob is named, never the exact "
        "count or casing.",
    ),
    Verdict(
        "modelguard.detect.column_marks.x_related_columns",
        "gap",
        "T-11's own walk (D-117), landed after the run these verdicts were first "
        "written against. `get_lineage`'s `max_hops`/`count` arguments swap for "
        "`None` or drop and survive, the same argument-not-asserted case as "
        "x_marked_ancestor above and closeable the same way: only a live GMS "
        "distinguishes a wrong argument from a right one, because the fixture "
        "answers with a canned cone regardless of what was asked.",
    ),
    Verdict(
        "modelguard.detect.column_marks.x_derivation_chains",
        "equivalent",
        "T-19's walk (D-130). One survivor, and it is provably equivalent: the "
        'sort tiebreaker\'s fallback constant, `step.column_name or ""`, '
        "mutated to any other string. The fallback exists so `sorted` never "
        "compares None to str on a step GMS returned with no column name; which "
        "constant stands in for the absent name cannot change an ordering, "
        "because a chain that has one has it on every step of the comparison. "
        "That the fallback is load-bearing *at all* is covered: mutating it to "
        '`and ""`, which collapses every key to the same value and hands the '
        "ordering back to the server's arrival order, is killed by a trial that "
        "seeds two equal-length chains in reverse.",
    ),
    Verdict(
        "modelguard.detect.governance.x_proxy_candidate_findings",
        "gap",
        "T-11 (D-117). Two patterns, both already named elsewhere in this table: "
        "the `properties is None or not properties.mlFeatures` guard mutated to "
        "`and` survives because no trial reaches that line with `properties` "
        "None, and several arguments threaded into the walk swap for `None` "
        "without any assertion on what was actually sent. The detector's own "
        "decisions (the direct-descent exclusion, the hop cap, the "
        "nearest-ancestor choice) are covered: tests/detect/test_proxy.py "
        "mutation-checks each, and two of those tests had to be rewritten "
        "before they could fail.",
    ),
    Verdict(
        "modelguard.detect.governance.x__proxy_finding",
        "gap",
        "T-11 (D-117). The built `ProxyCandidate`'s identifying fields swap for "
        "`None` and survive, the single biggest class in this whole table: a "
        "trial checks that a candidate exists without checking what it says.",
    ),
    Verdict(
        "modelguard.detect.governance.x__first_per_pair",
        "gap",
        "T-11 (D-117). `current is None or _distance(...) < _distance(...)` "
        "mutated to `and` survives: with `current` None the `and` short-circuits "
        "before the comparison, so both forms keep the first finding for a pair "
        "seen once. Only a pair reached through three or more generations, where "
        "the middle one is nearest, separates them; the two-generation test that "
        "covers the ordering does not.",
    ),
    Verdict(
        "modelguard.detect.coverage.x__proxy_gap",
        "gap",
        "T-11 (D-117), and the largest single group here. Prose content in the "
        "reason and remedy sentences, the same class as the five `_gap` "
        "functions above and unpinned for the same reason: exact wording is not "
        "a contract the offline suite holds. The minority worth a trial is the "
        "unasserted `target_urn`, identical to its siblings.",
    ),
    # --- degraded.py -----------------------------------------------------
    Verdict(
        "modelguard.detect.degraded.x__classified",
        "gap",
        "Prose content in the finding's reason/remedy text, the same class as "
        "coverage.py's _gap functions, plus one unasserted field.",
    ),
    Verdict(
        "modelguard.detect.degraded.x__deprecated",
        "gap",
        "Same pattern as x__classified: prose content plus an unasserted "
        "field on the returned finding.",
    ),
    Verdict(
        "modelguard.detect.degraded.x__stale",
        "gap",
        "Same pattern: prose content plus an unasserted field.",
    ),
    Verdict(
        "modelguard.detect.degraded.x__upstream_datasets",
        "gap",
        "`get_lineage`'s own `max_hops=1` survives becoming `max_hops=2` "
        "(degraded mode's own docstring promises *one* hop upstream), and the "
        '`"urn:li:dataset:"` entity-type filter survives having its string '
        "content, case, or argument mutated. The fixture behind this test "
        "never mixes a non-dataset URN into the lineage response, so the "
        "filter's job is never exercised.",
    ),
    Verdict(
        "modelguard.detect.degraded.x_table_level_findings",
        "gap",
        "`model_ref(conn, model_urn, properties=properties)` swapped for "
        "`None`/dropped survives: the same unasserted-model-reference pattern "
        "as blast_radius.x__model_at_risk.",
    ),
    Verdict(
        "modelguard.detect.degraded.x_training_tables",
        "gap",
        "The model_urn argument threaded into `_upstream_datasets` swaps for "
        "`None` and survives: the union with `model_input_datasets` masks it "
        "whenever both sides return the same fixture data regardless of the "
        "argument.",
    ),
    # --- governance.py ---------------------------------------------------
    Verdict(
        "modelguard.detect.governance.x__sensitive_finding",
        "gap",
        "The built `SensitiveFeature`'s `feature_urn`/`feature_name` fields "
        "swap for `None` and survive: nothing asserts them on the finding "
        "this constructs, only the finding's presence.",
    ),
    Verdict(
        "modelguard.detect.governance.x_deprecated_input_findings",
        "gap",
        "The dominant survivor here is `continue` mutated to `break` in the "
        "loop over a model's input datasets: with every seeded model having "
        "exactly one input, stopping the loop early is indistinguishable from "
        "skipping one entry and continuing. A model with two inputs, the "
        "first live and the second deprecated, would kill it. One more "
        "survivor swaps the built finding's `dataset_name` for `None`, "
        "unasserted.",
    ),
    Verdict(
        "modelguard.detect.governance.x_model_input_datasets",
        "equivalent",
        "`seen.setdefault(dataset_urn, None)` mutated to `seen.setdefault"
        "(dataset_urn,)` is `dict.setdefault`'s own default parameter value, "
        "not a behaviour change: the two calls are identical for every input.",
    ),
    Verdict(
        "modelguard.detect.governance.x_sensitive_source_findings",
        "gap",
        "Two patterns here: the guard `properties is None or not "
        "properties.mlFeatures` mutated to `and` survives (no trial has "
        "`properties is None` reach this exact line to notice the `and` form "
        "would crash on it), and `model_ref(conn, model_urn, ...)`'s URN "
        "argument swapped for `None` survives, unasserted on the finding.",
    ),
    # --- graph_reads.py ----------------------------------------------------
    Verdict(
        "modelguard.detect.graph_reads.x_live_deployments",
        "gap",
        "`entity_type(deployment_urns[0])` swapped for `None` in the "
        "`get_entities` call survives: the fixture's canned response does not "
        "depend on the argument actually sent.",
    ),
    Verdict(
        "modelguard.detect.graph_reads.x_model_ref",
        "gap",
        "`properties.name if properties and properties.name else None` "
        "mutated to `properties or properties.name` survives: no trial has "
        "`properties` truthy with `properties.name` falsy (or vice versa) to "
        "tell the two guards apart.",
    ),
    # --- leakage.py --------------------------------------------------------
    Verdict(
        "modelguard.detect.leakage.x__finding",
        "gap",
        "The built `LeakingFeature`'s `feature_name` field swaps for `None` "
        "and survives, unasserted, the same pattern as governance's "
        "_sensitive_finding.",
    ),
    Verdict(
        "modelguard.detect.leakage.x_leakage_findings",
        "gap",
        "`model_ref(conn, model_urn, properties=properties)`'s `properties` "
        "argument swaps for `None` and survives, unasserted on the finding "
        "this builds.",
    ),
    # --- schema_drift.py -----------------------------------------------------
    Verdict(
        "modelguard.detect.schema_drift.x__run_findings",
        "gap",
        "`continue` mutated to `break` in the loop over a model's training "
        "runs survives: the same single-item-fixture blind spot as "
        "governance.x_deprecated_input_findings. A model with two training "
        "runs, the first with no snapshot and the second with a real drift, "
        "would kill it.",
    ),
    Verdict(
        "modelguard.detect.schema_drift.x_training_snapshot",
        "gap",
        "The guard `properties is None or not properties.customProperties` "
        "mutated to `and` survives: no trial reaches this line with "
        "`properties is None` to notice the `and` form would crash on it.",
    ),
    Verdict(
        "modelguard.detect.schema_drift.x_diff_schema",
        "gap",
        "An ADDED `SchemaChange`'s `current_type` field swaps for `None` and "
        "survives: nothing asserts the change record itself carries the new "
        "type, only that a change of kind ADDED exists.",
    ),
    Verdict(
        "modelguard.detect.schema_drift.x_schema_drift_candidate_resources",
        "gap",
        "`continue` mutated to `break` in the loop over training runs "
        "survives, the same single-item-fixture pattern as x__run_findings.",
    ),
    Verdict(
        "modelguard.detect.schema_drift.x_schema_drift_findings",
        "gap",
        "`model_ref(conn, model_urn, properties=properties)`'s `properties` "
        "argument swaps for `None` and survives, unasserted.",
    ),
    # --- trust_score.py ------------------------------------------------------
    Verdict(
        "modelguard.detect.trust_score.x__band",
        "gap",
        "Both band thresholds' `>=` mutate to `>` and survive: no trial plants "
        "a score at exactly `trust_band_healthy_min` or `trust_band_watch_min` "
        "to prove the boundary belongs to the higher band. The same class of "
        "boundary trial RESULTS.md's freshness sweep already runs for P2, not "
        "yet run for the bands themselves.",
    ),
    Verdict(
        "modelguard.detect.trust_score.x_trust_inputs_from_findings",
        "equivalent",
        "Every survivor here is one of the five `has_*` flags' `False` "
        "initializer mutated to `None`. Each field is declared `bool` on the "
        "dataclass it feeds and is read only through truthiness "
        "(`if inputs.has_x:`), where `None` and `False` answer identically; "
        "a real `None` assignment there is also independently refused by "
        "mypy strict before it could ever reach a review, let alone a trial.",
    ),
    Verdict(
        "modelguard.detect.trust_score.x_trust_score",
        "gap",
        "`points[DEDUCTION_SENSITIVE_SOURCE] = config.trust_weight_..."
        "` and the deprecated-input deduction's weight both swap for `None` "
        "and survive: the trials assert which deductions exist, not the "
        "point value each one carries. A weight silently zeroed would ship "
        "unnoticed; this is the T-01 waterfall's own scoring arithmetic.",
    ),
)


def run_results() -> str:
    """Run `mutmut results --all=true` against the last `mutmut run` and return its stdout."""
    return subprocess.run(
        ["mutmut", "results", "--all=true"], capture_output=True, text=True, check=True
    ).stdout


def _parse(results_text: str) -> tuple[dict[str, int], list[str]]:
    """Counts by status, and the list of surviving mutant names."""
    counts: dict[str, int] = {}
    survivors: list[str] = []
    for line in results_text.splitlines():
        if not line.strip():
            continue
        match = _NAME_RE.match(line)
        if not match:
            raise SystemExit(f"mutation_report: unparseable 'mutmut results' line: {line!r}")
        name, status = match.group(1), match.group(2).strip()
        counts[status] = counts.get(status, 0) + 1
        if status == "survived":
            survivors.append(name)
    return counts, survivors


def _prefix(name: str) -> str:
    match = _PREFIX_RE.match(name)
    if not match:
        raise ValueError(f"mutant name does not match mutmut's naming scheme: {name}")
    return match.group(1)


def _verify_coverage(
    survivors: list[str], *, verdicts: tuple[Verdict, ...] = VERDICTS
) -> dict[str, list[str]]:
    """Every survivor must fall under exactly one Verdict. Returns prefix -> names."""
    by_verdict: dict[str, list[str]] = {v.prefix: [] for v in verdicts}
    unexplained: list[str] = []
    for name in survivors:
        prefix = _prefix(name)
        owners = [v.prefix for v in verdicts if prefix == v.prefix]
        if not owners:
            unexplained.append(name)
            continue
        by_verdict[owners[0]].append(name)
    if unexplained:
        raise SystemExit(
            "mutation_report: the following survivors have no verdict in "
            "VERDICTS and cannot be published (T-08):\n  " + "\n  ".join(sorted(unexplained))
        )
    return by_verdict


def render_mutation_section(results_text: str, *, verdicts: tuple[Verdict, ...] = VERDICTS) -> str:
    """Pure: every count comes from `results_text` (benchmarks/CLAUDE.md rule 4)."""
    counts, survivors = _parse(results_text)
    by_verdict = _verify_coverage(survivors, verdicts=verdicts)

    total = sum(counts.values())
    killed = counts.get("killed", 0)
    survived = counts.get("survived", 0)
    other = total - killed - survived
    score = killed / (killed + survived) if (killed + survived) else 0.0

    lines = [
        START_MARKER,
        "## Mutation score (T-08)",
        "",
        "Generated by `python -m benchmarks.mutation_report` from `mutmut "
        "results --all=true`, after `mutmut run` (pyproject.toml's "
        "`[tool.mutmut]`). Scoped to `modelguard/detect/` only: the claim "
        "under test is detection, and mutating the rest of the package would "
        "measure something this task never claimed. Logging calls are "
        "excluded from mutation entirely (`do_not_mutate_patterns`): a "
        "corrupted log line is invisible to every consumer this project has.",
        "",
        f"- Mutants generated: {total}",
        f"- Killed: {killed}",
        f"- Survived: {survived}",
        f"- Other (timeout, no tests, suspicious): {other}",
        f"- Score (killed / (killed + survived)): {score:.2f}",
        "",
        "### Survivors, grouped by root cause",
        "",
        "Every survivor falls under exactly one row; a survivor with no row "
        "fails this render rather than going unlisted. Two verdicts recur "
        "enough to name once here rather than in every row: a `continue` "
        "mutated to `break` inside a per-item loop survives whenever the "
        "seeded fixture gives that loop exactly one item, the single biggest "
        "class of real gap this run found (schema_drift and governance); and "
        "a finding's own identifying field (a URN, a name) swapped for "
        "`None` survives whenever a trial checks that a finding exists "
        "without checking what it says, the single biggest class overall.",
        "",
        "| Function | Survivors | Verdict | Why |",
        "|---|---|---|---|",
    ]

    for v in verdicts:
        names = by_verdict[v.prefix]
        if not names:
            continue
        kind_label = "real gap" if v.kind == "gap" else "provably equivalent"
        short = v.prefix.removeprefix("modelguard.detect.")
        lines.append(f"| `{short}` | {len(names)} | {kind_label} | {v.note} |")

    gap_total = sum(len(by_verdict[v.prefix]) for v in verdicts if v.kind == "gap")
    equiv_total = sum(len(by_verdict[v.prefix]) for v in verdicts if v.kind == "equivalent")
    lines += [
        "",
        f"Of {survived} survivors: {gap_total} are real gaps (T-09 is where "
        f"the trials that close them get written), {equiv_total} are "
        "provably equivalent mutations, not gaps.",
        "",
        END_MARKER,
    ]
    return "\n".join(lines) + "\n"


def _splice(results_md: Path, section: str) -> str:
    text = results_md.read_text() if results_md.exists() else ""
    if START_MARKER in text and END_MARKER in text:
        before, after = text.split(START_MARKER)[0], text.split(END_MARKER)[1]
    else:
        before, after = text, ""

    # Both neighbours are stripped and rebuilt rather than kept as found,
    # because CI compares this output byte for byte against the committed file.
    # `section` carries its own leading and trailing newline, so reusing the
    # ones already around the old section added a line every run: appending
    # (no markers yet) left three newlines where replacing leaves two, and
    # replacing left a growing run of blank lines at the end of the file. The
    # job then reported RESULTS.md stale forever over whitespace, which is the
    # permanently red advisory job ci.yml warns about (D-124).
    head = before.rstrip("\n") + "\n\n" if before.strip() else ""
    tail = "\n" + after.lstrip("\n") if after.strip() else ""
    return head + section + tail


def main() -> None:
    """Entry point: render the mutation section and splice it into RESULTS.md."""
    parser = argparse.ArgumentParser(
        description="Render the mutation-score section into benchmarks/RESULTS.md, "
        "from the last 'mutmut run'."
    )
    parser.add_argument("--out", type=Path, default=Path("benchmarks/RESULTS.md"))
    args = parser.parse_args()

    section = render_mutation_section(run_results())
    args.out.write_text(_splice(args.out, section))
    print(f"Wrote the mutation section to {args.out}")


if __name__ == "__main__":
    main()
