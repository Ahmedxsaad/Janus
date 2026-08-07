"""The single place Janus reads its environment.

Every configuration value: connection endpoints, credentials, the LLM provider
and model, and the scan thresholds, enters the process here and nowhere else.
Modules ask this module; they never touch :data:`os.environ` themselves.

Why one loader
--------------
``.env`` is loaded by :func:`load_environment`, which every read goes through.
Before this module existed, ``load_dotenv`` ran inside ``client.connect()`` only,
so whether a ``JANUS_*`` value in ``.env`` was honored depended on whether
something had already opened a DataHub connection. ``janus scan`` read its
thresholds *before* connecting, so a freshness SLA set in ``.env`` was silently
ignored and the built-in default was used instead. Configuration that depends on
call order is configuration that lies.

No fallbacks for identity, ever
-------------------------------
A hardcoded default for anything that identifies a system, an account, or a
vendor, a server URL, a username, an API key, a provider name, a model name, is
a machine-specific value living in tracked code. It turns a missing ``.env`` into
a silent connection to the wrong place, or a silent call to the wrong vendor's
API, billed to whoever's key happens to be in the ambient environment. Those
values have no defaults here: they are either configured or absent, and absent
fails loudly (see CONTRIBUTING.md).

Algorithm parameters are different. A six hour freshness SLA or a three hop cap
is a documented product decision, reproducible on every machine, and it belongs
in :mod:`janus.config` with a default. It is not identity.

Secrets never appear in a log line, an exception message, or a repr. Errors name
the *variable*, never its value. :func:`scrub` exists for the one case we cannot
control: text that came back from somebody else's SDK.
"""

from __future__ import annotations

import os
from typing import TypeVar

from datahub.metadata.urns import Urn
from datahub.utilities.urns.error import InvalidUrnError
from dotenv import find_dotenv, load_dotenv

#: Any SDK URN class. The helpers below only ever call ``from_string`` and read
#: ``ENTITY_TYPE`` on it, both of which every subclass carries.
_UrnT = TypeVar("_UrnT", bound=Urn)

#: Set once ``.env`` has been loaded, so repeated reads do not re-parse the file
#: and, more importantly, so load order can never change what a caller sees.
_loaded = False


class ConfigError(ValueError):
    """Configuration is missing, or is present but unusable.

    Never carries a *credential*: a message here may be logged, and some of the
    values this module reads are secrets. Identity values (URLs, keys, provider
    names) are reported by variable name only, which is why
    :func:`required_value` takes a hint instead of echoing what it found.

    Algorithm parameters are the deliberate exception. :func:`optional_float` and
    :func:`optional_int` quote the offending value back, because
    ``JANUS_MAX_HOPS='3 '`` is a typo nobody can fix from a message that
    declines to say what was wrong, and a hop cap is not a secret. Anything
    secret-bearing must go through :func:`required_value` or
    :func:`optional_value`, never through the numeric readers.
    """


def load_environment(*, force: bool = False) -> None:
    """Load ``.env`` into the process environment, at most once.

    A variable already exported in the shell, or set by CI, wins over ``.env``:
    ``.env`` is the developer's local default, not an override.

    Args:
        force: Re-read the file even if it was already loaded. Only tests need
            this, after they have manipulated the environment.
    """
    global _loaded
    if _loaded and not force:
        return
    load_dotenv(find_dotenv(usecwd=True), override=False)
    _loaded = True


def optional_value(name: str) -> str | None:
    """Return a configured value, or None when it is unset or blank.

    ``.env.example`` ships its optional keys with empty values, so an empty
    string means "not configured" rather than "the empty value".
    """
    load_environment()
    value = os.environ.get(name, "").strip()
    return value or None


def required_value(name: str, hint: str) -> str:
    """Return a configured value, or fail loudly.

    Args:
        name: The environment variable to read.
        hint: What the reader should do about it. Must not contain a credential.

    Raises:
        ConfigError: The variable is unset or blank. The message names the
            variable, never a value.
    """
    value = optional_value(name)
    if value is None:
        raise ConfigError(f"{name} is not set. {hint}")
    return value


def optional_list(name: str) -> tuple[str, ...]:
    """Return a comma-separated value as a tuple, or empty when it is unset.

    Blanks and surrounding whitespace are dropped, so a trailing comma or a
    value pasted across two lines is not an empty entry that would then be
    compared against every URN in the graph and match nothing.

    Order is preserved and duplicates are kept: this is configuration a human
    typed, and reordering or silently deduplicating it makes an error report
    harder to line up against what they wrote.
    """
    raw = optional_value(name)
    if raw is None:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def optional_urn_list(name: str, urn_type: type[_UrnT]) -> tuple[str, ...]:
    """Return a comma-separated list of URNs of one type, or empty when unset.

    The same parsing as :func:`optional_list`, plus the check that every entry is
    actually a URN of the expected kind.

    That check is not pedantry, it is the difference between a configured check
    and a check that cannot fire. These variables switch on the classification
    detectors by naming the glossary terms or tags an organization already marks
    columns with. A value that is not a URN at all (``PII`` rather than
    ``urn:li:glossaryTerm:PII``, the obvious thing to type) matches no column in
    any catalog, so the detector runs, compares against nothing, and reports
    clean forever. Worse, coverage counts it as configured: a catalog went from
    33% to 93% guarded by setting one variable to the string ``not-a-urn``,
    which is exactly the silent pass this project exists to catch.

    A URN of the wrong entity type is rejected for the same reason and is the
    likelier typo of the two, since the term and tag variables sit next to each
    other in ``.env.example``.

    Args:
        name: The environment variable.
        urn_type: The SDK URN class every entry must parse as.

    Returns:
        The entries, in the order they were written, unparsed. Callers compare
        these as strings against what the graph holds, so what is stored stays
        the user's own text.

    Raises:
        ConfigError: An entry is not a URN, or is a URN of another type. The
            message quotes the offending entry: these are public identifiers,
            never credentials (CONTRIBUTING.md).
    """
    # ENTITY_TYPE is declared on the concrete URN classes, not on the Urn base
    # the TypeVar is bound to, so it is read defensively rather than typed.
    kind = getattr(urn_type, "ENTITY_TYPE", urn_type.__name__)
    values = optional_list(name)
    for value in values:
        try:
            urn_type.from_string(value)
        except InvalidUrnError as exc:
            raise ConfigError(
                # rstrip: the SDK's own message ends in a period, and two in a
                # row in the middle of a sentence reads like a typo.
                f"{name}={value!r} is not a {kind} URN: {str(exc).rstrip('.')}. A value that is "
                "not one matches no column, so the check would run against "
                "nothing and report clean."
            ) from exc
    return values


def optional_float(name: str, default: float) -> float:
    """Return a positive float override, or the default.

    Raises:
        ConfigError: The variable is set but is not a positive number. Falling
            back to the default here would hide a typo in a threshold.
    """
    raw = optional_value(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not a number") from exc
    if value <= 0:
        raise ConfigError(f"{name}={raw!r} must be positive")
    return value


def optional_int(name: str, default: int) -> int:
    """Return a positive integer override, or the default.

    Raises:
        ConfigError: The variable is set but is not a positive integer.
    """
    raw = optional_value(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name}={raw!r} must be positive")
    return value


def scrub(text: str, *secrets: str | None) -> str:
    """Replace every occurrence of each secret in ``text`` with a placeholder.

    For text we did not write: an exception message from a vendor SDK, an HTTP
    error body. We cannot promise such a string is free of the credential we just
    handed it, so we remove the credential before the string reaches a log.

    Short secrets are ignored: replacing a two-character string would corrupt the
    message without protecting anything real.
    """
    cleaned = text
    for secret in secrets:
        if secret and len(secret) >= 8:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned
