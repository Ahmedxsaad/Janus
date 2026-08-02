from __future__ import annotations

import pytest
from pydantic import SecretStr

from modelguard import env as env_module
from modelguard.env import ConfigError
from modelguard.llm import (
    _PROVIDERS,
    ENV_LLM_API_KEY,
    ENV_LLM_MODEL,
    ENV_LLM_PROVIDER,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    SUPPORTED_PROVIDERS,
    LLMConfig,
    LLMUnavailableError,
    build_chat_model,
    llm_config_from_env,
)

SECRET = "sk-super-secret-key-1234567890"
_LLM_VARS = (ENV_LLM_PROVIDER, ENV_LLM_MODEL, ENV_LLM_API_KEY)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend .env was already loaded, then control the three variables outright.

    Without pinning `_loaded`, load_environment() would read the developer's real
    .env and a configured key would leak into these assertions.
    """
    monkeypatch.setattr(env_module, "_loaded", True)
    for name in _LLM_VARS:
        monkeypatch.delenv(name, raising=False)


def _configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# --------------------------------------------------------------------------
# All three, or none. Never a silent half.
# --------------------------------------------------------------------------


def test_no_llm_variables_means_run_without_an_llm():
    assert llm_config_from_env() is None


def test_all_three_variables_build_a_config(monkeypatch):
    _configure(
        monkeypatch,
        MODELGUARD_LLM_PROVIDER="openai",
        MODELGUARD_LLM_MODEL="gpt-5",
        MODELGUARD_LLM_API_KEY=SECRET,
    )
    config = llm_config_from_env()

    assert config is not None
    assert config.provider == "openai"
    assert config.model == "gpt-5"
    assert config.secret() == SECRET


@pytest.mark.parametrize("provided", list(_LLM_VARS))
def test_setting_only_one_variable_fails_loudly(monkeypatch, provided: str):
    """A half-configured LLM must not quietly downgrade to template prose."""
    _configure(monkeypatch, **{provided: "openai" if "PROVIDER" in provided else SECRET})

    with pytest.raises(ConfigError, match="partially configured"):
        llm_config_from_env()


def test_the_partial_config_error_names_the_missing_variables_not_their_values(monkeypatch):
    _configure(monkeypatch, MODELGUARD_LLM_API_KEY=SECRET)

    with pytest.raises(ConfigError) as caught:
        llm_config_from_env()

    message = str(caught.value)
    assert ENV_LLM_PROVIDER in message
    assert ENV_LLM_MODEL in message
    assert SECRET not in message, "the API key appeared in an exception message"


# --------------------------------------------------------------------------
# No vendor is assumed
# --------------------------------------------------------------------------


def test_an_unknown_provider_is_refused_and_the_supported_ones_are_named(monkeypatch):
    _configure(
        monkeypatch,
        MODELGUARD_LLM_PROVIDER="acme",
        MODELGUARD_LLM_MODEL="m",
        MODELGUARD_LLM_API_KEY=SECRET,
    )
    with pytest.raises(ConfigError, match="not a supported LLM provider"):
        llm_config_from_env()


def test_the_supported_providers_are_the_ones_we_verified():
    assert {"anthropic", "openai", "google"} == SUPPORTED_PROVIDERS


@pytest.mark.parametrize(
    ("provider", "model", "expected_class"),
    [
        ("anthropic", "claude-opus-4-8", "ChatAnthropic"),
        ("openai", "gpt-5", "ChatOpenAI"),
        ("google", "gemini-2.5-pro", "ChatGoogleGenerativeAI"),
    ],
)
def test_every_provider_builds_its_own_chat_model(provider: str, model: str, expected_class: str):
    """One uniform call reaches three vendors whose field names all differ.

    Each binding is an optional extra, and pyproject tells the reader to install
    only the one they configure. This test therefore skips the providers whose
    package is absent rather than failing: following the project's own install
    instructions must not produce a red suite. The module name comes from the
    registry, so it cannot drift from the code under test.
    """
    pytest.importorskip(
        _PROVIDERS[provider][0],
        reason=f"the {provider} binding is an optional extra: pip install -e '.[{provider}]'",
    )

    chat_model = build_chat_model(
        LLMConfig(provider=provider, model=model, api_key=SecretStr(SECRET))
    )
    assert type(chat_model).__name__ == expected_class
    # The model id reaches the client whichever kwarg name the vendor uses.
    resolved = getattr(chat_model, "model", None) or getattr(chat_model, "model_name", None)
    assert resolved == model
    # F13: no incident should wait on somebody else's API. Each binding names
    # its own field differently (default_request_timeout, request_timeout,
    # timeout), so this checks whichever one the constructed instance carries
    # rather than assuming a single name across all three.
    timeout = next(
        getattr(chat_model, name)
        for name in ("default_request_timeout", "request_timeout", "timeout")
        if getattr(chat_model, name, None) is not None
    )
    assert timeout == LLM_TIMEOUT_SECONDS
    assert chat_model.max_retries == LLM_MAX_RETRIES


def test_a_missing_provider_package_names_the_extra_to_install(monkeypatch):
    import modelguard.llm as llm_module

    monkeypatch.setitem(
        llm_module._PROVIDERS, "openai", ("modelguard_no_such_module", "Chat", "langchain-openai")
    )
    with pytest.raises(LLMUnavailableError, match="langchain-openai"):
        build_chat_model(LLMConfig(provider="openai", model="gpt-5", api_key=SecretStr(SECRET)))


# --------------------------------------------------------------------------
# The key never escapes
# --------------------------------------------------------------------------


def test_the_key_is_hidden_in_the_configs_repr_and_str():
    config = LLMConfig(provider="openai", model="gpt-5", api_key=SecretStr(SECRET))
    assert SECRET not in repr(config)
    assert SECRET not in str(config)


def test_the_raw_key_is_reachable_only_through_an_explicit_call():
    config = LLMConfig(provider="openai", model="gpt-5", api_key=SecretStr(SECRET))
    assert config.secret() == SECRET
