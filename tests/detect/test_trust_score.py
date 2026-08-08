"""The trust score is a deterministic weighted rollup of a model's findings."""

from __future__ import annotations

from janus.config import ScanConfig
from janus.detect.trust_score import (
    DEDUCTION_FRESHNESS_LAG,
    DEDUCTION_LEAKAGE,
    DEDUCTION_MISSING_OWNER,
    DEDUCTION_SCHEMA_DRIFT,
    DEDUCTION_UPSTREAM_FAILURE,
    TrustInputs,
    trust_inputs_from_findings,
    trust_score,
)
from janus.models import ModelRef, TrustBand, TrustScore
from tests.conftest import (
    MODEL_URN,
    make_finding,
    make_leakage_finding,
    make_schema_drift_finding,
)

CONFIG = ScanConfig()


def _points(score: TrustScore, name: str) -> float:
    """The points one named deduction cost, for a test that asserts on a weight."""
    return next(deduction.points for deduction in score.deductions if deduction.name == name)


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
    assert set(score.names) == {
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
    assert DEDUCTION_UPSTREAM_FAILURE not in score.names
    assert DEDUCTION_FRESHNESS_LAG not in score.names


def test_a_single_owned_leakage_finding_scores_high_but_bands_watch():
    # 100 - 20 (leakage) = 80, which points alone would call healthy (>= 70),
    # but a live leaking model is CRITICAL: gate blocks it, so the band must
    # not call it healthy too. See _SEVERITIES_THAT_CAP_HEALTHY.
    inputs = trust_inputs_from_findings([make_leakage_finding(live=True)], _ref(has_owner=True))
    score = trust_score(inputs, CONFIG)
    assert score.value == 80
    assert score.band == TrustBand.WATCH


def test_a_non_live_schema_drift_finding_keeps_the_healthy_band():
    # A model with no deployment at all makes schema drift MEDIUM, not HIGH
    # (SchemaDriftFinding.severity's own non-live case): a training-time
    # concern, not a live model scoring drifted inputs, so no cap fires.
    inputs = trust_inputs_from_findings(
        [make_schema_drift_finding(live=False)], _ref(has_owner=True)
    )
    score = trust_score(inputs, CONFIG)
    assert score.value == 85
    assert score.band == TrustBand.HEALTHY


def test_a_live_schema_drift_finding_also_caps_the_band_at_watch():
    # 100 - 15 (drift) = 85, comfortably healthy on points, but a live model
    # scoring drifted inputs is HIGH severity and must not read as healthy.
    inputs = trust_inputs_from_findings(
        [make_schema_drift_finding(live=True)], _ref(has_owner=True)
    )
    score = trust_score(inputs, CONFIG)
    assert score.value == 85
    assert score.band == TrustBand.WATCH


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
    assert _points(scaled, DEDUCTION_FRESHNESS_LAG) == 7.5


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
    assert score.deductions == ()


# --------------------------------------------------------------------------
# The waterfall: what a reader sees before the integer
# --------------------------------------------------------------------------


def test_each_deduction_names_the_finding_that_caused_it():
    """A deduction a reader cannot trace to a finding is not actionable."""
    findings = [make_leakage_finding(live=True), make_schema_drift_finding(live=True)]
    score = trust_score(trust_inputs_from_findings(findings, _ref(has_owner=False)), CONFIG)

    causes = {deduction.name: deduction.cause for deduction in score.deductions}
    assert causes[DEDUCTION_LEAKAGE] == findings[0].title
    assert causes[DEDUCTION_SCHEMA_DRIFT] == findings[1].title
    # Ownership is read off the model, not off a finding, so it names the model.
    assert "Credit Risk v3" in causes[DEDUCTION_MISSING_OWNER]


def test_the_freshness_deduction_names_the_worst_lagging_table():
    """The deduction is set by the worst lag, so it must name that table's finding."""
    # Both below the clamp, so the ratios actually differ: two saturated lags
    # are equally bad and the first is then rightly the one named.
    mild = make_finding(live=True, lag_hours=3.0, sla_hours=6.0, table_name="ecommerce.public.mild")
    worst = make_finding(
        live=True, lag_hours=5.0, sla_hours=6.0, table_name="ecommerce.public.worst"
    )
    score = trust_score(trust_inputs_from_findings([mild, worst], _ref(has_owner=True)), CONFIG)

    causes = {deduction.name: deduction.cause for deduction in score.deductions}
    assert causes[DEDUCTION_FRESHNESS_LAG] == worst.title


def test_deductions_are_ordered_worst_first():
    findings = [
        make_finding(live=True, lag_hours=30.0, sla_hours=6.0),
        make_leakage_finding(live=True),
        make_schema_drift_finding(live=True),
    ]
    score = trust_score(trust_inputs_from_findings(findings, _ref(has_owner=False)), CONFIG)

    points = [deduction.points for deduction in score.deductions]
    assert points == sorted(points, reverse=True)


def test_the_waterfall_leads_with_the_deductions_and_ends_with_the_total():
    findings = [make_leakage_finding(live=True)]
    score = trust_score(trust_inputs_from_findings(findings, _ref(has_owner=False)), CONFIG)

    lines = score.waterfall()
    assert lines[0].strip() == "100  starting"
    assert lines[1].strip().startswith("-20  leakage:")
    assert lines[2].strip().startswith("-10  missing_owner:")
    assert set(lines[3].strip()) == {"-"}
    assert lines[4].strip() == f"{score.value}  {score.band}"


def test_a_clean_score_still_renders_a_waterfall_with_no_steps():
    """No deductions is a real answer, not an empty render."""
    score = trust_score(TrustInputs(False, False, False, 0.0, False), CONFIG)

    assert score.waterfall() == ("100  starting", "---", "100  healthy")
