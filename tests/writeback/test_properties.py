"""Structured property definitions are declared in YAML and assigned by merge."""

from __future__ import annotations

from pathlib import Path

import pytest
from datahub.metadata.schema_classes import (
    StructuredPropertiesClass,
    StructuredPropertyValueAssignmentClass,
)

from modelguard.writeback.properties import (
    RISK_FLAGS,
    RUN_ID,
    TRUST_BAND,
    TRUST_SCORE,
    PropertyDefinitionError,
    assign_properties,
    define_properties,
    load_definitions,
    read_properties,
)
from tests.conftest import FakeGraph, make_connection

MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"


def test_the_shipped_declarations_parse_and_cover_the_planned_properties():
    names = {d.qualified_name for d in load_definitions()}
    assert names == {TRUST_SCORE, TRUST_BAND, RISK_FLAGS, RUN_ID}


def test_declarations_render_to_datahub_urn_forms():
    by_name = {d.qualified_name: d.as_aspect() for d in load_definitions()}
    assert by_name[TRUST_SCORE].valueType == "urn:li:dataType:datahub.number"
    assert by_name[RISK_FLAGS].valueType == "urn:li:dataType:datahub.string"
    assert by_name[RISK_FLAGS].cardinality == "MULTIPLE"
    assert by_name[TRUST_SCORE].entityTypes == ["urn:li:entityType:datahub.mlModel"]


def test_a_bad_cardinality_is_rejected(tmp_path: Path):
    bad = tmp_path / "props.yaml"
    bad.write_text(
        "properties:\n"
        "  - qualified_name: modelguard.x\n"
        "    display_name: X\n"
        "    description: d\n"
        "    value_type: number\n"
        "    cardinality: MANY\n"
        "    entity_types: [mlModel]\n"
    )
    with pytest.raises(PropertyDefinitionError, match="cardinality"):
        load_definitions(bad)


def test_a_missing_field_is_reported_with_its_name(tmp_path: Path):
    bad = tmp_path / "props.yaml"
    bad.write_text("properties:\n  - qualified_name: modelguard.x\n")
    with pytest.raises(PropertyDefinitionError, match="missing field"):
        load_definitions(bad)


def test_define_properties_emits_one_definition_per_declaration():
    graph = FakeGraph()
    urns = define_properties(make_connection(graph))
    assert len(graph.emitted) == len(urns) == 4
    assert "urn:li:structuredProperty:modelguard.trust_score" in urns
    assert "urn:li:structuredProperty:modelguard.trust_band" in urns


def test_assign_writes_numbers_as_floats():
    graph = FakeGraph()
    assign_properties(make_connection(graph), MODEL, {TRUST_SCORE: [62]})
    aspect = graph.emitted[0].aspect
    assert aspect.properties[0].values == [62.0]


def test_assign_preserves_properties_this_run_did_not_touch():
    # A trust-score write must not wipe an unrelated property another writer set.
    other = StructuredPropertyValueAssignmentClass(
        propertyUrn="urn:li:structuredProperty:someone.else", values=["keep me"]
    )
    graph = FakeGraph(
        aspects={(MODEL, StructuredPropertiesClass): StructuredPropertiesClass(properties=[other])}
    )
    assign_properties(make_connection(graph), MODEL, {TRUST_SCORE: [62]})

    written = {a.propertyUrn: a.values for a in graph.emitted[0].aspect.properties}
    assert written["urn:li:structuredProperty:someone.else"] == ["keep me"]
    assert written["urn:li:structuredProperty:modelguard.trust_score"] == [62.0]


def test_assign_replaces_rather_than_appends_an_existing_value():
    stale = StructuredPropertyValueAssignmentClass(
        propertyUrn="urn:li:structuredProperty:modelguard.trust_score", values=[10.0]
    )
    graph = FakeGraph(
        aspects={(MODEL, StructuredPropertiesClass): StructuredPropertiesClass(properties=[stale])}
    )
    assign_properties(make_connection(graph), MODEL, {TRUST_SCORE: [62]})

    written = graph.emitted[0].aspect.properties
    assert len(written) == 1
    assert written[0].values == [62.0]


def test_booleans_are_rejected_rather_than_stored_as_numbers():
    # bool is a subclass of int in Python; storing True as 1.0 would be silent
    # data corruption in a UI that renders numbers.
    graph = FakeGraph()
    with pytest.raises(ValueError, match="may not be booleans"):
        assign_properties(make_connection(graph), MODEL, {RISK_FLAGS: [True]})
    assert graph.emitted == []


def test_unsupported_value_types_are_rejected():
    graph = FakeGraph()
    with pytest.raises(ValueError, match="unsupported structured property value"):
        assign_properties(make_connection(graph), MODEL, {RISK_FLAGS: [{"a": 1}]})  # type: ignore[list-item]


def test_read_properties_keys_by_qualified_name():
    assignment = StructuredPropertyValueAssignmentClass(
        propertyUrn="urn:li:structuredProperty:modelguard.trust_score", values=[62.0]
    )
    graph = FakeGraph(
        aspects={
            (MODEL, StructuredPropertiesClass): StructuredPropertiesClass(properties=[assignment])
        }
    )
    assert read_properties(make_connection(graph), MODEL) == {TRUST_SCORE: [62.0]}


def test_read_properties_of_an_untouched_entity_is_empty():
    assert read_properties(make_connection(FakeGraph()), MODEL) == {}
