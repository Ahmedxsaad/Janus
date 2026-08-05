"""Decoding DataHub's change log (T-20). Offline: no broker, no registry.

The interesting failure modes of ``mcl.py`` are all in the shape of a record,
not in Kafka: a ``GenericAspect`` whose payload is JSON bytes rather than nested
Avro, a record naming no entity, a half-configured consumer group. Those are what
this pins. The Kafka half is exercised against a live broker in the integration
suite, which is where a subscription can actually be observed.
"""

from __future__ import annotations

import json

import pytest

from janus.env import ConfigError
from janus.mcl import (
    ENV_KAFKA_BOOTSTRAP,
    ENV_KAFKA_GROUP_ID,
    ENV_SCHEMA_REGISTRY_URL,
    ChangeLog,
    MclConfig,
    mcl_config_from_env,
    to_event,
)

MODEL_URN = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,telco_churn_1,PROD)"


def record(**overrides: object) -> dict:
    """One deserialised change-log record, shaped the way a live GMS emits it."""
    payload = {
        "entityType": "mlModel",
        "entityUrn": MODEL_URN,
        "aspectName": "mlModelProperties",
        "changeType": "UPSERT",
        "aspect": {
            "contentType": "application/json",
            "value": json.dumps({"mlFeatures": [], "customProperties": {}}),
        },
    }
    payload.update(overrides)
    return payload


def test_a_generic_aspects_payload_is_parsed_as_json_and_not_as_avro():
    """`GenericAspect.value` is JSON bytes inside an Avro record, verified live.

    Reading it as anything else is the one decoding mistake that would make
    every handler in this project see an empty aspect and conclude nothing had
    changed.
    """
    event = to_event(record())

    assert event is not None
    assert event.aspect == {"mlFeatures": [], "customProperties": {}}


def test_a_record_naming_no_entity_is_dropped_rather_than_half_built():
    """Nothing downstream can act on an event with no URN."""
    assert to_event(record(entityUrn=None)) is None
    assert to_event(record(aspectName=None)) is None


def test_an_unparseable_payload_is_treated_as_absent_rather_than_raising():
    """This stream is written by systems this project does not control.

    A consumer that died on one malformed record would stop reacting to every
    well-formed one behind it.
    """
    event = to_event(record(aspect={"contentType": "application/json", "value": "{not json"}))

    assert event is not None
    assert event.aspect == {}


def test_a_delete_carries_no_aspect_and_says_so():
    event = to_event(record(changeType="DELETE", aspect=None))

    assert event is not None
    assert event.removed
    assert event.aspect == {}


def test_no_change_log_configuration_is_not_an_error():
    """Polling is the default and needs none of this."""
    assert mcl_config_from_env() is None


def test_all_three_variables_or_none(monkeypatch):
    """Root rule 6c: a half-configured consumer fails loudly rather than degrading.

    Two deployments sharing one group id split the partitions and each silently
    sees half the events, which looks exactly like a quiet catalog. That is the
    failure a missing group id would produce if it had a default.
    """
    monkeypatch.setenv(ENV_KAFKA_BOOTSTRAP, "localhost:9092")
    monkeypatch.setenv(ENV_SCHEMA_REGISTRY_URL, "http://localhost:8080/schema-registry/api/")
    monkeypatch.delenv(ENV_KAFKA_GROUP_ID, raising=False)

    with pytest.raises(ConfigError) as raised:
        mcl_config_from_env()

    assert ENV_KAFKA_GROUP_ID in str(raised.value)


def test_all_three_present_builds_the_configuration(monkeypatch):
    monkeypatch.setenv(ENV_KAFKA_BOOTSTRAP, "broker:9092")
    monkeypatch.setenv(ENV_SCHEMA_REGISTRY_URL, "http://gms:8080/schema-registry/api/")
    monkeypatch.setenv(ENV_KAFKA_GROUP_ID, "janus-prod")

    config = mcl_config_from_env()

    assert config == MclConfig(
        bootstrap_servers="broker:9092",
        schema_registry_url="http://gms:8080/schema-registry/api/",
        group_id="janus-prod",
    )


def test_the_kafka_client_missing_fails_at_startup_with_its_install_command(monkeypatch):
    """Better than a daemon that connected to nothing and reported nothing."""
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("confluent_kafka"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", refuse)
    log = ChangeLog(MclConfig("b:9092", "http://r/", "g"))

    with pytest.raises(ConfigError) as raised:
        log.__enter__()

    assert "kafka" in str(raised.value)


def test_events_outside_the_context_manager_refuses_rather_than_returning_nothing():
    """Silence from a consumer that was never opened is the worst answer."""
    with pytest.raises(RuntimeError):
        next(ChangeLog(MclConfig("b:9092", "http://r/", "g")).events())
