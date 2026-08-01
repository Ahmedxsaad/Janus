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
than a loud failure. Every connection value comes from the environment through
:mod:`modelguard.env`, which is the only module that loads the git-ignored
``.env``; the shipped ``.env.example`` documents each key. Secrets are never
logged, echoed, or embedded in an exception message.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
from datahub.sdk.main_client import DataHubClient

from modelguard.env import optional_value

ENV_GMS_URL = "DATAHUB_GMS_URL"
ENV_GMS_TOKEN = "DATAHUB_GMS_TOKEN"

#: Hosts that mean "the Quickstart on this machine". Advice that assumes a
#: Quickstart (start it with `datahub docker quickstart`, populate it with
#: `modelguard-seed`) is correct here and wrong anywhere else: told to somebody
#: pointed at their company's catalog, it invites them to write demo datasets
#: into production metadata, or to go looking for a container that was never
#: meant to exist. Every user-facing hint that names the demo is gated on this.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def is_local_gms(gms_url: str) -> bool:
    """Whether a GMS URL points at a DataHub running on this machine."""
    return (urlparse(gms_url).hostname or "") in _LOCAL_HOSTS


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
    url = optional_value(ENV_GMS_URL)
    if url is None:
        raise DataHubConnectionError(
            f"{ENV_GMS_URL} is not set. Copy .env.example to .env and fill it in. "
            "For a local Quickstart the value is http://localhost:8080"
        )
    return url.rstrip("/")


def gms_token() -> str | None:
    """Return the personal access token, or None when it is unset or blank.

    ``.env.example`` ships ``DATAHUB_GMS_TOKEN=`` with an empty value, so an
    empty string means "not configured" rather than "the empty token".

    Public, and it is the one function here that hands out a secret. Callers get
    it for exactly one purpose: passing it to :func:`modelguard.env.scrub` so a
    third-party exception message can be cleaned before it reaches a log. Never
    print, format, or store what this returns.
    """
    return optional_value(ENV_GMS_TOKEN)


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
    url = _gms_url()
    token = gms_token()

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
            hint = (
                "Is the Quickstart running? Start it with: datahub docker quickstart"
                if is_local_gms(url)
                else f"Check {ENV_GMS_URL}, that the host is reachable from here, "
                f"and that {ENV_GMS_TOKEN} is set if that instance requires one."
            )
            raise DataHubConnectionError(f"Could not reach DataHub at {url}. {hint}") from exc

    return DataHubConnection(
        graph=graph,
        client=DataHubClient(graph=graph),
        gms_url=url,
        has_token=token is not None,
    )
