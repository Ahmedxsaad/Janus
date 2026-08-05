"""The degraded mode, against fixture graphs. Offline: no DataHub.

Two obligations, and the second is the one that keeps this honest.

It has to *say something* about a model nobody has linked, which is the state
every real catalog starts in: the tables it trains on are readable even when its
features are not, so a stale or deprecated or classified one is worth reporting.

And it has to *stop talking* the moment a column-level answer exists. A mode that
ran unconditionally would put a maybe next to every proof, and a reader who
cannot tell which is which has been handed the false-positive rate of the weaker
one across the whole report.
"""

from __future__ import annotations

from typing import Any

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DeploymentStatusClass,
    DeprecationClass,
    GlobalTagsClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    SchemaMetadataClass,
    TagAssociationClass,
)
from datahub.metadata.urns import SchemaFieldUrn

from janus.config import TABLE_LEVEL_PRECISION, ScanConfig
from janus.detect.degraded import table_level_findings
from janus.detect.leakage import SOURCE_COLUMN_PROPERTY
from janus.detect.trust_score import trust_inputs_from_findings, trust_score
from janus.models import FindingType, Severity, TableRisk
from tests.conftest import (
    CLEAN_FEATURE_URN,
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    MODEL_URN,
    TRAINING_RUN_URN,
    FakeClient,
    FakeGraph,
    make_connection,
    make_table_level_finding,
    schema_metadata,
)

SENSITIVE_TAG = "urn:li:tag:janus.sensitive"
SENSITIVE_TERM = "urn:li:glossaryTerm:Sensitive"
CONFIG = ScanConfig()
CLASSIFIED_CONFIG = ScanConfig(sensitive_tag_urns=(SENSITIVE_TAG,))

NOW_MS = 1_800_000_000_000
_HOUR_MS = 3_600_000


class _CountingGraph(FakeGraph):
    """A FakeGraph that counts the schema reads issued against it.

    The only way to tell "found nothing" from "never looked", which is the
    distinction this module's unconfigured case turns on.
    """

    def __init__(self, wrapped: FakeGraph) -> None:
        super().__init__()
        self.__dict__.update(wrapped.__dict__)
        self.schema_reads = 0

    def get_aspect(self, entity_urn: str, aspect_type: type, version: int = 0) -> Any:
        if aspect_type is SchemaMetadataClass:
            self.schema_reads += 1
        return super().get_aspect(entity_urn, aspect_type, version)


def _graph(
    *,
    linked: bool = False,
    deprecated: bool = False,
    withdrawn: bool = False,
    lag_hours: float | None = None,
    classified: str | None = None,
    live: bool = True,
) -> FakeGraph:
    """A model with a training run that read the feature table, and nothing else.

    ``linked`` is the switch the whole module is about: with it, the model
    declares a feature carrying a source column, which is exactly the condition
    the column-level detectors need and this mode refuses to run under.
    """
    aspects: dict[tuple[str, type], object] = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            name="Credit Risk v3",
            mlFeatures=[CLEAN_FEATURE_URN] if linked else [],
            deployments=[DEPLOYMENT_URN],
            trainingJobs=[TRAINING_RUN_URN],
        ),
        (TRAINING_RUN_URN, DataProcessInstanceInputClass): DataProcessInstanceInputClass(
            inputs=[FEATURE_TABLE_URN]
        ),
    }
    if linked:
        aspects[(CLEAN_FEATURE_URN, MLFeaturePropertiesClass)] = MLFeaturePropertiesClass(
            sources=[FEATURE_TABLE_URN],
            customProperties={
                SOURCE_COLUMN_PROPERTY: str(SchemaFieldUrn(FEATURE_TABLE_URN, "applicant_income"))
            },
        )
    if live:
        aspects[(DEPLOYMENT_URN, MLModelDeploymentPropertiesClass)] = (
            MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
        )
    if deprecated or withdrawn:
        aspects[(FEATURE_TABLE_URN, DeprecationClass)] = DeprecationClass(
            deprecated=deprecated,
            note="Superseded by the v2 feature pipeline." if deprecated else "",
            actor="urn:li:corpuser:datahub",
        )
    if classified is not None:
        aspects[(FEATURE_TABLE_URN, SchemaMetadataClass)] = schema_metadata(
            {"applicant_income": "NUMBER", classified: "VARCHAR"}
        )
        aspects[(str(SchemaFieldUrn(FEATURE_TABLE_URN, classified)), GlobalTagsClass)] = (
            GlobalTagsClass(tags=[TagAssociationClass(tag=SENSITIVE_TAG)])
        )

    graph = FakeGraph(aspects=aspects)  # type: ignore[arg-type]
    if lag_hours is not None:
        graph._timeseries[(FEATURE_TABLE_URN, OperationClass)] = OperationClass(
            timestampMillis=NOW_MS,
            operationType=OperationTypeClass.UPDATE,
            lastUpdatedTimestamp=NOW_MS - int(lag_hours * _HOUR_MS),
        )
    return graph


def _findings(graph: FakeGraph, config: ScanConfig = CONFIG) -> tuple[Any, ...]:
    conn = make_connection(graph, FakeClient())
    return table_level_findings(conn, MODEL_URN, config, now_ms=NOW_MS)


class TestWhenItSpeaks:
    def test_a_deprecated_training_table_is_reported_for_an_unlinked_model(self):
        findings = _findings(_graph(deprecated=True))

        assert [f.risk for f in findings] == [TableRisk.DEPRECATED]
        assert findings[0].resource_urn == FEATURE_TABLE_URN
        assert findings[0].finding_type is FindingType.TABLE_LEVEL_RISK
        assert findings[0].evidence["deprecation_note"] == "Superseded by the v2 feature pipeline."

    def test_a_stale_training_table_is_reported_with_its_measured_lag(self):
        findings = _findings(_graph(lag_hours=30.0))

        assert [f.risk for f in findings] == [TableRisk.STALE]
        # The lag is measured against the SLA in force, not asserted as a constant.
        assert float(findings[0].evidence["lag_hours"]) > CONFIG.freshness_sla_hours

    def test_a_table_inside_its_sla_is_not_reported(self):
        assert _findings(_graph(lag_hours=CONFIG.freshness_sla_hours - 1)) == ()

    def test_a_classified_column_in_the_training_table_is_reported_by_name(self):
        findings = _findings(_graph(classified="ssn"), CLASSIFIED_CONFIG)

        assert [f.risk for f in findings] == [TableRisk.CLASSIFIED]
        assert findings[0].evidence["classified_columns"] == "ssn"

    def test_a_term_classification_is_honored_as_well_as_a_tag(self):
        """Catalogs classify through either surface; many use only one.

        A term-only config, checked against a column carrying no tag at all:
        the tag this fixture also happens to write is not what this test is
        proving, so the assertion has to reach past it and depend on the term.
        """
        graph = _graph(classified="ssn")
        column_urn = str(SchemaFieldUrn(FEATURE_TABLE_URN, "ssn"))
        graph.set_aspect(
            column_urn,
            GlossaryTermsClass(
                terms=[GlossaryTermAssociationClass(urn=SENSITIVE_TERM)], auditStamp=None
            ),
        )
        graph.set_aspect(column_urn, GlobalTagsClass(tags=[]))
        term_only_config = ScanConfig(sensitive_term_urns=(SENSITIVE_TERM,))

        findings = _findings(graph, term_only_config)

        assert [f.risk for f in findings] == [TableRisk.CLASSIFIED]
        assert findings[0].evidence["classified_columns"] == "ssn"

    def test_an_unconfigured_classification_never_looks_at_the_schema(self):
        """Silence here has to come from "nothing to look for", not from looking.

        Asserted on the read that was issued rather than on the empty result,
        because an index with nothing in it answers "not classified" for every
        column: the result is identical either way and would test nothing
        (the same reasoning as D-077).
        """
        graph = _CountingGraph(_graph(classified="ssn"))

        assert _findings(graph) == ()
        assert graph.schema_reads == 0

        configured = _CountingGraph(_graph(classified="ssn"))
        assert _findings(configured, CLASSIFIED_CONFIG) != ()
        assert configured.schema_reads == 1

    def test_a_withdrawn_deprecation_is_not_a_deprecation(self):
        """``deprecated=false`` is how DataHub records a withdrawal (D-079).

        A detector reading the aspect's presence rather than its value would
        report every table anybody has ever un-deprecated.
        """
        assert _findings(_graph(withdrawn=True)) == ()

    def test_two_risks_on_one_table_are_two_findings(self):
        """Different incident types and different remedies, so not one merged row."""
        findings = _findings(_graph(deprecated=True, lag_hours=30.0))

        assert {f.risk for f in findings} == {TableRisk.STALE, TableRisk.DEPRECATED}
        assert len({f.title for f in findings}) == 2


class TestWhenItStaysQuiet:
    def test_a_linked_model_gets_nothing_even_though_the_table_is_deprecated(self):
        """The mode's whole gate. The column-level detector answers this graph."""
        assert _findings(_graph(linked=True, deprecated=True)) == ()

    def test_a_model_whose_features_reach_no_source_column_is_still_degraded(self):
        """Features alone are not a link: the column-level walk needs a column.

        A model in this state looks linked and is not, and reporting nothing about
        it would be the silence this mode exists to replace.
        """
        graph = _graph(linked=True, deprecated=True)
        graph.set_aspect(CLEAN_FEATURE_URN, MLFeaturePropertiesClass(sources=[FEATURE_TABLE_URN]))

        assert [f.risk for f in _findings(graph)] == [TableRisk.DEPRECATED]

    def test_a_model_with_no_declared_training_table_gets_nothing(self):
        graph = _graph(deprecated=True)
        graph.set_aspect(TRAINING_RUN_URN, DataProcessInstanceInputClass(inputs=[]))

        assert _findings(graph) == ()


class TestHowItIsRanked:
    def test_severity_never_reaches_the_column_level_range(self):
        live = _findings(_graph(deprecated=True))[0]
        assert live.severity is Severity.MEDIUM

        idle = _findings(_graph(deprecated=True, live=False))[0]
        assert idle.severity is Severity.LOW

    def test_the_finding_states_the_mode_and_its_measured_precision(self):
        finding = _findings(_graph(deprecated=True))[0]

        assert finding.evidence["mode"] == "table-level"
        assert finding.precision == TABLE_LEVEL_PRECISION
        assert f"{TABLE_LEVEL_PRECISION:.2f}" in finding.mode_note
        assert "table level only" in finding.mode_note

    def test_each_risk_states_its_own_limit_rather_than_one_blanket_caveat(self):
        """A deprecation is exact at table level; a stale table's reach is not.

        Both are reported by this mode, and saying the same thing about them
        would be wrong twice: understating the deadline, overstating the rest.
        """
        stale = _findings(_graph(lag_hours=30.0))[0]
        deprecated = _findings(_graph(deprecated=True))[0]

        assert "not knowable" in stale.limitation
        assert "exact at table level" in deprecated.limitation

    def test_declaring_the_link_is_the_first_remedy_offered(self):
        finding = _findings(_graph(deprecated=True))[0]

        assert "janus link" in finding.counterfactual.remedies[0].summary

    def test_it_contributes_nothing_at_all_to_the_trust_score(self):
        """A maybe must not move a number people compare release over release.

        Asserted on the inputs and not only on the total: this finding's severity
        is below the band cap, so a version that *did* roll it in would score the
        same today and start capping bands the moment somebody raised the
        ceiling. The invariant is that it never reaches the rollup.
        """
        finding = make_table_level_finding()
        model = finding.models_at_risk[0]

        inputs = trust_inputs_from_findings([finding], model)
        assert inputs.worst_severity is None

        scored = trust_score(inputs, CONFIG)
        unscored = trust_score(trust_inputs_from_findings([], model), CONFIG)
        assert scored.value == unscored.value
