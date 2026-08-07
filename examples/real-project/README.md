# Janus on a real ML project

Everything else in this repository is measured against a graph
`janus-seed` built. A seeded graph is exactly the one where the links the
detectors read already exist, which is precisely the assumption a real project
breaks. So this is the same product run against an ordinary stack that knows
nothing about it, and a record of what that found (2026-08-01).

It is also a benchmark target now, not only a validation exercise: Janus-Bench
scores the detectors against the graph **this** stack's ingestion produced, in its
own section of `benchmarks/RESULTS.md`, never merged with the seeded numbers
. Stand the stack up as below and `python -m benchmarks.run_bench`
fills that section in; leave it down and the section says it was not run.

Nothing here is a fixture. It is a public dataset, four dbt models, one
scikit-learn script, two feature declarations, and DataHub's own ingestion
sources.

| Piece | What it is |
|---|---|
| `load_raw.py` | Lands IBM's public Telco customer-churn extract (7043 customers) in postgres, the way an EL job would |
| `churn_analytics/` | A dbt project: a staging model, a feature table, a label table, a semantic model over the features, and the time spine dbt requires beside one |
| `feature_repo/` | A Feast repo declaring the same join a second way: feature view, label view, feature service |
| `ml/train_churn.py` | Trains a logistic-regression churn model and tracks it in MLflow |
| `ingestion/` | Three DataHub recipes: postgres, dbt, mlflow |

Two declarations of one join is the point rather than duplication: `janus
link --from dbt` and `--from feast` each read one of them, and the benchmark
checks that both arrive at the same seven columns of the same ingested table.

## The mistake, and why it is the realistic one

`customer_features.sql` builds `contract_renewed_flag` as
`case when churn = 'No' then 1 else 0 end`. An analyst writes that meaning
"account health"; it is the label inverted. The model trains on it and scores:

| Model | ROC AUC |
|---|---|
| With `contract_renewed_flag` | **1.0000** |
| With it removed | **0.8322** |

That gap is the cost of the bug, measured on this data, not asserted. A perfect
offline score is the tell, and it is a tell nobody sees in time, because
everything downstream of it looks like a success.

## What the run found

Verified live on the demo VM, 2026-08-01, against DataHub GMS 1.5.0.6 with
`acryl-datahub` 1.6.0.13.

1. **DataHub ingests both halves and joins neither.** The postgres and dbt
   sources produce real column-level lineage:
   `customer_features.contract_renewed_flag` -> `stg_customers.churn` ->
   `raw.telco_customers.churn`, all of it derived from the compiled SQL. The
   mlflow source produces an `mlModel` with its training run attached and
   nothing else: no features, no inputs on the run, no link to a single column.
   A scan of that model can only report that it had nothing to check, which is
   what it now says rather than reporting the model healthy.
2. **One `janus link` call closes the gap**, and the leak is caught on the
   real graph, with the derivation quoted from DataHub's own lineage:
   `contract_renewed_flag <- churn`. `janus gate` exits 1 on it.
3. **Fixing it closes the incident.** Delete the column, rebuild with dbt,
   re-ingest, re-link, rescan: the finding is gone and the incident resolves
   itself. Closing that loop needed a fix (a known gap: a leak fixed by
   deleting the column left the incident open forever, because reconciliation
   walked only the model's current features and a deleted column is in none).
4. **Re-ingestion un-links every model.** DataHub's mlflow source upserts the
   whole `mlModelProperties` aspect, dropping the features `link` wrote. Filed
   as [feedback #14](../../docs/16-most-valuable-feedback.md). Janus records
   what `link` was told as structured properties, an aspect ingestion does not
   touch, so replaying it is `janus link --all`: no arguments, every linked
   model, safe on a schedule.

## What the second run found (2026-08-04)

Promoting this from a validation exercise to a benchmark target meant adding the
two declarations and running the whole stack again. Three things fell out of
that, each measured rather than reasoned about:

5. **A semantic model named after its dbt model overwrites it.** DataHub's dbt
   source keys a semantic model as a dataset by its *name*, so declaring
   `semantic_models: - name: customer_features` over the model
   `customer_features` lands both on one URN: the feature table then carries the
   semantic model's entities as its columns (`customer` instead of
   `customer_id`) and loses the column-level lineage the compiled SQL produced,
   which is the one thing the leak detector reads. The scan went quiet on a
   graph that still held the leak. The semantic model here is named `customers`
   for that reason, and the collision is filed as
   [feedback #15](../../docs/16-most-valuable-feedback.md).
6. **A declared relation names two datasets.** The dbt source emits a
   dbt-platform dataset beside the warehouse one, both named after the same
   relation, so `--features analytics.customer_features` matches two. `link`
   refuses and prints both, which is right; pass the warehouse URN, which is the
   table the training script queried.
7. **The table-level degraded mode has nothing to stand on here.** It falls
   back to the tables a model trains on, and this model has none: the mlflow
   source records no inputs on the training run and emits no lineage from the
   model to a dataset. So on an ingested graph the honest report is the one in
   point 1 (nothing was checked, and here is what each check was missing), not a
   weaker answer. The benchmark measures that as a number rather than asserting
   it.

## Running it yourself

Needs a DataHub (the Quickstart is fine), a postgres, and an MLflow tracking
server. Two virtualenvs, because a real team has them separately: dbt and
training in one, the DataHub CLI in the other.

```bash
export WAREHOUSE_HOST=localhost WAREHOUSE_PORT=5433 WAREHOUSE_DB=warehouse
export WAREHOUSE_USER=warehouse WAREHOUSE_PASSWORD=<yours>
export WAREHOUSE_URL="postgresql+psycopg2://$WAREHOUSE_USER:$WAREHOUSE_PASSWORD@$WAREHOUSE_HOST:$WAREHOUSE_PORT/$WAREHOUSE_DB"
export MLFLOW_TRACKING_URI=http://localhost:5000
export DATAHUB_GMS_URL=http://localhost:8080

curl -sSO https://raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv
TELCO_CSV=Telco-Customer-Churn.csv python load_raw.py

cd churn_analytics && DBT_PROFILES_DIR=$PWD dbt run && DBT_PROFILES_DIR=$PWD dbt docs generate && cd ..
python ml/train_churn.py

cd ingestion && for r in postgres dbt mlflow; do datahub ingest -c $r.yml; done && cd ..

janus inventory                    # telco_churn_1: not checked

# The feature table is spelled by two datasets on a dbt stack (the warehouse
# table and its dbt sibling), so name the warehouse one. `link` prints both and
# refuses rather than choosing, which is what you want it to do.
FEATURES='urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.analytics.customer_features,PROD)'
LABELS='urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.analytics.customer_labels,PROD)'
janus link --model telco_churn_1 \
  --features "$FEATURES" --label-table "$LABELS" \
  --label-column churned --exclude customer_id
janus scan --model telco_churn_1   # the leak, with its derivation
```

Or import the join instead of typing it, from either declaration this project
already carries (`--from` proposes and writes nothing until you confirm):

```bash
janus link --model telco_churn_1 --from feast --repo feature_repo
janus link --model telco_churn_1 --from dbt --repo churn_analytics \
  --label-table "$LABELS" --label-column churned
```

On a schedule, the last three become `janus link --all && janus scan
--all-models`, which is the post-ingestion step this stack actually needs.

Then play the fix: delete the `contract_renewed_flag` line from
`customer_features.sql`, drop it from `NUMERIC` in `ml/train_churn.py`, and
repeat from `dbt run`. The AUC falls to 0.83 and the incident closes.
