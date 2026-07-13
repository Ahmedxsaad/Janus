from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr

from modelguard.agent import narrate as narrate_module
from modelguard.agent.narrate import (
    MAX_NARRATIVE_CHARS,
    NarrativeSource,
    fact_block,
    incident_description,
    narrate,
    template_narrative,
)
from modelguard.llm import LLMConfig
from tests.conftest import make_finding as _finding

SECRET = "sk-super-secret-key-1234567890"


def _llm(provider: str = "anthropic") -> LLMConfig:
    return LLMConfig(provider=provider, model="a-model", api_key=SecretStr(SECRET))


# --------------------------------------------------------------------------
# The deterministic half, which every other path falls back to
# --------------------------------------------------------------------------


def test_the_fact_block_states_the_measured_lag_and_the_sla():
    block = fact_block(_finding())
    assert "30.0 hours ago" in block
    assert "freshness SLA of 6.0 hours" in block
    assert "Credit Risk v3" in block
    # The provenance disclaimer must survive: it is what keeps the report honest.
    assert "did not query the warehouse" in block


def test_the_template_names_the_live_model_and_says_it_is_serving():
    text = template_narrative(_finding(live=True))
    assert "Credit Risk v3" in text
    assert "live endpoint" in text


def test_the_template_de_escalates_when_no_model_is_serving():
    text = template_narrative(_finding(live=False))
    assert "none of them currently serving" in text
    assert "live endpoint" not in text


def test_the_template_says_nothing_is_scoring_when_no_model_consumes_the_table():
    text = template_narrative(_finding(with_model=False))
    assert "No model consumes" in text


# --------------------------------------------------------------------------
# The narrator reads no environment and knows no vendor
# --------------------------------------------------------------------------


def test_the_narrator_module_never_touches_the_environment():
    """Configuration enters through modelguard.env only. No os.environ here."""
    source = (
        narrate_module.__file__.replace(".pyc", ".py")  # defensive, .py in practice
    )
    with open(source) as handle:
        text = handle.read()
    assert "os.environ" not in text
    assert "getenv" not in text


def test_the_narrator_hardcodes_no_provider_and_no_model_name():
    with open(narrate_module.__file__) as handle:
        text = handle.read()
    for vendor_string in ("claude-", "gpt-", "gemini-", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        assert vendor_string not in text, f"{vendor_string!r} is hardcoded in narrate.py"


# --------------------------------------------------------------------------
# The LLM path, and every way it is allowed to fail
# --------------------------------------------------------------------------


def test_no_llm_config_writes_the_template_without_touching_a_provider(monkeypatch):
    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("no provider may be built when llm is None")

    monkeypatch.setattr(narrate_module, "build_chat_model", _explode)

    result = narrate(_finding(), None)
    assert result.source is NarrativeSource.TEMPLATE
    assert result.assessment == template_narrative(_finding())


def test_a_failing_llm_call_degrades_to_the_template(monkeypatch):
    monkeypatch.setattr(
        narrate_module,
        "_llm_narrative",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429 rate limited")),
    )
    assert narrate(_finding(), _llm()).source is NarrativeSource.TEMPLATE


def test_an_uninstalled_provider_degrades_to_the_template(monkeypatch):
    """A missing binding is a config problem, not a reason to fail the scan."""
    from modelguard.llm import LLMUnavailableError

    def _missing(_config: LLMConfig) -> None:
        raise LLMUnavailableError("needs langchain-openai")

    monkeypatch.setattr(narrate_module, "build_chat_model", _missing)
    assert narrate(_finding(), _llm("openai")).source is NarrativeSource.TEMPLATE


def test_a_successful_llm_call_is_used_and_labelled_as_llm(monkeypatch):
    monkeypatch.setattr(narrate_module, "_llm_narrative", lambda *a, **k: "Model is at risk.")

    result = narrate(_finding(), _llm())
    assert result.source is NarrativeSource.LLM
    assert result.assessment == "Model is at risk."


def test_any_configured_provider_is_reached_through_the_same_call(monkeypatch):
    """The narrator is vendor-blind: it builds whatever llm.py hands it."""
    built: list[str] = []

    class _Response:
        text = "prose"

    class _Chat:
        def invoke(self, _messages: object) -> _Response:
            return _Response()

    def _build(config: LLMConfig) -> _Chat:
        built.append(config.provider)
        return _Chat()

    monkeypatch.setattr(narrate_module, "build_chat_model", _build)

    for provider in ("anthropic", "openai", "google"):
        assert narrate(_finding(), _llm(provider)).source is NarrativeSource.LLM
    assert built == ["anthropic", "openai", "google"]


def _chat_returning(text: str) -> object:
    class _Response:
        pass

    _Response.text = text

    class _Chat:
        def invoke(self, _messages: object) -> object:
            return _Response()

    return lambda _config: _Chat()


def test_an_over_long_generation_is_rejected(monkeypatch):
    monkeypatch.setattr(
        narrate_module, "build_chat_model", _chat_returning("x" * (MAX_NARRATIVE_CHARS + 1))
    )
    with pytest.raises(ValueError, match="over the"):
        narrate_module._llm_narrative(_finding(), _llm())


def test_an_empty_generation_is_rejected(monkeypatch):
    monkeypatch.setattr(narrate_module, "build_chat_model", _chat_returning("   "))
    with pytest.raises(ValueError, match="empty narrative"):
        narrate_module._llm_narrative(_finding(), _llm())


# --------------------------------------------------------------------------
# Secrets never reach a log line
# --------------------------------------------------------------------------


def test_a_provider_error_that_echoes_the_key_is_redacted_before_logging(monkeypatch, caplog):
    """Vendor SDKs put the failing request in the exception. We handed them the key."""
    leaky = RuntimeError(f"401 Unauthorized: Bearer {SECRET} was rejected")
    monkeypatch.setattr(
        narrate_module, "_llm_narrative", lambda *a, **k: (_ for _ in ()).throw(leaky)
    )

    with caplog.at_level(logging.WARNING):
        result = narrate(_finding(), _llm())

    assert result.source is NarrativeSource.TEMPLATE
    logged = caplog.text
    assert SECRET not in logged, "the API key was written to the log"
    assert "[redacted]" in logged
    # The operator still learns what went wrong and which vendor failed.
    assert "RuntimeError" in logged
    assert "anthropic" in logged


def test_the_config_repr_does_not_expose_the_key():
    assert SECRET not in repr(_llm())
    assert SECRET not in str(_llm())


# --------------------------------------------------------------------------
# Prompt injection: graph text is data, never instructions (OWASP LLM01)
# --------------------------------------------------------------------------


def test_graph_metadata_is_passed_to_the_llm_inside_a_delimited_untrusted_block():
    prompt = narrate_module._evidence_prompt(_finding())
    assert prompt.startswith("<evidence>")
    assert prompt.endswith("</evidence>")
    assert "lag_hours: 30.0" in prompt


def test_the_system_prompt_tells_the_model_the_evidence_is_untrusted_data():
    prompt = narrate_module._system_prompt(_finding())
    assert "UNTRUSTED DATA" in prompt
    assert "ignore" in prompt.lower()


# --------------------------------------------------------------------------
# Assembly: facts first, prose second
# --------------------------------------------------------------------------


def test_the_incident_body_leads_with_facts_and_never_replaces_them_with_prose():
    finding = _finding()
    narrative = narrate(finding, None)
    body = incident_description(finding, narrative)

    assert body.startswith(fact_block(finding))
    assert "Assessment:" in body
    assert narrative.assessment in body
