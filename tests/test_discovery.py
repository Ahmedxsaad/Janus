"""Model discovery, and the version-hiding it exists to defeat.

The behaviour under test is a negative one: a model DataHub has stopped calling
the latest version must still be found. A test that only checked "some URNs come
back" would pass against the bug, because the bug returns the newest version
perfectly well and drops the older one silently, so these assert on the query
that was sent (tests/CLAUDE.md rule 6).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from datahub.configuration.common import GraphError

from modelguard.discovery import search_model_urns

from .conftest import FakeClient, FakeGraph, make_connection

V1 = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,telco_churn_1,PROD)"
V2 = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,telco_churn_2,PROD)"


class ScrollGraph:
    """A graph whose GraphQL scroll hands back a fixed list of pages."""

    def __init__(self, pages: list[list[str]] | None = None, *, error: bool = False) -> None:
        self._pages = pages if pages is not None else [[V1, V2]]
        self._error = error
        self.sent: list[tuple[str, dict[str, Any] | None]] = []

    def execute_graphql(self, query: str, variables: dict[str, Any] | None = None, **_: Any) -> Any:
        self.sent.append((query, variables))
        if self._error:
            raise GraphError("SearchFlags.filterNonLatestVersions is not supported")
        index = len(self.sent) - 1
        page = self._pages[index] if index < len(self._pages) else []
        more = index + 1 < len(self._pages)
        return {
            "scrollAcrossEntities": {
                "nextScrollId": f"cursor-{index}" if more else None,
                "searchResults": [{"entity": {"urn": urn}} for urn in page],
            }
        }


def _conn(graph: Any, search_urns: list[str] | None = None) -> Any:
    connection = make_connection(FakeGraph(), FakeClient(search_urns=search_urns or []))
    return type(connection)(
        graph=graph,
        client=connection.client,
        gms_url=connection.gms_url,
        has_token=connection.has_token,
    )


def test_gms_is_told_not_to_hide_non_latest_versions():
    """The regression test. Drop the flag and an older version silently vanishes."""
    graph = ScrollGraph()

    search_model_urns(_conn(graph))

    query, _ = graph.sent[0]
    assert "filterNonLatestVersions: false" in query


def test_a_non_latest_version_comes_back():
    graph = ScrollGraph([[V1, V2]])

    assert search_model_urns(_conn(graph)) == (V1, V2)


def test_soft_deleted_models_are_still_filtered_out():
    """Version hiding is the only search behaviour this module means to change."""
    graph = ScrollGraph()

    search_model_urns(_conn(graph))

    _, variables = graph.sent[0]
    assert variables is not None
    sent = json.dumps(variables["orFilters"])
    assert "removed" in sent
    assert '"negated": true' in sent


def test_every_page_is_collected():
    """A graph with more models than one page must not be silently truncated."""
    graph = ScrollGraph([[V1], [V2]])

    assert search_model_urns(_conn(graph)) == (V1, V2)
    assert len(graph.sent) == 2
    assert graph.sent[1][1] is not None
    assert graph.sent[1][1]["scrollId"] == "cursor-0"


def test_a_query_reaches_gms_rather_than_being_filtered_here():
    graph = ScrollGraph()

    search_model_urns(_conn(graph), query="telco_churn_1")

    _, variables = graph.sent[0]
    assert variables is not None
    assert variables["query"] == "telco_churn_1"


def test_a_gms_too_old_for_the_flag_falls_back_to_search():
    """Older servers reject the field outright; they also never hide versions."""
    graph = ScrollGraph(error=True)

    assert search_model_urns(_conn(graph, search_urns=[V2])) == (V2,)


@pytest.mark.parametrize("query", [None, "*"])
def test_no_query_asks_for_everything(query: str | None):
    graph = ScrollGraph()

    search_model_urns(_conn(graph), query=query)

    _, variables = graph.sent[0]
    assert variables is not None
    assert variables["query"] == "*"
