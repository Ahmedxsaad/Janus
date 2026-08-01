# ModelGuard on a real ML project

Everything else in this repository is measured against a graph
`modelguard-seed` built. A seeded graph is exactly the one where the links the
detectors read already exist, which is precisely the assumption a real project
breaks. So this is the same product run against an ordinary stack that knows
nothing about it, and a record of what that found (D-074, 2026-08-01).

Nothing here is a fixture. It is a public dataset, three dbt models, one
scikit-learn script, and DataHub's own ingestion sources.

| Piece | What it is |
|---|---|
| `load_raw.py` | Lands IBM's public Telco customer-churn extract (7043 customers) in postgres, the way an EL job would |
| `churn_analytics/` | A dbt project: a staging model, a feature table, a label table |
| `ml/train_churn.py` | Trains a logistic-regression churn model and tracks it in MLflow |
| `ingestion/` | Three DataHub recipes: postgres, dbt, mlflow |

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
2. **One `modelguard link` call closes the gap**, and the leak is caught on the
   real graph, with the derivation quoted from DataHub's own lineage:
   `contract_renewed_flag <- churn`. `modelguard gate` exits 1 on it.
3. **Fixing it closes the incident.** Delete the column, rebuild with dbt,
   re-ingest, re-link, rescan: the finding is gone and the incident resolves
   itself. Closing that loop needed a fix (D-069's known gap: a leak fixed by
   deleting the column left the incident open forever, because reconciliation
   walked only the model's current features and a deleted column is in none).
4. **Re-ingestion un-links every model.** DataHub's mlflow source upserts the
   whole `mlModelProperties` aspect, dropping the features `link` wrote. Filed
   as [feedback #14](../../docs/most-valuable-feedback.md). ModelGuard records
   what `link` was told as structured properties, an aspect ingestion does not
   touch, so replaying it is `modelguard link --model <name>`.

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

modelguard inventory                    # telco_churn_1: not checked
modelguard link --model telco_churn_1 \
  --features analytics.customer_features \
  --label-table analytics.customer_labels \
  --label-column churned --exclude customer_id
modelguard scan --model telco_churn_1   # the leak, with its derivation
```

Then play the fix: delete the `contract_renewed_flag` line from
`customer_features.sql`, drop it from `NUMERIC` in `ml/train_churn.py`, and
repeat from `dbt run`. The AUC falls to 0.83 and the incident closes.
