"""The catalog-level fold over per-model coverage gaps (T-15)."""

from __future__ import annotations

from modelguard.detect.coverage import (
    CHECK_DEPRECATED_INPUT,
    CHECK_LEAKAGE,
    CHECK_PROXY,
    CHECK_SCHEMA_DRIFT,
    CHECK_SENSITIVE_SOURCE,
    MODEL_CHECKS,
    Unevaluated,
)
from modelguard.detect.guard_coverage import ModelCoverage, aggregate

MODEL_A = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,a,PROD)"
MODEL_B = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,b,PROD)"
MODEL_C = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,c,PROD)"


def gap(check: str, model_urn: str, *, remedy: str = "Run `modelguard link`.") -> Unevaluated:
    """One check that could not run against one model."""
    return Unevaluated(check=check, target_urn=model_urn, reason="no metadata", remedy=remedy)


def test_a_check_no_model_can_run_reports_zero_not_silence():
    """A check blocked everywhere is a row at 0%, never a row that is missing.

    The whole point of the figure: a catalog where nothing is classified must
    read as "this was never asked", not as five checks passing and one absent.
    """
    sweep = [
        ModelCoverage(model_urn=MODEL_A, gaps=(gap(CHECK_SENSITIVE_SOURCE, MODEL_A),)),
        ModelCoverage(model_urn=MODEL_B, gaps=(gap(CHECK_SENSITIVE_SOURCE, MODEL_B),)),
    ]

    catalog = aggregate(sweep)

    assert catalog.by_check[CHECK_SENSITIVE_SOURCE].covered == 0
    assert catalog.by_check[CHECK_SENSITIVE_SOURCE].rate == 0.0
    assert catalog.by_check[CHECK_LEAKAGE].rate == 1.0


def test_the_denominator_counts_clean_models_too():
    """A model with no gaps still enlarges the denominator.

    Dropping the clean ones would make a catalog where one model of ten is
    linked report 100% leakage coverage, which is the inflation this whole
    module exists to avoid.
    """
    sweep = [
        ModelCoverage(model_urn=MODEL_A, gaps=()),
        ModelCoverage(model_urn=MODEL_B, gaps=(gap(CHECK_LEAKAGE, MODEL_B),)),
        ModelCoverage(model_urn=MODEL_C, gaps=(gap(CHECK_LEAKAGE, MODEL_C),)),
    ]

    catalog = aggregate(sweep)

    assert catalog.models == 3
    assert catalog.by_check[CHECK_LEAKAGE].covered == 1
    assert catalog.by_check[CHECK_LEAKAGE].total == 3


def test_the_headline_rate_is_over_model_check_pairs():
    """The one figure divides evaluable pairs by asked pairs, not models by models."""
    sweep = [
        ModelCoverage(
            model_urn=MODEL_A,
            gaps=(gap(CHECK_LEAKAGE, MODEL_A), gap(CHECK_SCHEMA_DRIFT, MODEL_A)),
        ),
        ModelCoverage(model_urn=MODEL_B, gaps=()),
    ]

    catalog = aggregate(sweep)

    # Two models times five checks, minus the two gaps.
    assert catalog.total_checks == 2 * len(MODEL_CHECKS)
    assert catalog.covered_checks == 2 * len(MODEL_CHECKS) - 2
    assert catalog.rate == catalog.covered_checks / catalog.total_checks


def test_next_join_ranks_by_remedy_so_one_link_is_recommended_once():
    """The advice is keyed on the action, not on the check it unblocks.

    One `link` on one model unblocks leakage, sensitive source and proxy. A
    ranking by check would put three separate recommendations in front of
    somebody who has one thing to do.
    """
    link = "Declare them with `modelguard link`."
    classify = "Set MODELGUARD_SENSITIVE_TERM_URNS."
    sweep = [
        ModelCoverage(
            model_urn=MODEL_A,
            gaps=(
                gap(CHECK_LEAKAGE, MODEL_A, remedy=link),
                gap(CHECK_SENSITIVE_SOURCE, MODEL_A, remedy=link),
                gap(CHECK_PROXY, MODEL_A, remedy=link),
            ),
        ),
        ModelCoverage(
            model_urn=MODEL_B,
            gaps=(
                gap(CHECK_SENSITIVE_SOURCE, MODEL_B, remedy=classify),
                gap(CHECK_PROXY, MODEL_B, remedy=classify),
            ),
        ),
    ]

    catalog = aggregate(sweep)

    assert catalog.next_join is not None
    assert catalog.next_join.remedy == link
    assert catalog.next_join.unblocks == 3
    assert catalog.next_join.models == (MODEL_A,)


def test_next_join_is_stable_when_two_remedies_are_worth_the_same():
    """A tie resolves the same way twice, whichever order the sweep arrived in.

    A recommendation that reshuffled between two identical runs would read as
    the tool changing its mind about a graph that did not change.
    """
    first = "Aaa do this."
    second = "Zzz do that."
    forwards = [
        ModelCoverage(model_urn=MODEL_A, gaps=(gap(CHECK_LEAKAGE, MODEL_A, remedy=first),)),
        ModelCoverage(model_urn=MODEL_B, gaps=(gap(CHECK_SCHEMA_DRIFT, MODEL_B, remedy=second),)),
    ]
    backwards = list(reversed(forwards))

    assert aggregate(forwards).next_join == aggregate(backwards).next_join


def test_a_fully_covered_catalog_names_no_next_join():
    """Nothing blocked means no advice, rather than advice about nothing."""
    catalog = aggregate([ModelCoverage(model_urn=MODEL_A, gaps=())])

    assert catalog.next_join is None
    assert catalog.rate == 1.0


def test_an_empty_sweep_is_zero_and_not_a_clean_bill():
    """Zero models is 0%, because an unasked catalog has passed nothing."""
    catalog = aggregate([])

    assert catalog.models == 0
    assert catalog.rate == 0.0
    assert all(check.rate == 0.0 for check in catalog.checks)


def test_a_gap_for_a_check_outside_the_model_set_does_not_invent_a_row():
    """A freshness gap in the sweep is ignored, not folded into the model figure.

    Freshness is asked of a table. Counting it here would put a sixth row in a
    five-row table and divide two different denominators into one number.
    """
    sweep = [ModelCoverage(model_urn=MODEL_A, gaps=(gap("freshness", MODEL_A),))]

    catalog = aggregate(sweep)

    assert tuple(check.check for check in catalog.checks) == MODEL_CHECKS
    assert catalog.covered_checks == catalog.total_checks


def test_check_rows_come_back_in_the_declared_order():
    """Two runs of the same catalog render the rows the same way."""
    catalog = aggregate([ModelCoverage(model_urn=MODEL_A, gaps=())])

    assert tuple(check.check for check in catalog.checks) == (
        CHECK_LEAKAGE,
        CHECK_SCHEMA_DRIFT,
        CHECK_SENSITIVE_SOURCE,
        CHECK_DEPRECATED_INPUT,
        CHECK_PROXY,
    )
