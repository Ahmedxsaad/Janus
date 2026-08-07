# The link: joining a model to its columns

This is the piece of Janus that does not exist anywhere else, and the piece every
detector depends on. It gets its own page because on a real catalog it is the
whole difference between a tool that works and a tool that reports "not checked"
forever.

## The gap it fills

DataHub holds two graphs. Its dbt, Spark and warehouse sources record excellent
**column-level lineage between tables**. Its MLflow, SageMaker, Vertex AI and
Databricks sources record the **ML supply chain**: training dataset, training
run, model, deployment, feature tables, features.

Nothing joins them.

This was verified end to end on an ordinary stack rather than assumed: postgres
holding the warehouse, dbt building the feature and label tables, MLflow tracking
the training run, and DataHub's own postgres, dbt and mlflow sources run against
all three. The result is column-level lineage between the tables, and an
`mlModel` that knows nothing at all: **no features, no inputs on its training
run, no link to a single column.** That stack is committed at
[examples/real-project/](../examples/real-project) so anyone can reproduce it.

A detector that walks from a feature back to its source column has nowhere to
start. That is why a fresh catalog reports mostly "not checked", and it is not a
failure of the detectors.

## What `link` writes

```bash
janus link \
  --model churn_model \
  --features analytics.customer_features \
  --label-table analytics.customer_labels \
  --label-column churned \
  --exclude customer_id
```

Three things, from facts the caller already knows at training time:

1. **The model's features**, one `mlFeature` per column of the feature table,
   each carrying the exact source column it came from in
   `janus.source_column`. Column-level lineage in DataHub is dataset-to-dataset
   only, and feature-to-dataset lineage goes through the ML sources aspect, which
   is dataset-granular. The custom property is where a feature's exact column
   lives, and it is where every detector starts its walk.
2. **The label declaration**, as a glossary term on the label column, propagated
   up the label's own lineage so a feature derived from the *same ancestor* as
   the label is caught rather than missed.
3. **The training run's inputs, and the schema those inputs had at training
   time**, which is the snapshot the drift detector diffs against. A model with
   several inputs merges snapshots rather than overwriting on a second link.

Everything is read-merge-emit, so re-running after a retrain converges instead of
duplicating.

## Four ways to get the arguments

### 1. Type them

The command above. One call from the script that trains the model.

### 2. Let Janus infer them: `link --infer`

Most of those arguments are already somewhere in DataHub. `--infer` reads them
out and proposes the exact command a person would have typed, **writing nothing
until you answer**.

It tries four routes, in descending order of how much the answer is *declared*
rather than guessed, and it tells you which one answered:

| Route | Source | Confidence |
|---|---|---|
| 1 | The inputs the training run recorded (`dataProcessInstanceInput`) | A declaration. Highest by a distance |
| 2 | A run parameter naming a table, where DataHub's MLflow source puts MLflow params | A declaration, one step removed |
| 3 | A dataset the catalog already declares upstream of the model | Emitted by Spark and some sources |
| 4 | A shortlist of nearby tables, by name | Explicitly not an answer |

Output names its own certainty, line by line:

```
Inferred from the graph:
  feature table: the only input recorded on churn_model's training run(s), from dataProcessInstanceInput
  label column: churned matches a known label name (JANUS_LABEL_COLUMN_NAMES). This one is a guess: check it
  excluded columns: customer_id, from the schema's own key declarations (primaryKeys, isPartOfKey,
    isPartitioningKey) and the label itself

Proposed:
janus link --model churn_model --features analytics.customer_features \
  --label-column churned --exclude customer_id

Declare this? [Y/n]
```

There is no language model in any of that. Three rules keep it honest:

- **A column already carrying the label term was declared, not guessed**, and the
  proposal says so, including when the declaration is on a column the feature
  table descends from, which is where a warehouse usually keeps its labels.
- **Where nothing in the graph names a label, it refuses to invent one** and asks
  for `--label-column`. A wrong label makes every leakage verdict wrong in both
  directions.
- **Exclusions come only from the warehouse's own key declarations**
  (`primaryKeys`, `isPartOfKey`, `isPartitioningKey`), never from column names
  that look like identifiers. `customer_id` is usually a join key and `score_id`
  is usually a feature, and no rule over names tells them apart.

A plain MLflow ingest often carries none of the first three routes. Verified
live: it produces a model whose training run records no inputs at all. `--infer`
then says exactly that, names what would fix it, and lists the nearest tables
rather than refusing:

```
  feature table: NOT FOUND. churn_model's training run records no inputs and no dataset
    parameter, which is the usual state after an mlflow ingest, and nothing in the catalog
    declares a dataset upstream of it. Pass --features <table>, or log the training table as
    an MLflow run parameter (janus_features=...) and re-ingest so this can be read
    rather than guessed
```

One line in the training script makes the next ingest self-describing:

```python
mlflow.log_param("janus_features", "analytics.customer_features")
```

### 3. Import it from a declaration you already maintain: `link --from`

A Feast repo and a dbt semantic model already say which column each feature is
read from, in a file the training pipeline reads and the team keeps correct.

```bash
janus link --model churn_model --from feast --repo ./feature_repo
janus link --model churn_model --from dbt   --repo ./churn_analytics
```

**The Feast reader** parses the repo's declarations through
`feast.repo_operations.parse_repo`, which imports the repo's own Python modules
and returns the declared objects. It does not open the registry, contact an
online store, or run `feast apply`. It reads: a feature view's source and schema,
the source's `field_mapping` (the warehouse column behind a renamed feature), a
label view naming the predicted column, and a feature service naming the exact
set one model trains on. It asks a source for its relation through
`get_table_query_string` when the source exposes it nowhere else, which is every
SQL contrib source. Needs the `feast` extra.

**The dbt reader** parses `target/manifest.json` with the standard library.
**No dependency at all**, which also means it works against a `manifest.json`
somebody emailed you, with no dbt on the machine. It reads each semantic model's
`node_relation`, entities, dimensions and measures, where `expr` names the source
column. Verified against dbt-core 1.12.0 and manifest schema v12; the fields used
are stable across v9 to v12, and a manifest whose schema version is older is read
anyway with the version quoted in the reasons, so a surprise is attributable.

The output is the same proposal `--infer` prints, with the declaration each line
came from:

```
Read from the feast declaration:
  read 'churn_model_v1' from the Feast repo at ./feature_repo
  feature table: warehouse.analytics.customer_features, the batch source of 'customer_features'
  features: 3 declared, of which 1 name a warehouse column different from the feature
  label: churned of warehouse.analytics.customer_labels, from label view 'churn_label'
  not features: customer_id, event_timestamp (entity join keys and event timestamps)

Features 'churn_model_v1' declares:
  tenure <- tenure_months  (feature view 'customer_features')
```

That first line is the case a name match gets wrong: the feature is `tenure`, the
column is `tenure_months`, and only the declaration knows.

Four rules the adapters hold to:

- **Offline and read-only.** An adapter parses a file on disk. It never connects
  to a vendor's service, never writes to DataHub, and never decides anything the
  declaration does not say.
- **What it could not read is reported as unread, never guessed at.** A dbt
  measure whose `expr` is an expression (`amount * rate`, a `case when`) names no
  single source column, so it is reported rather than parsed. Guessing which
  column an expression is "really" about is how a detector ends up walking the
  lineage of a column that does not exist.
- **A column the table does not have stops the import** rather than linking the
  rest. A half-declared model is one whose unchecked columns nobody would ever
  hear about.
- **Exclusions are not the adapter's to return.** Exclusion is the complement of
  the declaration against the table's real schema, and the schema lives in
  DataHub. `--select` picks between several declarations in one repo.

### 4. Replay it: `link --all`

The arguments are recorded on the model in structured properties, so the replay
needs no arguments at all and covers every linked model at once. A model nobody
has linked is skipped rather than guessed at, so it is safe to run on a schedule.

## Keeping the link alive: the adoption cliff

**DataHub's MLflow source upserts the whole `mlModelProperties` aspect on every
ingest, which drops the `mlFeatures` that `link` attached.**

From that moment the leakage, sensitive-source and proxy checks have nothing to
walk, and each reports "not evaluated" on a model that was fully checked
yesterday. Nothing errors. Nothing is logged. The tool simply stops working on
that model until a human remembers.

This is the single most important operational fact about Janus, and it is why
three separate mechanisms exist:

1. **`link` writes its arguments to structured properties**, not to
   `mlModelProperties`. Structured properties are a separate aspect that
   ingestion does not touch, so the declaration outlives the ingest and can be
   replayed.
2. **`link --all` is the manual replay**, and the `janus-watch` Helm chart ships
   it as a CronJob (`link.enabled=true`) for the warehouse with one ingestion
   window.
3. **`watch --events` is the automatic one.** `mcl.py` consumes DataHub's own
   `MetadataChangeLog`, and `reconcile.py` watches for the exact aspect write
   that causes the damage and re-applies the recorded arguments, catalog-wide.

That third one does something polling structurally cannot. A poll only ever
notices the failure on the target it was pointed at; the failure is catalog-wide,
so it needs a catalog-wide signal, and the change log is the only one there is.

Verified the way it should be: a model ingested twice through DataHub's own
MLflow source, with the features surviving the second ingest and no human
touching anything.

**Only links a human already confirmed are ever replayed.** An inferred join
looks identical to a confirmed one in the graph, and replaying a guess would make
every detector downstream confident about the wrong columns.

Polling remains the default and needs no broker. `watch --events` is an opt-in
extra (`[kafka]`), and it was deliberately not built on DataHub's
`datahub-actions` framework, which would put a second configuration surface next
to `env.py` for the same records.

## Reported upstream

The MLflow overwrite is filed as feedback item 14, alongside a related one: a dbt
semantic model silently overwrites the dbt model it is built on. See
[16-most-valuable-feedback.md](16-most-valuable-feedback.md).

## From a training script

The link belongs in the script that trains the model, because that is the only
moment when the feature table, the label column and the training-time schema are
all known:

```python
import mlflow
from janus import link_model, scan_model

FEATURE_TABLE = "analytics.customer_features"
mlflow.log_param("janus_features", FEATURE_TABLE)

link_model(model="churn_model", features=FEATURE_TABLE,
           label_column="churned", exclude=["customer_id"])

report = scan_model(model="churn_model", dry_run=True)
if not report.clean:
    raise SystemExit(f"{len(report.writes)} finding(s) before this model ships")
```

Declared here, the link is re-declared by the same run that produces the model,
so the next training run repairs it whether or not anybody noticed.

`link_model` and `scan_model` are the entire supported Python surface, along with
the two errors they raise: `LinkError`, and `TableResolutionError` for a name
matching no dataset or more than one (which is what a relation named the way the
warehouse names it usually does). They are thin wrappers over exactly the
functions the CLI calls.

One resolution detail worth knowing: `resolve_table` accepts any dotted suffix of
a dataset's name, because a declaration names a relation the way the warehouse
does (`analytics.customer_features`) while DataHub names the dataset with the
database in front. Without that, every imported declaration resolved against
nothing.
