"""Replaying a link an ingest dropped (T-20, F11). Offline: no broker, no DataHub.

Two things this must get right, and one it must refuse to do. It has to fire on
the exact aspect write that causes the damage and on nothing else, because a
handler that re-linked on every model write would fight whatever else is writing.
And it must never link a model nobody linked: an inferred join looks identical to
a confirmed one and would make every detector downstream confident about the
wrong columns.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import MLModelPropertiesClass

from modelguard.config import ScanConfig
from modelguard.mcl import MclEvent
from modelguard.reconcile import consider
from modelguard.writeback.properties import (
    EXCLUDED_COLUMNS,
    FEATURE_TABLE,
    LABEL_COLUMN,
)
from tests.conftest import (
    FEATURE_TABLE_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    make_connection,
    schema_metadata,
)

CONFIG = ScanConfig()
LABEL_COLUMN_URN = f"urn:li:schemaField:({TABLE_URN},default_status)"


def event(**overrides: object) -> MclEvent:
    """The change-log event an mlflow ingest produces when it drops a link."""
    payload: dict = {
        "entity_type": "mlModel",
        "entity_urn": MODEL_URN,
        "aspect_name": "mlModelProperties",
        "change_type": "UPSERT",
        "aspect": {"mlFeatures": [], "customProperties": {}},
    }
    payload.update(overrides)
    return MclEvent(**payload)  # type: ignore[arg-type]


def linked_graph(*, recorded: bool = True) -> FakeGraph:
    """A model whose link was recorded, and whose feature table has a schema."""
    graph = FakeGraph()
    graph.set_aspect(FEATURE_TABLE_URN, schema_metadata({"applicant_income": "NUMBER"}))
    graph.set_aspect(TABLE_URN, schema_metadata({"default_status": "BOOLEAN"}))
    graph.set_aspect(MODEL_URN, MLModelPropertiesClass(mlFeatures=[]))
    if recorded:
        _record_link(graph)
    return graph


def _record_link(graph: FakeGraph) -> None:
    """Put the structured properties `link` writes onto the model."""
    from datahub.metadata.schema_classes import (
        StructuredPropertiesClass,
        StructuredPropertyValueAssignmentClass,
    )
    from datahub.metadata.urns import StructuredPropertyUrn

    graph.set_aspect(
        MODEL_URN,
        StructuredPropertiesClass(
            properties=[
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(FEATURE_TABLE)),
                    values=[FEATURE_TABLE_URN],
                ),
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(LABEL_COLUMN)),
                    values=[LABEL_COLUMN_URN],
                ),
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(EXCLUDED_COLUMNS)),
                    values=["customer_id"],
                ),
            ]
        ),
    )


def test_an_ingest_that_dropped_the_features_gets_the_recorded_link_replayed():
    """The whole of T-20, and the failure F11 names, in one assertion."""
    conn = make_connection(linked_graph(), FakeClient())

    result = consider(conn, CONFIG, event())

    assert result is not None
    assert result.relinked
    assert result.features == 1


def test_a_model_nobody_linked_is_left_alone_rather_than_guessed_at():
    """The one thing this module refuses to do.

    An inferred join is indistinguishable from a confirmed one in the graph, and
    every detector downstream would then be confident about the wrong columns.
    """
    conn = make_connection(linked_graph(recorded=False), FakeClient())

    result = consider(conn, CONFIG, event())

    assert result is not None
    assert not result.relinked
    assert "nothing" in result.reason


def test_an_upsert_that_left_the_features_in_place_is_not_acted_on():
    """A write that broke nothing must not trigger a write."""
    conn = make_connection(linked_graph(), FakeClient())

    assert consider(conn, CONFIG, event(aspect={"mlFeatures": ["urn:li:mlFeature:(a,b)"]})) is None


def test_a_patch_is_not_acted_on_because_it_cannot_have_dropped_a_field():
    """A PATCH is a partial update, so an absent mlFeatures says nothing.

    Reacting to one would re-link on writes that never broke anything, which on
    a busy catalog means fighting whatever else is patching the model.
    """
    conn = make_connection(linked_graph(), FakeClient())

    assert consider(conn, CONFIG, event(change_type="PATCH")) is None


def test_a_write_to_another_aspect_of_the_model_is_not_acted_on():
    """Only mlModelProperties carries the features an ingest overwrites."""
    conn = make_connection(linked_graph(), FakeClient())

    assert consider(conn, CONFIG, event(aspect_name="globalTags")) is None


def test_a_write_to_a_dataset_is_not_acted_on():
    """Most of the topic is datasets, and none of it is this handler's business."""
    conn = make_connection(linked_graph(), FakeClient())

    assert consider(conn, CONFIG, event(entity_type="dataset", entity_urn=TABLE_URN)) is None


def test_a_dry_run_decides_everything_and_writes_nothing():
    graph = linked_graph()
    conn = make_connection(graph, FakeClient())

    result = consider(conn, CONFIG, event(), dry_run=True)

    assert result is not None
    assert not result.relinked
    assert graph.emitted == []


def test_a_link_that_can_no_longer_be_replayed_is_a_refusal_and_not_an_exception():
    """One broken model must not stop a daemon protecting every other one.

    The feature table has been deleted since the link was recorded, which is the
    realistic version of this: somebody dropped the mart and nobody told the
    model.
    """
    graph = linked_graph()
    # No schema for the recorded feature table any more.
    graph._aspects = {
        key: value for key, value in graph._aspects.items() if key[0] != FEATURE_TABLE_URN
    }
    conn = make_connection(graph, FakeClient())

    result = consider(conn, CONFIG, event())

    assert result is not None
    assert not result.relinked
    assert "could not be replayed" in result.reason
