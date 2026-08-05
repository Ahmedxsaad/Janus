"""Train the churn model the way a data scientist actually would.

Pulls the dbt feature table straight out of the warehouse, joins the labels,
fits a model, and logs the run to MLflow. Deliberately ordinary: this is the
script that produces the metadata everything downstream has to work with, and
its ordinariness is the point. Nothing in it knows Janus exists.
"""

import os

import mlflow
import mlflow.sklearn
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import create_engine

# No default: a warehouse URL identifies a system, and a fallback in a
# committed file is somebody else's database.
ENGINE = create_engine(os.environ["WAREHOUSE_URL"])

NUMERIC = ["tenure_months", "monthly_charges", "total_charges", "contract_renewed_flag"]
CATEGORICAL = ["contract_type", "internet_service", "payment_method"]

features = pd.read_sql("select * from analytics.customer_features", ENGINE)
labels = pd.read_sql("select * from analytics.customer_labels", ENGINE)
data = features.merge(labels, on="customer_id").dropna()

x_train, x_test, y_train, y_test = train_test_split(
    data[NUMERIC + CATEGORICAL], data["churned"], test_size=0.2, random_state=42
)

model = Pipeline(
    [
        (
            "prep",
            ColumnTransformer(
                [
                    ("num", StandardScaler(), NUMERIC),
                    ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
                ]
            ),
        ),
        ("clf", LogisticRegression(max_iter=1000)),
    ]
)

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment("telco_churn")

with mlflow.start_run(run_name="churn_baseline") as run:
    model.fit(x_train, y_train)
    auc = roc_auc_score(y_test, model.predict_proba(x_test)[:, 1])

    mlflow.log_params({"model": "logistic_regression", "max_iter": 1000})
    mlflow.log_metric("roc_auc", auc)
    mlflow.sklearn.log_model(model, name="model", registered_model_name="telco_churn")

    print(f"run_id={run.info.run_id} roc_auc={auc:.4f}")
