"""The baselines, checked offline. Their job is to be *fair*, not to lose.

A comparison is only worth publishing if the opposition was implemented honestly,
so the assertions here are mostly about the table-level approach being good: it
must genuinely catch the leak, from the same facts, before its failure to isolate
which feature leaks means anything. A baseline that found nothing would make the
headline number a fabrication, and it would pass a test suite that only ever
checked ModelGuard came first.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
)

from benchmarks.baselines import no_lineage_leakage, table_level_leakage
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY, leakage_findings
from tests.conftest import (
    CLEAN_COLUMN_URN,
    CLEAN_FEATURE_URN,
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    LABEL_COLUMN_URN,
    LABEL_TERM_URN,
    LEAK_COLUMN_URN,
    LEAK_FEATURE_URN,
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

#: loans_raw as the catalog holds it: the label column is one of several.
SOURCE_SCHEMA = {
    "applicant_id": "VARCHAR",
    "income": "NUMBER",
    "default_status": "BOOLEAN",
}


def _feature(source_column_urn: str) -> MLFeaturePropertiesClass:
    return MLFeaturePropertiesClass(
        sources=[FEATURE_TABLE_URN],
        customProperties={SOURCE_COLUMN_PROPERTY: source_column_urn},
    )


def _graph(*, column_lineage: bool) -> tuple[FakeGraph, FakeClient]:
    """The seeded graph, with or without the leaking column edge.

    ``column_lineage=False`` is the graph after a team fixes the leak: the feature
    no longer descends from the label, though the two *tables* are still related,
    because ``customer_features`` is still built from ``loans_raw``. That residual
    table relationship is the whole point of the comparison.
    """
    aspects: dict[tuple[str, type], object] = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            name="Credit Risk v3",
            mlFeatures=[LEAK_FEATURE_URN, CLEAN_FEATURE_URN],
            deployments=[DEPLOYMENT_URN],
        ),
        (LEAK_FEATURE_URN, MLFeaturePropertiesClass): _feature(LEAK_COLUMN_URN),
        (CLEAN_FEATURE_URN, MLFeaturePropertiesClass): _feature(CLEAN_COLUMN_URN),
        (LABEL_COLUMN_URN, GlossaryTermsClass): GlossaryTermsClass(
            terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
        ),
        (TABLE_URN, schema_metadata({}).__class__): schema_metadata(SOURCE_SCHEMA),
    }

    by_column: dict[str, list] = {}
    if column_lineage:
        by_column["prior_default_flag"] = [
            lineage_result(
                TABLE_URN,
                hops=1,
                direction="upstream",
                paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
            )
        ]
    by_column["applicant_income"] = [
        lineage_result(
            TABLE_URN,
            hops=1,
            direction="upstream",
            paths=column_path(
                CLEAN_COLUMN_URN, LABEL_COLUMN_URN.replace("default_status", "income")
            ),
        )
    ]

    # The table-level query carries no source_column, so FakeLineage answers it
    # from `results`: customer_features derives from loans_raw, whatever the
    # columns do. That relationship survives the fix, which is what makes the
    # baseline keep alerting.
    client = FakeClient(
        lineage_results=[lineage_result(TABLE_URN, hops=1, direction="upstream")],
        lineage_by_column=by_column,
    )
    return FakeGraph(aspects=aspects), client  # type: ignore[arg-type]


def _conn(*, column_lineage: bool) -> DataHubConnection:
    graph, client = _graph(column_lineage=column_lineage)
    return make_connection(graph, client)


def test_the_table_level_baseline_really_does_catch_the_leak():
    """The headline comparison is worthless if the opposition cannot detect at all."""
    flagged = table_level_leakage(_conn(column_lineage=True), MODEL_URN, CONFIG)

    assert LEAK_FEATURE_URN in flagged, "a baseline that misses the leak is a strawman"


def test_the_table_level_baseline_cannot_say_which_feature_leaks():
    """Both features descend from the labelled table; only one descends from the label."""
    flagged = table_level_leakage(_conn(column_lineage=True), MODEL_URN, CONFIG)

    assert set(flagged) == {LEAK_FEATURE_URN, CLEAN_FEATURE_URN}


def test_column_level_isolates_the_one_feature_that_leaks():
    """The contrast the comparison rests on, asserted rather than assumed."""
    findings = leakage_findings(_conn(column_lineage=True), MODEL_URN, CONFIG)

    assert [finding.leak.feature_urn for finding in findings] == [LEAK_FEATURE_URN]


def test_the_table_level_baseline_keeps_alerting_after_the_leak_is_fixed():
    """The alert-fatigue number: it cannot see a remediation it could not see cause."""
    flagged = table_level_leakage(_conn(column_lineage=False), MODEL_URN, CONFIG)

    assert set(flagged) == {LEAK_FEATURE_URN, CLEAN_FEATURE_URN}


def test_column_level_goes_quiet_once_the_leak_is_fixed():
    assert leakage_findings(_conn(column_lineage=False), MODEL_URN, CONFIG) == ()


def test_the_baseline_is_handed_the_same_label_declaration():
    """Fairness check: remove the label and the baseline stops flagging.

    If it flagged regardless of whether a label was declared, it would not be a
    lineage approach at all, it would be a function that always says yes, and its
    precision would be meaningless.
    """
    graph, client = _graph(column_lineage=True)
    graph._aspects.pop((LABEL_COLUMN_URN, GlossaryTermsClass))

    assert table_level_leakage(make_connection(graph, client), MODEL_URN, CONFIG) == ()


def test_the_no_lineage_baseline_cannot_express_leakage_at_all():
    assert no_lineage_leakage(_conn(column_lineage=True), MODEL_URN, CONFIG) == ()


def test_the_baseline_honours_the_hop_cap_the_detector_honours():
    """Fairness: DataHub over-returns past the cap, and ModelGuard filters it (D-020).

    Without the same guard the baseline would inherit distant tables ModelGuard
    never sees, and any label sitting in one of them would score as a false
    positive caused by this harness rather than by the approach it stands for.
    """
    graph, client = _graph(column_lineage=True)

    # A far-away table, past the cap, holding a column that is genuinely *declared*
    # to be a label. Giving it a column merely named default_status would prove
    # nothing: the detector keys on the glossary term, so an undeclared column is
    # invisible to it and the test would pass whether or not the cap is honoured.
    distant = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.archive,PROD)"
    graph.set_aspect(distant, schema_metadata({"default_status": "BOOLEAN"}))
    graph.set_aspect(
        f"urn:li:schemaField:({distant},default_status)",
        GlossaryTermsClass(
            terms=[GlossaryTermAssociationClass(urn=LABEL_TERM_URN)], auditStamp=None
        ),
    )
    client.lineage.results = [
        lineage_result(TABLE_URN, hops=1, direction="upstream"),
        lineage_result(distant, hops=CONFIG.leakage_max_hops + 1, direction="upstream"),
    ]

    flagged = table_level_leakage(make_connection(graph, client), MODEL_URN, CONFIG)

    # Still flags both, but on the strength of loans_raw at one hop, not the
    # archive past the cap. Removing loans_raw must silence it entirely.
    client.lineage.results = [
        lineage_result(distant, hops=CONFIG.leakage_max_hops + 1, direction="upstream")
    ]
    beyond_cap_only = table_level_leakage(make_connection(graph, client), MODEL_URN, CONFIG)

    assert set(flagged) == {LEAK_FEATURE_URN, CLEAN_FEATURE_URN}
    assert beyond_cap_only == (), "a table past the hop cap must not reach the baseline"
