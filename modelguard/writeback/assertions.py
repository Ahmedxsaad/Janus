"""Emit the guarding assertion that would have caught the failure.

Detecting a stale table once is worth little; leaving behind a check that fires
the next time is the point. ModelGuard writes a freshness assertion onto the
offending table in two forms:

* **open-assertions YAML**, the portable declarative artifact (committed to
  ``examples/`` and readable by anyone), and
* **the assertion entity** in the graph, so it appears on the dataset's Quality
  tab, plus a run event recording the result ModelGuard actually measured.

What is OSS and what is not
---------------------------
``DataHubClient.assertions`` is **DataHub Cloud only**: the property imports
``acryl_datahub_cloud`` and raises ``SdkUsageError`` on OSS. Smart and anomaly
assertions, and the monitoring that evaluates assertions on a schedule, are also
Cloud features. What OSS gives us is the assertion *entity* and its *run events*,
so ModelGuard supplies the check logic itself and writes both.

The run event is not synthetic
------------------------------
``AssertionResult`` carries the freshness lag ModelGuard computed from the
dataset's ``operation`` aspect during this very scan, and ``nativeResults``
records where that number came from. The assertion's own type is
``DATASET_CHANGE``, which is precisely what the operation aspect measures, so the
declared check and the executed check are the same check. Nothing is fabricated:
if the table is fresh, this writes SUCCESS.

An upstream bug this module works around
----------------------------------------
``FixedIntervalFreshnessAssertion.get_assertion_info`` builds its schedule from
``timedelta.seconds``, not ``timedelta.total_seconds()``. A lookback of 30 hours
therefore silently emits an assertion of 6 hours (``timedelta(hours=30).seconds``
is 21600). Rather than emit a wrong assertion, :func:`build_assertion` rejects an
SLA of a day or more. Reported upstream; see docs/decision-log.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from datahub.api.entities.assertion.assertion_config_spec import AssertionsConfigSpec
from datahub.api.entities.assertion.freshness_assertion import (
    FixedIntervalFreshnessAssertion,
)
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    AssertionInfoClass,
    AssertionResultClass,
    AssertionResultTypeClass,
    AssertionRunEventClass,
    AssertionRunStatusClass,
    AssertionSourceClass,
    AssertionSourceTypeClass,
    AuditStampClass,
)
from datahub.metadata.urns import AssertionUrn, DatasetUrn

from modelguard.client import DataHubConnection
from modelguard.models import FreshnessSignal

#: The actor credited with creating the assertion.
ASSERTION_ACTOR = "urn:li:corpuser:datahub"

#: Above this the SDK's timedelta.seconds bug would truncate the lookback.
_MAX_SLA_HOURS = 24.0

#: Where the freshness number in the run event came from. Recorded on every
#: result so nobody mistakes it for a warehouse query.
EVALUATED_FROM = "datahub operation aspect (lastUpdatedTimestamp)"


class AssertionWriteError(RuntimeError):
    """The assertion could not be built from the rendered YAML."""


@dataclass(frozen=True)
class AssertionWrite:
    """The outcome of upserting a guarding assertion."""

    urn: str
    yaml_text: str
    created: bool
    """False when the assertion already existed and its definition was unchanged."""


def _format_hours(hours: float) -> str:
    """Render an hour count the way humanfriendly parses it back.

    Integral values lose their decimal point so the YAML reads "6 hours".
    """
    if hours == int(hours):
        return f"{int(hours)} hours"
    return f"{hours:g} hours"


def assertion_spec(table_urn: str, sla_hours: float, freshness_field: str) -> dict[str, Any]:
    """Build the open-assertions declaration for a table's freshness check.

    ``id_raw`` is a stable string derived from the table, so the assertion's
    generated id (a guid over entity, type, and id_raw) is the same on every run
    and reruns update one assertion rather than creating many.

    ``last_modified_field`` documents the column a warehouse-side executor would
    read. ModelGuard evaluates the check from DataHub's operation aspect instead;
    both answer the same question, "when did this table last change".
    """
    table_name = DatasetUrn.from_string(table_urn).name
    interval = _format_hours(sla_hours)
    return {
        "version": 1,
        "assertions": [
            {
                "entity": table_urn,
                "type": "freshness",
                "id_raw": f"modelguard.freshness.{table_name}",
                "description": (
                    f"ModelGuard guarding assertion: {table_name} must change at least "
                    f"every {interval}. Raised after a stale load reached a live model."
                ),
                "lookback_interval": interval,
                "last_modified_field": freshness_field,
                "schedule": {"type": "interval", "interval": interval},
            }
        ],
    }


def render_assertion_yaml(table_urn: str, sla_hours: float, freshness_field: str) -> str:
    """Render the open-assertions YAML artifact for a table."""
    return yaml.safe_dump(
        assertion_spec(table_urn, sla_hours, freshness_field),
        sort_keys=False,
        default_flow_style=False,
    )


def build_assertion(
    table_urn: str,
    sla_hours: float,
    freshness_field: str,
) -> tuple[FixedIntervalFreshnessAssertion, str]:
    """Validate the declaration through DataHub's own parser.

    Round-tripping the YAML we emit through ``AssertionsConfigSpec`` means the
    artifact in ``examples/`` and the entity in the graph cannot drift: if the
    YAML would not load, nothing is written.

    Returns:
        The parsed assertion and the YAML it was parsed from.

    Raises:
        ValueError: The SLA is not positive, or is a day or more, where the SDK's
            ``timedelta.seconds`` truncation would emit a wrong lookback.
        AssertionWriteError: DataHub's parser rejected the declaration, or
            produced an assertion of an unexpected type.
    """
    if sla_hours <= 0:
        raise ValueError(f"the freshness SLA must be positive, got {sla_hours}")
    if sla_hours >= _MAX_SLA_HOURS:
        raise ValueError(
            f"a freshness SLA of {sla_hours} hours cannot be emitted correctly: "
            "FixedIntervalFreshnessAssertion reads timedelta.seconds rather than "
            "total_seconds(), so any lookback of a day or more is silently truncated. "
            f"Keep the SLA under {_MAX_SLA_HOURS:.0f} hours."
        )

    yaml_text = render_assertion_yaml(table_urn, sla_hours, freshness_field)
    try:
        spec = AssertionsConfigSpec.model_validate(yaml.safe_load(yaml_text))
    except Exception as exc:
        raise AssertionWriteError(f"DataHub rejected the assertion declaration: {exc}") from exc

    parsed = spec.assertions[0]
    if not isinstance(parsed, FixedIntervalFreshnessAssertion):
        raise AssertionWriteError(
            f"expected a fixed-interval freshness assertion, got {type(parsed).__name__}"
        )
    return parsed, yaml_text


def upsert_guarding_assertion(
    conn: DataHubConnection,
    *,
    table_urn: str,
    sla_hours: float,
    freshness_field: str,
    created_ms: int,
) -> AssertionWrite:
    """Create or update the freshness assertion guarding a table.

    Idempotent in two ways. The assertion URN is a guid over the declaration, so
    the same table always resolves to the same assertion. And the ``source.created``
    audit stamp is read back from any existing assertion rather than restamped:
    ``BaseEntityAssertion.get_assertion_info_aspect`` calls ``make_assertion_source``,
    which stamps the current time and would make the aspect differ on every run.
    This function calls ``get_assertion_info`` and sets the source itself.

    Args:
        conn: A connection with write credentials.
        table_urn: The dataset the assertion guards.
        sla_hours: How long the table may go unchanged.
        freshness_field: The column named in the YAML artifact.
        created_ms: Creation stamp, used only when the assertion is new.

    Returns:
        The assertion URN, the YAML artifact, and whether it was newly created.
    """
    parsed, yaml_text = build_assertion(table_urn, sla_hours, freshness_field)
    urn = str(AssertionUrn(parsed.get_id()))

    existing = conn.graph.get_aspect(urn, AssertionInfoClass)
    created_stamp = (
        existing.source.created
        if existing is not None and existing.source is not None and existing.source.created
        else AuditStampClass(time=created_ms, actor=ASSERTION_ACTOR)
    )

    info = parsed.get_assertion_info()
    info.source = AssertionSourceClass(
        # INFERRED: ModelGuard derived this check from an observed failure, rather
        # than a human authoring it (NATIVE) or an external tool owning it.
        type=AssertionSourceTypeClass.INFERRED,
        created=created_stamp,
    )
    conn.graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=info))

    return AssertionWrite(urn=urn, yaml_text=yaml_text, created=existing is None)


def record_assertion_result(
    conn: DataHubConnection,
    *,
    assertion_urn: str,
    signal: FreshnessSignal,
    run_id: str,
) -> str:
    """Record what this scan actually measured against the assertion.

    The result is the detector's own verdict on the same question the assertion
    asks, so a stale table produces FAILURE and a fresh one produces SUCCESS.
    ``nativeResults`` names the aspect the lag was computed from, so a reader can
    tell this apart from a warehouse-executed check.

    Run events are a timeseries aspect: each scan appends one, which is correct.
    A scan is a run, and the history of runs is the point.

    Returns:
        The result type that was written, SUCCESS or FAILURE.
    """
    result_type = (
        AssertionResultTypeClass.FAILURE if signal.is_stale else AssertionResultTypeClass.SUCCESS
    )
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=assertion_urn,
            aspect=AssertionRunEventClass(
                timestampMillis=signal.observed_at_ms,
                runId=run_id,
                asserteeUrn=signal.dataset_urn,
                status=AssertionRunStatusClass.COMPLETE,
                assertionUrn=assertion_urn,
                result=AssertionResultClass(
                    type=result_type,
                    actualAggValue=float(signal.lag_hours),
                    nativeResults={
                        "lag_hours": f"{signal.lag_hours:.2f}",
                        "sla_hours": f"{signal.sla_hours:.2f}",
                        "last_updated_ms": str(signal.last_updated_ms),
                        "evaluated_from": EVALUATED_FROM,
                    },
                ),
            ),
        )
    )
    return result_type
