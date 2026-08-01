from __future__ import annotations

from dataclasses import replace

from modelguard.models import FindingType
from modelguard.writeback.documents import (
    RUN_ID_PROPERTY,
    _document_id,
    publish_impact_report,
    render_impact_report,
)
from tests.conftest import MODEL_URN as MODEL
from tests.conftest import TABLE_URN, FakeClient, FakeGraph, make_connection
from tests.conftest import make_finding as _finding
from tests.conftest import make_leakage_finding as _leakage_finding
from tests.conftest import make_schema_drift_finding as _drift_finding


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
