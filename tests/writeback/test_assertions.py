from __future__ import annotations

import pytest
import yaml
from datahub.api.entities.assertion.assertion_config_spec import AssertionsConfigSpec
from datahub.metadata.schema_classes import (
    AssertionInfoClass,
    AssertionResultTypeClass,
    AssertionRunEventClass,
    AssertionSourceClass,
    AssertionSourceTypeClass,
    AssertionTypeClass,
    AuditStampClass,
    FreshnessAssertionInfoClass,
    FreshnessAssertionScheduleClass,
    FreshnessAssertionScheduleTypeClass,
    FreshnessAssertionTypeClass,
)

from modelguard.models import FreshnessSignal
from modelguard.writeback.assertions import (
    EVALUATED_FROM,
    build_assertion,
    record_assertion_result,
    render_assertion_yaml,
    upsert_guarding_assertion,
)
from tests.conftest import FakeGraph, make_connection

TABLE = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)"
OTHER_TABLE = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.payments,PROD)"
NOW = 1_800_000_000_000
HOUR = 3_600_000


def _signal(lag_hours: float, sla_hours: float = 6.0) -> FreshnessSignal:
    return FreshnessSignal(
        dataset_urn=TABLE,
        last_updated_ms=NOW - int(lag_hours * HOUR),
        observed_at_ms=NOW,
        sla_hours=sla_hours,
    )


# --------------------------------------------------------------------------
# The YAML artifact, and the fact that DataHub itself can read it
# --------------------------------------------------------------------------


def test_the_emitted_yaml_parses_back_through_datahubs_own_spec():
    """The artifact in examples/ and the entity in the graph cannot drift apart."""
    text = render_assertion_yaml(TABLE, 6.0, "updated_at")
    spec = AssertionsConfigSpec.model_validate(yaml.safe_load(text))

    assert spec.version == 1
    assert len(spec.assertions) == 1
    assert spec.assertions[0].entity == TABLE


def test_the_yaml_names_the_freshness_column_and_the_interval():
    parsed = yaml.safe_load(render_assertion_yaml(TABLE, 6.0, "updated_at"))
    assertion = parsed["assertions"][0]

    assert assertion["type"] == "freshness"
    assert assertion["lookback_interval"] == "6 hours"
    assert assertion["last_modified_field"] == "updated_at"
    assert assertion["schedule"] == {"type": "interval", "interval": "6 hours"}


def test_a_fractional_sla_renders_in_a_form_the_parser_accepts():
    text = render_assertion_yaml(TABLE, 4.5, "updated_at")
    assert yaml.safe_load(text)["assertions"][0]["lookback_interval"] == "4.5 hours"
    AssertionsConfigSpec.model_validate(yaml.safe_load(text))  # must not raise


def test_the_assertion_id_is_stable_for_a_table_and_differs_between_tables():
    """A stable id is what makes a rerun update one assertion instead of piling up."""
    first, _ = build_assertion(TABLE, 6.0, "updated_at")
    again, _ = build_assertion(TABLE, 6.0, "updated_at")
    other, _ = build_assertion(OTHER_TABLE, 6.0, "updated_at")

    assert first.get_id() == again.get_id()
    assert first.get_id() != other.get_id()


def test_an_sla_of_a_day_or_more_is_refused_rather_than_silently_truncated():
    """FixedIntervalFreshnessAssertion reads timedelta.seconds, so 30h would emit as 6h."""
    with pytest.raises(ValueError, match="silently truncated"):
        build_assertion(TABLE, 24.0, "updated_at")


def test_a_non_positive_sla_is_refused():
    with pytest.raises(ValueError, match="must be positive"):
        build_assertion(TABLE, 0.0, "updated_at")


def test_the_emitted_aspect_carries_the_sla_the_caller_asked_for():
    """Guards the truncation bug from the other side: 6h must arrive as 21600 seconds."""
    parsed, _ = build_assertion(TABLE, 6.0, "updated_at")
    info = parsed.get_assertion_info()

    assert info.type == AssertionTypeClass.FRESHNESS
    assert info.freshnessAssertion.schedule.fixedInterval.multiple == 6 * 3600


# --------------------------------------------------------------------------
# Upsert: idempotent, and never restamped
# --------------------------------------------------------------------------


def _emitted_info(graph: FakeGraph) -> AssertionInfoClass:
    assert len(graph.emitted) == 1
    aspect = graph.emitted[0].aspect
    assert isinstance(aspect, AssertionInfoClass)
    return aspect


def test_a_new_assertion_is_marked_inferred_and_stamped_with_the_run_instant():
    graph = FakeGraph()
    write = upsert_guarding_assertion(
        make_connection(graph),
        table_urn=TABLE,
        sla_hours=6.0,
        freshness_field="updated_at",
        created_ms=NOW,
    )

    assert write.created is True
    assert write.urn.startswith("urn:li:assertion:")
    info = _emitted_info(graph)
    assert info.source.type == AssertionSourceTypeClass.INFERRED
    assert info.source.created.time == NOW


def test_reupserting_preserves_the_original_created_stamp():
    """get_assertion_info_aspect() would restamp with now, making the aspect differ every run."""
    parsed, _ = build_assertion(TABLE, 6.0, "updated_at")
    urn = f"urn:li:assertion:{parsed.get_id()}"
    original = AuditStampClass(time=123456789, actor="urn:li:corpuser:datahub")

    existing = AssertionInfoClass(
        type=AssertionTypeClass.FRESHNESS,
        source=AssertionSourceClass(type=AssertionSourceTypeClass.INFERRED, created=original),
        freshnessAssertion=FreshnessAssertionInfoClass(
            type=FreshnessAssertionTypeClass.DATASET_CHANGE,
            entity=TABLE,
            schedule=FreshnessAssertionScheduleClass(
                type=FreshnessAssertionScheduleTypeClass.FIXED_INTERVAL
            ),
        ),
    )
    graph = FakeGraph({(urn, AssertionInfoClass): existing})

    write = upsert_guarding_assertion(
        make_connection(graph),
        table_urn=TABLE,
        sla_hours=6.0,
        freshness_field="updated_at",
        created_ms=NOW,
    )

    assert write.created is False
    assert _emitted_info(graph).source.created.time == 123456789


# --------------------------------------------------------------------------
# The run event: the result we actually measured, never a fabricated one
# --------------------------------------------------------------------------


def _emitted_event(graph: FakeGraph) -> AssertionRunEventClass:
    assert len(graph.emitted) == 1
    aspect = graph.emitted[0].aspect
    assert isinstance(aspect, AssertionRunEventClass)
    return aspect


def test_a_stale_table_records_a_failing_assertion_run():
    graph = FakeGraph()
    result = record_assertion_result(
        make_connection(graph),
        assertion_urn="urn:li:assertion:x",
        signal=_signal(30.0),
        run_id="scan-abc",
    )

    assert result == AssertionResultTypeClass.FAILURE
    event = _emitted_event(graph)
    assert event.result.type == AssertionResultTypeClass.FAILURE
    assert event.runId == "scan-abc"
    assert event.asserteeUrn == TABLE


def test_a_fresh_table_records_a_passing_assertion_run():
    """The result follows the measurement. Nothing is hardcoded to fail."""
    graph = FakeGraph()
    result = record_assertion_result(
        make_connection(graph),
        assertion_urn="urn:li:assertion:x",
        signal=_signal(1.0),
        run_id="scan-abc",
    )

    assert result == AssertionResultTypeClass.SUCCESS
    assert _emitted_event(graph).result.type == AssertionResultTypeClass.SUCCESS


def test_the_run_event_carries_the_measured_lag_and_names_where_it_came_from():
    graph = FakeGraph()
    record_assertion_result(
        make_connection(graph),
        assertion_urn="urn:li:assertion:x",
        signal=_signal(30.0),
        run_id="scan-abc",
    )

    result = _emitted_event(graph).result
    assert result.actualAggValue == pytest.approx(30.0)
    assert result.nativeResults["lag_hours"] == "30.00"
    assert result.nativeResults["sla_hours"] == "6.00"
    # Nobody should mistake this for a warehouse query.
    assert result.nativeResults["evaluated_from"] == EVALUATED_FROM
