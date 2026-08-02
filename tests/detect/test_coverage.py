"""What a scan reports it could not check. Offline: no DataHub, no network.

The obligation here is the mirror image of a detector's: a detector must not fire
on a clean graph, and this must not stay silent on a graph that never gave a
detector anything to read. Both failures look identical to a user ("no finding"),
and the second is the one that grants false confidence on a real catalog, where a
table with no operation aspect and a model with no declared features are the
normal case.
"""

from __future__ import annotations

from dataclasses import replace

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
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
    make_connection,
    make_finding,
)

CONFIG = ScanConfig()


def _gaps(
    graph: FakeGraph,
    *,
    client: FakeClient | None = None,
    config: ScanConfig = CONFIG,
    **kwargs: object,
) -> tuple[str, ...]:
    """Run the coverage read and return one description per gap.

    A default, empty ``FakeClient`` rather than ``make_connection``'s own
    ``None``: the leakage and sensitive-source gap checks re-walk lineage to
    tell a real "no leak" from a truncated one (F1, docs/plan/07), so they
    need a working ``conn.client.lineage`` even on the already-clean path.
    """
    gaps = coverage_gaps(
        make_connection(graph, client or FakeClient()),
        config,
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


def test_a_truncated_leakage_walk_is_reported_as_a_gap_not_clean():
    """A walk that hit the cap with no leak found is uncertain, not clean.

    leakage_findings() already ran and found nothing, which is why this gap
    check is even asked, but "nothing found" and "nothing found because the
    cap cut the walk short" read identically to a caller unless this checks
    WalkResult.truncated itself (F1, docs/plan/07).
    """
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
    capped = replace(CONFIG, lineage_result_cap=1)
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(TABLE_URN, hops=1, direction="upstream", paths=column_path())
            ]
        }
    )
    described = _gaps(graph, model_urn=MODEL_URN, client=client, config=capped)
    assert any(
        "target leakage" in line and "cap" in line and "may not have" in line for line in described
    )


def test_a_truncated_sensitive_source_walk_is_reported_as_a_gap_not_clean():
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
    capped = replace(
        CONFIG, lineage_result_cap=1, sensitive_tag_urns=("urn:li:tag:modelguard.sensitive",)
    )
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(TABLE_URN, hops=1, direction="upstream", paths=column_path())
            ]
        }
    )
    described = _gaps(graph, model_urn=MODEL_URN, client=client, config=capped)
    assert any(
        "sensitive source" in line and "cap" in line and "may not have" in line
        for line in described
    )
