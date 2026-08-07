# What Janus is, and the problem it solves

Janus is a data-to-model reliability agent. It reads DataHub's metadata graph,
finds the failures that break machine learning models silently, and writes what
it finds back into the catalog as incidents, trust scores, reports and guarding
assertions.

This page explains the problem and the shape of the answer. How it is built is
[02-architecture.md](02-architecture.md); what each check actually does is
[04-detectors.md](04-detectors.md); how to run it is
[docs.ahmedxsaad.me](https://docs.ahmedxsaad.me).

## The failure

A credit-risk model scores live loan applications. Upstream, one warehouse table
quietly stops refreshing. Every pipeline is green: the job ran, the tests passed,
the table exists, the row count is normal. The model keeps serving, now on stale
inputs, and nothing in the serving path knows.

The same silence covers two siblings:

- **Target leakage.** A feature is derived, directly or through several hops,
  from the column holding the label the model predicts. Offline accuracy is
  excellent and it is a fiction. The model collapses the day it meets data that
  does not carry the answer.
- **Training and serving schema drift.** A column is retyped or renamed upstream
  after the model was trained. The model is now fed values it was never trained
  to parse, and no error is raised anywhere.

None of these are monitoring failures in the usual sense. Model monitors watch
predictions. Data quality tools watch tables. Neither can see the edge between
them, which is exactly where the failure lives.

## Why this has no tooling

Software engineering solved the analogous problem with continuous integration:
every change runs tests before it ships. The machine learning data supply chain
has no equivalent. Data quality tooling stops at the warehouse edge. Machine
learning monitoring starts at the model. The dangerous middle, where features are
derived from tables and a schema change three hops upstream reaches a live
endpoint, belongs to neither.

The reason is structural rather than a gap somebody forgot to fill: answering the
question requires holding two graphs at once, and most systems hold one.

## Why DataHub, specifically

DataHub is the one place that already holds both:

- **Column-level warehouse lineage.** Its dbt, Spark and warehouse sources record
  which column each column was computed from, with the SQL that did it.
- **Machine learning metadata.** Its MLflow, SageMaker, Vertex AI and Databricks
  sources record the chain `training dataset -> training run -> model ->
  deployment`, plus feature tables and features.

Both graphs are in one metadata store, queryable through one API. Nothing else in
the ecosystem has that.

## The gap DataHub still leaves, and what Janus does about it

The two graphs sit side by side and **nothing joins them**. An MLflow ingest
produces a model whose features are empty and whose training run often records no
inputs at all. A dbt ingest produces excellent column-level lineage between
tables. There is no edge from a model to a single column, so a detector that
walks from a feature back to its source column has nowhere to start.

Janus writes that join. `janus link` declares, for one model, which feature came
from which warehouse column, which column carries the label, and what the input
schema looked like at training time. It can read that declaration from a Feast
repository or a dbt semantic model, infer it from what the graph already holds,
or take it from arguments. Once the join exists, every check below becomes a
graph traversal.

The join is the product's one genuinely novel move, and it is what the
[benchmark](08-evaluation.md) measures: the same graph, the same ground truth, read
three ways, scored per feature.

## What it checks

Seven checks, described in full in [04-detectors.md](04-detectors.md):

| Check | The question it answers |
|---|---|
| Blast radius | This table is stale. Which live models are scoring on it right now? |
| Target leakage | Which of this model's features descends from the label column? |
| Schema drift | Which training-time columns have been retyped or dropped since? |
| Sensitive source | Which feature descends from a column somebody classified as restricted? |
| Deprecated input | Is this model trained on a table its owners have marked for removal? |
| Proxy candidate | Does a feature share an ancestor with a protected attribute? |
| Table-level risk (degraded mode) | No feature is declared yet. What can the model's *tables* alone say, before anybody runs `janus link`? |

Three of them ask about correctness. Three ask about something the
organization already decided elsewhere in DataHub and that nothing today joins
back to a model. The seventh is not a fourth category: it runs in place of all
six above, at a lower and honestly measured precision, for the common
out-of-the-box case where a model has no declared features yet.

## What it writes

Every run's output is a write to the graph, not a chat reply. A scan can produce:

- An **incident** on the offending dataset or column, with the derivation chain
  as evidence.
- A **model-at-risk tag** and structured **risk properties** on the model.
- A **trust score** and band, with the deductions that produced them.
- A **Model Impact Report** as a DataHub document, attached to the model.
- A **guarding assertion** with its measured result, so the same failure is
  caught by the catalog next time.
- A **dataProcessInstance** recording the scan itself: what it read, what it
  wrote, how long it took. The agent is an entity in the graph it guards.

Reruns converge. See [02-architecture.md](02-architecture.md) on idempotency.

## Who it is for

- A **platform or data engineer** who owns the warehouse and gets paged when a
  model misbehaves for reasons that turn out to be upstream.
- An **ML engineer** who needs to know, before promoting a model, whether any of
  its features leak and whether its inputs still match training.
- A **platform lead** who has to report a number upward, which is what
  `janus coverage` and `janus finops` produce.
- A **governance or risk function** that needs evidence rather than assurances,
  which is what the model card, evidence pack and NIST AI RMF crosswalk produce.

## What it deliberately is not

Janus composes DataHub's shipped features rather than reimplementing them. It is
not a text-to-SQL assistant (DataHub's Analytics Agent does that), not an
auto-documentation or PII tagging steward (the `datahub-enrich` skill does that),
not a generic incident triage chatbot, and not a dashboard over metadata (the
Data Health Dashboard is native). It also never connects to a warehouse and never
issues a query against a table: see [10-security.md](10-security.md).

Where it stops, and where it is weak, is written down in
[08-evaluation.md](08-evaluation.md).
