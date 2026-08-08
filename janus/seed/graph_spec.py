"""Deterministic specification of the ML supply chain Janus seeds.

Every URN and value here is fixed. Seeding twice converges on the same graph
(docs/02-architecture.md), and detectors, scenarios, tests, and the benchmark all
resolve the same entities without passing URNs around.

The shape mirrors a real credit-risk stack::

    loans_raw  --(column lineage)-->  customer_features  --(sources)-->  MLFeature
                                                                             |
                                              MLFeatureTable <--------------- |
                                                                             v
                        TrainingRun ---> MLModel(credit_risk_v3) ---> MLModelDeployment

Two columns matter for the detectors that arrive in later phases:

* ``loans_raw.default_status`` is the **label source**: the ground truth a credit
  model is trained to predict.
* ``customer_features.prior_default_flag`` is derived from it by column lineage,
  and is exposed as the feature ``prior_default_flag``. A model consuming that
  feature is reading its own label. That is the planted target leakage (P1).

Column granularity for features
-------------------------------
``MLFeatureProperties.sources`` declares ``entityTypes: [dataset]`` in the
metadata model, so a feature may point at the dataset it derives from but not at
a specific column. Pointing it at a ``schemaField`` URN would create a dangling
relationship. The exact column is therefore recorded in the feature's
``customProperties`` under :data:`SOURCE_COLUMN_PROPERTY`, which gives the leakage
detector the precise column to start its upstream traversal from.
"""

from __future__ import annotations

from dataclasses import dataclass

from datahub.metadata.urns import (
    DataProcessInstanceUrn,
    DatasetUrn,
    MlFeatureTableUrn,
    MlFeatureUrn,
    MlModelDeploymentUrn,
    MlModelGroupUrn,
    MlModelUrn,
    MlPrimaryKeyUrn,
    SchemaFieldUrn,
)

from janus.config import ScanConfig

WAREHOUSE_PLATFORM = "snowflake"
ML_PLATFORM = "mlflow"

#: Custom property on an MLFeature naming the exact source column it derives from.
#: Bridges the dataset-only granularity of MLFeatureProperties.sources.
SOURCE_COLUMN_PROPERTY = "janus.source_column"

#: Custom property on the training run holding the input schema fingerprint the
#: model was trained against. Derived from config so the seeder writes the very
#: property the schema-drift detector reads, and the two cannot drift apart.
TRAINING_SCHEMA_PROPERTY = ScanConfig().training_schema_property

SOURCE_TABLE = "ecommerce.public.loans_raw"
FEATURE_TABLE = "ecommerce.public.customer_features"

FEATURE_NAMESPACE = "credit_risk"
MODEL_GROUP_ID = "credit_risk_models"
MODEL_ID = "credit_risk_v3"
TRAINING_RUN_ID = "credit_risk_v3_run"
DEPLOYMENT_ID = "credit_risk_v3_prod"

#: The column holding the ground truth the model predicts. Any feature whose
#: upstream column cone reaches this column is target leakage.
LABEL_SOURCE_COLUMN = "default_status"

#: The feature that (deliberately) derives from the label source column.
LEAKAGE_FEATURE = "prior_default_flag"

#: The glossary term that declares a column to be a label. Owned by
#: janus.config, because the *detector* is what gives it meaning and
#: production code may never import this package. The seeder attaches the very
#: term the detector goes looking for, so the two cannot drift apart.
LABEL_TERM_URN = ScanConfig().label_term_urn

LABEL_TERM_NAME = "label"
LABEL_TERM_DEFINITION = (
    "The ground truth a model is trained to predict, known only after the fact. "
    "Any feature whose upstream column lineage reaches a column bearing this term "
    "is target leakage: the model is being trained on the answer."
)


@dataclass(frozen=True)
class Column:
    """One column in a seeded warehouse table."""

    name: str
    native_type: str
    description: str


SOURCE_COLUMNS: tuple[Column, ...] = (
    Column("applicant_id", "VARCHAR", "Loan applicant identifier"),
    Column("income", "NUMBER", "Self-reported annual income"),
    Column(
        LABEL_SOURCE_COLUMN,
        "BOOLEAN",
        "Whether the applicant defaulted. This is the model's label: known only after the fact.",
    ),
    Column("updated_at", "TIMESTAMP", "Row load time, used for the freshness assertion"),
)

FEATURE_COLUMNS: tuple[Column, ...] = (
    Column("applicant_id", "VARCHAR", "Loan applicant identifier"),
    Column("applicant_income", "NUMBER", "Applicant income, carried through from loans_raw"),
    Column(
        LEAKAGE_FEATURE,
        "BOOLEAN",
        "Derived from loans_raw.default_status, which is the label. Target leakage.",
    ),
    Column("updated_at", "TIMESTAMP", "Row load time"),
)

#: Column-level lineage, downstream column -> upstream columns, exactly as
#: LineageClient.add_lineage expects it.
COLUMN_LINEAGE: dict[str, list[str]] = {
    "applicant_id": ["applicant_id"],
    "applicant_income": ["income"],
    LEAKAGE_FEATURE: [LABEL_SOURCE_COLUMN],
    "updated_at": ["updated_at"],
}

#: Features the model consumes, mapped to the feature-table column each derives from.
MODEL_FEATURES: dict[str, str] = {
    "applicant_income": "applicant_income",
    LEAKAGE_FEATURE: LEAKAGE_FEATURE,
}

#: The feature table's primary key, mapped to its source column.
PRIMARY_KEY: tuple[str, str] = ("applicant_id", "applicant_id")

TRAINING_METRICS: dict[str, str] = {"auc": "0.88", "ks": "0.61"}
HYPER_PARAMS: dict[str, str] = {"max_depth": "6", "n_estimators": "300"}


def training_schema_fingerprint() -> dict[str, dict[str, str]]:
    """Return the training-time input schemas, keyed by input dataset URN.

    Each value maps field path to native type. Keying by dataset means a run with
    several inputs records a distinct baseline per input, so the schema-drift
    detector diffs each input against the schema that input actually had at
    training time, never against another input's. The seeded run has one input,
    the feature table, whose training-time schema is exactly :data:`FEATURE_COLUMNS`.
    """
    return {
        str(feature_table_dataset_urn()): {
            column.name: column.native_type for column in FEATURE_COLUMNS
        }
    }


def source_table_urn() -> DatasetUrn:
    """Return the URN of the raw warehouse table holding the label column."""
    return DatasetUrn(platform=WAREHOUSE_PLATFORM, name=SOURCE_TABLE)


def feature_table_dataset_urn() -> DatasetUrn:
    """Return the URN of the warehouse table the features are read from."""
    return DatasetUrn(platform=WAREHOUSE_PLATFORM, name=FEATURE_TABLE)


def label_column_urn() -> SchemaFieldUrn:
    """Return the URN of the label column, the target of leakage detection."""
    return SchemaFieldUrn(parent=source_table_urn(), field_path=LABEL_SOURCE_COLUMN)


def source_column_urn(column: str) -> SchemaFieldUrn:
    """Return the URN of one column of the source table."""
    return SchemaFieldUrn(parent=source_table_urn(), field_path=column)


def feature_column_urn(column: str) -> SchemaFieldUrn:
    """Return the URN of one column of the feature table."""
    return SchemaFieldUrn(parent=feature_table_dataset_urn(), field_path=column)


def feature_urn(name: str) -> MlFeatureUrn:
    """Return the URN of one seeded MLFeature."""
    return MlFeatureUrn(feature_namespace=FEATURE_NAMESPACE, name=name)


def primary_key_urn() -> MlPrimaryKeyUrn:
    """Return the URN of the feature table's MLPrimaryKey."""
    return MlPrimaryKeyUrn(feature_namespace=FEATURE_NAMESPACE, name=PRIMARY_KEY[0])


def feature_table_urn() -> MlFeatureTableUrn:
    """Return the URN of the MLFeatureTable grouping the seeded features."""
    return MlFeatureTableUrn(platform=ML_PLATFORM, name=f"{FEATURE_NAMESPACE}.{FEATURE_TABLE}")


def model_group_urn() -> MlModelGroupUrn:
    """Return the URN of the model group the seeded model belongs to."""
    return MlModelGroupUrn(platform=ML_PLATFORM, name=MODEL_GROUP_ID)


def model_urn() -> MlModelUrn:
    """Return the URN of the seeded model, the entity Janus writes findings onto."""
    return MlModelUrn(platform=ML_PLATFORM, name=MODEL_ID)


def training_run_urn() -> DataProcessInstanceUrn:
    """Return the URN of the training run that produced the seeded model."""
    return DataProcessInstanceUrn(TRAINING_RUN_ID)


def deployment_urn() -> MlModelDeploymentUrn:
    """Return the URN of the live deployment, which makes findings production-severity."""
    return MlModelDeploymentUrn(platform=ML_PLATFORM, name=DEPLOYMENT_ID)
