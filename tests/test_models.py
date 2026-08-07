from __future__ import annotations

import re
from dataclasses import replace

import pytest

from janus.models import (
    ChangeKind,
    ModelAtRisk,
    RemedyKind,
    SchemaChange,
    Severity,
    severity_rank,
)
from tests.conftest import (
    DEPLOYMENT_URN,
    MODEL_URN,
    make_deprecated_input_finding,
    make_finding,
    make_leakage_finding,
    make_schema_drift_finding,
    make_sensitive_source_finding,
)

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


def test_the_drift_title_names_the_dataset_and_keeps_the_columns_in_evidence():
    """The drifted column set can grow between scans, so it stays out of the key."""
    one = make_schema_drift_finding(changes=(SchemaChange("a", ChangeKind.RETYPED, "INT", "STR"),))
    many = make_schema_drift_finding(
        changes=(
            SchemaChange("a", ChangeKind.RETYPED, "INT", "STR"),
            SchemaChange("b", ChangeKind.REMOVED, "INT", None),
        )
    )
    # Same dataset, so the same dedup title regardless of how much has drifted.
    assert one.title == many.title
    assert "customer_features" in one.title
    # The columns live in the evidence, where changing them is fine.
    assert "a: INT -> STR" in many.evidence["drifted_fields"]
    assert many.evidence["retyped"] == "1"
    assert many.evidence["removed"] == "1"


def test_two_models_drifting_on_one_dataset_do_not_share_a_dedup_key():
    """Drift is a property of the (model, dataset) pair, not of the dataset.

    The title is the dedup key, and the incident attaches to the dataset. A title
    naming only the dataset made two models' drift on a shared input one and the
    same incident: the second model's finding was deduplicated into silence, and
    whichever model recovered first resolved the other's live incident.
    """
    v3 = make_schema_drift_finding()
    v4 = replace(v3, model=replace(v3.model, urn=f"{MODEL_URN}_v4", name="Credit Risk v4"))

    assert v3.resource_urn == v4.resource_urn, "both incidents land on the same dataset"
    assert v3.title != v4.title
    assert "Credit Risk v4" in v4.title


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
        urn="urn:li:mlModel:(urn:li:dataPlatform:mlflow,a,PROD)",
        name="a",
        deployments=(),
        live_deployments=(),
        has_owner=True,
        hops=3,
        features_at_risk=(),
    )
    unowned = ModelAtRisk(
        urn="urn:li:mlModel:(urn:li:dataPlatform:mlflow,b,PROD)",
        name="b",
        deployments=(),
        live_deployments=(),
        has_owner=False,
        hops=3,
        features_at_risk=(),
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


# --------------------------------------------------------------------------
# Counterfactuals
# --------------------------------------------------------------------------


def test_every_finding_type_offers_at_least_one_way_out():
    """A detector that can prove a fault and not name a fix is half a detector.

    `counterfactual` is abstract on the Finding ABC, so a new type cannot skip
    it, but it could still be implemented as an empty tuple. This is the check
    that it is not.
    """
    for finding in (
        make_finding(),
        make_leakage_finding(),
        make_schema_drift_finding(),
        make_sensitive_source_finding(),
        make_deprecated_input_finding(),
    ):
        remedies = finding.counterfactual.remedies
        assert remedies, type(finding).__name__
        assert all(remedy.summary.strip() and remedy.targets for remedy in remedies)


def test_a_single_path_leak_offers_the_one_edge_that_carries_it():
    counterfactual = make_leakage_finding().counterfactual
    cut = next(r for r in counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE)

    assert counterfactual.multi_path is False
    assert cut.targets == ("prior_default_flag <- default_status",)


def test_a_two_path_leak_names_both_edges_and_says_one_cut_is_not_enough():
    """The measurement the whole multi-path scenario exists to make.

    A remedy naming one edge of two is a remedy that does not work, and it is the
    kind that looks convincing: the incident quotes that path, so cutting it is
    exactly what a reader would do.
    """
    finding = make_leakage_finding(other_paths=(("prior_default_flag", "default_status_backup"),))
    counterfactual = finding.counterfactual
    cut = next(r for r in counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE)

    assert counterfactual.paths == 2
    assert cut.targets == (
        "prior_default_flag <- default_status",
        "prior_default_flag <- default_status_backup",
    )
    assert any("Cutting one of them" in line for line in counterfactual.lines())


def test_a_column_that_is_itself_the_label_is_never_told_to_cut_an_edge():
    """A direct alias has no derivation to cut, so no cut can clear it.

    The walk returns a one-column chain for a feature wired straight off the
    label. Offering "cut the first edge" there would be offering a remedy with
    nothing to act on, which the benchmark would then have to apply and fail.
    """
    finding = make_leakage_finding()
    direct = replace(finding, leak=replace(finding.leak, column_path=("default_status",)))

    kinds = {remedy.kind for remedy in direct.counterfactual.remedies}
    assert RemedyKind.CUT_LINEAGE not in kinds
    assert RemedyKind.DROP_FEATURE in kinds


def test_two_paths_sharing_a_first_edge_are_named_once():
    """A shared first edge is one thing to do, and listing it twice reads as two."""
    finding = make_leakage_finding(
        other_paths=(("prior_default_flag", "default_status", "raw_outcome"),)
    )
    cut = next(r for r in finding.counterfactual.remedies if r.kind is RemedyKind.CUT_LINEAGE)

    assert cut.targets == ("prior_default_flag <- default_status",)


def test_a_stale_table_with_no_model_downstream_offers_only_the_refresh():
    """There is nothing to disconnect, and naming a disconnection would be noise."""
    kinds = {r.kind for r in make_finding(with_model=False).counterfactual.remedies}

    assert kinds == {RemedyKind.REFRESH_SOURCE}


def test_a_deprecation_with_no_note_says_to_go_and_ask():
    """The note is the only place a successor is named; with none, nothing is invented."""
    lines = " ".join(make_deprecated_input_finding(note="").counterfactual.lines())

    assert "ask them" in lines
    assert "loans_v2" not in lines
