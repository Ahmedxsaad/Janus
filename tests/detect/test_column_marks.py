"""marked_ancestor's WalkResult.

Whether a mark was found, and whether the walk could have missed one past the
lineage result cap (docs/08-evaluation.md).
"""

from __future__ import annotations

from dataclasses import replace

from datahub.metadata.schema_classes import GlossaryTermAssociationClass, GlossaryTermsClass

from janus.config import ScanConfig
from janus.detect.column_marks import (
    ColumnMarkIndex,
    derivation_chains,
    marked_ancestor,
    split_paths,
)
from tests.conftest import (
    FEATURE_TABLE_URN,
    INCOME_COLUMN_URN,
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


# --- hop cap (F1: distinct from the result-count cap above) ------------


def test_a_mark_exactly_at_the_hop_cap_fires():
    """The cap is a hard limit, not an exclusive one: hops == cap still counts."""
    graph = FakeGraph(aspects={(LABEL_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=CONFIG.leakage_max_hops,
                    direction="upstream",
                    paths=column_path(LABEL_COLUMN_URN),
                )
            ]
        }
    )
    conn = make_connection(graph, client)

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is not None
    assert walk.hit[0] == LABEL_COLUMN_URN
    assert walk.hop_capped is False


def test_a_mark_one_hop_beyond_the_cap_does_not_fire_and_is_reported_hop_capped():
    """GMS answers past the cap; the walk must decline it, not miss it."""
    graph = FakeGraph(aspects={(LABEL_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=CONFIG.leakage_max_hops + 1,
                    direction="upstream",
                    paths=column_path(LABEL_COLUMN_URN),
                )
            ]
        }
    )
    conn = make_connection(graph, client)

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is None
    assert walk.hop_capped is True


def test_a_result_within_the_hop_cap_is_not_reported_hop_capped():
    graph = FakeGraph()
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=CONFIG.leakage_max_hops,
                    direction="upstream",
                    paths=column_path(),
                )
            ]
        }
    )
    conn = make_connection(graph, client)

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is None
    assert walk.hop_capped is False


def test_a_hit_within_the_cap_does_not_hide_a_hop_capped_result_elsewhere():
    """The two flags answer different questions and must not shadow each other.

    The hop-capped result comes first and the real hit second, on purpose: a
    walk that stopped entirely at the first hop-capped result (mistaking
    "decline this one" for "stop looking") would never reach the hit at all,
    where a hop-capped result trailing the hit would let that bug hide behind
    an answer that happened to already be found.
    """
    graph = FakeGraph(aspects={(LABEL_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    client = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    FEATURE_TABLE_URN,
                    hops=CONFIG.leakage_max_hops + 1,
                    direction="upstream",
                    paths=column_path(),
                ),
                lineage_result(
                    TABLE_URN, hops=1, direction="upstream", paths=column_path(LABEL_COLUMN_URN)
                ),
            ]
        }
    )
    conn = make_connection(graph, client)

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is not None
    assert walk.hop_capped is True


def test_a_column_that_is_itself_marked_is_never_reported_hop_capped():
    """Nothing was walked to find a direct hit, so nothing could be hop-capped."""
    graph = FakeGraph(aspects={(LEAK_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    conn = make_connection(graph, FakeClient())

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is not None
    assert walk.hop_capped is False


BACKUP_COLUMN_URN = f"urn:li:schemaField:({TABLE_URN},default_status_backup)"


def _two_flattened_paths() -> FakeClient:
    """One upstream table reached by two derivations, as the SDK returns them.

    ``LineageResult.paths`` is a single flat list: the SDK appends every step of
    every path GMS answered with into one list, so two derivations through the
    same upstream table arrive concatenated. Each one starts at the column that
    was queried, which is the only boundary there is.
    """
    return FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(
                        LEAK_COLUMN_URN,
                        LABEL_COLUMN_URN,
                        LEAK_COLUMN_URN,
                        BACKUP_COLUMN_URN,
                    ),
                )
            ]
        }
    )


def test_the_shortest_chain_is_still_the_one_quoted_as_proof():
    """The precondition: widening the result must move no existing output.

    The walk now carries every match, and `hit` still has to answer with the
    shortest, ties broken deterministically, because that chain is what an
    incident quotes and a proof that moves between runs is not a proof.
    """
    graph = FakeGraph(
        aspects={
            (LABEL_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN),
            (BACKUP_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN),
        }
    )
    conn = make_connection(
        graph,
        FakeClient(
            lineage_by_column={
                "prior_default_flag": [
                    lineage_result(
                        TABLE_URN,
                        hops=1,
                        direction="upstream",
                        paths=column_path(
                            LEAK_COLUMN_URN,
                            INCOME_COLUMN_URN,
                            BACKUP_COLUMN_URN,
                            LEAK_COLUMN_URN,
                            LABEL_COLUMN_URN,
                        ),
                    )
                ]
            }
        ),
    )

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is not None
    assert walk.hit[2] == ("prior_default_flag", "default_status")
    assert len(walk.matches) == 2


def test_every_derivation_is_carried_not_only_the_winner():
    """The counterfactual needs them: cutting one path of two clears nothing."""
    graph = FakeGraph(
        aspects={
            (LABEL_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN),
            (BACKUP_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN),
        }
    )
    conn = make_connection(graph, _two_flattened_paths())

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.others == (("prior_default_flag", "default_status_backup"),)


def test_a_chain_never_carries_the_tail_of_the_derivation_before_it():
    """The flattened list is cut back into paths before any of it is quoted.

    Truncating by index into the concatenation would quote
    "prior_default_flag <- default_status <- prior_default_flag <-
    default_status_backup" as one derivation, which is two derivations wearing
    the shape of one and is not what the graph says.
    """
    graph = FakeGraph(aspects={(BACKUP_COLUMN_URN, GlossaryTermsClass): _terms(LABEL_TERM_URN)})
    conn = make_connection(graph, _two_flattened_paths())

    walk = marked_ancestor(conn, LEAK_COLUMN_URN, _index(graph), CONFIG)

    assert walk.hit is not None
    assert walk.hit[2] == ("prior_default_flag", "default_status_backup")


def test_a_path_list_that_does_not_start_at_the_queried_column_is_left_whole():
    """A fixture, or a GMS that omits the start entity, must not lose its path."""
    steps = column_path(LABEL_COLUMN_URN, INCOME_COLUMN_URN)

    assert split_paths(steps, LEAK_COLUMN_URN) == [steps]


def test_derivation_chains_cuts_a_flattened_list_into_the_paths_it_came_from():
    """Two derivations must not render as one impossible chain on a card.

    The same split `marked_ancestor` needs, asked without a mark: a provenance
    card wants the whole derivation, including the part nobody classified.
    """
    conn = make_connection(FakeGraph(), _two_flattened_paths())

    chains = derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=CONFIG.leakage_max_hops)

    assert [tuple(step.column_name for step in chain) for chain in chains] == [
        ("prior_default_flag", "default_status"),
        ("prior_default_flag", "default_status_backup"),
    ]


def test_derivation_chains_returns_one_entry_for_a_derivation_reached_twice():
    """A card listing the same derivation twice claims two paths nothing measured."""
    duplicated = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                ),
                lineage_result(
                    TABLE_URN,
                    hops=2,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                ),
            ]
        }
    )
    conn = make_connection(FakeGraph(), duplicated)

    chains = derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=CONFIG.leakage_max_hops)

    assert len(chains) == 1


def test_derivation_chains_honors_the_hop_cap_rather_than_trusting_the_server():
    """Above two hops DataHub answers past max_hops (detect rule 3)."""
    beyond = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=4,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ]
        }
    )
    conn = make_connection(FakeGraph(), beyond)

    assert derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3) == ()


def test_a_column_with_no_upstream_lineage_yields_no_chain_rather_than_a_stub():
    """The card renders "not recorded" for this, which needs an empty answer."""
    conn = make_connection(FakeGraph(), FakeClient())

    assert derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3) == ()


def test_a_path_holding_only_the_queried_column_is_not_a_derivation():
    """A column derived from itself is not provenance, it is a rendering bug.

    Kept out here rather than in the card, because a one-step chain renders as
    a single backtick-quoted name and reads exactly like a complete derivation
    to a source.
    """
    self_only = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN),
                )
            ]
        }
    )
    conn = make_connection(FakeGraph(), self_only)

    assert derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3) == ()


def test_derivation_chains_asks_the_graph_for_the_columns_own_upstream_cone():
    """Asserted on the call issued, not only on the result it happened to return.

    `FakeLineage` answers whatever it was seeded with regardless of its
    arguments, so a walk that queried the wrong column, the wrong direction or
    an unbounded depth returns the same list here and every assertion about the
    *answer* passes. The read is the behaviour, so the read is what is pinned
    (the same correction the degraded-mode tests needed).
    """
    client = _two_flattened_paths()
    conn = make_connection(FakeGraph(), client)

    derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=4)

    call = client.lineage.lineage_calls[0]
    assert call["source_urn"] == FEATURE_TABLE_URN
    assert call["source_column"] == "prior_default_flag"
    assert call["direction"] == "upstream"
    assert call["max_hops"] == 4
    assert call["count"] == CONFIG.lineage_result_cap


def test_derivation_chains_keeps_a_result_exactly_at_the_hop_cap():
    """The boundary itself, which `> max_hops` includes and `>= max_hops` drops."""
    at_cap = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=3,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ]
        }
    )
    conn = make_connection(FakeGraph(), at_cap)

    assert len(derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3)) == 1


def test_a_result_past_the_cap_skips_that_result_and_not_the_rest():
    """`continue`, never `break`: GMS does not return results in hop order.

    Above two hops it answers from a full-graph search in network order, so the
    first result past the cap can precede every result inside it. Stopping there
    would drop a real derivation and the card would print a shorter provenance
    than the graph holds.
    """
    unordered = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=9,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, INCOME_COLUMN_URN),
                ),
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                ),
            ]
        }
    )
    conn = make_connection(FakeGraph(), unordered)

    chains = derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3)

    assert [tuple(step.column_name for step in chain) for chain in chains] == [
        ("prior_default_flag", "default_status")
    ]


def test_a_path_without_the_queried_column_first_still_skips_only_its_own_step():
    """The inner `continue` skips one step of one path, never the whole walk.

    Turned to `break` it would stop at the first step and lose every chain
    behind it in the same result.
    """
    two_paths = _two_flattened_paths()
    conn = make_connection(FakeGraph(), two_paths)

    chains = derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=CONFIG.leakage_max_hops)

    assert len(chains) == 2


def test_a_one_step_path_is_skipped_without_losing_the_paths_behind_it():
    """The inner guard is `continue`, and `break` would cost the real chain.

    The flattened list can open a path at the queried column and immediately
    open another, which leaves a one-step path in front of a genuine
    derivation. Stopping at the first would return no provenance at all for a
    column that has some.
    """
    leading_stub = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(LEAK_COLUMN_URN, LEAK_COLUMN_URN, LABEL_COLUMN_URN),
                )
            ]
        }
    )
    conn = make_connection(FakeGraph(), leading_stub)

    chains = derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3)

    assert [tuple(step.column_name for step in chain) for chain in chains] == [
        ("prior_default_flag", "default_status")
    ]


def test_two_chains_of_equal_length_are_ordered_by_their_column_names():
    """The tiebreaker is real, not decorative, and it is what makes a card stable.

    Above two hops GMS answers in network order, so two derivations of the same
    depth can arrive either way round. A card that printed them in whichever
    order the server chose would show a different provenance on two reads of an
    unchanged graph. Seeded here in the *reverse* of the expected order, so a
    tiebreaker that collapsed to a constant would leave them as they arrived.
    """
    reversed_order = FakeClient(
        lineage_by_column={
            "prior_default_flag": [
                lineage_result(
                    TABLE_URN,
                    hops=1,
                    direction="upstream",
                    paths=column_path(
                        LEAK_COLUMN_URN,
                        BACKUP_COLUMN_URN,
                        LEAK_COLUMN_URN,
                        LABEL_COLUMN_URN,
                    ),
                )
            ]
        }
    )
    conn = make_connection(FakeGraph(), reversed_order)

    chains = derivation_chains(conn, LEAK_COLUMN_URN, CONFIG, max_hops=3)

    assert [tuple(step.column_name for step in chain) for chain in chains] == [
        ("prior_default_flag", "default_status"),
        ("prior_default_flag", "default_status_backup"),
    ]
