# Devpost text description (draft)

Copy-paste source for the submission form's "Text description" field. Kept
here rather than only in the form so it is reviewable, and so the numbers in
it can be checked against `benchmarks/RESULTS.md` rather than remembered.

Everything below is claim-checked: every number appears in a generated file in
this repository, and nothing describes a capability that is not implemented.

Credentials for the live demo are **not** in this file. They go in the
submission form's testing-instructions field.

---

## Short version (the elevator pitch, for the top of the form)

ModelGuard is the missing CI for your ML supply chain. It reads the
warehouse-to-model column-level lineage that DataHub uniquely spans, catches
the data-to-model failures that pass every existing check silently, and writes
incidents, model trust scores, impact reports, and guarding assertions back
into the graph as first-class DataHub metadata.

---

## Full description

### The problem

A credit-risk model scores live loan applications. Upstream, one Snowflake
table quietly stops refreshing. Every pipeline is green: the job ran, the
tests passed, the table exists. The model keeps serving, now on stale inputs,
and nothing in the serving path knows. The same silence covers target
leakage (a feature secretly derived from the label the model predicts, so
offline accuracy is a fiction) and training-serving schema drift (a column
retyped upstream, feeding the model values it was never trained to parse).

These are not monitoring failures. Model monitors watch predictions, and data
quality tools watch tables. Neither can see the edge between them, which is
where the failure actually lives.

### What ModelGuard does

It reads that edge. DataHub is the one place where column-level warehouse
lineage and ML metadata (features, training runs, models, deployments) live in
the same graph, so ModelGuard traverses from a failing column all the way to
the live deployment scoring traffic on it, and then writes what it found back
into the same graph:

- **An incident** on the asset that is actually wrong (the dataset, or the
  precise column), because that is where a human has to go and fix it.
- **A trust score and risk flags** on the model, as structured properties.
- **A Model Impact Report** naming the affected model, its deployment status,
  and the blast radius.
- **A guarding freshness assertion**, with its measured result, so the check
  that would have caught this exists afterward.

Four detectors, all deterministic Python:

1. **Target leakage:** traces a model's features back through column-level
   lineage to any column declared as a label.
2. **Upstream blast radius:** finds every model downstream of a table that
   breached its freshness SLA, ranked by whether it is actually serving.
3. **Training-serving schema drift:** diffs an input's current schema against
   the snapshot captured on the training run itself, the way TFX/TFDV do,
   rather than reconstructing it from version history.
4. **Trust score:** a weighted rollup of the above into one 0-100 number.

### Four ways to run it, one shared core

- `modelguard scan` for a batch audit.
- `modelguard watch` to poll and act the moment something changes, the
  long-running mode behind the live demo.
- `modelguard gate` for CI: the preventive half, judging a pull request
  against a policy and answering in an exit code, so a leaking model fails the
  build instead of shipping.
- `modelguard-mcp` for a conversational MCP client: ask "is credit_risk_v3
  leaking?" in plain language.

All four call the identical detect-reason-write core.

### The design law: the LLM never decides

Detection is deterministic Python, end to end. The language model only words
the incident description and the report's assessment. It never decides whether
a finding exists, never sets a severity, never composes GraphQL, and nothing
it emits reaches a deduplication key or a URN. ModelGuard runs completely
without an LLM configured, writing template prose instead, and that is the
out-of-the-box path.

This matters for a reliability tool. A finding a model hallucinated is worse
than no finding, and a graph full of them destroys the trust the tool exists
to provide.

### Measured, not asserted

`benchmarks/RESULTS.md` scores the detectors against a live DataHub, never
against fixtures, which would only measure the fixtures. Across 14 trials,
precision 1.00, recall 1.00, false-positive rate 0.00, against targets of
0.90, 0.95 and 0.05.

The freshness sweep deliberately walks the lag across the SLA boundary rather
than only planting an obvious 30-hour failure, because the boundary is where a
detector actually goes wrong. Changing one comparison from `>` to `>=` is
caught by the trial sitting exactly on the SLA.

The headline claim, that column-level lineage is what makes this work, is also
a number rather than an assertion. Scored per feature, on the same graph:

| Approach | Precision | Recall | Still alerting after the fix |
|---|---|---|---|
| ModelGuard (column-level lineage) | 1.00 | 1.00 | 0 features |
| Table-level lineage | 0.25 | 1.00 | 2 features |
| Table quality checks, no lineage | - | 0.00 | 0 features |

Table-level lineage has perfect recall: it does catch the leak. It just
cannot say which of the two features carries it, and having never seen the
column edge, it cannot see that edge being removed either, so it keeps
alerting on a graph somebody already fixed. That last column is what gets a
reliability tool switched off.

### Technologies

Python 3.11, the DataHub Python SDK (`acryl-datahub`), LangGraph for the
human-approval agent, Typer, Pydantic, MCP. Deterministic detection needs no
LLM; when one is configured for prose, the provider is pluggable (Anthropic,
OpenAI, or Google) behind a single module, and no vendor SDK is imported
anywhere else.

Packaged as a `pip`-installable distribution, a non-root Docker image, a Helm
chart for the long-running watch mode, and a reusable GitHub Action for the CI
gate.

### Data used

No real or proprietary data. `modelguard-seed` builds a synthetic ecommerce
lending graph (a `loans_raw` source table, a `customer_features` feature
table, ML features, a training run, and a `credit_risk_v3` model behind a live
deployment), because DataHub's own sample datapacks contain no ML supply chain
to guard. `modelguard-scenario` plants and reverts the three failures on
demand, which is what makes the demo reproducible and the benchmark scoreable.

### Given back to the DataHub ecosystem

- **`datahub-ml-guard` skill**, for the DataHub skills registry: a thin
  wrapper over this repository's tested detection engine rather than an LLM
  asked to eyeball a lineage graph.
- **A `raise_incident` MCP tool** for `acryldata/mcp-server-datahub`, which
  today has no incident-write tool, with an RFC for first-class ML incidents.
- **Thirteen reproducible bug reports and documentation gaps** found while
  building, each with a repro and a workaround.

One of those is a genuine finding about the metadata model: DataHub refuses an
incident on an `mlModel` entity, which is why ModelGuard attaches findings to
the data asset and carries model-level risk as structured properties. That
constraint shaped the design and is written up as an RFC rather than worked
around silently.
