"""Shared fixtures. Unit tests never touch a network or a live DataHub."""

from __future__ import annotations

from typing import Any

import pytest

from modelguard.client import DataHubConnection


class FakeGraph:
    """A stand-in for DataHubGraph that records writes instead of sending them.

    Only the handful of methods the write-back layer calls are implemented, so a
    test that reaches for anything else fails loudly rather than silently passing.
    """

    def __init__(
        self,
        aspects: dict[tuple[str, type], Any] | None = None,
        *,
        exists: bool = True,
        graphql_response: dict[str, Any] | None = None,
    ) -> None:
        self._aspects = aspects or {}
        self._exists = exists
        self.graphql_response = graphql_response or {}
        self.emitted: list[Any] = []
        self.graphql_calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_aspect(self, entity_urn: str, aspect_type: type, version: int = 0) -> Any:
        return self._aspects.get((entity_urn, aspect_type))

    def exists(self, entity_urn: str) -> bool:
        return self._exists

    def emit_mcp(self, mcp: Any, **_: Any) -> None:
        self.emitted.append(mcp)

    def emit_mcps(self, mcps: Any, **_: Any) -> list[Any]:
        self.emitted.extend(mcps)
        return []

    def execute_graphql(self, query: str, variables: dict[str, Any] | None = None, **_: Any) -> Any:
        self.graphql_calls.append((query, variables))
        return self.graphql_response


def make_connection(graph: FakeGraph) -> DataHubConnection:
    """Wrap a FakeGraph in the connection object the write-back layer expects."""
    return DataHubConnection(
        graph=graph,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        gms_url="http://fake-gms:8080",
        has_token=True,
    )


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def conn(graph: FakeGraph) -> DataHubConnection:
    return make_connection(graph)
