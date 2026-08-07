"""Finding models in the graph, including the versions search hides by default.

Every model-discovery path goes through here: the bulk sweeps
(``inventory``, ``scan --all-models``, ``link --all``) and the name-to-URN
resolution behind ``--model <name>``.

Why this module exists
----------------------
DataHub versions entities. Registering a second version of an MLflow model
produces a *second* ``mlModel`` entity, and stamps the first one's
``versionProperties.isLatest`` to false. GMS then drops every non-latest
version out of search results, so the earlier entity becomes invisible to
every query that finds models by searching for them, while remaining perfectly
alive: not soft-deleted, still carrying its aspects, still carrying whatever
Janus wrote to it.

That is a silent correctness failure rather than a cosmetic one, because
Janus reconciles through exactly those sweeps:

* ``link --all`` replays the link an ingestion run dropped. A model it
  cannot see is a model that silently stops being checked, which is the failure
  that command exists to prevent.
* An incident raised on a version that later stops being the latest can never
  be resolved, because nothing reaches the model to notice the finding is gone.
  A finding that cannot be closed is the failure mode this project's own
  about, arriving through a new door.

``SearchFlags.filterNonLatestVersions`` is what turns the hiding off. It is not
exposed by ``DataHubClient.search``, so the query is issued here directly
(verified against acryl-datahub 1.6.0.13 and GMS 1.5.0.6).

Scanning every version is deliberate, and it is cheaper than it sounds: the
detectors only *write* for a model somebody linked, and a linked version is one
a human named on purpose. An unlinked old version reports itself unchecked and
writes nothing.
"""

from __future__ import annotations

from typing import Any

from datahub.configuration.common import GraphError
from datahub.sdk.search_filters import FilterDsl as F

from janus.client import DataHubConnection

__all__ = ["search_model_urns"]

#: Results per scroll page. DataHub's own SDK scrolls at 5000; a graph with more
#: models than this is paged through, never truncated.
_BATCH = 1000

#: The soft-delete filter the SDK applies by default, in the raw shape GMS wants.
#: Kept because hiding non-latest versions is the only search behaviour this
#: module means to change: a soft-deleted model is still one nobody should scan.
_NOT_REMOVED = [
    {"and": [{"field": "removed", "condition": "EQUAL", "values": ["true"], "negated": True}]}
]

#: ``filterNonLatestVersions: false`` is the whole point of hand-writing this.
#: Everything else mirrors what ``get_urns_by_filter`` sends.
_SCROLL = """
query janusScrollModels(
  $query: String!
  $orFilters: [AndFilterInput!]
  $batch: Int!
  $scrollId: String
) {
  scrollAcrossEntities(input: {
    query: $query,
    count: $batch,
    scrollId: $scrollId,
    types: [MLMODEL],
    orFilters: $orFilters,
    searchFlags: {
      skipHighlighting: true,
      skipAggregates: true,
      filterNonLatestVersions: false
    }
  }) {
    nextScrollId
    searchResults { entity { urn } }
  }
}
"""


def search_model_urns(conn: DataHubConnection, *, query: str | None = None) -> tuple[str, ...]:
    """Return every mlModel URN matching ``query``, newest and older versions alike.

    Args:
        conn: An open connection.
        query: A search term, or None for every model in the graph.

    Returns:
        Matching URNs, sorted so a bulk run reads the same way twice.
    """
    try:
        return _scroll(conn, query or "*")
    except GraphError:
        # An older GMS has no filterNonLatestVersions field and rejects the
        # query outright. Falling back keeps this module working against those
        # servers, where the hiding does not exist to be turned off either.
        urns = conn.client.search.get_urns(query=query, filter=F.entity_type("mlModel"))
        return tuple(sorted(str(urn) for urn in urns))


def _scroll(conn: DataHubConnection, query: str) -> tuple[str, ...]:
    """Page through scrollAcrossEntities until GMS stops handing back a cursor."""
    found: set[str] = set()
    scroll_id: str | None = None
    while True:
        variables: dict[str, Any] = {
            "query": query,
            "orFilters": _NOT_REMOVED,
            "batch": _BATCH,
            "scrollId": scroll_id,
        }
        page = conn.graph.execute_graphql(query=_SCROLL, variables=variables)
        results = page["scrollAcrossEntities"]
        found.update(row["entity"]["urn"] for row in results["searchResults"])
        scroll_id = results.get("nextScrollId")
        # GMS signals the end by returning no cursor. It also returns the same
        # cursor forever on an empty final page, so an empty page ends it too.
        if not scroll_id or not results["searchResults"]:
            return tuple(sorted(found))
