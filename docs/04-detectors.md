# The checks

Six detectors, plus one degraded mode for models nothing has linked yet. Each one
uses a different slice of the graph, maps to a different write-back, and rests on
a published result rather than a heuristic somebody liked.

They share one engine, the cross-boundary lineage walk, so they compose one
product rather than six taped-together features.

This page explains what each check looks for and what it needs. How they are
scored is [08-evaluation.md](08-evaluation.md); how to invoke them is
[docs.ahmedxsaad.me](https://docs.ahmedxsaad.me).

## The rules every detector obeys

1. **Pure function of the graph.** No language model, no writes. This is what
   makes them benchmarkable and injection-resistant.
2. **Typed findings only.** Never a raw dict, never free text.
3. **Positive evidence only.** A missing aspect means unknown, not failing. A
   table that never reported an operation is not stale.
4. **Bounded traversal**, with the bound visible in the answer. A walk that hit
   its result cap having found nothing reports that it may not have seen
   everything.
5. **A gap is reported, never rendered as health.** Every check reports whether
   it had the metadata to run, and names what is missing if it did not.

## Blast radius (upstream freshness)

**The question.** This table has silently stopped refreshing. Which live models
are scoring on it right now, and through which columns?

**How.** Read the `operation` aspect on the table, which is a timeseries aspect,
and compare the last operation time against the freshness SLA (default 6 hours,
`JANUS_FRESHNESS_SLA_HOURS`). If it is past, traverse downstream across the
warehouse-to-model boundary, hop-capped, collecting every model and every live
deployment reached, deduplicated. Severity is driven by whether a deployment is
actually in service.

**Needs.** The `operation` aspect on the table, written by dbt, Airflow, Spark or
the SDK's `report_operation`.

**Writes.** An incident on the table, a `model-at-risk` tag and risk properties on
each model in the radius, an impact report, and a guarding freshness assertion
with its measured result.

**Literature.** Sculley et al., *Hidden Technical Debt in Machine Learning
Systems* (NeurIPS 2015), for undeclared consumers: a table acquiring model
consumers its owners never agreed to serve.

## Target leakage

**The question.** Which of this model's features descends, directly or through
several hops, from the column that holds the label the model predicts?

**How.** For every feature, walk upstream from its declared source column, up to
`leakage_max_hops` (default 6), looking for an ancestor column carrying the label
glossary term. The walk reads lineage **paths**, not lineage results: a column
query returns the dataset as its result URN, so reading the result would name the
wrong thing. It returns every marked ancestor a walk reached, not only the
nearest, and splits the SDK's flattened path list back into the paths GMS
returned, because two derivations through one upstream table would otherwise read
as a single impossible chain.

The finding quotes the derivation as evidence:

> `credit_risk_v3` feature `applicant_income` derives, through
> `applicant_income <- income`, from `loans_raw.income`, classified
> `janus.sensitive`.

**Needs.** Features carrying source columns, plus a column carrying the label
term. Both come from `janus link`. If your catalog already has a label term,
point `JANUS_LABEL_TERM_URN` at it and Janus honours yours instead of creating
one.

**Writes.** A `FIELD` incident on the offending column, a leakage risk term on the
feature, model risk properties, an impact report.

**Literature.** Kaufman, Rosset and Perlich, *Leakage in Data Mining:
Formulation, Detection, and Avoidance* (KDD 2011 / ACM TKDD 2012). Leakage as
illegitimate information about the target, found by inspecting how a feature was
constructed rather than by looking at its predictive power.

## Training and serving schema drift

**The question.** Which of the columns this model trained on have been retyped,
renamed or dropped since?

**How.** Diff the training-time schema snapshot recorded on the training run
against the source's current `schemaMetadata`. It is a snapshot comparison, not a
reconstructed timeline: the snapshot is what the model actually saw. Fields are
classified added, removed or retyped. A model trained on several inputs merges
snapshots rather than overwriting on a second link.

**Needs.** A training-time schema snapshot on the training run, written by
`janus link`.

**Writes.** An incident naming the drifted fields and the training run, model risk
properties. The incident title names the model as well as the dataset: two models
trained on one input would otherwise share a dedup key and each recovery would
close the other's incident.

**Literature.** Breck, Polyzotis, Roy, Whang and Zinkevich, *Data Validation for
Machine Learning* (MLSys 2019). A schema fixed at training time, against which
serving data is continuously validated.

## Sensitive source

**The question.** Somebody classified a column as PII, PHI or restricted. Three
joins downstream, a feature derives from it, and a live model trains on that
feature. Does it?

Nothing is broken and the model works. What is wrong is what it was allowed to
see, and the derivation is far enough upstream that neither team would notice.

**How.** The leakage walk with a different mark. It shares `column_marks.py` with
leakage rather than carrying a second copy of the paths-not-results traversal,
which would be a second chance to get it wrong.

**Needs.** Features with source columns, plus your own classification taxonomy in
`JANUS_SENSITIVE_TAG_URNS` or `JANUS_SENSITIVE_TERM_URNS`, comma-separated,
either surface or both.

**There is deliberately no default.** A guessed classification URN either matches
nothing or matches a term that means something else in your catalog, and a false
incident about a compliance exposure is the worst kind to be wrong about. Leave
both empty and every scan reports the check as **not evaluated**, never as clean.

## Deprecated input

**The question.** A table's owners marked it deprecated, with a note and sometimes
a decommission date. They have no way to know a model depends on it, and the
model's team has no way to know the flag was set.

**How.** Read DataHub's own `deprecation` aspect on the model's training inputs.

**Needs.** Nothing configured. `deprecation` has one meaning everywhere.

**Severity is capped at medium**, always. This is a deadline, not a defect.

## Proxy candidate

**The question.** Does a feature share an ancestor with a column classified as a
protected attribute, without either descending from the other?

This looks for a fork rather than a chain, which is why it is a separate detector
and not a variant of the leakage walk. Its hop cap (`proxy_max_hops`, default 3)
is deliberately lower than leakage's: a leak is a leak however far away, but two
columns eight hops from a shared ancestor are not evidence of anything.

**Needs.** Its own configuration group, with no default, for the same reason
sensitive source has none.

**Deliberately weak.** Severity is capped at medium and does not escalate for a
live model, and it contributes nothing to the trust score. It raises a question a
human has to settle, and a positive-evidence rule cannot settle one.

## The table-level answer, for a model nothing has linked

Until a model is linked, none of the column-level checks can run on it. Rather
than only listing what it could not do, a scan says what it can see about the
tables that model is recorded as training on: whether one is past its freshness
SLA, marked deprecated by its owners, or holds a column your organization
classified.

It is its own finding type, it never outranks a column-level finding, it is gated
so it stays silent whenever a column-level detector can answer, and it is
excluded from the trust score, because a maybe must not move a number people
compare over time.

It also states its own limit, with a measured number:

> Checked at table level only (`churn_model` declares no features): the table this
> model trains on is past its freshness SLA. Which of the model's features carry
> the stale values is not knowable without a column-level link. Asked which
> feature carries it, table-level reasoning scores a measured precision of 0.25,
> which is why this finding names the table and not a feature.

That 0.25 is the table-level baseline scored in
[benchmarks/RESULTS.md](../benchmarks/RESULTS.md), and the benchmark compares the
figure the tool quotes against the one it measures on every run, so a stale number
is reported rather than quietly repeated.

## What each check needs, in one table

| Check | Needs | Who normally writes it |
|---|---|---|
| Blast radius | The `operation` aspect on the table | dbt, Airflow, Spark, or the SDK's `report_operation` |
| Target leakage | Features with source columns, plus a column carrying the label term | `janus link` |
| Schema drift | A training-time schema snapshot on the training run | `janus link` |
| Sensitive source | Features with source columns, plus your classification URNs | Your classifier, or a human in the UI |
| Deprecated input | The model's training run, and the `deprecation` aspect | The table's own owners |
| Proxy candidate | Features with source columns, plus your protected-attribute URNs | Your classifier |

## Coverage: what a scan could not check, and why

A detector that returns nothing is saying one of two very different things: "I
checked, and it is clean", or "I had nothing to check with". Collapsing those
into one green line is the single most misleading thing a reliability tool can
do, and on a real catalog the second case is the common one: most tables carry no
`operation` aspect, most models declare no features, and almost no training run
carries a schema snapshot until somebody sets that up.

`detect/coverage.py` runs only for the checks that produced **no** finding (a
finding is itself proof the check ran), asks the graph the one cheap question that
separates "clean" from "not evaluated", and names the missing aspect and the
command that supplies it.

It distinguishes three states a reader would otherwise conflate:

- **Never linked.** Nobody has run `link` on this model.
- **De-linked.** The model carries a recorded `janus.feature_table` but declares
  no features, which is an ingest having dropped the join. The report names the
  ingest and the replay command.
- **Partially linked.** Some features resolve and some do not, which is reported
  as a gap rather than silently passing as clean.

It also names **which cap actually bound** when a walk found nothing: `truncated`
(the result cap, raise it) or `hop_capped` (an ancestor was seen and declined on
distance alone, raise the hop cap), or both.

This is the first and only import from `writeback/` into `detect/`, and it is two
pure reads: the property reader and the property name. Layer purity holds;
`detect/` still writes nothing.

## Counterfactuals: the fix, not just the fault

Every finding carries the changes that would make it stop existing, each one
sufficient on its own: cut the derivation, drop the feature, withdraw the
declaration the finding rests on, refresh the source, stop consuming it.

Where a feature reaches a label by more than one path, the finding says so and
names every edge, because cutting one of two fixes nothing.

These are derived from the same graph facts as the finding, so no language model
is involved. They are not advice: the benchmark **applies** them to the graph and
checks the finding actually clears.

## The trust score

A weighted rollup of a scan's findings into a number from 0 to 100 and a band
(`healthy`, `watch`, `at-risk`), written onto the model as a structured property.

It leads with its deductions, worst first, each naming the finding that caused it,
so the number a reader acts on carries its own evidence. Default weights:

| Deduction | Points |
|---|---|
| Upstream failure | 40 |
| Target leakage | 20 |
| Schema drift | 15 |
| Freshness lag | 15 |
| Sensitive source | 15 |
| Missing owner | 10 |
| Deprecated input | 5 |

The band caps at `watch` when the worst finding is critical or high, whatever the
point total. Points alone let a live leaking model read healthy at exactly the 70
floor while `gate` correctly blocked it.

Two things are said about this score wherever it is shown, and they matter more
than the number:

- **The weights are a stated preference ordering, not a calibrated model.** A
  team that sets `--min-trust 80` has calibrated nothing against a scale with no
  units. `janus gate --min-trust` prints that caution itself, and
  `--block-at-or-above` (which acts on a severity a detector decided) is the
  better of the two.
- **A score is only comparable to another computed the same way.** Every history
  entry carries a scoring version, bumped whenever a weight, a band boundary or
  the contributing finding set changes. Without it, a trend that drops because a
  release added a detector looks exactly like a trend that drops because somebody
  shipped a bug.

The degraded table-level mode and the proxy-candidate detector both contribute
nothing to it, on purpose.

**Literature.** Mitchell et al., *Model Cards for Model Reporting* (FAT* 2019),
for the reporting surface the score lands on.
