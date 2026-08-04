"""A Feast repository that already declares the join ModelGuard needs.

Nothing here is written for ModelGuard. It is an ordinary feature repo: a
customer entity, one feature view over a warehouse table, the label declared as
a label view over another, and a feature service naming the pair a model trains
on.

`modelguard link --from feast --repo examples/feature-repo/feature_repo` reads
exactly this file and proposes the link a human would otherwise have typed by
hand, column by column.
"""

from datetime import timedelta

from feast import BigQuerySource, Entity, FeatureService, FeatureView, Field
from feast.labeling.label_view import LabelView
from feast.types import Float32, Int64
from feast.value_type import ValueType

customer = Entity(
    name="customer",
    join_keys=["customer_id"],
    value_type=ValueType.STRING,
    description="One retail customer.",
)

customer_features_source = BigQuerySource(
    name="customer_features_source",
    table="warehouse.analytics.customer_features",
    timestamp_field="event_timestamp",
    # The warehouse column is tenure_months; the feature is called tenure. This
    # mapping is the reason an adapter beats a name match: the feature name
    # alone would send a detector looking for a column the table does not have.
    field_mapping={"tenure_months": "tenure"},
)

customer_labels_source = BigQuerySource(
    name="customer_labels_source",
    table="warehouse.analytics.customer_labels",
    timestamp_field="event_timestamp",
)

customer_features = FeatureView(
    name="customer_features",
    entities=[customer],
    ttl=timedelta(days=1),
    schema=[
        Field(name="tenure", dtype=Int64),
        Field(name="monthly_charges", dtype=Float32),
        Field(name="support_calls", dtype=Int64),
    ],
    source=customer_features_source,
)

churn_label = LabelView(
    name="churn_label",
    entities=[customer],
    schema=[Field(name="churned", dtype=Int64)],
    source=customer_labels_source,
)

churn_model_v1 = FeatureService(
    name="churn_model_v1",
    features=[customer_features, churn_label],
    description="What churn_model_v1 is trained on: three features and the label.",
)
