"""The trust score is a deterministic weighted rollup of a model's findings."""

from __future__ import annotations

from modelguard.config import ScanConfig
from modelguard.detect.trust_score import (
    DEDUCTION_FRESHNESS_LAG,
    DEDUCTION_LEAKAGE,
    DEDUCTION_MISSING_OWNER,
    DEDUCTION_SCHEMA_DRIFT,
    DEDUCTION_UPSTREAM_FAILURE,
    TrustInputs,
    trust_inputs_from_findings,
    trust_score,
)
from modelguard.models import ModelRef, TrustBand
from tests.conftest import (
    MODEL_URN,
    make_finding,
    make_leakage_finding,
    make_schema_drift_finding,
)

CONFIG = ScanConfig()


def _ref(*, has_owner: bool) -> ModelRef:
    return ModelRef(
        urn=MODEL_URN,
        name="Credit Risk v3",
        deployments=(),
        live_deployments=(),
        has_owner=has_owner,
    )


def test_every_risk_present_drives_the_score_to_zero():
    # A stale source (live model, 30h/6h lag), leakage, and schema drift, unowned.
    findings = [
        make_finding(live=True, lag_hours=30.0, sla_hours=6.0),
        make_leakage_finding(live=True),
        make_schema_drift_finding(live=True),
    ]
    inputs = trust_inputs_from_findings(findings, _ref(has_owner=False))
    score = trust_score(inputs, CONFIG)

    assert score.value == 0
    assert score.band == TrustBand.AT_RISK
    assert set(score.deductions) == {
        DEDUCTION_UPSTREAM_FAILURE,
        DEDUCTION_LEAKAGE,
        DEDUCTION_SCHEMA_DRIFT,
        DEDUCTION_FRESHNESS_LAG,
        DEDUCTION_MISSING_OWNER,
    }


def test_leakage_and_drift_only_lands_on_watch():
    # A --model scan: leakage + drift + no owner, no freshness signal at all.
    findings = [make_leakage_finding(live=True), make_schema_drift_finding(live=True)]
    inputs = trust_inputs_from_findings(findings, _ref(has_owner=False))
    score = trust_score(inputs, CONFIG)

    # 100 - 20 (leakage) - 15 (drift) - 10 (owner) = 55.
    assert score.value == 55
    assert score.band == TrustBand.WATCH
    assert DEDUCTION_UPSTREAM_FAILURE not in score.deductions
    assert DEDUCTION_FRESHNESS_LAG not in score.deductions


def test_a_single_owned_leakage_finding_stays_healthy_band_boundary():
    # 100 - 20 (leakage) = 80, which is healthy (>= 70).
    inputs = trust_inputs_from_findings([make_leakage_finding()], _ref(has_owner=True))
    score = trust_score(inputs, CONFIG)
    assert score.value == 80
    assert score.band == TrustBand.HEALTHY


def test_freshness_deduction_scales_with_lag_over_sla():
    # Lag is 3h against a 6h SLA: ratio 0.5, so the 15-point weight costs 7.5.
    scaled = trust_score(
        TrustInputs(
            has_upstream_failure=True,
            has_leakage=False,
            has_schema_drift=False,
            freshness_lag_ratio=0.5,
            missing_owner=False,
        ),
        CONFIG,
    )
    # 100 - 40 (upstream failure) - 7.5 (half the freshness weight) = 52.5 -> 52.
    assert scaled.value == 52
    assert scaled.deductions[DEDUCTION_FRESHNESS_LAG] == 7.5


def test_lag_ratio_is_capped_at_one():
    inputs = trust_inputs_from_findings(
        [make_finding(live=True, lag_hours=600.0, sla_hours=6.0)],
        _ref(has_owner=True),
    )
    # 100 hours over SLA would be a ratio of 100, but it is clamped to 1.
    assert inputs.freshness_lag_ratio == 1.0


def test_worst_lag_wins_across_multiple_freshness_findings():
    inputs = trust_inputs_from_findings(
        [
            make_finding(live=True, lag_hours=7.0, sla_hours=6.0),
            make_finding(live=True, lag_hours=3.0, sla_hours=6.0),
        ],
        _ref(has_owner=True),
    )
    assert inputs.freshness_lag_ratio == 1.0


def test_a_clean_owned_model_scores_one_hundred():
    score = trust_score(
        TrustInputs(
            has_upstream_failure=False,
            has_leakage=False,
            has_schema_drift=False,
            freshness_lag_ratio=0.0,
            missing_owner=False,
        ),
        CONFIG,
    )
    assert score.value == 100
    assert score.band == TrustBand.HEALTHY
    assert score.deductions == {}
