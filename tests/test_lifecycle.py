"""How long Janus's findings stay open. Offline: no DataHub.

Two obligations. The classification has to agree with the titles models.py
actually writes, or an MTTR table silently drops a whole detector. And an
incident somebody else opened on the same column must not be counted, or the
number stops being about this tool.
"""

from __future__ import annotations

import pytest
from datahub.metadata.schema_classes import (
    AuditStampClass,
    DataProcessInstanceInputClass,
    IncidentInfoClass,
    IncidentStateClass,
    IncidentStatusClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
)

from janus.config import ScanConfig
from janus.detect.leakage import SOURCE_COLUMN_PROPERTY
from janus.lifecycle import (
    TITLE_PREFIXES,
    IncidentLifecycle,
    model_resources,
    mttr_by_type,
    read_lifecycles,
)
from janus.models import FindingType
from tests.conftest import (
    FEATURE_TABLE_URN,
    LEAK_COLUMN_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    lineage_result,
    make_connection,
    one_of_every_finding,
)

CONFIG = ScanConfig()
HOUR_MS = 3_600_000
OPENED = 1_800_000_000_000

INCIDENT = "urn:li:incident:abc"
OTHER_INCIDENT = "urn:li:incident:def"

FOOTER = "\n\nRaised by Janus run scan-abc123."


def place_incident(
    graph: FakeGraph,
    *,
    resource_urn: str = LEAK_COLUMN_URN,
    incident_urn: str = INCIDENT,
    title: str = "Target leakage: prior_default_flag derives from label default_status",
    description: str = "body" + FOOTER,
    opened_ms: int = OPENED,
    resolved_ms: int | None = None,
) -> None:
    """Put one incident on the fake graph, the way a live GMS would serve it."""
    state = IncidentStateClass.RESOLVED if resolved_ms is not None else IncidentStateClass.ACTIVE
    graph._related.setdefault(resource_urn, []).append(incident_urn)
    graph.set_aspect(
        incident_urn,
        IncidentInfoClass(
            type="FIELD",
            entities=[resource_urn],
            title=title,
            description=description,
            status=IncidentStatusClass(
                state=state,
                lastUpdated=AuditStampClass(
                    time=resolved_ms if resolved_ms is not None else opened_ms,
                    actor="urn:li:corpuser:datahub",
                ),
            ),
            created=AuditStampClass(time=opened_ms, actor="urn:li:corpuser:datahub"),
        ),
    )


def linked_model_graph() -> FakeGraph:
    """A model whose declared feature reaches the column incidents land on."""
    graph = FakeGraph()
    graph.set_aspect(
        MODEL_URN,
        MLModelPropertiesClass(mlFeatures=["urn:li:mlFeature:(credit_risk,prior_default_flag)"]),
    )
    graph.set_aspect(
        "urn:li:mlFeature:(credit_risk,prior_default_flag)",
        _feature_properties(LEAK_COLUMN_URN),
    )
    return graph


def _feature_properties(source_column_urn: str) -> MLFeaturePropertiesClass:
    """An mlFeature carrying the source-column property leakage reads."""
    return MLFeaturePropertiesClass(customProperties={SOURCE_COLUMN_PROPERTY: source_column_urn})


@pytest.mark.parametrize("finding", one_of_every_finding(), ids=lambda f: type(f).__name__)
def test_every_findings_title_starts_with_the_prefix_it_is_registered_under(finding):
    """The registry is derived from models.py, so it may not drift from it.

    An incident already in the graph is classified by its title, and a detector
    whose title stopped matching would contribute nothing to the MTTR table
    while looking like a detector that has never fired.
    """
    prefix = TITLE_PREFIXES[finding.finding_type]

    assert finding.title.startswith(prefix)


def test_a_prefix_exists_for_every_finding_type():
    """A seventh detector cannot be added without appearing in the table."""
    assert set(TITLE_PREFIXES) == set(FindingType)


def test_an_incident_nobody_from_janus_raised_is_not_counted():
    """Somebody else's incident on the same column is excluded by a fact.

    The run footer is the marker, not the title: a human writing an incident
    about a leak could easily use the same words, and counting it would put
    somebody else's response time in Janus's own number.
    """
    graph = linked_model_graph()
    place_incident(graph, description="A human raised this one, no footer.")

    assert read_lifecycles(make_connection(graph, FakeClient()), CONFIG, [MODEL_URN]) == ()


def test_an_incident_with_an_unrecognised_title_is_not_counted():
    """A Janus footer with a title no detector writes says nothing usable."""
    graph = linked_model_graph()
    place_incident(graph, title="Something else entirely")

    assert read_lifecycles(make_connection(graph, FakeClient()), CONFIG, [MODEL_URN]) == ()


def test_an_open_incident_has_no_duration_rather_than_a_zero_one():
    """Still open is a different fact from resolved instantly."""
    graph = linked_model_graph()
    place_incident(graph)

    lifecycles = read_lifecycles(make_connection(graph, FakeClient()), CONFIG, [MODEL_URN])

    assert len(lifecycles) == 1
    assert lifecycles[0].resolved is False
    assert lifecycles[0].duration_ms is None
    assert lifecycles[0].run_id == "scan-abc123"


def test_a_resolved_incident_carries_the_gap_between_the_two_stamps():
    graph = linked_model_graph()
    place_incident(graph, opened_ms=OPENED, resolved_ms=OPENED + 3 * HOUR_MS)

    lifecycles = read_lifecycles(make_connection(graph, FakeClient()), CONFIG, [MODEL_URN])

    assert lifecycles[0].duration_ms == 3 * HOUR_MS


def test_a_resolution_stamped_before_the_creation_is_dropped_not_clamped():
    """A clamp to zero would pull a mean down with a number nothing measured."""
    graph = linked_model_graph()
    place_incident(graph, opened_ms=OPENED, resolved_ms=OPENED - HOUR_MS)

    lifecycles = read_lifecycles(make_connection(graph, FakeClient()), CONFIG, [MODEL_URN])

    assert lifecycles[0].resolved is True
    assert lifecycles[0].duration_ms is None
    rows = {row.finding_type: row for row in mttr_by_type(lifecycles)}
    leakage = rows[FindingType.TARGET_LEAKAGE]
    assert leakage.raised == 1
    assert leakage.resolved == 0
    assert leakage.mean_ms is None


def test_one_incident_reachable_from_two_resources_is_counted_once():
    """A shared incident must not pull the mean toward whatever it is attached to.

    ``incidentInfo.entities`` is a list, so one incident is reachable inbound
    from every entity it names, and a model's resource set routinely holds more
    than one of them: the column and the dataset it lives in. Counted twice, a
    single fast resolution would halve the mean of every detector it touches.
    """
    graph = linked_model_graph()
    graph.set_aspect(
        MODEL_URN,
        MLModelPropertiesClass(
            mlFeatures=["urn:li:mlFeature:(credit_risk,prior_default_flag)"],
            trainingJobs=["urn:li:dataProcessInstance:r"],
        ),
    )
    graph.set_aspect(
        "urn:li:dataProcessInstance:r",
        DataProcessInstanceInputClass(inputs=[FEATURE_TABLE_URN]),
    )
    place_incident(graph, opened_ms=OPENED, resolved_ms=OPENED + HOUR_MS)
    # The same incident, reachable from the dataset as well as from the column.
    graph._related.setdefault(FEATURE_TABLE_URN, []).append(INCIDENT)

    resources = model_resources(make_connection(graph, FakeClient()), CONFIG, MODEL_URN)
    assert {LEAK_COLUMN_URN, FEATURE_TABLE_URN} <= set(resources)

    lifecycles = read_lifecycles(make_connection(graph, FakeClient()), CONFIG, [MODEL_URN])

    assert len(lifecycles) == 1


def test_a_freshness_incident_upstream_of_the_model_is_reached():
    """The walk that made the freshness row stop reading zero on a graph full of them.

    A stale-table incident lands on the table that stopped refreshing, which is
    never the model's own input but something behind it. Without the upstream
    walk the whole detector reports as never having fired.
    """
    graph = linked_model_graph()
    graph.set_aspect(
        MODEL_URN, MLModelPropertiesClass(trainingJobs=["urn:li:dataProcessInstance:r"])
    )
    graph.set_aspect(
        "urn:li:dataProcessInstance:r",
        DataProcessInstanceInputClass(inputs=[FEATURE_TABLE_URN]),
    )
    client = FakeClient(lineage_results=[lineage_result(TABLE_URN, hops=1, direction="upstream")])
    place_incident(
        graph,
        resource_urn=TABLE_URN,
        title="Stale upstream data in ecommerce.public.loans_raw",
        opened_ms=OPENED,
        resolved_ms=OPENED + 2 * HOUR_MS,
    )

    lifecycles = read_lifecycles(make_connection(graph, client), CONFIG, [MODEL_URN])

    assert [item.finding_type for item in lifecycles] == [FindingType.UPSTREAM_FRESHNESS]
    assert lifecycles[0].duration_ms == 2 * HOUR_MS


def test_an_upstream_dataset_beyond_the_hop_cap_is_not_reached():
    """The cap is honored here for the reason docs/04-detectors.mdgives.

    Above two hops DataHub returns entities past max_hops, so a walk that
    trusted the server would sweep incidents on tables this model has no
    relationship with and count them as its own.
    """
    graph = linked_model_graph()
    graph.set_aspect(
        MODEL_URN, MLModelPropertiesClass(trainingJobs=["urn:li:dataProcessInstance:r"])
    )
    graph.set_aspect(
        "urn:li:dataProcessInstance:r",
        DataProcessInstanceInputClass(inputs=[FEATURE_TABLE_URN]),
    )
    client = FakeClient(
        lineage_results=[lineage_result(TABLE_URN, hops=CONFIG.max_hops + 1, direction="upstream")]
    )

    assert TABLE_URN not in model_resources(make_connection(graph, client), CONFIG, MODEL_URN)


def test_the_rollup_reports_a_row_for_a_detector_that_has_never_fired():
    """A missing row reads as a detector nobody built; a zero row is the fact."""
    rows = mttr_by_type([])

    assert {row.finding_type for row in rows} == set(FindingType)
    assert all(row.raised == 0 and row.mean_ms is None for row in rows)


def test_the_median_is_reported_beside_the_mean():
    """One stale incident must not be the only number a reader sees.

    Nine leaks closed in an hour and one left open for a month average to three
    days, which describes none of them.
    """
    lifecycles = [
        IncidentLifecycle(
            incident_urn=f"urn:li:incident:{index}",
            resource_urn=LEAK_COLUMN_URN,
            finding_type=FindingType.TARGET_LEAKAGE,
            run_id="scan-abc",
            opened_ms=OPENED,
            resolved_ms=OPENED + duration,
        )
        for index, duration in enumerate([HOUR_MS] * 9 + [720 * HOUR_MS])
    ]

    row = next(r for r in mttr_by_type(lifecycles) if r.finding_type == FindingType.TARGET_LEAKAGE)

    assert row.median_hours == 1.0
    assert row.mean_hours > 70.0
    assert "median 1.0h over 10" in row.describe()
