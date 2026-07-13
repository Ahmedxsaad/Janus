"""The connection factory is the only place connection env vars are read."""

from __future__ import annotations

import pytest

from modelguard import client as client_module
from modelguard import env as env_module
from modelguard.client import (
    ENV_GMS_TOKEN,
    ENV_GMS_URL,
    DataHubConnectionError,
    connect,
)


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real .env out of the unit tests.

    client.py no longer loads .env itself; modelguard.env does, once. Marking it
    already loaded is what stops these tests from reading the real file.
    """
    monkeypatch.setattr(env_module, "_loaded", True)


def test_a_missing_gms_url_fails_loudly_rather_than_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hardcoded fallback would turn a missing .env into a silent connection to
    # someone else's machine-specific default.
    monkeypatch.delenv(ENV_GMS_URL, raising=False)
    with pytest.raises(DataHubConnectionError, match="is not set"):
        client_module._gms_url()


def test_a_blank_gms_url_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GMS_URL, "   ")
    with pytest.raises(DataHubConnectionError, match="is not set"):
        client_module._gms_url()


def test_the_package_declares_no_default_server_url() -> None:
    # Guards against a fallback creeping back in under any name.
    hardcoded = [
        name
        for name, value in vars(client_module).items()
        if isinstance(value, str) and value.startswith(("http://", "https://"))
    ]
    assert hardcoded == []


def test_gms_url_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GMS_URL, "http://gms.example:8080/")
    assert client_module._gms_url() == "http://gms.example:8080"


def test_blank_token_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships DATAHUB_GMS_TOKEN= with an empty value.
    monkeypatch.setenv(ENV_GMS_TOKEN, "   ")
    assert client_module._gms_token() is None


def test_token_is_read_and_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GMS_TOKEN, "  secret-token\n")
    assert client_module._gms_token() == "secret-token"


def test_write_path_without_token_fails_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GMS_URL, "http://gms.example:8080")
    monkeypatch.delenv(ENV_GMS_TOKEN, raising=False)
    with pytest.raises(DataHubConnectionError, match="writes to DataHub"):
        connect(require_token=True, validate=False)


def test_an_unreachable_server_is_reported_without_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stack trace or message that echoes the PAT would leak it into CI logs.
    secret = "super-secret-pat-value"
    monkeypatch.setenv(ENV_GMS_URL, "http://gms.example:8080")
    monkeypatch.setenv(ENV_GMS_TOKEN, secret)

    class UnreachableGraph:
        def __init__(self, config: object) -> None:
            self.config = config

        def test_connection(self) -> None:
            raise OSError("connection refused")

    monkeypatch.setattr(client_module, "DataHubGraph", UnreachableGraph)

    with pytest.raises(DataHubConnectionError) as caught:
        connect(validate=True)

    message = str(caught.value)
    assert secret not in message
    assert "Could not reach DataHub" in message


def test_read_path_without_token_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GMS_TOKEN, raising=False)
    monkeypatch.setenv(ENV_GMS_URL, "http://gms.example:8080")
    connection = connect(require_token=False, validate=False)
    assert connection.has_token is False
    assert connection.gms_url == "http://gms.example:8080"
