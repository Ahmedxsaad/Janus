"""OpenTelemetry metrics for the one process that runs unattended (T-17).

``janus watch`` already emits one line per completed scan carrying every
number an SLO is built from: how many findings, how many writes, how long
detection took, how long the whole scan took (03-production-hardening.md section
C.3, D-073). A team running it as a daemon wants those numbers in the same place
the rest of their platform's numbers are, and hand-parsing a log format is not
that place.

So this exports them, and it exports *only* them. There is no second source of
truth here: the exporter is a logging handler that reads the very fields
:func:`~janus.agent.pipeline._log_scan` assembles, exactly the way
:class:`~janus.argos.handler.ArgosHandler` reads the phase field. A metric
and a log line cannot disagree about a scan, because there is one measurement
and two renderings of it.

Deliberately small
------------------
Three instruments, no traces, no spans, no auto-instrumentation of the DataHub
SDK's HTTP calls. 09 section 3.3 says to do it, keep it small, and not to oversell
it, and the reason is that everything past this point is somebody else's product:
a team that wants distributed traces of GMS calls installs
``opentelemetry-instrumentation-requests`` and gets them, without this project
shipping a second, worse copy of it.

Off unless configured
---------------------
``JANUS_OTEL_ENDPOINT`` is an address: it identifies a system, so it has no
default and no fallback (root rule 6a). Unset means no exporter is built, no
dependency is imported, and ``watch`` behaves exactly as it did before. Set to
something unusable, and it fails loudly at startup rather than running for a week
and exporting nowhere.

``opentelemetry-sdk`` and the OTLP exporter live behind the ``[otel]`` extra and
are imported inside :func:`start_exporter`, never at module import, so a core
install neither needs them nor pays for them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from janus.env import ConfigError, optional_value
from janus.logs import LOG_FIELDS

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from opentelemetry.sdk.metrics import MeterProvider

logger = logging.getLogger(__name__)

ENV_OTEL_ENDPOINT = "JANUS_OTEL_ENDPOINT"
ENV_OTEL_HEADERS = "JANUS_OTEL_HEADERS"

#: What the exported metrics are attributed to. A constant, not configuration:
#: it names this project rather than the operator's system, like the orchestrator
#: string in ``writeback/process_instance.py``. An operator who could rename it
#: would produce two deployments whose series cannot be compared.
SERVICE_NAME = "janus"

#: How often the reader pushes to the collector. A watch poll is measured in
#: minutes, so a shorter interval would export mostly-empty batches, and a longer
#: one would delay an alert past the poll that caused it. An algorithm parameter,
#: so it carries a documented default (root rule 6b).
EXPORT_INTERVAL_MS = 30_000

_METRIC_PREFIX = "janus.scan"


def endpoint_from_env() -> str | None:
    """Return the configured OTLP endpoint, or None when telemetry is off.

    None is the normal case and never an error. This is an optional export of
    numbers that already exist in the log; a missing endpoint means the operator
    did not ask for it, not that something is broken.
    """
    return optional_value(ENV_OTEL_ENDPOINT)


def headers_from_env() -> SecretStr | None:
    """Return the configured OTLP headers, or None.

    Carried as a :class:`~pydantic.SecretStr` because a collector behind an API
    gateway is authenticated with a token in a header, and root rule 6d forbids
    that value reaching a log line, an exception message or a repr. Optional
    rather than part of an all-or-nothing group: a collector inside the same
    cluster needs none, so an endpoint alone is a complete configuration.
    """
    raw = optional_value(ENV_OTEL_HEADERS)
    return SecretStr(raw) if raw is not None else None


def parse_headers(headers: SecretStr | None) -> dict[str, str]:
    """Parse ``key=value,key=value`` into the mapping the exporter takes.

    Raises:
        ConfigError: A pair has no ``=``. The message names the variable and the
            expected shape, never the value, which carries the token.
    """
    if headers is None:
        return {}

    parsed: dict[str, str] = {}
    for pair in headers.get_secret_value().split(","):
        if not pair.strip():
            continue
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            raise ConfigError(
                f"{ENV_OTEL_HEADERS} is not a comma-separated list of key=value pairs."
            )
        parsed[key.strip()] = value.strip()
    return parsed


class ScanMetricsHandler(logging.Handler):
    """Record a scan's own numbers as OTel metrics, off the log record.

    A handler rather than a call inside ``run_scan``, for the reason
    :mod:`janus.argos.handler` is one: the facts are already assembled and
    already emitted, and threading an exporter through the pipeline's signature
    would create a second place a scan's numbers are stated. One measurement,
    two renderings.
    """

    def __init__(self, meter: Any) -> None:
        """Build a handler recording into ``meter``'s instruments.

        Args:
            meter: An OpenTelemetry ``Meter``. Untyped here because typing it
                would import the SDK at module import, which is exactly what the
                ``[otel]`` extra exists to avoid.
        """
        super().__init__(level=logging.INFO)
        self._scans = meter.create_counter(
            f"{_METRIC_PREFIX}.completed",
            unit="1",
            description="Scans that ran to completion.",
        )
        self._findings = meter.create_counter(
            f"{_METRIC_PREFIX}.findings",
            unit="1",
            description="Findings raised across all scans.",
        )
        self._detect = meter.create_histogram(
            f"{_METRIC_PREFIX}.detect.duration",
            unit="ms",
            description=(
                "Time the detectors themselves took. The number an MTTD budget is "
                "built from: the poll interval and DataHub's own index convergence "
                "are not in it, because this process controls neither."
            ),
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Record one completed scan, or ignore a record that is not one.

        A handler that raises breaks logging for the whole process, so every
        failure goes through :meth:`handleError`, which is what the logging
        module provides for this. A telemetry export must never be able to take
        a scan down: the scan is the product and this is a view of it.
        """
        try:
            fields = getattr(record, LOG_FIELDS, None)
            if not isinstance(fields, dict) or "detect_ms" not in fields:
                return
            # A dry run is a preview and writes nothing, so it is labelled rather
            # than dropped: a dashboard that silently omitted every `gate` run
            # would under-report exactly the scans CI produces most of.
            attributes = {"dry_run": str(fields.get("dry_run", "false"))}
            self._scans.add(1, attributes)
            self._findings.add(int(fields.get("findings", 0)), attributes)
            self._detect.record(int(fields["detect_ms"]), attributes)
        except Exception:  # noqa: BLE001 - logging must survive its own handlers
            self.handleError(record)


def start_exporter(*, endpoint: str | None = None) -> MeterProvider | None:
    """Install the metrics handler on the root logger, when one is configured.

    Called by ``janus watch``, the one entry point that runs unattended, and
    never at import: a library that installed a background exporter on import
    would export from an application that never asked for it.

    Args:
        endpoint: Override for the configured OTLP endpoint. Tests pass one; a
            caller normally passes nothing and the environment decides.

    Returns:
        The provider, so the caller can flush and shut it down, or None when no
        endpoint is configured, which is the default and is not an error.

    Raises:
        ConfigError: An endpoint is configured but the ``[otel]`` extra is not
            installed, or the headers are malformed. Both fail at startup rather
            than after a week of exporting nowhere.
    """
    target = endpoint if endpoint is not None else endpoint_from_env()
    if target is None:
        return None

    headers = parse_headers(headers_from_env())

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        raise ConfigError(
            f"{ENV_OTEL_ENDPOINT} is set but the OpenTelemetry exporter is not installed. "
            "Install it with: pip install 'janus-datahub[otel]'"
        ) from exc

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=target, headers=headers),
        export_interval_millis=EXPORT_INTERVAL_MS,
    )
    provider = MeterProvider(
        metric_readers=[reader],
        resource=Resource.create({"service.name": SERVICE_NAME}),
    )
    logging.getLogger().addHandler(ScanMetricsHandler(provider.get_meter(SERVICE_NAME)))
    # The endpoint is logged and the headers never are: an address is operational
    # information somebody needs to confirm, and a header carries a token.
    logger.info("exporting scan metrics to %s", target)
    return provider
