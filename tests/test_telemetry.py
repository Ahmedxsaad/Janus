"""The OTLP metrics export (T-17). Offline: nothing here reaches a collector.

The obligations are small because the feature is: it must be off unless
configured, it must fail loudly rather than exporting nowhere, it must never put
a header token where a log can see it, and it must never be able to take a scan
down.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr

from janus.env import ConfigError
from janus.logs import LOG_FIELDS
from janus.telemetry import (
    ENV_OTEL_ENDPOINT,
    ENV_OTEL_HEADERS,
    ScanMetricsHandler,
    endpoint_from_env,
    headers_from_env,
    parse_headers,
    start_exporter,
)


class RecordingInstrument:
    """Stands in for an OTel counter or histogram, remembering what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, str] | None]] = []

    def add(self, amount: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((amount, attributes))

    def record(self, amount: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((amount, attributes))


class RecordingMeter:
    """A Meter whose instruments record instead of exporting."""

    def __init__(self) -> None:
        self.instruments: dict[str, RecordingInstrument] = {}

    def _make(self, name: str, **_: object) -> RecordingInstrument:
        instrument = RecordingInstrument()
        self.instruments[name] = instrument
        return instrument

    create_counter = _make
    create_histogram = _make


def scan_record(**fields: object) -> logging.LogRecord:
    """A completed-scan log record, shaped the way ``_log_scan`` emits one."""
    payload = {
        "run_id": "scan-abc",
        "dry_run": "false",
        "findings": 2,
        "writes": 2,
        "warnings": 0,
        "detect_ms": 350,
        "total_ms": 900,
        **fields,
    }
    record = logging.LogRecord("m", logging.INFO, "f", 1, "scan complete", None, None)
    setattr(record, LOG_FIELDS, payload)
    return record


def test_no_endpoint_means_no_exporter_and_no_import():
    """Unset is the default and is not an error: this is an optional export."""
    assert start_exporter(endpoint=None) is None


def test_an_endpoint_is_read_from_the_environment_with_no_fallback(monkeypatch):
    """An address identifies a system, so it has no default (root rule 6a)."""
    monkeypatch.delenv(ENV_OTEL_ENDPOINT, raising=False)
    assert endpoint_from_env() is None

    monkeypatch.setenv(ENV_OTEL_ENDPOINT, "http://collector:4318/v1/metrics")
    assert endpoint_from_env() == "http://collector:4318/v1/metrics"


def test_headers_are_carried_as_a_secret_not_a_string(monkeypatch):
    """Root rule 6d: this is where an authenticated collector's token goes."""
    monkeypatch.setenv(ENV_OTEL_HEADERS, "x-api-key=s3cret-token-value")

    headers = headers_from_env()

    assert isinstance(headers, SecretStr)
    # The whole point of SecretStr: the token is not in the repr, so it cannot
    # reach a log line through an f-string or a traceback.
    assert "s3cret-token-value" not in repr(headers)
    assert parse_headers(headers) == {"x-api-key": "s3cret-token-value"}


def test_no_headers_is_a_complete_configuration():
    """A collector inside the same cluster needs none, so this is not a group."""
    assert parse_headers(None) == {}


def test_a_malformed_header_pair_fails_without_quoting_the_value():
    """The message names the variable, never the value, which carries the token."""
    with pytest.raises(ConfigError) as raised:
        parse_headers(SecretStr("x-api-key s3cret-token-value"))

    assert ENV_OTEL_HEADERS in str(raised.value)
    assert "s3cret-token-value" not in str(raised.value)


def test_a_configured_endpoint_with_the_extra_missing_fails_loudly(monkeypatch):
    """Better at startup than after a week of exporting nowhere.

    Simulated by making the exporter import fail, which is exactly what a core
    install without the `[otel]` extra does.
    """
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("opentelemetry"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.delenv(ENV_OTEL_HEADERS, raising=False)

    with pytest.raises(ConfigError) as raised:
        start_exporter(endpoint="http://collector:4318/v1/metrics")

    assert "otel" in str(raised.value)


def test_a_completed_scan_records_its_own_numbers():
    """One measurement, two renderings: the handler reads the fields _log_scan set."""
    meter = RecordingMeter()
    ScanMetricsHandler(meter).emit(scan_record())

    assert meter.instruments["janus.scan.completed"].calls == [(1, {"dry_run": "false"})]
    assert meter.instruments["janus.scan.findings"].calls == [(2, {"dry_run": "false"})]
    assert meter.instruments["janus.scan.detect.duration"].calls == [(350, {"dry_run": "false"})]


def test_a_dry_run_is_labelled_rather_than_dropped():
    """A dashboard omitting every gate run would under-report what CI produces most."""
    meter = RecordingMeter()
    ScanMetricsHandler(meter).emit(scan_record(dry_run="true"))

    assert meter.instruments["janus.scan.completed"].calls == [(1, {"dry_run": "true"})]


def test_a_log_record_that_is_not_a_completed_scan_is_ignored():
    """Every other line in the process goes through this handler too.

    A phase line carries fields but no timing, and counting one as a scan would
    inflate the completed counter by four on every single scan.
    """
    meter = RecordingMeter()
    handler = ScanMetricsHandler(meter)

    handler.emit(logging.LogRecord("m", logging.INFO, "f", 1, "plain line", None, None))
    phase = logging.LogRecord("m", logging.INFO, "f", 1, "lineage walk", None, None)
    setattr(phase, LOG_FIELDS, {"argos_phase": "sniffing", "urn": "urn:li:dataset:x"})
    handler.emit(phase)

    assert meter.instruments["janus.scan.completed"].calls == []


def test_a_broken_metric_call_cannot_take_the_scan_down(monkeypatch):
    """The scan is the product; this is a view of it, and a view must not raise.

    Routed through logging's own handleError, which is what the module provides
    for a handler that fails.
    """
    meter = RecordingMeter()
    handler = ScanMetricsHandler(meter)
    handled: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", handled.append)

    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("collector is on fire")

    monkeypatch.setattr(meter.instruments["janus.scan.completed"], "add", explode)
    handler.emit(scan_record())

    assert len(handled) == 1
