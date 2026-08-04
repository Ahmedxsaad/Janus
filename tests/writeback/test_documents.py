from __future__ import annotations

from dataclasses import replace

from modelguard.config import SCORE_PROVENANCE, SCORING_VERSION
from modelguard.models import FindingType
from modelguard.writeback.documents import (
    RUN_ID_PROPERTY,
    _document_id,
    publish_impact_report,
    render_impact_report,
    render_trust_trend,
    render_trust_waterfall,
)
from modelguard.writeback.trust_history import TrustEntry
from tests.conftest import MODEL_URN as MODEL
from tests.conftest import TABLE_URN, FakeClient, FakeGraph, make_connection, make_trust_score
from tests.conftest import make_deprecated_input_finding as _deprecated_finding
from tests.conftest import make_finding as _finding
from tests.conftest import make_leakage_finding as _leakage_finding
from tests.conftest import make_schema_drift_finding as _drift_finding
from tests.conftest import make_sensitive_source_finding as _sensitive_finding


def test_the_document_id_is_derived_from_the_model_and_the_resource_so_reruns_converge():
    """Same model, same type, same resource: the same id, so a rerun converges."""
    first = _document_id(MODEL, FindingType.UPSTREAM_FRESHNESS, TABLE_URN)
    assert first == _document_id(MODEL, FindingType.UPSTREAM_FRESHNESS, TABLE_URN)


def test_two_findings_on_the_same_model_get_two_different_document_ids():
    """A leaking column and a stale table on one model must not collide.

    Without the resource_urn in the id, the second publish_impact_report call in
    a scan would silently overwrite the first finding's report.
    """
    leakage = _leakage_finding()
    freshness_id = _document_id(MODEL, FindingType.UPSTREAM_FRESHNESS, TABLE_URN)
    leakage_id = _document_id(MODEL, leakage.finding_type, leakage.resource_urn)
    assert freshness_id != leakage_id


def test_two_detectors_naming_the_same_resource_get_two_different_document_ids():
    """A table that is both stale and drifted must yield two reports, not one.

    The resource URN alone does not separate the detectors: a freshness finding's
    resource is the failing table, and a drift finding's resource is the drifted
    input dataset, and those are the same URN when the table a model trains on is
    the one that went stale. Keying on the resource alone let the second report
    overwrite the first.
    """
    drift = replace(_drift_finding(), dataset_urn=TABLE_URN)
    assert drift.resource_urn == _finding().resource_urn

    freshness_id = _document_id(MODEL, FindingType.UPSTREAM_FRESHNESS, TABLE_URN)
    drift_id = _document_id(MODEL, drift.finding_type, drift.resource_urn)
    assert freshness_id != drift_id


def test_a_stale_and_drifted_table_publishes_two_reports_not_one():
    """The collision, reproduced through the public write path."""
    client = FakeClient()
    conn = make_connection(FakeGraph(), client)
    drift = replace(_drift_finding(), dataset_urn=TABLE_URN)

    freshness_write = publish_impact_report(
        conn, model_urn=MODEL, finding=_finding(), narrative="prose", run_id="scan-abc"
    )
    drift_write = publish_impact_report(
        conn, model_urn=MODEL, finding=drift, narrative="prose", run_id="scan-abc"
    )

    assert freshness_write.urn != drift_write.urn
    assert len(client.entities.upserted) == 2


def test_the_report_states_the_measured_numbers_and_the_model_at_risk():
    markdown = render_impact_report(_finding(), "assessment prose", "scan-abc")

    assert "30.0 hours ago" in markdown
    assert "6.0 hours" in markdown
    assert "Credit Risk v3" in markdown
    assert "scan-abc" in markdown


def test_the_drift_report_lists_the_changed_columns_and_cites_breck():
    markdown = render_impact_report(_drift_finding(live=True), "assessment prose", "scan-xyz")

    assert "ecommerce.public.customer_features" in markdown
    assert "applicant_income: NUMBER -> VARCHAR" in markdown
    assert "Breck" in markdown
    assert "scan-xyz" in markdown
    # The narrative is quoted, not treated as fact.
    assert "assessment prose" in markdown


def test_every_finding_type_can_be_reported_on():
    """The gap a live scan fell into: two detectors shipped with no report at all.

    `report_subject` and `_report_body` are singledispatch tables, and D-079
    registered the two governance findings in narrate.py's four tables and not in
    these two. Nothing offline noticed, because no unit test rendered a report
    for them; the first sensitive-source scan against a real graph raised
    NotImplementedError after the incident was already written. One test over
    every concrete finding type, so the next detector cannot land half-wired.
    """
    for finding in (
        _finding(),
        _leakage_finding(),
        _drift_finding(),
        _sensitive_finding(),
        _deprecated_finding(),
    ):
        markdown = render_impact_report(finding, "assessment prose", "scan-abc")
        assert "Model Impact Report" in markdown
        assert "assessment prose" in markdown


def test_the_sensitive_source_report_names_the_classification_and_the_path():
    markdown = render_impact_report(_sensitive_finding(), "prose", "scan-abc")

    assert "modelguard.sensitive" in markdown
    assert "ecommerce.public.loans_raw.income" in markdown
    assert "applicant_income <- income" in markdown
    # It is an exposure, not an outage, and the report has to say so.
    assert "Nothing is broken" in markdown


def test_the_deprecated_input_report_quotes_the_owners_note_and_the_deadline():
    markdown = render_impact_report(_deprecated_finding(), "prose", "scan-abc")

    assert "Replaced by loans_v2 on 2026-09-01." in markdown
    assert "1800000000000" in markdown
    assert "ecommerce.public.customer_features" in markdown


def test_a_deprecation_with_no_note_renders_without_an_empty_quote():
    markdown = render_impact_report(_deprecated_finding(note=""), "prose", "scan-abc")

    assert "The owners left this note" not in markdown


def test_the_report_quotes_the_narrative_without_letting_it_supply_facts():
    markdown = render_impact_report(_finding(), "TOTALLY MADE UP PROSE", "scan-abc")
    assert "TOTALLY MADE UP PROSE" in markdown
    # The numbers come from the finding, not from the prose.
    assert "Models reached: 1" in markdown


def test_the_report_flags_an_unowned_model():
    markdown = render_impact_report(_finding(), "prose", "scan-abc")
    assert "Unowned" in markdown


def test_the_report_discloses_the_cloud_boundary_and_the_freshness_source():
    markdown = render_impact_report(_finding(), "prose", "scan-abc")
    assert "DataHub Cloud" in markdown
    assert "did not query the warehouse" in markdown


def test_a_report_with_no_model_at_risk_says_so_rather_than_rendering_an_empty_list():
    markdown = render_impact_report(_finding(with_model=False), "prose", "scan-abc")
    assert "No model consumes this table" in markdown


def test_publishing_upserts_a_document_linked_to_the_model():
    client = FakeClient()
    write = publish_impact_report(
        make_connection(FakeGraph(), client),
        model_urn=MODEL,
        finding=_finding(),
        narrative="prose",
        run_id="scan-abc",
    )

    expected = _document_id(MODEL, FindingType.UPSTREAM_FRESHNESS, TABLE_URN)
    assert write.urn == f"urn:li:document:{expected}"
    assert len(client.entities.upserted) == 1

    document = client.entities.upserted[0]
    # related_assets is what makes the report reachable from the model's page.
    assert MODEL in [str(urn) for urn in document.related_assets]
    assert document.custom_properties[RUN_ID_PROPERTY] == "scan-abc"


def test_two_findings_on_one_model_publish_two_reports_not_one_overwriting_the_other():
    """Reproduces the collision directly.

    A model with both a freshness and a leakage finding in the same scan must
    end up with two documents, not one.
    """
    client = FakeClient()
    conn = make_connection(FakeGraph(), client)

    freshness_write = publish_impact_report(
        conn, model_urn=MODEL, finding=_finding(), narrative="prose", run_id="scan-abc"
    )
    leakage_write = publish_impact_report(
        conn,
        model_urn=MODEL,
        finding=_leakage_finding(),
        narrative="prose",
        run_id="scan-abc",
    )

    assert freshness_write.urn != leakage_write.urn
    assert len(client.entities.upserted) == 2


def test_the_published_body_is_the_rendered_report():
    client = FakeClient()
    write = publish_impact_report(
        make_connection(FakeGraph(), client),
        model_urn=MODEL,
        finding=_finding(),
        narrative="prose",
        run_id="scan-abc",
    )
    assert write.markdown == client.entities.upserted[0].text


# --------------------------------------------------------------------------
# The trust trend section (D-081)
# --------------------------------------------------------------------------


def make_leakage_finding(**kwargs):  # noqa: ANN003 - alias for the shared fixture
    return _leakage_finding(**kwargs)


def _entry(
    recorded_at: str,
    score: int,
    *,
    deductions: tuple[str, ...] = (),
    scoring_version: str = str(SCORING_VERSION),
) -> TrustEntry:
    return TrustEntry(
        recorded_at=recorded_at,
        run_id=f"scan-{score}",
        score=score,
        band="healthy" if score >= 70 else "at-risk",
        deductions=deductions,
        scoring_version=scoring_version,
    )


def test_one_scan_renders_no_trend_because_one_point_is_not_a_trend():
    """A one-row table invites a reader to draw a line through a single point."""
    assert render_trust_trend([_entry("2026-08-02T09:00:00Z", 82)]) == ""


def test_two_scans_render_the_direction_and_the_deductions_that_moved_it():
    trend = render_trust_trend(
        [
            _entry("2026-08-01T09:00:00Z", 95),
            _entry("2026-08-02T09:00:00Z", 64, deductions=("leakage",)),
        ]
    )

    assert "## Trust over time" in trend
    assert "95/100" in trend
    assert "64/100" in trend
    assert "leakage" in trend
    assert "down by 31 points" in trend


def test_a_recovering_model_reads_as_up_not_merely_as_changed():
    trend = render_trust_trend(
        [_entry("2026-08-01T09:00:00Z", 40), _entry("2026-08-02T09:00:00Z", 90)]
    )

    assert "up by 50 points" in trend


def test_an_unchanged_score_says_so_rather_than_reporting_a_zero_move():
    trend = render_trust_trend(
        [_entry("2026-08-01T09:00:00Z", 82), _entry("2026-08-02T09:00:00Z", 82)]
    )

    assert "unchanged." in trend
    assert "0 points" not in trend


def test_the_impact_report_carries_the_trend_when_there_is_one():
    finding = make_leakage_finding()
    history = [_entry("2026-08-01T09:00:00Z", 95), _entry("2026-08-02T09:00:00Z", 64)]

    with_history = render_impact_report(finding, "assessment", "scan-1", history)
    without = render_impact_report(finding, "assessment", "scan-1")

    assert "## Trust over time" in with_history
    assert "## Trust over time" not in without
    # The finding's own sections are unchanged either way.
    assert "## Assessment" in with_history
    assert "## Assessment" in without


# --------------------------------------------------------------------------
# The trust waterfall, and the version step that is not a regression (T-01)
# --------------------------------------------------------------------------


def test_the_waterfall_section_leads_with_what_the_model_lost_trust_for():
    section = render_trust_waterfall(make_trust_score(70, deductions={"leakage": 20.0}))

    assert "## Trust score" in section
    # The deduction is above the total in the rendered text, not merely present.
    assert section.index("-20  leakage") < section.index("70  healthy")
    assert SCORE_PROVENANCE in section


def test_a_model_with_no_deductions_renders_no_waterfall():
    """A waterfall with no steps is the number again with extra ceremony."""
    assert render_trust_waterfall(make_trust_score(100, deductions={})) == ""


def test_the_impact_report_carries_the_waterfall_when_a_score_is_given():
    finding = make_leakage_finding()
    score = make_trust_score(55, deductions={"leakage": 20.0, "missing_owner": 10.0})

    with_score = render_impact_report(finding, "assessment", "scan-1", (), score)
    without = render_impact_report(finding, "assessment", "scan-1")

    assert "## Trust score" in with_score
    assert "## Trust score" not in without
    assert "## Assessment" in without


def test_a_scoring_version_change_inside_the_window_is_called_out():
    """Otherwise a release that added a detector reads exactly like a regression."""
    trend = render_trust_trend(
        [
            _entry("2026-08-01T09:00:00Z", 95, scoring_version="1"),
            _entry("2026-08-02T09:00:00Z", 64, scoring_version="2"),
        ]
    )

    assert "The scoring function changed inside this window" in trend
    assert "not a regression" in trend


def test_one_scoring_version_throughout_says_nothing_about_versions():
    trend = render_trust_trend(
        [_entry("2026-08-01T09:00:00Z", 95), _entry("2026-08-02T09:00:00Z", 64)]
    )

    assert "The scoring function changed" not in trend
