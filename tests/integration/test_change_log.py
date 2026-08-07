"""The change-log consumer against a live Kafka and GMS.

Why a unit test cannot cover this: everything interesting here belongs to
somebody else's wire format. The topic name, the Confluent framing, the Avro
schema GMS registers under ``MetadataChangeLog_Versioned_v1-value``, and the fact
that a ``GenericAspect``'s payload is JSON bytes rather than nested Avro, are all
DataHub's contract and not this project's. A fake would encode whatever this
module already believes, which is the one thing worth checking.

The second thing measured is the whole claim: an ``mlModelProperties`` upsert
that drops a model's features produces an event this handler acts on, and the
replay puts the features back. The full end-to-end (DataHub's own mlflow source
run twice) was performed by hand once; what runs here is the
same aspect write, emitted directly, because standing an MLflow tracking server
up inside a test suite is a stack, not a fixture.
"""

from __future__ import annotations

import time
import uuid

import pytest
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import MLModelPropertiesClass

from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.mcl import ChangeLog, mcl_config_from_env
from janus.reconcile import consider
from janus.seed.seed_ml_graph import SeedResult, seed_ml_graph
from janus.writeback.link import link_model, recorded_link

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
        pytest.skip("change log not configured; set the three JANUS_KAFKA_* variables")
    # A fresh group per run, so this test always reads from `latest` at its own
    # subscription rather than resuming a cursor a previous run committed.
    from dataclasses import replace

    with ChangeLog(replace(mcl, group_id=f"{mcl.group_id}-test-{uuid.uuid4().hex[:8]}")) as log:
        yield log


@pytest.fixture(scope="module")
def seeded(conn: DataHubConnection, config: ScanConfig):
    """Seed the graph, and make sure the model carries a *recorded* link.

    The seeder attaches features directly, which leaves no
    ``janus.feature_table`` property behind, and the recorded arguments are
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
    yield result
    # Re-seed on the way out. `link` declares one feature per column of the
    # feature table, which is more than the seeder attaches, and the benchmark
    # scores its trials against this same graph: a module that leaves a wider
    # feature set behind moves numbers in RESULTS.md that have nothing to do
    # with it. The seed is idempotent, so this is a restore and not a second
    # setup (CONTRIBUTING.md's shared-graph discipline).
    seed_ml_graph(conn)


def drop_features(
    conn: DataHubConnection, model_urn: str, *, description: str | None = None
) -> None:
    """Rewrite the model's properties with no features, keeping everything else.

    An ingest genuinely upserts the whole aspect, and that is the failure under
    test. What it does *not* do is throw away the training runs and deployments,
    because its own source supplies them; a test that blind-wrote a bare
    ``MLModelPropertiesClass`` would strip the seeded model of both and quietly
    break the freshness, drift, deprecation and degraded-mode trials the
    benchmark runs against that same graph afterwards.

    Which is precisely docs/02-architecture.md: a whole-list aspect is
    read-merge-emit, never a blind write. It is written down for the product and
    it holds for a test that writes to a shared graph just as hard, which is how
    this function came to exist.
    """
    current = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=model_urn,
            aspect=MLModelPropertiesClass(
                mlFeatures=[],
                description=description if description is not None else current.description,
                trainingJobs=current.trainingJobs,
                deployments=current.deployments,
                customProperties=current.customProperties,
                hyperParams=current.hyperParams,
                trainingMetrics=current.trainingMetrics,
                groups=current.groups,
                version=current.version,
                externalUrl=current.externalUrl,
                type=current.type,
                date=current.date,
                created=current.created,
                lastModified=current.lastModified,
            ),
        )
    )


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
    drop_features(conn, seeded.model, description=description)

    event = await_event(change_log, urn=seeded.model, aspect_name="mlModelProperties")

    assert event.entity_type == "mlModel"
    assert event.change_type in {"UPSERT", "CREATE", "RESTATE"}
    # Read off the wire, not off the object that was sent.
    assert event.aspect.get("description") == description


def test_an_ingest_shaped_write_gets_the_recorded_link_replayed(
    conn: DataHubConnection, config: ScanConfig, seeded: SeedResult, change_log: ChangeLog
) -> None:
    """The claim, against a live graph: the join survives with no human action.

    The write below is exactly what DataHub's mlflow source does, which is upsert
    the whole ``mlModelProperties`` aspect and thereby drop the ``mlFeatures``
    that `link` attached. What is asserted is that the features come
    back without anybody running `link --all`.
    """
    drop_features(conn, seeded.model)
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
