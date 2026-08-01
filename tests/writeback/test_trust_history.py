"""The trust score's history. Offline: no DataHub, no network.

The property this guards is the one every write in this package shares: a rerun
converges. A history that appended on every scan of an unchanged graph would turn
a `watch` polling every thirty seconds into a structured property with two
thousand rows in it by lunchtime, which is why the run id is the key and why the
list is capped.

The second property is that nothing here can break a scan. The history is a
convenience on top of the score and no decision reads it, so a hand-edited or
truncated entry costs a missing row and never an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime

from modelguard.models import TrustBand, TrustScore
from modelguard.writeback.properties import TRUST_HISTORY, assign_properties
from modelguard.writeback.trust_history import (
    HISTORY_LIMIT,
    TrustEntry,
    append_entry,
    parse_entry,
    project_history,
    read_history,
)
from tests.conftest import MODEL_URN, FakeGraph, make_connection

WHEN = datetime(2026, 8, 2, 9, 30, tzinfo=UTC)


def _score(value: int, *, deductions: dict[str, float] | None = None) -> TrustScore:
    return TrustScore(
        value=value,
        band=TrustBand.HEALTHY if value >= 70 else TrustBand.AT_RISK,
        deductions=deductions if deductions is not None else {"leakage": 20.0},
    )


def _conn(graph: FakeGraph | None = None):  # noqa: ANN202 - a DataHubConnection
    return make_connection(graph or FakeGraph())


class TestRoundTrip:
    def test_an_entry_survives_rendering_and_parsing(self):
        entry = TrustEntry(
            recorded_at="2026-08-02T09:30:00Z",
            run_id="scan-abc",
            score=64,
            band="watch",
            deductions=("leakage", "missing_owner"),
        )

        assert parse_entry(entry.render()) == entry

    def test_an_entry_with_no_deductions_round_trips_as_empty_not_as_one_blank(self):
        """A model that scored 100 has no deductions; it must not read as one named ''."""
        entry = TrustEntry(
            recorded_at="2026-08-02T09:30:00Z",
            run_id="scan-abc",
            score=100,
            band="healthy",
            deductions=(),
        )

        parsed = parse_entry(entry.render())
        assert parsed is not None
        assert parsed.deductions == ()

    def test_a_malformed_entry_is_dropped_rather_than_raised_on(self):
        """The property is editable by anyone; a bad row must not fail a scan."""
        assert parse_entry("not an entry") is None
        assert parse_entry("2026-08-02T09:30:00Z|scan-abc|not-a-number|watch|leakage") is None

    def test_an_unparseable_stored_row_is_skipped_and_the_rest_are_read(self):
        """Somebody edited the property by hand. The scan keeps working."""
        conn = _conn()
        good = TrustEntry(
            recorded_at="2026-08-02T09:30:00Z",
            run_id="scan-1",
            score=80,
            band="healthy",
            deductions=(),
        )
        assign_properties(conn, MODEL_URN, {TRUST_HISTORY: ["garbage", good.render()]})

        history = read_history(conn, MODEL_URN)

        assert history == (good,)


class TestAppend:
    def test_the_first_scan_records_one_entry(self):
        conn = _conn()

        history = append_entry(conn, MODEL_URN, _score(82), "scan-1", now=WHEN)

        assert len(history) == 1
        assert history[0].score == 82
        assert history[0].run_id == "scan-1"

    def test_a_later_scan_appends_so_the_direction_is_visible(self):
        conn = _conn()
        append_entry(conn, MODEL_URN, _score(95), "scan-1", now=WHEN)

        history = append_entry(conn, MODEL_URN, _score(64), "scan-2", now=WHEN)

        assert [entry.score for entry in history] == [95, 64]

    def test_a_rerun_of_the_same_run_replaces_its_own_row(self):
        """The convergence property every write in this package shares.

        Without it a `watch` polling every thirty seconds would write two
        thousand rows a day into a structured property.
        """
        conn = _conn()
        append_entry(conn, MODEL_URN, _score(82), "scan-1", now=WHEN)

        history = append_entry(conn, MODEL_URN, _score(82), "scan-1", now=WHEN)

        assert len(history) == 1

    def test_the_history_is_capped_and_drops_the_oldest_first(self):
        conn = _conn()
        for index in range(HISTORY_LIMIT + 5):
            append_entry(conn, MODEL_URN, _score(index), f"scan-{index}", now=WHEN)

        history = read_history(conn, MODEL_URN)

        assert len(history) == HISTORY_LIMIT
        # The five oldest are gone; the newest is last.
        assert history[0].score == 5
        assert history[-1].score == HISTORY_LIMIT + 4

    def test_deductions_are_recorded_by_name_and_never_by_points(self):
        """Weights are configuration and can change; a stored point value would rot."""
        conn = _conn()

        history = append_entry(
            conn,
            MODEL_URN,
            _score(40, deductions={"leakage": 20.0, "missing_owner": 10.0}),
            "scan-1",
            now=WHEN,
        )

        assert history[0].deductions == ("leakage", "missing_owner")
        assert "20" not in history[0].render().rsplit("|", 1)[-1]

    def test_a_model_never_scored_has_an_empty_history_not_a_perfect_one(self):
        """Empty means unmeasured. It is not the same as a model that scored well."""
        assert read_history(_conn(), MODEL_URN) == ()


class TestProjection:
    def test_the_projection_matches_what_a_write_would_store(self):
        """The report renders the projection; the graph stores it. They must agree."""
        conn = _conn()
        append_entry(conn, MODEL_URN, _score(95), "scan-1", now=WHEN)

        projected = project_history(conn, MODEL_URN, _score(64), "scan-2", now=WHEN)
        append_entry(conn, MODEL_URN, _score(64), "scan-2", now=WHEN)

        assert read_history(conn, MODEL_URN) == projected

    def test_the_projection_writes_nothing(self):
        conn = _conn()

        project_history(conn, MODEL_URN, _score(64), "scan-1", now=WHEN)

        assert read_history(conn, MODEL_URN) == ()
