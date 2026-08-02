"""The single factory for the language model, whoever provides it.

ModelGuard is bring-your-own-LLM. The provider, the model, and the key are three
configuration values, read together, with **no defaults**:

* ``MODELGUARD_LLM_PROVIDER``: one of :data:`SUPPORTED_PROVIDERS`.
* ``MODELGUARD_LLM_MODEL``: the provider's model id, verbatim.
* ``MODELGUARD_LLM_API_KEY``: the credential for that provider.

Why no default provider or model
--------------------------------
A default of ``anthropic`` plus ``claude-opus-4-8`` compiled into the package is
a vendor decision made on the reader's behalf, and it is a machine-specific value
in tracked code. Worse, it silently picks *which company gets billed* when a key
is present in the ambient environment. Configuration that identifies a vendor,
an account, or an endpoint has no fallback here. Either all three values are set
and the LLM is used, or none are and ModelGuard writes deterministic template
prose. Setting some but not all is a mistake, and it fails loudly rather than
quietly downgrading (see :mod:`modelguard.env` and D-029).

Nothing about detection depends on any of this. The LLM writes prose. See
:mod:`modelguard.agent.narrate`.

Adding a provider
-----------------
Add a row to :data:`_PROVIDERS`. Every binding below was introspected from the
installed package: all three chat classes accept the same four keyword
arguments (``model``, ``api_key``, ``temperature``, ``max_tokens``), though the
underlying field names differ, so the call site stays uniform. Verify that for
any provider you add before adding it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from modelguard.env import ConfigError, optional_value

ENV_LLM_PROVIDER = "MODELGUARD_LLM_PROVIDER"
ENV_LLM_MODEL = "MODELGUARD_LLM_MODEL"
ENV_LLM_API_KEY = "MODELGUARD_LLM_API_KEY"

#: provider name -> (module to import, chat class, pip package that supplies it).
#: The pip package is named so a missing import produces an actionable error.
_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("langchain_anthropic", "ChatAnthropic", "langchain-anthropic"),
    "openai": ("langchain_openai", "ChatOpenAI", "langchain-openai"),
    "google": ("langchain_google_genai", "ChatGoogleGenerativeAI", "langchain-google-genai"),
}

SUPPORTED_PROVIDERS: frozenset[str] = frozenset(_PROVIDERS)

#: Determinism: the same finding produces the same prose, so a rerun's diff is
#: empty and the recorded demo is reproducible. Not configuration; a design law.
TEMPERATURE = 0.0

#: Enough for a short paragraph, and a hard ceiling on a runaway generation.
MAX_TOKENS = 400

#: Seconds to wait for the narrator before giving up and writing the template.
#: The prose is a nice-to-have and the finding is not: no incident should wait
#: on somebody else's API. In `scan` an unbounded call means a hung terminal;
#: in `watch` it means a daemon that stops polling and stops reporting while
#: still looking alive, the specific irony a reliability tool exists to avoid
#: (F13, docs/plan/07). Deliberately short, since this sits between a detected
#: failure and the incident that reports it. Every provider names its own
#: field differently (ChatAnthropic's is `default_request_timeout`, ChatOpenAI's
#: is `request_timeout`, ChatGoogleGenerativeAI's is `timeout`), but all three
#: accept `timeout` as a constructor keyword, verified against the installed
#: langchain-anthropic 1.4.8, langchain-openai 1.3.4, langchain-google-genai
#: 4.2.7 (root CLAUDE.md rule 7), so the call site below stays uniform.
LLM_TIMEOUT_SECONDS = 30.0

#: One attempt, not LangChain's own default retry behaviour, which would
#: multiply the timeout by the retry count: a narrator that takes two minutes
#: to fail has already lost to the template it falls back to.
LLM_MAX_RETRIES = 0


class LLMUnavailableError(RuntimeError):
    """The configured provider's package is not installed."""


@dataclass(frozen=True)
class LLMConfig:
    """A fully specified, ready-to-build language model.

    The key is a :class:`~pydantic.SecretStr`, so it does not appear in a repr, a
    traceback frame dump, or a log line that formats this object.
    """

    provider: str
    model: str
    api_key: SecretStr

    def __post_init__(self) -> None:
        """Reject a provider we have no verified binding for."""
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ConfigError(
                f"{self.provider!r} is not a supported LLM provider; "
                f"{ENV_LLM_PROVIDER} must be one of {sorted(SUPPORTED_PROVIDERS)}"
            )

    def secret(self) -> str:
        """Return the raw key. Call this only to hand it to the provider client."""
        return self.api_key.get_secret_value()


def llm_config_from_env() -> LLMConfig | None:
    """Build the LLM configuration, or report that there is none.

    Returns:
        The configuration when all three variables are set, or None when none of
        them are, which means "run without an LLM" and is a supported mode.

    Raises:
        ConfigError: Some, but not all, of the three are set, or the provider is
            not supported. A half-configured LLM is a mistake: silently writing
            template prose would hide it until someone noticed the reports had
            gone bland. The message names the missing variables, never a value.
    """
    provider = optional_value(ENV_LLM_PROVIDER)
    model = optional_value(ENV_LLM_MODEL)
    api_key = optional_value(ENV_LLM_API_KEY)

    present = {
        ENV_LLM_PROVIDER: provider,
        ENV_LLM_MODEL: model,
        ENV_LLM_API_KEY: api_key,
    }
    missing = sorted(name for name, value in present.items() if value is None)

    if len(missing) == len(present):
        return None
    if missing:
        raise ConfigError(
            "the LLM is partially configured: "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set. "
            "Set all three to use an LLM, or none of them to write template prose."
        )

    # mypy: the three are non-None here, since `missing` is empty.
    assert provider is not None and model is not None and api_key is not None
    return LLMConfig(provider=provider, model=model, api_key=SecretStr(api_key))


def build_chat_model(config: LLMConfig) -> Any:
    """Instantiate the provider's LangChain chat model.

    Imported lazily: each provider's package is an optional extra, and a run with
    no LLM must not need any of them installed.

    Raises:
        LLMUnavailableError: The provider's package is not installed.
    """
    module_name, class_name, package = _PROVIDERS[config.provider]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise LLMUnavailableError(
            f"{ENV_LLM_PROVIDER}={config.provider} needs the {package} package: "
            f'pip install -e ".[{config.provider}]"'
        ) from exc

    chat_class = getattr(module, class_name)
    return chat_class(
        model=config.model,
        api_key=config.api_key,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )
