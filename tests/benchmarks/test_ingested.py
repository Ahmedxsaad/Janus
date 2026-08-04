"""The ingested-graph measurement's two offline halves: ground truth, and the report.

Everything that touches DataHub in ``benchmarks.ingested`` is exercised by
running the benchmark; what can be checked without one is the part where a silent
mistake would be invisible. Ground truth here is read from the example's dbt
model, so a rewritten model must move it, and a report whose section was not run
must say so rather than printing zeros that read as a clean graph.
"""

from __future__ import annotations

from datetime import UTC, datetime

from benchmarks import metrics
from benchmarks.ingested import IngestedScore, Route, leaking_features
from benchmarks.inject import Target, Trial
from benchmarks.run_bench import BlastRadiusCheck, TrialOutcome, WriteBackCheck, render_results
from modelguard.config import ScanConfig
from modelguard.models import FindingType

CONFIG = ScanConfig(freshness_sla_hours=6.0)
WHEN = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
WRITEBACK = WriteBackCheck(
    incidents_after_first=1,
    incidents_after_second=1,
    trust_score_written=True,
    trust_band_written=True,
)
BLAST = BlastRadiusCheck(expected_models=1, found_models=1, named_the_live_deployment=True)


def _outcomes() -> list[TrialOutcome]:
    trial = Trial(
        name="leakage",
        family=FindingType.TARGET_LEAKAGE,
        target=Target.MODEL,
        expected=True,
        detail="a leak",
        plant=lambda conn, trial, now_ms: None,
    )
    return [TrialOutcome(trial=trial, observed=True, detect_seconds=0.1, settle_seconds=1.0)]


def _score(**overrides: object) -> IngestedScore:
    defaults: dict[str, object] = {
        "model_urn": "urn:li:mlModel:(urn:li:dataPlatform:mlflow,telco_churn_1,PROD)",
        "feature_dataset_urn": (
            "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.analytics.customer_features,PROD)"
        ),
        "unlinked_findings": 0,
        "unlinked_not_evaluated": ("schema drift", "target leakage"),
        "unlinked_training_tables": 0,
        "routes": (
            Route(
                adapter="feast",
                declared_table="analytics.customer_features",
                candidates=2,
                source_columns=("contract_renewed_flag", "tenure_months"),
                label_column="churned",
                excluded=("customer_id",),
            ),
        ),
        "routes_agree": True,
        "leakage": metrics.confusion([(True, True), (False, False)]),
        "truth": ("contract_renewed_flag",),
        "flagged": ("contract_renewed_flag",),
        "leak_path": "contract_renewed_flag <- churn",
        "label_reached": "warehouse.analytics.stg_customers.churn",
        "linked_not_evaluated": ("sensitive source",),
    }

    return IngestedScore(**{**defaults, **overrides})  # type: ignore[arg-type]


def _report(ingested: IngestedScore | None) -> str:
    return render_results(
        _outcomes(), BLAST, WRITEBACK, CONFIG, generated_at=WHEN, ingested=ingested
    )


def test_ground_truth_comes_from_the_dbt_model_on_disk():
    """The example still builds the leaking column, so it is still ground truth."""
    assert leaking_features() == frozenset({"contract_renewed_flag"})


def test_ground_truth_is_the_alias_not_a_mention_of_the_column(tmp_path, monkeypatch):
    """A comment naming the column is not the model building it.

    The example's SQL explains the mistake in a comment above the line that makes
    it, so a substring match on the column name would call a fixed project
    leaking and report a false positive as a miss.
    """
    fixed = tmp_path / "customer_features.sql"
    fixed.write_text("-- contract_renewed_flag was deleted here\nselect customer_id from x\n")
    monkeypatch.setattr("benchmarks.ingested.FEATURE_MODEL_SQL", fixed)

    assert leaking_features() == frozenset()


def test_a_section_that_did_not_run_says_so_instead_of_printing_zeroes():
    report = _report(None)

    assert "## Against a graph this project did not build" in report
    assert "**Not run.**" in report
    assert "examples/real-project/README.md" in report


def test_the_ingested_section_reports_the_measured_precision():
    report = _report(_score())

    assert "Precision 1.00" in report
    assert "Named exactly the leaking feature(s) and nothing else: **True**" in report


def test_a_detector_that_flagged_a_clean_feature_is_reported_as_wrong():
    """The row must move when the detector over-reports, not only when it misses."""
    score = _score(
        leakage=metrics.confusion([(True, True), (False, True)]),
        flagged=("contract_renewed_flag", "tenure_months"),
    )

    report = _report(score)

    assert "Precision 0.50" in report
    assert "Named exactly the leaking feature(s) and nothing else: **False**" in report


def test_a_finding_on_the_unlinked_model_is_marked_wrong():
    """Nothing is knowable there, so anything raised is a claim with no basis."""
    report = _report(_score(unlinked_findings=1))

    assert "Findings raised: **1** (**wrong**)" in report


def test_an_adapter_that_disagreed_with_the_catalog_is_named_in_its_row():
    score = _score(
        routes=(
            Route(
                adapter="dbt",
                declared_table="analytics.customer_features",
                candidates=2,
                source_columns=("gone",),
                label_column=None,
                excluded=(),
                error="the ingested table has no column gone",
            ),
        ),
    )

    report = _report(score)

    assert "the ingested table has no column gone" in report
    assert "Every declared column is a column the ingested table has: **False**" in report
