"""The schema-drift detector diffs a training snapshot against the live schema.

A known-drift fixture must flag exactly the drifted columns; a fixture whose
current schema matches the snapshot must flag nothing (the false-positive
control). The detector fires only on positive evidence, so a run with no snapshot
and a dataset with no current schema both yield nothing (CONTRIBUTING.md).
"""

from __future__ import annotations

import json

from datahub.metadata.schema_classes import (
    AuditStampClass,
    DataProcessInstanceInputClass,
    DataProcessInstancePropertiesClass,
    DeploymentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
)

from janus.config import ScanConfig
from janus.detect.schema_drift import diff_schema, schema_drift_findings
from janus.models import ChangeKind
from tests.conftest import (
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    MODEL_URN,
    TRAINING_RUN_URN,
    FakeGraph,
    make_connection,
    schema_metadata,
)

CONFIG = ScanConfig()

#: The schema the model was trained on, keyed by input dataset URN.
TRAINING_SCHEMA = {
    "applicant_id": "VARCHAR",
    "applicant_income": "NUMBER",
    "prior_default_flag": "BOOLEAN",
    "updated_at": "TIMESTAMP",
}

#: The feature table's live schema after the planted drift: one retype, one drop,
#: one add. applicant_id and prior_default_flag are untouched.
DRIFTED_SCHEMA = {
    "applicant_id": "VARCHAR",
    "applicant_income": "VARCHAR",
    "prior_default_flag": "BOOLEAN",
    "debt_to_income": "NUMBER",
}


def _graph(
    *,
    snapshot: dict[str, dict[str, str]] | str | None,
    current: dict[str, str] | None,
    live: bool = True,
) -> FakeGraph:
    """Build a graph with a model, its training run, and the input dataset."""
    aspects: dict = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            name="Credit Risk v3",
            trainingJobs=[TRAINING_RUN_URN],
            deployments=[DEPLOYMENT_URN],
        ),
        (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): MLModelDeploymentPropertiesClass(
            status=(
                DeploymentStatusClass.IN_SERVICE if live else DeploymentStatusClass.OUT_OF_SERVICE
            )
        ),
        (TRAINING_RUN_URN, DataProcessInstanceInputClass): DataProcessInstanceInputClass(
            inputs=[FEATURE_TABLE_URN]
        ),
    }

    if snapshot is not None:
        raw = snapshot if isinstance(snapshot, str) else json.dumps(snapshot)
        aspects[(TRAINING_RUN_URN, DataProcessInstancePropertiesClass)] = (
            DataProcessInstancePropertiesClass(
                name="credit_risk_v3_run",
                created=AuditStampClass(time=0, actor="urn:li:corpuser:datahub"),
                customProperties={CONFIG.training_schema_property: raw},
            )
        )
    else:
        aspects[(TRAINING_RUN_URN, DataProcessInstancePropertiesClass)] = (
            DataProcessInstancePropertiesClass(
                name="credit_risk_v3_run",
                created=AuditStampClass(time=0, actor="urn:li:corpuser:datahub"),
            )
        )

    if current is not None:
        aspects[(FEATURE_TABLE_URN, schema_metadata({}).__class__)] = schema_metadata(current)

    return FakeGraph(aspects=aspects)


def test_drift_flags_every_changed_column_on_the_input_dataset():
    conn = make_connection(
        _graph(snapshot={FEATURE_TABLE_URN: TRAINING_SCHEMA}, current=DRIFTED_SCHEMA)
    )

    findings = schema_drift_findings(conn, MODEL_URN, CONFIG)

    assert len(findings) == 1
    finding = findings[0]
    # The incident lands on the drifted dataset, never the model.
    assert finding.resource_urn == FEATURE_TABLE_URN
    assert finding.incident_type == "DATA_SCHEMA"
    kinds = {change.field_path: change.kind for change in finding.changes}
    assert kinds == {
        "applicant_income": ChangeKind.RETYPED,
        "debt_to_income": ChangeKind.ADDED,
        "updated_at": ChangeKind.REMOVED,
    }
    retype = next(c for c in finding.changes if c.field_path == "applicant_income")
    assert retype.training_type == "NUMBER"
    assert retype.current_type == "VARCHAR"


def test_a_matching_schema_is_not_flagged():
    # False-positive control: the current schema equals the training snapshot.
    conn = make_connection(
        _graph(snapshot={FEATURE_TABLE_URN: TRAINING_SCHEMA}, current=dict(TRAINING_SCHEMA))
    )
    assert schema_drift_findings(conn, MODEL_URN, CONFIG) == ()


def test_a_run_without_a_snapshot_is_skipped_not_cleared():
    # No baseline captured, so drift cannot be judged: positive evidence only.
    conn = make_connection(_graph(snapshot=None, current=DRIFTED_SCHEMA))
    assert schema_drift_findings(conn, MODEL_URN, CONFIG) == ()


def test_a_dataset_without_a_current_schema_is_skipped():
    conn = make_connection(_graph(snapshot={FEATURE_TABLE_URN: TRAINING_SCHEMA}, current=None))
    assert schema_drift_findings(conn, MODEL_URN, CONFIG) == ()


def test_a_malformed_snapshot_is_treated_as_absent():
    conn = make_connection(_graph(snapshot="not json {", current=DRIFTED_SCHEMA))
    assert schema_drift_findings(conn, MODEL_URN, CONFIG) == ()


def test_a_snapshot_for_a_different_input_does_not_flag_this_one():
    # The snapshot describes some other dataset, so this run's input has no
    # baseline and must not be diffed against the wrong schema.
    other = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.other,PROD)"
    conn = make_connection(_graph(snapshot={other: TRAINING_SCHEMA}, current=DRIFTED_SCHEMA))
    assert schema_drift_findings(conn, MODEL_URN, CONFIG) == ()


def test_severity_tracks_whether_the_model_is_live():
    from janus.models import Severity

    live = make_connection(
        _graph(snapshot={FEATURE_TABLE_URN: TRAINING_SCHEMA}, current=DRIFTED_SCHEMA, live=True)
    )
    idle = make_connection(
        _graph(snapshot={FEATURE_TABLE_URN: TRAINING_SCHEMA}, current=DRIFTED_SCHEMA, live=False)
    )
    assert schema_drift_findings(live, MODEL_URN, CONFIG)[0].severity == Severity.HIGH
    assert schema_drift_findings(idle, MODEL_URN, CONFIG)[0].severity == Severity.MEDIUM


def test_diff_schema_classifies_add_remove_retype_and_ignores_unchanged():
    changes = diff_schema(
        {"a": "INT", "b": "STR", "c": "FLOAT"},
        {"a": "BIGINT", "b": "STR", "d": "BOOL"},
    )
    by_field = {c.field_path: c.kind for c in changes}
    # b is unchanged and must not appear.
    assert by_field == {
        "a": ChangeKind.RETYPED,
        "c": ChangeKind.REMOVED,
        "d": ChangeKind.ADDED,
    }
