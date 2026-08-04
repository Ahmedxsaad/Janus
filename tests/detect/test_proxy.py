"""T-11: proxy-attribute candidates. Offline: no DataHub, no network.

The shape under test is a fork rather than a chain, and the tests are mostly
about the three ways it can be mistaken for something else:

* a feature that *descends from* the protected attribute is P5's finding, and
  reporting it here too would raise two incidents about one column;
* a feature sharing an ancestor with an unrelated, unclassified column is not a
  candidate at all;
* a shared ancestor past the hop cap is not a shared ancestor for this purpose,
  because everything in a warehouse shares a raw table eventually.

The lineage fake answers per column (``lineage_by_column``), the way a real GMS
does, so a fixture cannot hand one column's cone to another and manufacture a
fork that is not there.
"""

from __future__ import annotations

from dataclasses import replace

from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    GlobalTagsClass,
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    TagAssociationClass,
)

from modelguard.config import ScanConfig
from modelguard.detect.governance import proxy_candidate_findings
from modelguard.detect.leakage import SOURCE_COLUMN_PROPERTY
from modelguard.models import FindingType, ProxyCandidateFinding, Severity
from tests.conftest import (
    DEPLOYMENT_URN,
    FEATURE_TABLE_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
    make_connection,
)

PROTECTED_TAG = "urn:li:tag:modelguard.protected"
CONFIG = ScanConfig(protected_attribute_tag_urns=(PROTECTED_TAG,))

#: The fork. `applicant_income` (the model's feature) and `ethnicity_band` (the
#: protected attribute) both descend from `income`; neither descends from the
#: other.
FEATURE_COLUMN = f"urn:li:schemaField:({FEATURE_TABLE_URN},applicant_income)"
ANCESTOR_COLUMN = f"urn:li:schemaField:({TABLE_URN},income)"
PROTECTED_COLUMN = f"urn:li:schemaField:({FEATURE_TABLE_URN},ethnicity_band)"
UNRELATED_COLUMN = f"urn:li:schemaField:({FEATURE_TABLE_URN},updated_at)"


def _tags(*urns: str) -> GlobalTagsClass:
    return GlobalTagsClass(tags=[TagAssociationClass(tag=urn) for urn in urns])


def _graph(*, protected: str | None = PROTECTED_COLUMN) -> FakeGraph:
    """A model declaring one feature computed from `applicant_income`."""
    aspects: dict = {
        (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
            mlFeatures=[LEAK_FEATURE_URN], deployments=[DEPLOYMENT_URN]
        ),
        (LEAK_FEATURE_URN, MLFeaturePropertiesClass): MLFeaturePropertiesClass(
            customProperties={SOURCE_COLUMN_PROPERTY: FEATURE_COLUMN}
        ),
        (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): MLModelDeploymentPropertiesClass(
            status=DeploymentStatusClass.IN_SERVICE
        ),
    }
    if protected is not None:
        aspects[(protected, GlobalTagsClass)] = _tags(PROTECTED_TAG)
    return FakeGraph(aspects=aspects)  # type: ignore[arg-type]


def _client(
    *,
    feature_upstream: list | None = None,
    ancestor_downstream: list | None = None,
) -> FakeClient:
    """Wire the fork: the feature's cone up, and the ancestor's cone down."""
    return FakeClient(
        lineage_by_column={
            "applicant_income": feature_upstream
            if feature_upstream is not None
            else [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(FEATURE_COLUMN, ANCESTOR_COLUMN),
                )
            ],
            "income": ancestor_downstream
            if ancestor_downstream is not None
            else [
                lineage_result(
                    FEATURE_TABLE_URN,
                    hops=1,
                    direction="downstream",
                    paths=column_path(ANCESTOR_COLUMN, PROTECTED_COLUMN),
                )
            ],
        }
    )


def _findings(
    graph: FakeGraph, client: FakeClient, config: ScanConfig = CONFIG
) -> tuple[ProxyCandidateFinding, ...]:
    return proxy_candidate_findings(make_connection(graph, client), MODEL_URN, config)


class TestUnconfigured:
    def test_no_classification_configured_means_no_walk_at_all(self):
        """Rule 5's posture: unset reports not-evaluated elsewhere, never clean.

        Asserts on the lineage calls issued rather than on the empty result:
        with nothing configured the answer is empty either way, so a result
        assertion would pass against a detector that walked the whole graph
        and found nothing to match.
        """
        client = _client()

        assert _findings(_graph(), client, ScanConfig()) == ()
        assert client.lineage.lineage_calls == []

    def test_a_configured_classification_does_walk(self):
        client = _client()

        _findings(_graph(), client)

        assert client.lineage.lineage_calls


class TestTheFork:
    def test_a_feature_sharing_an_ancestor_with_a_protected_column_is_a_candidate(self):
        findings = _findings(_graph(), _client())

        assert len(findings) == 1
        candidate = findings[0].candidate
        assert candidate.source_column_name == "applicant_income"
        assert candidate.protected_column_name == "ethnicity_band"
        assert candidate.ancestor_name == "income"

    def test_the_finding_names_the_shared_ancestor_as_its_evidence(self):
        """The thing a human goes and looks at. Without it the finding is an.

        accusation with no starting point.
        """
        findings = _findings(_graph(), _client())

        evidence = findings[0].evidence
        assert evidence["shared_ancestor"] == "income"
        assert "income" in evidence["shared_path"]

    def test_the_finding_says_in_its_own_evidence_that_it_is_not_a_determination(self):
        """09 section 5.1: this is the whole feature, built in from the start."""
        findings = _findings(_graph(), _client())

        assert "human review" in findings[0].evidence["finding_is"]
        assert "not a determination" in findings[0].evidence["finding_is"]

    def test_the_severity_is_medium_even_for_a_live_model(self):
        """Every other detector escalates for a live model. This one must not:.

        a maybe that outranks a proof sends triage to the wrong finding.
        """
        findings = _findings(_graph(), _client())

        assert findings[0].model.is_live
        assert findings[0].severity is Severity.MEDIUM

    def test_the_finding_type_is_its_own_and_not_sensitive_source(self):
        findings = _findings(_graph(), _client())

        assert findings[0].finding_type is FindingType.PROXY_CANDIDATE

    def test_the_first_remedy_asks_a_human_rather_than_changing_the_graph(self):
        """A tool that led with "drop the feature" would push a team to delete.

        something they may be entitled to use, on a suggestion.
        """
        remedies = _findings(_graph(), _client())[0].counterfactual.remedies

        assert remedies[0].kind.value == "review"


class TestNotACandidate:
    def test_direct_descent_is_left_to_the_sensitive_source_detector(self):
        """P5 proves that relationship. Reporting it here as well would raise a.

        second, weaker incident about one column.

        The graph is built so the guard is actually reached, which an earlier
        version of this test was not: `income` is both an ancestor of the
        feature *and* a descendant of `raw_income`, so without the exclusion the
        walk reaches it as a sibling through the grandparent and reports a
        candidate for a relationship that is plain descent.
        """
        grandparent = f"urn:li:schemaField:({TABLE_URN},raw_income)"
        graph = _graph(protected=ANCESTOR_COLUMN)
        client = FakeClient(
            lineage_by_column={
                "applicant_income": [
                    lineage_result(
                        TABLE_URN,
                        hops=1,
                        direction="upstream",
                        paths=column_path(FEATURE_COLUMN, ANCESTOR_COLUMN, grandparent),
                    )
                ],
                "income": [
                    lineage_result(
                        FEATURE_TABLE_URN,
                        hops=1,
                        direction="downstream",
                        paths=column_path(ANCESTOR_COLUMN, FEATURE_COLUMN),
                    )
                ],
                "raw_income": [
                    lineage_result(
                        FEATURE_TABLE_URN,
                        hops=1,
                        direction="downstream",
                        paths=column_path(grandparent, ANCESTOR_COLUMN, FEATURE_COLUMN),
                    )
                ],
            }
        )

        assert _findings(graph, client) == ()

    def test_a_shared_ancestor_with_an_unclassified_column_is_not_a_candidate(self):
        client = _client(
            ancestor_downstream=[
                lineage_result(
                    FEATURE_TABLE_URN,
                    hops=1,
                    direction="downstream",
                    paths=column_path(ANCESTOR_COLUMN, UNRELATED_COLUMN),
                )
            ]
        )

        assert _findings(_graph(), client) == ()

    def test_a_shared_ancestor_beyond_the_hop_cap_is_not_a_candidate(self):
        """Everything in a warehouse shares a raw table eventually, so a.

        candidate that named half the catalog would be worse than silence.
        """
        client = _client(
            feature_upstream=[
                lineage_result(
                    TABLE_URN,
                    hops=CONFIG.proxy_max_hops + 1,
                    direction="upstream",
                    paths=column_path(FEATURE_COLUMN, ANCESTOR_COLUMN),
                )
            ]
        )

        assert _findings(_graph(), client) == ()

    def test_a_feature_at_exactly_the_hop_cap_is_still_a_candidate(self):
        """The boundary belongs inside the cap, as it does everywhere else here."""
        client = _client(
            feature_upstream=[
                lineage_result(
                    TABLE_URN,
                    hops=CONFIG.proxy_max_hops,
                    direction="upstream",
                    paths=column_path(FEATURE_COLUMN, ANCESTOR_COLUMN),
                )
            ]
        )

        assert len(_findings(_graph(), client)) == 1

    def test_the_feature_is_never_reported_as_a_proxy_for_itself(self):
        client = _client(
            ancestor_downstream=[
                lineage_result(
                    FEATURE_TABLE_URN,
                    hops=1,
                    direction="downstream",
                    paths=column_path(ANCESTOR_COLUMN, FEATURE_COLUMN),
                )
            ]
        )
        graph = _graph(protected=FEATURE_COLUMN)

        assert _findings(graph, client) == ()

    def test_a_model_declaring_no_features_yields_nothing(self):
        graph = FakeGraph(
            aspects={(MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(mlFeatures=[])}  # type: ignore[arg-type]
        )

        assert _findings(graph, _client()) == ()


class TestOnePerPair:
    def test_one_pair_reached_through_two_generations_is_reported_once(self):
        """Two columns sharing an ancestor share its ancestors too, so an.

        unfiltered walk reports the same pair once per generation.

        The grandparent is named so it sorts *before* the nearer ancestor
        (`base_income` < `income`), which is what makes this test able to fail:
        with both orderings agreeing, keeping the first and keeping the nearest
        return the same column and the assertion below proves nothing.
        """
        grandparent = f"urn:li:schemaField:({TABLE_URN},base_income)"
        client = FakeClient(
            lineage_by_column={
                "applicant_income": [
                    lineage_result(
                        TABLE_URN,
                        hops=1,
                        direction="upstream",
                        paths=column_path(FEATURE_COLUMN, ANCESTOR_COLUMN, grandparent),
                    )
                ],
                "income": [
                    lineage_result(
                        FEATURE_TABLE_URN,
                        hops=1,
                        direction="downstream",
                        paths=column_path(ANCESTOR_COLUMN, PROTECTED_COLUMN),
                    )
                ],
                "base_income": [
                    lineage_result(
                        FEATURE_TABLE_URN,
                        hops=1,
                        direction="downstream",
                        paths=column_path(grandparent, ANCESTOR_COLUMN, PROTECTED_COLUMN),
                    )
                ],
            }
        )

        findings = _findings(_graph(), client)

        assert len(findings) == 1
        assert findings[0].candidate.ancestor_name == "income", "the nearest ancestor is quoted"


class TestReadOnly:
    def test_the_detector_writes_nothing(self):
        """detect/CLAUDE.md rule 1: detectors are pure functions of the graph."""
        graph = _graph()

        _findings(graph, _client())

        assert graph.emitted == []


class TestConfig:
    def test_a_term_alone_is_a_complete_configuration(self):
        """Many catalogs classify with terms only, many with tags only."""
        from datahub.metadata.schema_classes import (
            GlossaryTermAssociationClass,
            GlossaryTermsClass,
        )

        term = "urn:li:glossaryTerm:modelguard.protected"
        graph = FakeGraph(
            aspects={  # type: ignore[arg-type]
                (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                    mlFeatures=[LEAK_FEATURE_URN]
                ),
                (LEAK_FEATURE_URN, MLFeaturePropertiesClass): MLFeaturePropertiesClass(
                    customProperties={SOURCE_COLUMN_PROPERTY: FEATURE_COLUMN}
                ),
                (PROTECTED_COLUMN, GlossaryTermsClass): GlossaryTermsClass(
                    terms=[GlossaryTermAssociationClass(urn=term)], auditStamp=None
                ),
            }
        )

        findings = _findings(
            graph, _client(), replace(ScanConfig(), protected_attribute_term_urns=(term,))
        )

        assert len(findings) == 1
