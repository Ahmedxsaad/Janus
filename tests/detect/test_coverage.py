"""What a scan reports it could not check. Offline: no DataHub, no network.

The obligation here is the mirror image of a detector's: a detector must not fire
on a clean graph, and this must not stay silent on a graph that never gave a
detector anything to read. Both failures look identical to a user ("no finding"),
and the second is the one that grants false confidence on a real catalog, where a
table with no operation aspect and a model with no declared features are the
normal case.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
)

from modelguard.config import ScanConfig
from modelguard.detect.coverage import coverage_gaps
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from tests.conftest import (
    LEAK_COLUMN_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    NOW_MS,
    TABLE_URN,
    FakeGraph,
    make_connection,
    make_finding,
)

CONFIG = ScanConfig()


def _gaps(graph: FakeGraph, **kwargs: object) -> tuple[str, ...]:
    """Run the coverage read and return one description per gap."""
    gaps = coverage_gaps(
        make_connection(graph),
        CONFIG,
        table_urn=kwargs.get("table_urn"),  # type: ignore[arg-type]
        model_urn=kwargs.get("model_urn"),  # type: ignore[arg-type]
        findings=kwargs.get("findings", ()),  # type: ignore[arg-type]
    )
    return tuple(gap.describe() for gap in gaps)


def test_a_table_nobody_instrumented_is_reported_unevaluated_not_healthy():
    described = _gaps(FakeGraph(), table_urn=TABLE_URN)
    assert len(described) == 1
    assert "freshness not evaluated" in described[0]
    assert "operation aspect" in described[0]


def test_a_table_with_an_operation_aspect_leaves_no_gap():
    graph = FakeGraph(timeseries={(TABLE_URN, OperationClass): OperationClass(0, "UPDATE", NOW_MS)})
    assert _gaps(graph, table_urn=TABLE_URN) == ()


def test_a_check_that_produced_a_finding_is_never_called_unevaluated():
    """A finding is proof the check ran, whatever the graph looks like now."""
    assert _gaps(FakeGraph(), table_urn=TABLE_URN, findings=(make_finding(),)) == ()


def test_a_model_with_no_declared_features_cannot_be_checked_for_leakage():
    described = _gaps(FakeGraph(), model_urn=MODEL_URN)
    assert any(
        "target leakage not evaluated" in line and "mlFeatures" in line for line in described
    )


def test_a_model_with_no_training_run_cannot_be_checked_for_drift():
    described = _gaps(FakeGraph(), model_urn=MODEL_URN)
    assert any(
        "schema drift not evaluated" in line and "trainingJobs" in line for line in described
    )


def test_a_missing_label_term_is_named_so_the_user_can_go_and_create_it():
    """The default term exists only in a seeded graph, so this is the real-catalog case."""
    graph = FakeGraph(
        aspects={
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                mlFeatures=[LEAK_FEATURE_URN]
            )
        },
        exists=False,
    )
    described = _gaps(graph, model_urn=MODEL_URN)
    assert any(CONFIG.label_term_urn in line for line in described)


def test_features_without_column_lineage_are_named_as_the_reason():
    """The term exists and features are declared, but nothing links them to a column."""
    graph = FakeGraph(
        aspects={
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                mlFeatures=[LEAK_FEATURE_URN]
            )
        },
        exists=True,
    )
    described = _gaps(graph, model_urn=MODEL_URN)
    assert any("column-level lineage" in line for line in described)


def test_a_fully_wired_model_leaves_no_leakage_gap():
    graph = FakeGraph(
        aspects={
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                mlFeatures=[LEAK_FEATURE_URN]
            ),
            (LEAK_FEATURE_URN, MLFeaturePropertiesClass): MLFeaturePropertiesClass(
                customProperties={SOURCE_COLUMN_PROPERTY: LEAK_COLUMN_URN}
            ),
        },
        exists=True,
    )
    described = _gaps(graph, model_urn=MODEL_URN)
    assert not any("target leakage" in line for line in described)
