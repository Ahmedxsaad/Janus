"""The ODCS input-contract renderer turns a model's inputs into a valid contract.

A model with an input table produces a contract whose schema object mirrors the
table's columns and whose SLA property carries the freshness ModelGuard guards. A
model with no readable input schema raises rather than emitting an empty contract
(positive evidence, like the detectors). The native-to-logical type mapping is
spot-checked, and it omits rather than guesses on an unknown type.
"""

from __future__ import annotations

import pytest
import yaml
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)

from modelguard.config import ScanConfig
from modelguard.writeback.contract import (
    ContractError,
    _logical_type,
    render_input_contract,
)
from tests.conftest import (
    FEATURE_TABLE_URN,
    MODEL_URN,
    TRAINING_RUN_URN,
    FakeGraph,
    make_connection,
    schema_metadata,
)

CONFIG = ScanConfig()

SCHEMA = {
    "applicant_income": "DOUBLE",
    "prior_default_flag": "BOOLEAN",
    "updated_at": "TIMESTAMP",
    "notes": "SOME_EXOTIC_TYPE",
}


def _graph(*, with_schema: bool = True) -> FakeGraph:
    aspects: dict = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            name="Credit Risk v3",
            trainingJobs=[TRAINING_RUN_URN],
        ),
        (TRAINING_RUN_URN, DataProcessInstanceInputClass): DataProcessInstanceInputClass(
            inputs=[FEATURE_TABLE_URN]
        ),
    }
    if with_schema:
        aspects[(FEATURE_TABLE_URN, SchemaMetadataClass)] = schema_metadata(SCHEMA)
    return FakeGraph(aspects)


def test_renders_valid_odcs_contract():
    conn = make_connection(_graph())

    contract = yaml.safe_load(render_input_contract(conn, MODEL_URN, CONFIG))

    assert contract["apiVersion"] == "v3.1.0"
    assert contract["kind"] == "DataContract"
    assert contract["id"] == "modelguard.input-contract.credit_risk_v3"

    (table,) = contract["schema"]
    assert table["name"] == "ecommerce.public.customer_features"
    assert table["logicalType"] == "object"

    by_name = {prop["name"]: prop for prop in table["properties"]}
    # Columns are sorted, physical type is verbatim, logical type is mapped.
    assert list(by_name) == sorted(SCHEMA)
    assert by_name["prior_default_flag"]["physicalType"] == "BOOLEAN"
    assert by_name["prior_default_flag"]["logicalType"] == "boolean"
    # An unmappable native type keeps its physical type but carries no logical one.
    assert "logicalType" not in by_name["notes"]

    (sla,) = contract["slaProperties"]
    assert sla["property"] == "frequency"
    assert sla["value"] == CONFIG.freshness_sla_hours
    assert sla["element"] == f"ecommerce.public.customer_features.{CONFIG.freshness_field}"


def test_no_input_schema_raises():
    conn = make_connection(_graph(with_schema=False))
    with pytest.raises(ContractError):
        render_input_contract(conn, MODEL_URN, CONFIG)


@pytest.mark.parametrize(
    ("native", "logical"),
    [
        ("TIMESTAMP", "timestamp"),
        ("DATETIME", "timestamp"),
        ("DATE", "date"),
        ("VARCHAR(255)", "string"),
        ("BIGINT", "integer"),
        ("DECIMAL(10,2)", "number"),
        ("BOOLEAN", "boolean"),
        ("GEOGRAPHY", None),
    ],
)
def test_logical_type_mapping(native, logical):
    assert _logical_type(native) == logical
