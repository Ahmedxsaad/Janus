"""marked_ancestor's WalkResult.

Whether a mark was found, and whether the walk could have missed one past the
lineage result cap (F1, docs/plan/07).
"""

from __future__ import annotations

from dataclasses import replace

from datahub.metadata.schema_classes import GlossaryTermAssociationClass, GlossaryTermsClass

from modelguard.config import ScanConfig
from modelguard.detect.column_marks import ColumnMarkIndex, marked_ancestor
from tests.conftest import (
    FEATURE_TABLE_URN,
    LABEL_COLUMN_URN,
    LABEL_TERM_URN,
    LEAK_COLUMN_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    column_path,
    lineage_result,
    make_connection,
)

CONFIG = ScanConfig()
_CAPPED = replace(CONFIG, lineage_result_cap=2)


def _terms(*urns: str) -> GlossaryTermsClass:
    return GlossaryTermsClass(
        terms=[GlossaryTermAssociationClass(urn=urn) for urn in urns], auditStamp=None
    )


def _index(graph: FakeGraph) -> ColumnMarkIndex:
    return ColumnMarkIndex(make_connection(graph), terms=frozenset({LABEL_TERM_URN}))


def test_a_cone_short_of_the_cap_with_no_hit_is_not_truncated():
    """One result under a cap of two: the walk genuinely saw the whole cone."""
    graph = FakeGraph()
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(TABLE_URN, hops=1, direction="upstream", paths=column_path())
            ]
        }
    )
    conn = make_connection(graph, client)
    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), _CAPPED)

    assert walk.hit is None
    assert walk.truncated is False


def test_a_cone_exactly_at_the_cap_with_no_hit_is_truncated():
    """Two results at a cap of two: a mark beyond the cap may exist, unseen."""
    graph = FakeGraph()
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(TABLE_URN, hops=1, direction="upstream", paths=column_path()),
                lineage_result(
                    FEATURE_TABLE_URN, hops=1, direction="upstream", paths=column_path()
                ),
            ]
        }
    )
    conn = make_connection(graph, client)
    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), _CAPPED)

    assert walk.hit is None
    assert walk.truncated is True


def test_a_hit_within_a_truncated_cone_is_still_reported():
    """The evidence is real even when the walk that found it was capped.

    A truncated walk that found nothing is uncertain; one that found a hit is
    not, since the hit did not depend on whatever lies past the cap.
    """
    graph = FakeGraph(aspects={(LABEL_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN, hops=1, direction="upstream", paths=column_path(LABEL_COLUMN_URN)
                ),
                lineage_result(
                    FEATURE_TABLE_URN, hops=1, direction="upstream", paths=column_path()
                ),
            ]
        }
    )
    conn = make_connection(graph, client)
    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), _CAPPED)

    assert walk.hit is not None
    assert walk.hit[0] == LABEL_COLUMN_URN
    assert walk.truncated is True


def test_a_column_that_is_itself_marked_is_never_reported_truncated():
    """Nothing was walked to find a direct hit, so nothing could be truncated."""
    graph = FakeGraph(aspects={(LEAK_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    conn = make_connection(graph, FakeClient())

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), _CAPPED)

    assert walk.hit is not None
    assert walk.truncated is False
