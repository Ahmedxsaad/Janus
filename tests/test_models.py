from __future__ import annotations

import re

import pytest

from modelguard.models import ModelAtRisk, Severity, severity_rank
from tests.conftest import DEPLOYMENT_URN, make_finding

DIGIT = re.compile(r"\d")


def _model(
    *, deployments: tuple[str, ...], live: tuple[str, ...], features: int = 1
) -> ModelAtRisk:
    return ModelAtRisk(
        urn="urn:li:mlModel:(urn:li:dataPlatform:mlflow,m,PROD)",
        name="m",
        hops=3,
        deployments=deployments,
        live_deployments=live,
        features_at_risk=tuple(f"f{i}" for i in range(features)),
        has_owner=False,
    )


# --------------------------------------------------------------------------
# The invariant the whole idempotency story rests on
# --------------------------------------------------------------------------


def test_the_incident_title_never_contains_a_measurement():
    """The title is part of the dedup key. A number in it duplicates the incident."""
    title = make_finding(lag_hours=30.0).title
    assert not DIGIT.search(title), f"the title must carry no measurement, got {title!r}"


def test_the_same_table_yields_the_same_title_however_stale_it_has_become():
    first = make_finding(lag_hours=30.0)
    later = make_finding(lag_hours=31.7)
    assert first.title == later.title


def test_the_measurements_live_in_the_evidence_not_in_the_title():
    finding = make_finding(lag_hours=30.0)
    assert finding.evidence["lag_hours"] == "30.0"
    assert finding.evidence["sla_hours"] == "6.0"
    assert finding.evidence["live_models_at_risk"] == "1"


# --------------------------------------------------------------------------
# Severity
# --------------------------------------------------------------------------


def test_a_live_model_is_critical():
    assert (
        _model(deployments=(DEPLOYMENT_URN,), live=(DEPLOYMENT_URN,)).severity is Severity.CRITICAL
    )


def test_a_deployed_but_idle_model_is_high():
    assert _model(deployments=(DEPLOYMENT_URN,), live=()).severity is Severity.HIGH


def test_an_undeployed_model_is_medium():
    assert _model(deployments=(), live=()).severity is Severity.MEDIUM


def test_a_stale_table_with_no_model_downstream_does_not_inherit_a_models_severity():
    assert make_finding(with_model=False).blast_radius.severity is Severity.MEDIUM


def test_the_blast_radius_takes_the_severity_of_its_worst_model():
    assert make_finding(live=True).blast_radius.severity is Severity.CRITICAL
    assert make_finding(live=False).blast_radius.severity is Severity.HIGH


def test_severity_rank_orders_critical_first():
    ordered = sorted(Severity, key=severity_rank)
    assert ordered[0] is Severity.CRITICAL
    assert ordered[-1] is Severity.LOW


# --------------------------------------------------------------------------
# Ranking inside a severity band
# --------------------------------------------------------------------------


def test_live_models_sort_ahead_of_idle_ones():
    live = _model(deployments=(DEPLOYMENT_URN,), live=(DEPLOYMENT_URN,))
    idle = _model(deployments=(DEPLOYMENT_URN,), live=())
    assert sorted([idle, live], key=ModelAtRisk.sort_key)[0] is live


def test_within_a_band_a_wider_blast_sorts_first():
    wide = _model(deployments=(), live=(), features=5)
    narrow = _model(deployments=(), live=(), features=1)
    assert sorted([narrow, wide], key=ModelAtRisk.sort_key)[0] is wide


def test_an_unowned_model_sorts_ahead_of_an_owned_one():
    """Nobody is on the hook for the unowned one, so it needs a human first."""
    owned = ModelAtRisk(
        "urn:li:mlModel:(urn:li:dataPlatform:mlflow,a,PROD)", "a", 3, (), (), (), True
    )
    unowned = ModelAtRisk(
        "urn:li:mlModel:(urn:li:dataPlatform:mlflow,b,PROD)", "b", 3, (), (), (), False
    )
    assert sorted([owned, unowned], key=ModelAtRisk.sort_key)[0] is unowned


# --------------------------------------------------------------------------
# Freshness arithmetic
# --------------------------------------------------------------------------


def test_lag_is_measured_from_the_observation_instant():
    signal = make_finding(lag_hours=30.0).blast_radius.signal
    assert signal.lag_hours == pytest.approx(30.0)
    assert signal.is_stale is True


def test_a_table_inside_its_sla_is_not_stale():
    assert make_finding(lag_hours=2.0).blast_radius.signal.is_stale is False
