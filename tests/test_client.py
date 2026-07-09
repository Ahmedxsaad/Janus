"""The connection factory is the only place connection env vars are read."""

from __future__ import annotations

import pytest

from modelguard import client as client_module
from modelguard.client import (
    DEFAULT_GMS_URL,
    ENV_GMS_TOKEN,
    ENV_GMS_URL,
    DataHubConnectionError,
    connect,
)


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real .env out of the unit tests."""
    monkeypatch.setattr(client_module, "load_dotenv", lambda **_: False)


def test_gms_url_defaults_to_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GMS_URL, raising=False)
    assert client_module._gms_url() == DEFAULT_GMS_URL


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
    monkeypatch.delenv(ENV_GMS_TOKEN, raising=False)
    with pytest.raises(DataHubConnectionError, match="writes to DataHub"):
        connect(require_token=True, validate=False)


def test_read_path_without_token_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GMS_TOKEN, raising=False)
    monkeypatch.setenv(ENV_GMS_URL, "http://gms.example:8080")
    connection = connect(require_token=False, validate=False)
    assert connection.has_token is False
    assert connection.gms_url == "http://gms.example:8080"
