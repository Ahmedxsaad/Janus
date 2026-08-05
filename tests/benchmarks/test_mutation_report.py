"""T-08's rendering of mutmut's own output, offline.

``render_mutation_section`` never calls mutmut itself; it is a pure function
of the text `mutmut results --all=true` would have printed, so these fixture
strings stand in for a real run. The behaviour worth pinning is the refusal:
a survivor mutmut reports that VERDICTS does not cover must stop the render
rather than publish a report that silently dropped it (10-depth-
implementation.md T-08).
"""

from __future__ import annotations

import pytest

from benchmarks.mutation_report import (
    END_MARKER,
    START_MARKER,
    Verdict,
    _parse,
    _splice,
    _verify_coverage,
    render_mutation_section,
)


def _results(*lines: str) -> str:
    return "\n".join(f"    {line}" for line in lines) + "\n"


class TestParse:
    def test_counts_every_status(self):
        counts, _survivors = _parse(
            _results(
                "janus.detect.leakage.x_a__mutmut_1: killed",
                "janus.detect.leakage.x_a__mutmut_2: survived",
                "janus.detect.leakage.x_a__mutmut_3: killed",
            )
        )

        assert counts == {"killed": 2, "survived": 1}

    def test_only_survivors_are_returned_by_name(self):
        _, survivors = _parse(
            _results(
                "janus.detect.leakage.x_a__mutmut_1: killed",
                "janus.detect.leakage.x_a__mutmut_2: survived",
            )
        )

        assert survivors == ["janus.detect.leakage.x_a__mutmut_2"]

    def test_an_unparseable_line_stops_the_render_rather_than_being_skipped(self):
        with pytest.raises(SystemExit):
            _parse("this is not a mutmut results line\n")

    def test_a_status_mutmut_has_never_printed_is_still_counted(self):
        """A status _parse has never seen before is still tallied, not dropped.

        The parser names no closed set of statuses; mutmut's own vocabulary
        (killed, survived, timeout, suspicious, no tests, ...) is not
        hard-coded here.
        """
        counts, _ = _parse(_results("janus.detect.leakage.x_a__mutmut_1: timeout"))

        assert counts == {"timeout": 1}


class TestVerifyCoverage:
    def test_a_survivor_matching_a_verdicts_prefix_is_assigned_to_it(self):
        verdicts = (Verdict("janus.detect.leakage.x_a", "gap", "because"),)

        by_verdict = _verify_coverage(["janus.detect.leakage.x_a__mutmut_1"], verdicts=verdicts)

        assert by_verdict["janus.detect.leakage.x_a"] == ["janus.detect.leakage.x_a__mutmut_1"]

    def test_a_survivor_with_no_matching_verdict_stops_the_render(self):
        with pytest.raises(SystemExit, match=r"janus\.detect\.leakage\.x_unlisted"):
            _verify_coverage(
                ["janus.detect.leakage.x_unlisted__mutmut_1"],
                verdicts=(Verdict("janus.detect.leakage.x_a", "gap", "because"),),
            )

    def test_a_prefix_that_is_a_substring_of_another_function_does_not_steal_it(self):
        """x_a must not swallow x_ancestor's survivors on a naive prefix match.

        x_a is a prefix of x_ancestor as a string, so this pins the exact-match
        semantics rather than str.startswith.
        """
        verdicts = (
            Verdict("janus.detect.leakage.x_a", "gap", "because"),
            Verdict("janus.detect.leakage.x_ancestor", "gap", "unrelated"),
        )

        by_verdict = _verify_coverage(
            ["janus.detect.leakage.x_ancestor__mutmut_1"], verdicts=verdicts
        )

        assert by_verdict["janus.detect.leakage.x_a"] == []
        assert by_verdict["janus.detect.leakage.x_ancestor"] == [
            "janus.detect.leakage.x_ancestor__mutmut_1"
        ]


class TestRenderMutationSection:
    VERDICTS = (
        Verdict("janus.detect.leakage.x_a", "gap", "a real gap explanation"),
        Verdict("janus.detect.leakage.x_b", "equivalent", "an equivalence explanation"),
    )

    def test_the_score_is_killed_over_killed_plus_survived(self):
        text = _results(
            "janus.detect.leakage.x_a__mutmut_1: killed",
            "janus.detect.leakage.x_a__mutmut_2: killed",
            "janus.detect.leakage.x_a__mutmut_3: killed",
            "janus.detect.leakage.x_b__mutmut_1: survived",
        )

        section = render_mutation_section(text, verdicts=self.VERDICTS)

        assert "Score (killed / (killed + survived)): 0.75" in section

    def test_every_survivor_group_gets_its_own_row(self):
        text = _results(
            "janus.detect.leakage.x_a__mutmut_1: survived",
            "janus.detect.leakage.x_b__mutmut_1: survived",
        )

        section = render_mutation_section(text, verdicts=self.VERDICTS)

        assert "`leakage.x_a`" in section
        assert "`leakage.x_b`" in section
        assert "a real gap explanation" in section
        assert "an equivalence explanation" in section

    def test_a_function_with_no_survivors_gets_no_row(self):
        text = _results(
            "janus.detect.leakage.x_a__mutmut_1: killed",
            "janus.detect.leakage.x_b__mutmut_1: survived",
        )

        section = render_mutation_section(text, verdicts=self.VERDICTS)

        assert "`leakage.x_a`" not in section
        assert "`leakage.x_b`" in section

    def test_gap_and_equivalent_counts_are_disjoint_and_sum_to_survived(self):
        text = _results(
            "janus.detect.leakage.x_a__mutmut_1: survived",
            "janus.detect.leakage.x_a__mutmut_2: survived",
            "janus.detect.leakage.x_b__mutmut_1: survived",
        )

        section = render_mutation_section(text, verdicts=self.VERDICTS)

        assert "Of 3 survivors: 2 are real gaps" in section
        assert "1 are provably equivalent" in section

    def test_the_section_is_wrapped_in_the_splice_markers(self):
        section = render_mutation_section(
            _results("janus.detect.leakage.x_a__mutmut_1: killed"), verdicts=self.VERDICTS
        )

        assert section.startswith(START_MARKER)
        assert section.rstrip("\n").endswith(END_MARKER)

    def test_an_unlisted_survivor_stops_the_render(self):
        text = _results("janus.detect.leakage.x_unlisted__mutmut_1: survived")

        with pytest.raises(SystemExit):
            render_mutation_section(text, verdicts=self.VERDICTS)


class TestSplice:
    def test_a_file_with_no_prior_section_gets_one_appended(self, tmp_path):
        results_md = tmp_path / "RESULTS.md"
        results_md.write_text("# Janus-Bench results\n\nExisting content.\n")

        spliced = _splice(results_md, f"{START_MARKER}\nnew section\n{END_MARKER}\n")

        assert "Existing content." in spliced
        assert "new section" in spliced

    def test_a_file_with_a_prior_section_has_it_replaced_not_duplicated(self, tmp_path):
        results_md = tmp_path / "RESULTS.md"
        results_md.write_text(
            f"# Results\n\nBefore.\n\n{START_MARKER}\nold section\n{END_MARKER}\n\nAfter.\n"
        )

        spliced = _splice(results_md, f"{START_MARKER}\nnew section\n{END_MARKER}\n")

        assert "old section" not in spliced
        assert "new section" in spliced
        assert spliced.count(START_MARKER) == 1
        assert "Before." in spliced
        assert "After." in spliced

    def test_splicing_the_same_section_twice_changes_nothing(self, tmp_path):
        """The CI job diffs this output against the committed file (D-124).

        Anything that is not a fixed point reports RESULTS.md stale on every
        run, which is a permanently red advisory job over whitespace.
        """
        results_md = tmp_path / "RESULTS.md"
        results_md.write_text("# Results\n\nBefore.\n")
        section = f"{START_MARKER}\nnew section\n{END_MARKER}\n"

        results_md.write_text(_splice(results_md, section))
        once = results_md.read_text()
        results_md.write_text(_splice(results_md, section))

        assert results_md.read_text() == once

    def test_a_section_at_the_end_of_the_file_keeps_one_trailing_newline(self, tmp_path):
        """The shape RESULTS.md actually has: nothing follows the section."""
        results_md = tmp_path / "RESULTS.md"
        results_md.write_text(f"# Results\n\nBefore.\n\n{START_MARKER}\nold\n{END_MARKER}\n")

        spliced = _splice(results_md, f"{START_MARKER}\nnew section\n{END_MARKER}\n")

        assert spliced.endswith(f"{END_MARKER}\n")
        assert not spliced.endswith(f"{END_MARKER}\n\n")
