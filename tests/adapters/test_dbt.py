"""Reading a link out of a dbt semantic model. Offline: it is a JSON file.

``dbt_manifest.json`` beside this file is real dbt output, not a hand-written
shape: a project declaring one semantic model was parsed with dbt-core 1.12.0
(manifest schema v12) and the artifact trimmed to the two keys the adapter reads,
with the machine-specific ids dbt stamps into metadata removed. Trimming keeps
the file readable; inventing it would have tested only that this module agrees
with itself.

The load-bearing assertion is the same as the Feast adapter's: a measure declared
as ``tenure`` with ``expr: tenure_months`` has to come back as the *column*, not
as the measure's name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelguard.adapters import AdapterError, read_declaration
from modelguard.adapters.dbt import read_manifest

MANIFEST = Path(__file__).resolve().parent / "dbt_manifest.json"


def test_reads_the_semantic_model_as_a_human_would_have_typed_it():
    declaration = read_manifest(MANIFEST)

    assert declaration.name == "customer_features"
    # node_relation, which is what the warehouse holds and therefore what
    # DataHub's ingestion of it is named after.
    assert declaration.source_table == "main.customer_features"
    assert [(f.name, f.source_column) for f in declaration.features] == [
        ("updated_at", "updated_at"),
        ("tenure", "tenure_months"),
        ("monthly_charges", "monthly_charges"),
        ("support_calls", "support_calls"),
    ]
    # The dbt semantic layer has no notion of a label, and saying so is the
    # honest answer: a guessed label makes every leakage verdict wrong.
    assert declaration.label_column is None
    assert declaration.label_table is None


def test_the_reasons_quote_the_manifest_version_and_the_entity_key():
    declaration = read_manifest(MANIFEST)

    reasons = "\n".join(declaration.reasons)
    assert "v12.json" in reasons
    assert "customer_id" in reasons
    assert "--label-column" in reasons
    assert {f.declared_in for f in declaration.features} == {"semantic model 'customer_features'"}


def test_a_project_root_finds_its_own_target_manifest(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    target.joinpath("manifest.json").write_text(MANIFEST.read_text())

    # Everything but the reasons, which quote the path they were given.
    from_root = read_manifest(tmp_path)
    from_file = read_manifest(MANIFEST)
    assert (from_root.source_table, from_root.features) == (
        from_file.source_table,
        from_file.features,
    )
    assert read_declaration("dbt", tmp_path) == from_root


def test_a_computed_measure_is_reported_unread_rather_than_parsed(tmp_path: Path):
    manifest = json.loads(MANIFEST.read_text())
    model = next(iter(manifest["semantic_models"].values()))
    model["measures"].append(
        {"name": "revenue", "agg": "sum", "expr": "monthly_charges * tenure_months"}
    )
    written = tmp_path / "manifest.json"
    written.write_text(json.dumps(manifest))

    declaration = read_manifest(written)

    assert "revenue" not in {feature.name for feature in declaration.features}
    assert "revenue (measure)" in "\n".join(declaration.reasons)


def test_two_semantic_models_ask_which_one(tmp_path: Path):
    manifest = json.loads(MANIFEST.read_text())
    only = next(iter(manifest["semantic_models"].values()))
    second = json.loads(json.dumps(only))
    second["name"] = "customer_labels"
    manifest["semantic_models"]["semantic_model.churn_analytics.customer_labels"] = second
    written = tmp_path / "manifest.json"
    written.write_text(json.dumps(manifest))

    with pytest.raises(AdapterError) as caught:
        read_manifest(written)
    assert "--select" in str(caught.value)

    # ...and naming one answers it, by the readable name as well as the unique id.
    assert read_manifest(written, "customer_labels").name == "customer_labels"
    assert (
        read_manifest(written, "semantic_model.churn_analytics.customer_labels").name
        == "customer_labels"
    )


def test_a_manifest_without_the_semantic_layer_says_which_key_is_missing(tmp_path: Path):
    written = tmp_path / "manifest.json"
    written.write_text(json.dumps({"metadata": {}, "nodes": {}}))

    with pytest.raises(AdapterError) as caught:
        read_manifest(written)

    assert "semantic_models" in str(caught.value)


def test_an_uncompiled_project_names_the_dbt_command(tmp_path: Path):
    with pytest.raises(AdapterError) as caught:
        read_manifest(tmp_path)

    assert "dbt parse" in str(caught.value)
