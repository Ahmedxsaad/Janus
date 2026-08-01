from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
)

from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import (
    blast_radius,
    finding_for,
    freshness_signal,
)
from modelguard.models import Severity
from tests.conftest import FakeClient, FakeGraph, lineage_result, make_connection

TABLE = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_raw,PROD)"
FEATURE_TABLE = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
)
FAR_TABLE = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.scoring_log,PROD)"
MODEL = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)"
GROUP = "urn:li:mlModelGroup:(urn:li:dataPlatform:mlflow,credit_risk_models,PROD)"
DEPLOYMENT = "urn:li:mlModelDeployment:(urn:li:dataPlatform:mlflow,credit_risk_v3_prod,PROD)"
FEATURE_INCOME = "urn:li:mlFeature:(credit_risk,applicant_income)"
FEATURE_LEAK = "urn:li:mlFeature:(credit_risk,prior_default_flag)"

NOW = 1_800_000_000_000
HOUR = 3_600_000


def _operation(last_updated_ms: int) -> OperationClass:
    return OperationClass(
        timestampMillis=NOW,
        operationType=OperationTypeClass.UPDATE,
        lastUpdatedTimestamp=last_updated_ms,
        actor="urn:li:corpuser:datahub",
    )


def _model_properties(deployments: list[str], features: list[str]) -> MLModelPropertiesClass:
    return MLModelPropertiesClass(
        name="Credit Risk v3", deployments=deployments, mlFeatures=features
    )


def _graph(
    *,
    lag_hours: float | None,
    deployment_status: str | None = DeploymentStatusClass.IN_SERVICE,
    owned: bool = False,
    model_features: list[str] | None = None,
) -> FakeGraph:
    timeseries = {}
    if lag_hours is not None:
        timeseries[(TABLE, OperationClass)] = _operation(NOW - int(lag_hours * HOUR))

    aspects: dict[tuple[str, type], object] = {
        (MODEL, MLModelPropertiesClass): _model_properties(
            [DEPLOYMENT],
            model_features if model_features is not None else [FEATURE_INCOME, FEATURE_LEAK],
        ),
    }
    if deployment_status is not None:
        aspects[(DEPLOYMENT, MLModelDeploymentPropertiesClass)] = MLModelDeploymentPropertiesClass(
            status=deployment_status
        )
    if owned:
        aspects[(MODEL, OwnershipClass)] = OwnershipClass(
            owners=[OwnerClass(owner="urn:li:corpuser:ml", type=OwnershipTypeClass.TECHNICAL_OWNER)]
        )
    return FakeGraph(aspects, timeseries=timeseries)


def _client(hops_for_model: int = 3) -> FakeClient:
    return FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(FEATURE_INCOME, 2),
            lineage_result(FEATURE_LEAK, 2),
            lineage_result(MODEL, hops_for_model),
            # DataHub returns entities past the cap once max_hops exceeds 2. A
            # dataset at hop 4 is the case that actually exercises the filter:
            # a model group would be discarded by entity type regardless.
            lineage_result(FAR_TABLE, 4),
            lineage_result(GROUP, 4),
        ]
    )


# --------------------------------------------------------------------------
# The freshness predicate. False positives are the expensive failure here.
# --------------------------------------------------------------------------


def test_a_table_within_its_sla_produces_no_signal_of_staleness():
    conn = make_connection(_graph(lag_hours=2.0), _client())
    signal = freshness_signal(conn, TABLE, ScanConfig(freshness_sla_hours=6.0), now_ms=NOW)
    assert signal is not None
    assert signal.is_stale is False
    assert signal.lag_hours == pytest.approx(2.0)


def test_a_table_that_never_reported_an_operation_is_not_called_stale():
    """Absence of evidence is not staleness. An uninstrumented table must not fire."""
    conn = make_connection(_graph(lag_hours=None), _client())
    assert freshness_signal(conn, TABLE, ScanConfig(), now_ms=NOW) is None
    assert blast_radius(conn, TABLE, ScanConfig(), now_ms=NOW) is None


def test_a_fresh_table_has_no_blast_radius_and_never_traverses_lineage():
    client = _client()
    conn = make_connection(_graph(lag_hours=1.0), client)
    assert blast_radius(conn, TABLE, ScanConfig(freshness_sla_hours=6.0), now_ms=NOW) is None
    # The threshold gates the traversal: a healthy table costs one aspect read.
    assert client.lineage.lineage_calls == []


def test_lag_exactly_at_the_sla_is_not_stale_but_a_hair_over_is():
    config = ScanConfig(freshness_sla_hours=6.0)
    at = make_connection(_graph(lag_hours=6.0), _client())
    assert freshness_signal(at, TABLE, config, now_ms=NOW).is_stale is False

    over = make_connection(_graph(lag_hours=6.001), _client())
    assert freshness_signal(over, TABLE, config, now_ms=NOW).is_stale is True


# --------------------------------------------------------------------------
# The traversal and the severity it implies
# --------------------------------------------------------------------------


def test_a_stale_table_reaches_the_live_model_three_hops_downstream():
    conn = make_connection(_graph(lag_hours=30.0), _client())
    radius = blast_radius(conn, TABLE, ScanConfig(), now_ms=NOW)

    assert radius is not None
    assert radius.failing_table_name == "ecommerce.public.loans_raw"
    assert radius.downstream_datasets == (FEATURE_TABLE,)
    assert radius.downstream_features == (FEATURE_INCOME, FEATURE_LEAK)

    assert len(radius.models) == 1
    model = radius.models[0]
    assert model.urn == MODEL
    assert model.hops == 3
    assert model.is_live is True
    assert model.live_deployments == (DEPLOYMENT,)
    assert radius.severity is Severity.CRITICAL


def test_entities_beyond_the_hop_cap_are_dropped():
    """DataHub returns past the cap in full-graph mode. The detector must honor it."""
    conn = make_connection(_graph(lag_hours=30.0), _client())
    radius = blast_radius(conn, TABLE, ScanConfig(max_hops=3), now_ms=NOW)
    assert radius is not None

    # FAR_TABLE is a dataset at hop 4: only the hop filter can exclude it.
    assert radius.downstream_datasets == (FEATURE_TABLE,)
    assert FAR_TABLE not in radius.downstream_datasets


def test_raising_the_hop_cap_admits_the_further_dataset():
    """Proves the previous test measures the cap, not an unrelated exclusion."""
    conn = make_connection(_graph(lag_hours=30.0), _client())
    radius = blast_radius(conn, TABLE, ScanConfig(max_hops=4), now_ms=NOW)
    assert radius is not None
    assert set(radius.downstream_datasets) == {FEATURE_TABLE, FAR_TABLE}


def test_a_model_further_than_the_hop_cap_is_not_at_risk():
    conn = make_connection(_graph(lag_hours=30.0), _client(hops_for_model=5))
    radius = blast_radius(conn, TABLE, ScanConfig(max_hops=3), now_ms=NOW)
    assert radius is not None
    assert radius.models == ()
    # Still a data problem, but not a data-to-model one.
    assert radius.severity is Severity.MEDIUM


def test_an_entity_reached_by_two_paths_is_counted_once():
    """The full-graph search past hop 2 can return one entity via several paths.

    Listing it twice would double-count it in "Downstream datasets: N" on the
    impact report a human reads, and put the same URN on the page twice. The
    model is deduped on its shortest hop, which is what a second, longer path
    to the same model must not change.
    """
    client = FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(FEATURE_INCOME, 2),
            lineage_result(MODEL, 3),
            # The same three entities again, reached the long way round.
            lineage_result(FEATURE_TABLE, 2),
            lineage_result(FEATURE_INCOME, 3),
            lineage_result(MODEL, 3),
        ]
    )
    radius = blast_radius(
        make_connection(_graph(lag_hours=30.0), client), TABLE, ScanConfig(), now_ms=NOW
    )

    assert radius is not None
    assert radius.downstream_datasets == (FEATURE_TABLE,)
    assert radius.downstream_features == (FEATURE_INCOME,)
    assert [model.urn for model in radius.models] == [MODEL]


def test_a_deployed_but_not_serving_model_is_high_not_critical():
    graph = _graph(lag_hours=30.0, deployment_status=DeploymentStatusClass.OUT_OF_SERVICE)
    radius = blast_radius(make_connection(graph, _client()), TABLE, ScanConfig(), now_ms=NOW)
    assert radius is not None
    assert radius.models[0].is_live is False
    assert radius.models[0].severity is Severity.HIGH


def test_a_deployment_with_no_properties_aspect_is_not_assumed_live():
    """CRITICAL is only reached on positive evidence that traffic is being served."""
    graph = _graph(lag_hours=30.0, deployment_status=None)
    radius = blast_radius(make_connection(graph, _client()), TABLE, ScanConfig(), now_ms=NOW)
    assert radius is not None
    assert radius.models[0].live_deployments == ()
    assert radius.models[0].severity is Severity.HIGH


def test_only_the_models_own_features_that_the_failure_reaches_are_at_risk():
    """A model consuming a feature the failing table does not feed must not list it."""
    unrelated = "urn:li:mlFeature:(credit_risk,unrelated_feature)"
    graph = _graph(lag_hours=30.0, model_features=[FEATURE_LEAK, unrelated])
    radius = blast_radius(make_connection(graph, _client()), TABLE, ScanConfig(), now_ms=NOW)

    assert radius is not None
    assert radius.models[0].features_at_risk == (FEATURE_LEAK,)


def test_ownership_is_read_from_the_graph():
    owned = blast_radius(
        make_connection(_graph(lag_hours=30.0, owned=True), _client()),
        TABLE,
        ScanConfig(),
        now_ms=NOW,
    )
    unowned = blast_radius(
        make_connection(_graph(lag_hours=30.0), _client()), TABLE, ScanConfig(), now_ms=NOW
    )
    assert owned is not None and unowned is not None
    assert owned.models[0].has_owner is True
    assert unowned.models[0].has_owner is False


# --------------------------------------------------------------------------
# The finding handed to write-back
# --------------------------------------------------------------------------


def test_the_incident_attaches_to_the_failing_table_never_to_the_model():
    """DataHub rejects an incident on an mlModel with a 500. The finding must not try."""
    conn = make_connection(_graph(lag_hours=30.0), _client())
    radius = blast_radius(conn, TABLE, ScanConfig(), now_ms=NOW)
    assert radius is not None

    finding = finding_for(radius)
    assert finding.resource_urn == TABLE
    assert finding.incident_type == "FRESHNESS"
    assert finding.severity is Severity.CRITICAL


def test_the_finding_carries_the_measured_lag_as_evidence():
    conn = make_connection(_graph(lag_hours=30.0), _client())
    radius = blast_radius(conn, TABLE, ScanConfig(), now_ms=NOW)
    assert radius is not None

    evidence = finding_for(radius).evidence
    assert evidence["lag_hours"] == "30.0"
    assert evidence["live_models_at_risk"] == "1"
    assert evidence["failing_table"] == "ecommerce.public.loans_raw"
