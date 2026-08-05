from __future__ import annotations

from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass

from janus.writeback.labels import add_tag, ensure_tag, read_tags
from tests.conftest import FakeClient, FakeGraph, make_connection

MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
AT_RISK = "urn:li:tag:model-at-risk"
OTHER = "urn:li:tag:pii"


def _emitted_tags(graph: FakeGraph) -> list[str]:
    """The tag URNs in the aspect that was actually sent to DataHub."""
    assert len(graph.emitted) == 1
    aspect = graph.emitted[0].aspect
    assert isinstance(aspect, GlobalTagsClass)
    return [association.tag for association in aspect.tags]


def test_tagging_an_untagged_model_writes_the_tag():
    graph = FakeGraph()
    assert add_tag(make_connection(graph), MODEL, AT_RISK) is True
    assert _emitted_tags(graph) == [AT_RISK]


def test_tagging_a_model_that_already_carries_the_tag_writes_nothing():
    """The idempotency that keeps a second scan from churning the aspect."""
    graph = FakeGraph(
        {(MODEL, GlobalTagsClass): GlobalTagsClass(tags=[TagAssociationClass(AT_RISK)])}
    )

    assert add_tag(make_connection(graph), MODEL, AT_RISK) is False
    assert graph.emitted == [], "an already-tagged model must not be rewritten"


def test_tagging_preserves_tags_somebody_else_applied():
    """GlobalTags is an upsert of the whole aspect: a naive write would drop the pii tag."""
    graph = FakeGraph(
        {(MODEL, GlobalTagsClass): GlobalTagsClass(tags=[TagAssociationClass(OTHER)])}
    )

    assert add_tag(make_connection(graph), MODEL, AT_RISK) is True
    assert _emitted_tags(graph) == [OTHER, AT_RISK]


def test_ensure_tag_upserts_the_tag_entity_so_the_ui_shows_a_name():
    client = FakeClient()
    urn = ensure_tag(make_connection(FakeGraph(), client), "model-at-risk", "why it is at risk")

    assert urn == AT_RISK
    assert len(client.entities.upserted) == 1
    tag = client.entities.upserted[0]
    assert tag.description == "why it is at risk"


def test_read_tags_reports_what_the_graph_holds():
    graph = FakeGraph(
        {(MODEL, GlobalTagsClass): GlobalTagsClass(tags=[TagAssociationClass(AT_RISK)])}
    )
    assert read_tags(make_connection(graph), MODEL) == (AT_RISK,)


def test_read_tags_of_an_untagged_entity_is_empty():
    assert read_tags(make_connection(FakeGraph()), MODEL) == ()
