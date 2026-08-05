"""The same join a second time, in the notation a feature store uses.

`churn_analytics/` declares which columns are features through the dbt semantic
layer; this declares it through Feast. Both are ordinary files for the stack they
belong to, and `janus link --from dbt` and `--from feast` each read one of
them instead of asking a human to retype it (T-05, T-06).

Two declarations of one join is not duplication for its own sake: this repo is
how the two adapters get measured against the *ingested* graph rather than
against a fixture, and either route has to end at the same seven columns of
`analytics.customer_features` or one of them is wrong (T-14).

Nothing in `ml/train_churn.py` reads this file: that script queries the warehouse
directly, as it did before any of this existed. Reading the training set through
Feast would be a different example about a different problem.
"""

from datetime import timedelta

from feast import Entity, FeatureService, FeatureView, Field
from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import (
    PostgreSQLSource,
)
from feast.labeling.label_view import LabelView
from feast.types import Float32, Int64, String
from feast.value_type import ValueType

customer = Entity(
    name="customer",
    join_keys=["customer_id"],
    value_type=ValueType.STRING,
    description="One telco customer.",
)

customer_features_source = PostgreSQLSource(
    name="customer_features_source",
    table="analytics.customer_features",
    # The warehouse column is tenure_months and the feature is called tenure.
    # A name match would look for a column this table does not have; reading the
    # declaration is how the right one is found.
    field_mapping={"tenure_months": "tenure"},
)

customer_labels_source = PostgreSQLSource(
    name="customer_labels_source",
    table="analytics.customer_labels",
)

customer_features = FeatureView(
    name="customer_features",
    entities=[customer],
    ttl=timedelta(days=1),
    schema=[
        Field(name="tenure", dtype=Int64),
        Field(name="monthly_charges", dtype=Float32),
        Field(name="total_charges", dtype=Float32),
        Field(name="contract_type", dtype=String),
        Field(name="internet_service", dtype=String),
        Field(name="payment_method", dtype=String),
        # Declared like any other feature, because that is what whoever built it
        # believed it was. The declaration is faithful and the column is the
        # mistake; importing the declaration is what puts it where a detector can
        # say so.
        Field(name="contract_renewed_flag", dtype=Int64),
    ],
    source=customer_features_source,
)

churn_label = LabelView(
    name="churn_label",
    entities=[customer],
    schema=[Field(name="churned", dtype=Int64)],
    source=customer_labels_source,
)

telco_churn = FeatureService(
    name="telco_churn",
    features=[customer_features, churn_label],
    description="What the telco churn model is trained on: the features and the label.",
)
