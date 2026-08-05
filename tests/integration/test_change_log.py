"""The change-log consumer against a live Kafka and GMS (T-20).

Why a unit test cannot cover this: everything interesting here belongs to
somebody else's wire format. The topic name, the Confluent framing, the Avro
schema GMS registers under ``MetadataChangeLog_Versioned_v1-value``, and the fact
that a ``GenericAspect``'s payload is JSON bytes rather than nested Avro, are all
DataHub's contract and not this project's. A fake would encode whatever this
module already believes, which is the one thing worth checking.

The second thing measured is the whole of T-20: an ``mlModelProperties`` upsert
that drops a model's features produces an event this handler acts on, and the
replay puts the features back. The full end-to-end (DataHub's own mlflow source
run twice) was performed by hand and is recorded in D-132; what runs here is the
same aspect write, emitted directly, because standing an MLflow tracking server
up inside a test suite is a stack, not a fixture.
"""

from __future__ import annotations

import time
import uuid

import pytest
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import MLModelPropertiesClass

from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.mcl import ChangeLog, mcl_config_from_env
from modelguard.reconcile import consider
from modelguard.seed.seed_ml_graph import SeedResult, seed_ml_graph
from modelguard.writeback.link import link_model, recorded_link

pytestmark = pytest.mark.integration

#: How long to wait for an event this test provoked to come round the topic. A
#: broker on the same machine answers in under a second; this is the ceiling
#: before the test says the pipe is not working rather than hanging.
_EVENT_TIMEOUT_SECONDS = 60.0


@pytest.fixture(scope="module")
def conn() -> DataHubConnection:
    try:
        return connect()
    except DataHubConnectionError as exc:
        pytest.skip(f"no live DataHub: {exc}")


@pytest.fixture(scope="module")
def config() -> ScanConfig:
    return ScanConfig()


@pytest.fixture(scope="module")
def change_log():
    """Subscribe, or skip when this machine has no change log configured.

    Skipped rather than failed: the consumer is an optional extra behind
    ``[kafka]``, and a contributor running the integration suite against a
    Quickstart without setting the three variables has not broken anything.
    """
    mcl = mcl_config_from_env()
    if mcl is None:
        pytest.skip("change log not configured; set the three MODELGUARD_KAFKA_* variables")
    # A fresh group per run, so this test always reads from `latest` at its own
    # subscription rather than resuming a cursor a previous run committed.
    from dataclasses import replace

    with ChangeLog(replace(mcl, group_id=f"{mcl.group_id}-test-{uuid.uuid4().hex[:8]}")) as log:
        yield log


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection, config: ScanConfig) -> SeedResult:
    """Seed the graph, and make sure the model carries a *recorded* link.

    The seeder attaches features directly, which leaves no
    ``modelguard.feature_table`` property behind, and the recorded arguments are
    the whole of what the relink replays. Running `link` once here is what a user
    does anyway, and it is what makes the second test measure something rather
    than skip.
    """
    result = seed_ml_graph(conn)
    if recorded_link(conn, result.model) is None:
        link_model(
            conn,
            config,
            model_urn=result.model,
            feature_dataset_urn=result.feature_table_dataset,
            label_column_urn=result.label_column,
        )
    return result


def await_event(log: ChangeLog, *, urn: str, aspect_name: str):
    """Return the first event for this urn and aspect, or fail saying what was awaited."""
    deadline = time.monotonic() + _EVENT_TIMEOUT_SECONDS
    events = log.events()
    while time.monotonic() < deadline:
        event = next(events, None)
        if event is None:
            continue
        if event.entity_urn == urn and event.aspect_name == aspect_name:
            return event
    raise AssertionError(f"timed out waiting for {aspect_name} on {urn}")


def test_an_aspect_write_arrives_as_an_event_with_a_readable_payload(
    conn: DataHubConnection, seeded: SeedResult, change_log: ChangeLog
) -> None:
    """The wire format, end to end: topic, framing, Avro schema and JSON payload.

    The payload assertion is the one that matters. A ``GenericAspect``'s value is
    JSON bytes inside the Avro record; decoding it any other way leaves every
    handler in this project looking at an empty aspect and concluding nothing
    changed.
    """
    description = f"change-log probe {uuid.uuid4().hex[:8]}"
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=seeded.model,
            aspect=MLModelPropertiesClass(description=description, mlFeatures=[]),
        )
    )

    event = await_event(change_log, urn=seeded.model, aspect_name="mlModelProperties")

    assert event.entity_type == "mlModel"
    assert event.change_type in {"UPSERT", "CREATE", "RESTATE"}
    # Read off the wire, not off the object that was sent.
    assert event.aspect.get("description") == description


def test_an_ingest_shaped_write_gets_the_recorded_link_replayed(
    conn: DataHubConnection, config: ScanConfig, seeded: SeedResult, change_log: ChangeLog
) -> None:
    """T-20's claim, against a live graph: the join survives with no human action.

    The write below is exactly what DataHub's mlflow source does, which is upsert
    the whole ``mlModelProperties`` aspect and thereby drop the ``mlFeatures``
    that `link` attached (D-074, F11). What is asserted is that the features come
    back without anybody running `link --all`.
    """
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=seeded.model,
            aspect=MLModelPropertiesClass(mlFeatures=[]),
        )
    )
    event = await_event(change_log, urn=seeded.model, aspect_name="mlModelProperties")

    relink = consider(conn, config, event)

    assert relink is not None
    assert relink.relinked
    assert relink.features > 0
    # Read back from the graph, not from the return value: the point of the
    # feature is what the catalog holds afterwards.
    properties = conn.graph.get_aspect(seeded.model, MLModelPropertiesClass)
    assert properties is not None
    assert len(properties.mlFeatures or []) == relink.features
