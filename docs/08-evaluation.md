# How Janus is measured

There is no standard benchmark for "data-to-model incident detection", so this
project builds a small reproducible one, Janus-Bench, and publishes what it
measures alongside what it does not.

This page is the **method**. The **numbers** live in
[benchmarks/RESULTS.md](../benchmarks/RESULTS.md), which is generated, never
hand-edited, and reproduced by:

```bash
janus-seed
python -m benchmarks.run_bench
```

## The rules the benchmark holds itself to

1. **Measure against a live DataHub, never against fixtures.** A detector scored
   on this project's own fakes measures the fakes, and the number would not
   survive a judge asking what it was run against.
2. **Ground truth is deterministic.** Failures are planted by
   `janus.seed.scenarios` with fixed lags in a fixed order. The same run produces
   the same numbers.
3. **Trials call the shipped detectors.** Detection is never reimplemented inside
   the benchmark.
4. **Wait for the graph to show the planted state, never for the detector to give
   the expected answer.** Waiting for the answer manufactures perfect recall. A
   trial whose precondition never lands is an error, reported separately, and
   never counted as a miss.
5. **Numbers are never typed into the template.** Prose in the renderer is fine;
   a figure that a run did not produce is not.
6. **A perfect score is a claim about the trials, not about the detector.**

## Ground truth

Planted, reversible scenarios: a source table that silently stops refreshing at a
chosen lag, a feature derived from the label column, a column retyped after
training, a classified column three joins upstream of a feature, a deprecated
training input, and a protected attribute sharing an ancestor with a feature.
Clean variants of each are the negative controls, which is where the
false-positive rate comes from.

**The freshness sweep walks the lag across the SLA boundary** rather than only
planting the obvious 30-hour failure, because the boundary is where a detector
actually goes wrong. Changing one comparison from `>` to `>=` is caught by the
trial sitting exactly on the SLA and by nothing else. Under the demo scenario
alone, that same bug scores a clean 1.00.

That distinction is reported per detector: RESULTS.md carries a **boundary
trials** column and a column answering "could this row have failed?". A row with
no boundary trial is a construction proof rather than a measurement, and it says
so rather than leaving a perfect score to be read as evidence it is not.

## The comparison that matters

The claim under test is that the model-to-column join is what makes the answer
useful. So the same graph and the same ground truth are read three ways, and
scored **per feature**, because every approach can tell that a leaking model
leaks. The question that separates them is *which* feature leaks, which is what
somebody has to go and fix.

The result, and in particular the fourth column, is in
[RESULTS.md](../benchmarks/RESULTS.md). Table-level lineage reaches perfect
recall (it does catch the leak) at 0.25 precision, and, having never seen the
column edge, cannot see it removed either, so it keeps alerting on a graph
somebody has already fixed. That last property is what gets a reliability tool
switched off.

**A baseline is written to be fair, not to lose.** Each is handed every fact
Janus gets, the same label index and the same source-column resolution, and
differs in exactly one respect. Each is tested to genuinely detect *before* it is
tested to over-report: a baseline that finds nothing turns the comparison into a
fabrication no green suite would catch.

They are implementations of an **approach**, not of a product. No Great
Expectations, Deequ, Evidently or NannyML process was run, and RESULTS.md says so
rather than letting a reader assume those tools were benchmarked.

## Scored on a graph this project did not build

The seeded graph is built by this project, which makes it a weak place to prove
anything about somebody else's catalog. So the same command also scores the
detectors against [examples/real-project/](../examples/real-project): a postgres
warehouse, a dbt project, a scikit-learn training script and an MLflow registry,
ingested by DataHub's own sources.

The leak lives in the dbt model rather than in a seeding call, and the derivation
the finding quotes comes from DataHub's own SQL parser. RESULTS.md carries its own
section for that graph.

## Counterfactuals, applied

Every finding carries the changes that would clear it. The benchmark does not take
that on trust: it **performs** each counterfactual against the graph and asks the
detector again, checking the finding actually goes away. Where a feature reaches
the label by two paths, it also verifies that cutting one is not enough.

## Narrative faithfulness

Detection is language-model-free, so the detection numbers are identical with or
without a provider configured. What is scored instead is **faithfulness**:
whether the prose quotes only figures the narrator was actually shown, with no
invented URNs and no invented numbers.

Narrative **quality** is deliberately not scored. A rubric over prose is a
judgement, and this benchmark only reports things it can reproduce.

A provider row appears in RESULTS.md only when a credential for it was present in
the run. Its absence is not a passing grade.

## Mutation testing

A green suite proves nothing until a fault kills it. `mutmut` mutates
`janus/detect/` and the report records how many mutants the suite killed, scoped
to detection because that is the claim under test. Logging calls are excluded
from mutation entirely: a corrupted log line is invisible to every consumer this
project has.

The report does not stop at a score. **Every survivor is grouped under a root
cause with a verdict**, and a survivor with no row fails the render rather than
going unlisted. The two recurring classes it found are worth naming, because they
are ordinary and easy to reproduce anywhere:

- A `continue` mutated to `break` inside a per-item loop survives whenever the
  fixture gives that loop exactly one item.
- A finding's own identifying field swapped for `None` survives whenever a trial
  checks that a finding exists without checking what it says.

## Also measured

- **Idempotency**, by rerunning a scan and reading the graph back to count
  duplicate incidents. The answer is a measurement, not an assertion.
- **Incident lifecycle**: how long findings stay open, read back out of
  `incidentInfo`'s own stamps.
- **Write-back correctness**, by reading each write back through the API.
- **Latency and blast-radius recall.**
- **Scale**, as a sweep up to 50 models on one machine.
- **The constant the degraded mode quotes about itself.** The table-level
  precision the tool prints in its own findings is compared, on every run,
  against the precision the run just measured, so a stale constant is reported
  rather than quietly repeated.

## What this deliberately does not measure

Stated in RESULTS.md itself, and repeated here because it is the part a reader
should not have to hunt for:

- **One seeded graph** for the detection numbers. A 10k or 100k entity curve, a
  contended instance, and a catalog whose models do not share one feature table
  are not measured.
- **No named product's behaviour.** See the baselines note above.
- **Narrative quality**, deliberately.
- **Only the providers a given run could reach.** With no API key configured that
  is the template narrator alone, which is the path every offline test and every
  CI run takes.

The wider set of known gaps, including the ones the benchmark cannot see, is in
[08-evaluation.md](08-evaluation.md).
