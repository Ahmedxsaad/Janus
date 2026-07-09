"""The single factory for DataHub connections.

Every other module in the package receives a :class:`DataHubConnection`; nothing
else reads connection environment variables (see modelguard/CLAUDE.md).

Two handles are needed because the SDK splits the surface:

* :class:`~datahub.sdk.main_client.DataHubClient` carries the typed entity and
  lineage helpers (``client.entities``, ``client.lineage``).
* :class:`~datahub.ingestion.graph.client.DataHubGraph` carries the low-level
  escape hatches the SDK does not wrap yet: ``execute_graphql`` (incidents have
  no Python SDK wrapper) and ``get_aspect`` / ``emit_mcps`` (used for the ML
  aspects the SDK has no entity class for).

Both wrap the same underlying HTTP session, so constructing the client from the
graph keeps a single connection and a single token.

No defaults, on purpose
-----------------------
Nothing here falls back to a hardcoded server URL. A default such as
``http://localhost:8080`` is a machine-specific value living in tracked code, and
it turns a missing ``.env`` into a silent connection to the wrong place rather
than a loud failure. Every connection value comes from the environment, loaded
from the git-ignored ``.env``; the shipped ``.env.example`` documents each one.
Secrets are never logged, echoed, or embedded in an exception message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
from datahub.sdk.main_client import DataHubClient
from dotenv import find_dotenv, load_dotenv

ENV_GMS_URL = "DATAHUB_GMS_URL"
ENV_GMS_TOKEN = "DATAHUB_GMS_TOKEN"


class DataHubConnectionError(RuntimeError):
    """DataHub is unreachable, or a credential required for the call is missing."""


@dataclass(frozen=True)
class DataHubConnection:
    """A live, validated pair of handles onto one DataHub instance."""

    graph: DataHubGraph
    client: DataHubClient
    gms_url: str
    has_token: bool


def _gms_url() -> str:
    """Return the configured GMS URL.

    Raises:
        DataHubConnectionError: The variable is unset or blank. There is no
            default: see the module docstring.
    """
    url = os.environ.get(ENV_GMS_URL, "").strip()
    if not url:
        raise DataHubConnectionError(
            f"{ENV_GMS_URL} is not set. Copy .env.example to .env and fill it in. "
            "For a local Quickstart the value is http://localhost:8080"
        )
    return url.rstrip("/")


def _gms_token() -> str | None:
    """Return the personal access token, or None when it is unset or blank.

    ``.env.example`` ships ``DATAHUB_GMS_TOKEN=`` with an empty value, so an
    empty string means "not configured" rather than "the empty token".
    """
    token = os.environ.get(ENV_GMS_TOKEN, "").strip()
    return token or None


def connect(*, require_token: bool = False, validate: bool = True) -> DataHubConnection:
    """Build handles onto DataHub from the environment.

    Args:
        require_token: Fail unless a personal access token is configured. Set this
            for any code path that mutates the graph. A default Quickstart accepts
            unauthenticated reads, so read-only paths can leave it False.
        validate: Probe the server before returning. Disable only in unit tests.

    Returns:
        A connection carrying both the SDK client and the low-level graph handle.

    Raises:
        DataHubConnectionError: A required variable is unset, the token is
            required but absent, or the server did not answer.
    """
    # override=False: a variable already exported in the shell or set by CI wins
    # over .env, which is the developer's local default.
    load_dotenv(find_dotenv(usecwd=True), override=False)

    url = _gms_url()
    token = _gms_token()

    if require_token and token is None:
        raise DataHubConnectionError(
            f"{ENV_GMS_TOKEN} is not set, but this operation writes to DataHub. "
            "Generate a token in the DataHub UI under Settings -> Access Tokens "
            "and put it in .env (which is git-ignored)."
        )

    graph = DataHubGraph(DatahubClientConfig(server=url, token=token))

    if validate:
        try:
            graph.test_connection()
        except Exception as exc:
            raise DataHubConnectionError(
                f"Could not reach DataHub at {url}. Is the Quickstart running? "
                "Start it with: datahub docker quickstart"
            ) from exc

    return DataHubConnection(
        graph=graph,
        client=DataHubClient(graph=graph),
        gms_url=url,
        has_token=token is not None,
    )
