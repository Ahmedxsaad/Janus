"""How a long-running ModelGuard process talks to a log pipeline.

``modelguard watch`` is the one entry point that runs unattended for days, which
makes it the one whose output somebody eventually ships to Loki, CloudWatch, or
an ELK stack. Two readers want two different things from the same line:

* A human tailing the process wants ``scan complete run_id=... findings=1``,
  readable at a glance without a parser.
* A log pipeline wants fields it can index and alert on, without a per-tool
  regular expression that breaks the first time a key is added.

Both are served from one source of truth. A caller logs its message and attaches
the same facts as a mapping under :data:`LOG_FIELDS`; the human format renders
them as ``logfmt`` and the JSON format emits them as object keys. Nothing is
written twice, so the two renderings cannot drift.

Selected by ``MODELGUARD_LOG_FORMAT``, which is neither identity nor an
algorithm parameter but an operational preference of whoever runs the process.
It defaults to ``text``, because the default reader is a human at a terminal,
and an unknown value fails loudly rather than quietly picking one (root
CLAUDE.md rule 6).

What never appears in a log line
--------------------------------
Identifiers and counts only: URNs, hop counts, durations, the numbers a detector
measured. No aspect content, no narrative prose, and no credential. That rule is
older than this module (modelguard/CLAUDE.md rule 4) and JSON does not relax it:
a structured field is easier to ship somewhere else, not safer to fill.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from typing import Any, TextIO

from modelguard.env import ConfigError, optional_value

ENV_LOG_FORMAT = "MODELGUARD_LOG_FORMAT"

#: The ``LogRecord`` attribute carrying a caller's structured fields. Prefixed so
#: it cannot collide with one of logging's own attributes, which ``extra`` would
#: otherwise overwrite silently.
LOG_FIELDS = "modelguard_fields"

TEXT = "text"
JSON = "json"
_FORMATS = (TEXT, JSON)


def logfmt(fields: Mapping[str, Any]) -> str:
    """Render fields as ``key=value`` pairs, in the order given.

    One ``logfmt`` parser away from a metrics pipeline, and still readable in a
    terminal. Values are stringified as-is: every caller passes an identifier or
    a number, so nothing here needs quoting, and adding quoting rules for values
    that never arrive would be a parser nobody needs.
    """
    return " ".join(f"{key}={value}" for key, value in fields.items())


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object per line.

    The caller's structured fields are merged into the top level rather than
    nested under a key, because that is what makes them indexable without a
    pipeline-side transform. Logging's own attributes win on a name collision:
    a caller must not be able to overwrite the level or the timestamp a log
    search depends on.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a compact JSON document."""
        fields: dict[str, Any] = dict(getattr(record, LOG_FIELDS, {}) or {})
        fields.update(
            {
                "time": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                # getMessage() applies the %-args, so a caller's lazy formatting
                # is honored here exactly as it is in the text format.
                "message": record.getMessage(),
            }
        )
        if record.exc_info:
            fields["exception"] = self.formatException(record.exc_info)
        # default=str so an unexpected value (a Path, an enum) degrades to its
        # string form instead of killing the log call that was reporting a
        # problem in the first place.
        return json.dumps(fields, default=str)


def log_format_from_env() -> str:
    """Return the configured format, or ``text``.

    Raises:
        ConfigError: The variable is set to something that is not a format. A
            typo must not silently restore the default: somebody who asked for
            JSON and got logfmt discovers it through a broken dashboard.
    """
    raw = optional_value(ENV_LOG_FORMAT)
    if raw is None:
        return TEXT
    value = raw.lower()
    if value not in _FORMATS:
        raise ConfigError(f"{ENV_LOG_FORMAT}={raw!r} is not a log format. Use one of: text, json.")
    return value


def configure_logging(*, level: int = logging.INFO, stream: TextIO | None = None) -> str:
    """Install the configured formatter on the root logger.

    Called by the commands that run unattended, never at import: an application
    embedding ModelGuard as a library owns its own logging, and a package that
    configures handlers on import takes that away. ``basicConfig`` is itself a
    no-op once handlers exist, which is the second half of the same guarantee.

    Args:
        level: The threshold to install.
        stream: Where lines go. Defaults to stderr, so stdout stays free for a
            report a caller may be piping.

    Returns:
        The format that was installed, so the caller can say which one it is
        using rather than leaving the operator to guess from the first line.

    Raises:
        ConfigError: ``MODELGUARD_LOG_FORMAT`` is set to an unknown value.
    """
    chosen = log_format_from_env()
    handler = logging.StreamHandler(stream or sys.stderr)
    if chosen == JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level, handlers=[handler])
    return chosen
