"""Reading a link out of a Feast repo. Offline: parsing a declaration, nothing else.

The fixture is the committed example repo, not a stub, because the thing that can
break here is Feast's own declaration shape, and a stub would only ever assert
that this module agrees with itself.

The assertion that matters is the renamed feature: the repo declares `tenure`
read from the column `tenure_months`, and a link that carried `tenure` to the
detectors would point them at a column the table does not have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modelguard.adapters import AdapterError, read_declaration
from modelguard.adapters.feast import read_repo

EXAMPLE_REPO = Path(__file__).resolve().parents[2] / "examples" / "feature-repo" / "feature_repo"


def test_reads_the_example_repo_as_a_human_would_have_typed_it():
    declaration = read_repo(EXAMPLE_REPO)

    assert declaration.name == "churn_model_v1"
    assert declaration.source_table == "warehouse.analytics.customer_features"
    # The declared feature and the warehouse column it is read from, which are
    # not the same string for `tenure`. The field_mapping is the only place that
    # says so.
    assert [(f.name, f.source_column) for f in declaration.features] == [
        ("tenure", "tenure_months"),
        ("monthly_charges", "monthly_charges"),
        ("support_calls", "support_calls"),
    ]
    assert declaration.source_columns == {"tenure_months", "monthly_charges", "support_calls"}
    assert declaration.label_column == "churned"
    assert declaration.label_table == "warehouse.analytics.customer_labels"


def test_every_feature_names_the_declaration_it_came_from():
    declaration = read_repo(EXAMPLE_REPO)

    assert {f.declared_in for f in declaration.features} == {"feature view 'customer_features'"}
    reasons = "\n".join(declaration.reasons)
    assert "churn_model_v1" in reasons
    assert "label view 'churn_label'" in reasons
    # The join key and the event timestamp are columns of the table and not
    # features. A reader has to be told which, since they are what a hand-typed
    # link passes --exclude for.
    assert "customer_id" in reasons
    assert "event_timestamp" in reasons


def test_the_dispatcher_reaches_the_same_reader():
    assert read_declaration("feast", EXAMPLE_REPO) == read_repo(EXAMPLE_REPO)


def test_an_unknown_selection_lists_what_the_repo_does_declare():
    with pytest.raises(AdapterError) as caught:
        read_repo(EXAMPLE_REPO, "churn_model_v2")

    assert "churn_model_v1" in str(caught.value)


def test_a_path_that_is_not_a_repo_names_the_argument(tmp_path: Path):
    with pytest.raises(AdapterError) as caught:
        read_repo(tmp_path / "nowhere")

    assert "--repo" in str(caught.value)


def test_a_repo_whose_python_raises_is_reported_with_its_own_error(tmp_path: Path):
    (tmp_path / "feature_store.yaml").write_text("project: broken\nprovider: local\n")
    (tmp_path / "broken.py").write_text("raise ValueError('the repo is mid-edit')\n")

    with pytest.raises(AdapterError) as caught:
        read_repo(tmp_path)

    assert "the repo is mid-edit" in str(caught.value)
