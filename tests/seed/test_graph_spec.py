"""The seed spec must be internally consistent, or the seeded graph is a lie.

These tests guard the invariants detectors will rely on in later phases: the
planted leakage really is planted, the column lineage really connects columns
that exist, and the URNs are stable.
"""

from __future__ import annotations

from janus.seed import graph_spec as spec


def _source_column_names() -> set[str]:
    return {c.name for c in spec.SOURCE_COLUMNS}


def _feature_column_names() -> set[str]:
    return {c.name for c in spec.FEATURE_COLUMNS}


def test_column_lineage_only_references_columns_that_exist():
    assert set(spec.COLUMN_LINEAGE) <= _feature_column_names()
    upstream = {col for cols in spec.COLUMN_LINEAGE.values() for col in cols}
    assert upstream <= _source_column_names()


def test_label_column_exists_in_the_source_table():
    assert spec.LABEL_SOURCE_COLUMN in _source_column_names()


def test_the_leakage_feature_derives_from_the_label_column():
    # This is the whole point of the seeded graph: a feature whose upstream cone
    # reaches the label. Without this edge there is nothing for P1 to detect.
    assert spec.COLUMN_LINEAGE[spec.LEAKAGE_FEATURE] == [spec.LABEL_SOURCE_COLUMN]
    assert spec.LEAKAGE_FEATURE in spec.MODEL_FEATURES


def test_every_model_feature_maps_to_a_real_feature_table_column():
    assert set(spec.MODEL_FEATURES.values()) <= _feature_column_names()


def test_primary_key_maps_to_a_real_column():
    _, key_column = spec.PRIMARY_KEY
    assert key_column in _feature_column_names()


def test_no_feature_is_named_after_a_column_it_does_not_read():
    # applicant_income reads applicant_income; the leakage feature reads its own
    # name too. A mismatch here would silently break the source_column bridge.
    for feature, column in spec.MODEL_FEATURES.items():
        assert feature == column


def test_urn_forms_are_stable():
    assert (
        str(spec.source_table_urn())
        == "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)"
    )
    assert str(spec.label_column_urn()) == (
        "urn:li:schemaField:"
        "(urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD),"
        "default_status)"
    )
    assert str(spec.model_urn()) == (
        "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
    )
    assert str(spec.feature_urn(spec.LEAKAGE_FEATURE)) == (
        "urn:li:mlFeature:(credit_risk,prior_default_flag)"
    )
    assert str(spec.training_run_urn()) == "urn:li:dataProcessInstance:credit_risk_v3_run"
