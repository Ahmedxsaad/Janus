from __future__ import annotations

import pathlib

import pytest
from datahub.metadata.urns import GlossaryTermUrn

from janus import env as env_module
from janus.env import (
    ConfigError,
    optional_float,
    optional_int,
    optional_urn_list,
    optional_value,
    required_value,
    scrub,
)

SECRET = "sk-super-secret-key-1234567890"


@pytest.fixture(autouse=True)
def _already_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the loader so these tests never read the developer's real .env."""
    monkeypatch.setattr(env_module, "_loaded", True)


# --------------------------------------------------------------------------
# Blank means unset
# --------------------------------------------------------------------------


def test_an_unset_variable_is_none():
    assert optional_value("JANUS_TEST_ABSENT") is None


def test_a_blank_variable_is_none_because_the_example_file_ships_blanks(monkeypatch):
    monkeypatch.setenv("JANUS_TEST_BLANK", "   ")
    assert optional_value("JANUS_TEST_BLANK") is None


def test_a_value_is_stripped(monkeypatch):
    monkeypatch.setenv("JANUS_TEST_PADDED", "  value  ")
    assert optional_value("JANUS_TEST_PADDED") == "value"


# --------------------------------------------------------------------------
# Required values fail loudly, and never echo themselves
# --------------------------------------------------------------------------


def test_a_missing_required_value_names_the_variable_and_the_remedy():
    with pytest.raises(ConfigError, match=r"JANUS_TEST_REQUIRED is not set\. Do the thing\."):
        required_value("JANUS_TEST_REQUIRED", "Do the thing.")


def test_a_present_required_value_is_returned(monkeypatch):
    monkeypatch.setenv("JANUS_TEST_REQUIRED", "here")
    assert required_value("JANUS_TEST_REQUIRED", "hint") == "here"


# --------------------------------------------------------------------------
# A typo in a threshold must never restore the default
# --------------------------------------------------------------------------


def test_an_unset_number_falls_back_to_the_documented_default():
    assert optional_float("JANUS_TEST_FLOAT", 6.0) == 6.0
    assert optional_int("JANUS_TEST_INT", 3) == 3


def test_a_set_number_overrides_the_default(monkeypatch):
    monkeypatch.setenv("JANUS_TEST_FLOAT", "12.5")
    monkeypatch.setenv("JANUS_TEST_INT", "5")
    assert optional_float("JANUS_TEST_FLOAT", 6.0) == 12.5
    assert optional_int("JANUS_TEST_INT", 3) == 5


@pytest.mark.parametrize("value", ["abc", "0", "-1", ""])
def test_an_unusable_float_fails_rather_than_silently_defaulting(monkeypatch, value: str):
    monkeypatch.setenv("JANUS_TEST_FLOAT", value)
    if value == "":
        # Blank is "unset", which is a different thing from "wrong".
        assert optional_float("JANUS_TEST_FLOAT", 6.0) == 6.0
        return
    with pytest.raises(ConfigError, match="JANUS_TEST_FLOAT"):
        optional_float("JANUS_TEST_FLOAT", 6.0)


@pytest.mark.parametrize("value", ["abc", "2.5", "0", "-1"])
def test_an_unusable_int_fails_rather_than_silently_defaulting(monkeypatch, value: str):
    monkeypatch.setenv("JANUS_TEST_INT", value)
    with pytest.raises(ConfigError, match="JANUS_TEST_INT"):
        optional_int("JANUS_TEST_INT", 3)


# --------------------------------------------------------------------------
# scrub: for text we did not write
# --------------------------------------------------------------------------


def test_scrub_removes_every_occurrence_of_a_secret():
    text = f"Bearer {SECRET} rejected; retried with {SECRET}"
    cleaned = scrub(text, SECRET)
    assert SECRET not in cleaned
    assert cleaned.count("[redacted]") == 2


def test_scrub_keeps_the_rest_of_the_message_readable():
    assert scrub(f"401 Unauthorized: {SECRET}", SECRET) == "401 Unauthorized: [redacted]"


def test_scrub_tolerates_none_and_absent_secrets():
    assert scrub("nothing to hide", None, SECRET) == "nothing to hide"


def test_scrub_ignores_secrets_too_short_to_be_credentials():
    """Redacting a two-character string would corrupt the message for no gain."""
    assert scrub("a cat sat on a mat", "at") == "a cat sat on a mat"


# --------------------------------------------------------------------------
# The rule, enforced. Not a convention anyone has to remember.
# --------------------------------------------------------------------------


def _package_sources() -> list[pathlib.Path]:
    root = pathlib.Path(env_module.__file__).parent
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_only_env_py_reads_the_process_environment():
    """CONTRIBUTING.md: configuration enters through janus/env.py, nowhere else.

    A module that reads os.environ directly bypasses .env loading, so whether it
    sees a configured value depends on what ran before it. That is how a freshness
    SLA set in .env came to be silently ignored.
    """
    offenders = []
    for path in _package_sources():
        if path.name == "env.py":
            continue
        source = path.read_text()
        # Ignore prose: only flag a real attribute access or call.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith(("#", "*", ":"))
        )
        if "os.environ" in code or "os.getenv" in code:
            offenders.append(str(path.name))

    assert not offenders, f"these modules read the environment directly: {offenders}"


def test_only_env_py_loads_the_dotenv_file():
    offenders = [
        path.name
        for path in _package_sources()
        if path.name != "env.py" and "load_dotenv(" in path.read_text()
    ]
    assert not offenders, f"these modules load .env themselves: {offenders}"


def test_no_module_hardcodes_a_vendor_model_id_or_a_provider_key_name():
    """No default provider, no default model, no vendor-specific key variable."""
    forbidden = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
    offenders: list[str] = []
    for path in _package_sources():
        code = "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.lstrip().startswith(("#", "*", ":"))
        )
        offenders += [f"{path.name}:{token}" for token in forbidden if token in code]

    assert not offenders, f"vendor-specific configuration is hardcoded: {offenders}"


# --------------------------------------------------------------------------
# A classification URN that cannot match is a typo, not a configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "PII",  # the glossary term's name, the obvious thing to type
        "not-a-urn",
        "urn:li:tag:PII",  # right idea, wrong variable: this one takes terms
    ],
)
def test_a_value_that_could_never_match_a_column_is_refused(monkeypatch, value):
    """These variables switch a detector on. A dead one reads as a clean catalog.

    Nothing in a catalog carries a term whose URN is "PII", so the detector
    would run, compare against nothing, and report clean forever, while
    `janus coverage` counted it as configured. Setting one variable to the
    string "not-a-urn" took a real catalog from 33% to 93% guarded.
    """
    monkeypatch.setenv("JANUS_TEST_TERMS", value)

    with pytest.raises(ConfigError) as caught:
        optional_urn_list("JANUS_TEST_TERMS", GlossaryTermUrn)

    # Names the variable and quotes the offending entry: these are public
    # identifiers, so unlike a credential they can be echoed, and a message that
    # declined to say which entry was wrong would be unfixable in a long list.
    assert "JANUS_TEST_TERMS" in str(caught.value)
    assert value in str(caught.value)


def test_well_formed_urns_are_returned_as_the_user_wrote_them(monkeypatch):
    """Validated, not normalised: callers compare these as strings against the graph."""
    monkeypatch.setenv("JANUS_TEST_TERMS", "urn:li:glossaryTerm:PII, urn:li:glossaryTerm:PHI")

    assert optional_urn_list("JANUS_TEST_TERMS", GlossaryTermUrn) == (
        "urn:li:glossaryTerm:PII",
        "urn:li:glossaryTerm:PHI",
    )


def test_unset_stays_unset(monkeypatch):
    """Unset means the detector reports itself not evaluated, which is not an error."""
    monkeypatch.delenv("JANUS_TEST_TERMS", raising=False)

    assert optional_urn_list("JANUS_TEST_TERMS", GlossaryTermUrn) == ()
