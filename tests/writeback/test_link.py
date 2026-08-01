"""Linking a model to the columns it trained on. Offline: no DataHub, no network.

The load-bearing part is not the writes, which are the same read-merge-emit as
the rest of writeback/. It is what gets declared: the feature set excludes what
the caller excluded while the training snapshot does not (or drift reports every
excluded column as newly appeared), and the label declaration follows the
label's own lineage (or a label declared on a label mart is somewhere no feature
can reach, and a leaking model reads clean).
"""

from __future__ import annotations

import json

import pytest
from datahub.metadata.schema_classes import (
    DataProcessInstancePropertiesClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
    StructuredPropertiesClass,
    StructuredPropertyValueAssignmentClass,
)
from datahub.metadata.urns import StructuredPropertyUrn

from modelguard.config import ScanConfig
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from modelguard.writeback.link import (
    PROP_FEATURE_TABLE,
    PROP_LABEL_COLUMN,
    LinkError,
    LinkResult,
    link_model,
    models_with_recorded_link,
    recorded_link,
)
from tests.conftest import (
    FEATURE_TABLE_URN,
    LABEL_COLUMN_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
    make_connection,
    schema_metadata,
)

CONFIG = ScanConfig()
RUN_URN = "urn:li:dataProcessInstance:training-run"
SCHEMA = {"customer_id": "TEXT", "tenure": "INT", "renewed_flag": "INT"}


def _graph(*, with_run: bool = True) -> FakeGraph:
    """A model as DataHub's own ingestion leaves it: a run, and nothing else."""
    graph = FakeGraph()
    graph.set_aspect(FEATURE_TABLE_URN, schema_metadata(SCHEMA))
    graph.set_aspect(
        MODEL_URN,
        MLModelPropertiesClass(trainingJobs=[RUN_URN] if with_run else None),
    )
    return graph


def _link(graph: FakeGraph, client: FakeClient | None = None, **kwargs: object) -> LinkResult:
    return link_model(
        make_connection(graph, client or FakeClient()),
        CONFIG,
        model_urn=MODEL_URN,
        feature_dataset_urn=FEATURE_TABLE_URN,
        label_column_urn=LABEL_COLUMN_URN,
        **kwargs,  # type: ignore[arg-type]
    )


def test_every_column_becomes_a_feature_naming_its_own_source_column():
    graph = _graph()
    result = _link(graph)

    features = {
        mcp.entityUrn: mcp.aspect
        for mcp in graph.emitted
        if isinstance(mcp.aspect, MLFeaturePropertiesClass)
    }
    assert len(features) == len(SCHEMA)
    for feature_urn, aspect in features.items():
        column = feature_urn.rsplit(",", 1)[-1].rstrip(")")
        assert aspect.customProperties[SOURCE_COLUMN_PROPERTY].endswith(f",{column})")
    assert set(result.feature_urns) == set(features)


def test_an_excluded_column_is_not_a_feature_but_stays_in_the_training_snapshot():
    """Drop it from the snapshot too and the next scan reports it as drift."""
    graph = _graph()
    _link(graph, exclude=frozenset({"customer_id"}))

    features = [
        mcp.entityUrn for mcp in graph.emitted if isinstance(mcp.aspect, MLFeaturePropertiesClass)
    ]
    assert not any("customer_id" in urn for urn in features)

    snapshot = json.loads(
        graph.get_aspect(RUN_URN, DataProcessInstancePropertiesClass).customProperties[
            CONFIG.training_schema_property
        ]
    )
    assert snapshot[FEATURE_TABLE_URN] == SCHEMA


def test_the_label_declaration_follows_the_labels_own_lineage():
    """The label lives in a mart; the leaking feature descends from the raw column."""
    raw_label = f"urn:li:schemaField:({TABLE_URN},churn)"
    client = FakeClient(
        lineage_by_column={
            "default_status": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LABEL_COLUMN_URN, raw_label),
                )
            ]
        }
    )
    result = _link(_graph(), client)
    assert raw_label in result.label_column_urns
    assert LABEL_COLUMN_URN in result.label_column_urns


def test_a_feature_table_with_no_schema_is_refused_rather_than_guessed_at():
    graph = FakeGraph()
    graph.set_aspect(MODEL_URN, MLModelPropertiesClass())
    with pytest.raises(LinkError, match="schemaMetadata"):
        _link(graph)


def test_a_dry_run_writes_nothing_at_all():
    graph = _graph()
    result = _link(graph, dry_run=True)
    assert graph.emitted == []
    assert len(result.feature_urns) == len(SCHEMA)


def test_the_arguments_are_recorded_so_the_link_can_be_replayed():
    """An ingestion run drops the features; the record is what puts them back."""
    graph = _graph()
    _link(graph, exclude=frozenset({"customer_id"}))

    previous = recorded_link(make_connection(graph), MODEL_URN)
    assert previous is not None
    assert previous.feature_dataset_urn == FEATURE_TABLE_URN
    assert previous.label_column_urn == LABEL_COLUMN_URN
    assert previous.exclude == frozenset({"customer_id"})


def test_a_model_nobody_linked_is_skipped_by_the_replay_sweep():
    graph = FakeGraph()
    graph.set_aspect(
        MODEL_URN,
        StructuredPropertiesClass(
            properties=[
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(PROP_FEATURE_TABLE)),
                    values=[FEATURE_TABLE_URN],
                ),
                StructuredPropertyValueAssignmentClass(
                    propertyUrn=str(StructuredPropertyUrn(PROP_LABEL_COLUMN)),
                    values=[LABEL_COLUMN_URN],
                ),
            ]
        ),
    )
    unlinked = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,never_linked,PROD)"
    client = FakeClient(search_urns=[MODEL_URN, unlinked])

    found = models_with_recorded_link(make_connection(graph, client))
    assert [urn for urn, _ in found] == [MODEL_URN]
