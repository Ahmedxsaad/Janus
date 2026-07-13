from __future__ import annotations

from modelguard.writeback.documents import (
    RUN_ID_PROPERTY,
    _document_id,
    publish_impact_report,
    render_impact_report,
)
from tests.conftest import MODEL_URN as MODEL
from tests.conftest import FakeClient, FakeGraph, make_connection
from tests.conftest import make_finding as _finding


def test_the_document_id_is_derived_from_the_model_so_reruns_update_one_report():
    assert _document_id(MODEL) == "modelguard-impact-credit_risk_v3"


def test_the_report_states_the_measured_numbers_and_the_model_at_risk():
    markdown = render_impact_report(_finding(), "assessment prose", "scan-abc")

    assert "30.0 hours ago" in markdown
    assert "6.0 hours" in markdown
    assert "Credit Risk v3" in markdown
    assert "scan-abc" in markdown


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

    assert write.urn == "urn:li:document:modelguard-impact-credit_risk_v3"
    assert len(client.entities.upserted) == 1

    document = client.entities.upserted[0]
    # related_assets is what makes the report reachable from the model's page.
    assert MODEL in [str(urn) for urn in document.related_assets]
    assert document.custom_properties[RUN_ID_PROPERTY] == "scan-abc"


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
