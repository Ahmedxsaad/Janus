"""The pure half of the adapter framework: the join against a real schema.

A declaration names the features positively; ``link`` takes the complement. That
inversion is where a wrong answer is silent rather than loud, so it is tested on
its own, apart from any adapter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modelguard.adapters import (
    AdapterError,
    excluded_columns,
    missing_columns,
    read_declaration,
)


def test_every_column_the_declaration_does_not_name_is_excluded():
    excluded = excluded_columns(
        ["tenure_months", "monthly_charges"],
        ["customer_id", "tenure_months", "monthly_charges", "event_timestamp"],
    )

    assert excluded == {"customer_id", "event_timestamp"}


def test_a_table_of_only_declared_columns_excludes_nothing():
    assert excluded_columns(["a", "b"], ["a", "b"]) == frozenset()


def test_a_declared_column_the_table_does_not_have_is_not_silently_excluded():
    # It is neither a feature nor an exclusion: it is a disagreement, and it is
    # reported by missing_columns so the caller can refuse rather than link a
    # partial set.
    assert excluded_columns(["a", "gone"], ["a", "b"]) == {"b"}
    assert missing_columns(["a", "gone"], ["a", "b"]) == ("gone",)


def test_columns_that_all_exist_are_not_reported_missing():
    assert missing_columns(["a", "b"], ["b", "a", "c"]) == ()


def test_an_unknown_adapter_lists_the_ones_that_exist():
    with pytest.raises(AdapterError) as caught:
        read_declaration("sagemaker", Path("."))

    assert "feast" in str(caught.value)
    assert "dbt" in str(caught.value)
