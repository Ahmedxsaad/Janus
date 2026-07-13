from __future__ import annotations

import pytest

from modelguard.cli import TableResolutionError, resolve_table
from modelguard.client import DataHubConnection
from tests.conftest import TABLE_URN, FakeClient, FakeGraph, make_connection

OTHER_TABLE = "urn:li:dataset:(urn:li:dataPlatform:bigquery,analytics.public.loans_raw,PROD)"


def _conn(search_urns: list[str]) -> DataHubConnection:
    return make_connection(FakeGraph(), FakeClient(search_urns=search_urns))


def test_a_full_urn_is_used_as_given():
    assert resolve_table(_conn([]), TABLE_URN) == TABLE_URN


def test_a_malformed_urn_is_rejected_rather_than_scanned():
    with pytest.raises(Exception, match="urn"):
        resolve_table(_conn([]), "urn:li:dataset:not-a-real-urn")


def test_a_bare_table_name_resolves_through_search():
    assert resolve_table(_conn([TABLE_URN]), "loans_raw") == TABLE_URN


def test_a_fully_qualified_name_resolves_too():
    assert resolve_table(_conn([TABLE_URN]), "ecommerce.public.loans_raw") == TABLE_URN


def test_a_search_hit_whose_name_does_not_match_is_ignored():
    """Search is fuzzy. Only an exact name or last-segment match counts."""
    unrelated = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_archive,PROD)"
    with pytest.raises(TableResolutionError, match="no dataset named"):
        resolve_table(_conn([unrelated]), "loans_raw")


def test_an_unknown_table_fails_loudly_and_points_at_the_seeder():
    with pytest.raises(TableResolutionError, match="modelguard-seed"):
        resolve_table(_conn([]), "nonexistent")


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Two platforms hold a loans_raw. Scanning the wrong one silently is worse than failing."""
    with pytest.raises(TableResolutionError, match="matches 2 datasets"):
        resolve_table(_conn([TABLE_URN, OTHER_TABLE]), "loans_raw")
