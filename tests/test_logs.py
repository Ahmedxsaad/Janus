"""Log rendering, checked offline.

The failure this guards is quiet: a deployment asks for JSON, gets logfmt, and
finds out weeks later through a dashboard that was never populated. So an
unknown format fails loudly, and both renderings are asserted to carry the same
fields.
"""

from __future__ import annotations

import json
import logging

import pytest

from janus.env import ConfigError
from janus.logs import (
    ENV_LOG_FORMAT,
    LOG_FIELDS,
    JsonFormatter,
    configure_logging,
    log_format_from_env,
    logfmt,
)


def _record(message: str = "scan complete %s", *args: object, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="janus.agent.pipeline",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestFormatSelection:
    def test_the_default_is_the_human_format(self, monkeypatch):
        monkeypatch.delenv(ENV_LOG_FORMAT, raising=False)

        assert log_format_from_env() == "text"

    def test_json_is_selected_case_insensitively(self, monkeypatch):
        monkeypatch.setenv(ENV_LOG_FORMAT, "JSON")

        assert log_format_from_env() == "json"

    def test_an_unknown_format_fails_loudly_rather_than_defaulting(self, monkeypatch):
        """A typo that silently restored logfmt is discovered by an empty dashboard."""
        monkeypatch.setenv(ENV_LOG_FORMAT, "jsonl")

        with pytest.raises(ConfigError) as exc:
            log_format_from_env()

        assert ENV_LOG_FORMAT in str(exc.value)


class TestJsonFormatter:
    def test_structured_fields_become_top_level_keys(self):
        """Nested under one key they would need a pipeline-side transform to index."""
        fields = {"run_id": "scan-1", "findings": 2, "detect_ms": 41}
        rendered = JsonFormatter().format(_record(**{LOG_FIELDS: fields}))

        parsed = json.loads(rendered)
        assert parsed["run_id"] == "scan-1"
        assert parsed["findings"] == 2
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "janus.agent.pipeline"

    def test_a_caller_cannot_overwrite_the_level_a_log_search_depends_on(self):
        rendered = JsonFormatter().format(_record(**{LOG_FIELDS: {"level": "DEBUG"}}))

        assert json.loads(rendered)["level"] == "INFO"

    def test_lazy_percent_arguments_are_applied(self):
        rendered = JsonFormatter().format(_record("scan complete %s", "run_id=scan-1"))

        assert json.loads(rendered)["message"] == "scan complete run_id=scan-1"

    def test_a_record_with_no_fields_still_renders(self):
        """Every other logger in the process, and every library's, goes through this."""
        parsed = json.loads(JsonFormatter().format(_record("plain message")))

        assert parsed["message"] == "plain message"

    def test_an_unserializable_value_degrades_instead_of_killing_the_log_call(self):
        rendered = JsonFormatter().format(_record(**{LOG_FIELDS: {"path": object()}}))

        assert "object object at" in json.loads(rendered)["path"]


def test_logfmt_keeps_the_order_it_was_given():
    """The line is read left to right by a human; alphabetising it would not help."""
    assert logfmt({"run_id": "scan-1", "findings": 0}) == "run_id=scan-1 findings=0"


class TestConfigureLogging:
    def test_json_configuration_emits_one_parseable_object_per_line(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv(ENV_LOG_FORMAT, "json")
        # Root handlers persist between tests, and basicConfig is a deliberate
        # no-op once any exist, so the slate is cleared first.
        monkeypatch.setattr(logging.root, "handlers", [])

        chosen = configure_logging()
        logging.getLogger("janus.test").info(
            "scan complete %s", "run_id=scan-9", extra={LOG_FIELDS: {"run_id": "scan-9"}}
        )

        assert chosen == "json"
        line = capsys.readouterr().err.strip()
        assert json.loads(line)["run_id"] == "scan-9"

    def test_text_configuration_is_not_json(self, monkeypatch, capsys):
        monkeypatch.setenv(ENV_LOG_FORMAT, "text")
        monkeypatch.setattr(logging.root, "handlers", [])

        chosen = configure_logging()
        logging.getLogger("janus.test").info("scan complete %s", "run_id=scan-9")

        assert chosen == "text"
        captured = capsys.readouterr().err
        assert "run_id=scan-9" in captured
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.strip())
