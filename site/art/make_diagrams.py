"""Draw the page's explanatory diagrams, and write them into it.

Why a generator rather than SVG typed into the HTML
---------------------------------------------------
The same reason the ornaments have one (docs/11-argos.md). A diagram is a
few dozen coordinates that have to agree with each other, and the failure mode
of typing them by hand is a label three pixels outside the box it belongs to,
which nobody notices until it is on the page. Here a box knows how to place its
own two lines of text, an arrowhead is defined once, and the palette is the
page's rather than each diagram's own.

The first draft of these was written straight into `index.html`, and the
sub-label in every box shorter than 46 pixels fell out through the bottom of it:
the offset that fitted the one diagram it was written against fitted none of the
others. That is the bug this file exists to make impossible.

Run it from anywhere; it rewrites the diagrams in `site/index.html` in place,
matching each on a distinctive run of its own aria-label, so running it twice
changes nothing the second time:

    python site/art/make_diagrams.py

`tests/test_site.py` runs it against the committed page and fails if the two
disagree, which is the same joint `pixels.js` has.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable

PAGE = pathlib.Path(__file__).resolve().parent.parent / "index.html"

#: The house style, taken from the diagram the page already had.
INK, FAINT, ACCENT = "#2b1d13", "#a08b76", "#9a5d21"
PAPER, PANEL, RULE, EDGE = "#faf5ea", "#f2ead9", "#e2d5bf", "#d8c8ad"
DANGER, OK = "#6e1f26", "#4f6137"
MONO = "ui-monospace,monospace"


def markers(suffix: str) -> str:
    """Arrowheads, with ids unique to the diagram that uses them.

    One id shared across several SVGs in a document is how the first element on
    the page silently wins every reference to it, which is the same class of bug
    as the duplicated `argos` id that stopped the character rendering.
    """
    return (
        "<defs>"
        + "".join(
            f'<marker id="{key}{suffix}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{colour}"></path></marker>'
            for key, colour in (("a", INK), ("w", ACCENT), ("b", DANGER))
        )
        + "</defs>"
    )


def panel(x: int, y: int, w: int, h: int, title: str | None = None) -> str:
    """A soft container, optionally with a quiet uppercase label in its corner."""
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
        f'fill="{PANEL}" stroke="{RULE}"></rect>'
    )
    if title:
        out += (
            f'<text x="{x + 16}" y="{y + 22}" fill="{FAINT}" font-family="{MONO}" '
            f'font-size="11" letter-spacing="1.4">{title}</text>'
        )
    return out


def box(
    x: int,
    y: int,
    w: int,
    h: int,
    label: str,
    sub: str | None = None,
    stroke: str = EDGE,
    fill: str = PAPER,
    colour: str = INK,
    size: int = 11,
    sub_colour: str = FAINT,
) -> str:
    """A node: a label, and optionally a quieter second line under it."""
    mid = x + w / 2
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
        f'fill="{fill}" stroke="{stroke}"></rect>'
    )
    # Both lines are placed from the box's own height. A fixed offset only fits
    # the one height it was written against, and drops out of every shorter box.
    top = y + h / 2 + (4 if sub is None else -3)
    out += (
        f'<text x="{mid}" y="{top}" text-anchor="middle" fill="{colour}" '
        f'font-family="{MONO}" font-size="{size}">{label}</text>'
    )
    if sub:
        out += (
            f'<text x="{mid}" y="{y + h / 2 + 13}" text-anchor="middle" fill="{sub_colour}" '
            f'font-family="{MONO}" font-size="10">{sub}</text>'
        )
    return out


def line(
    path: str,
    colour: str = INK,
    marker: str | None = None,
    dash: str | None = None,
    width: float = 1.5,
) -> str:
    """An edge, optionally dashed and optionally arrowed."""
    out = f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="{width}"'
    if dash:
        out += f' stroke-dasharray="{dash}"'
    if marker:
        out += f' marker-end="url(#{marker})"'
    return out + "></path>"


def text(
    x: int,
    y: int,
    body: str,
    colour: str = FAINT,
    size: int = 10,
    anchor: str = "middle",
) -> str:
    """A run of monospaced text, which is the only type any of these use."""
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" fill="{colour}" '
        f'font-family="{MONO}" font-size="{size}">{body}</text>'
    )


def svg(view: str, label: str, body: str) -> str:
    """Wrap a drawing, with the alt text a screen reader is given instead."""
    return f'<svg viewBox="{view}" role="img" aria-label="{label}">{body}</svg>'


# ---------------------------------------------------------------------------
# The diagrams. Each returns one finished <svg> element.
# ---------------------------------------------------------------------------


def join() -> str:
    """The edge nothing else writes: the two graphs, and the join between them."""
    b = markers("j")
    b += panel(6, 6, 392, 196, "WAREHOUSE GRAPH")
    b += box(26, 52, 118, 46, "loans_raw", "raw table")
    b += box(166, 52, 118, 46, "stg_customers", "staging")
    b += box(
        18,
        130,
        274,
        46,
        "customer_features.contract_renewed_flag",
        "a column, not a table",
        stroke=INK,
        size=10,
        sub_colour=ACCENT,
    )
    b += line("M144,75 L162,75", INK, "aj")
    b += line("M225,98 L225,118 L160,118 L160,126", INK, "aj")

    b += panel(420, 6, 294, 196, "ML GRAPH")
    b += box(440, 52, 118, 46, "training run", "mlflow")
    b += box(578, 52, 118, 46, "credit_risk_v3", "live model")
    b += box(508, 130, 118, 46, "mlFeature", "what it trained on", stroke=INK)
    b += line("M558,75 L574,75", INK, "aj")
    b += line("M567,126 L567,112 L637,112 L637,102", INK, "aj")

    b += line("M296,153 L504,153", ACCENT, "wj", dash="7 5", width=2)
    b += text(395, 144, "janus link", ACCENT, 11)
    b += text(395, 192, "the edge no ingestion source writes", FAINT, 10)

    return svg(
        "0 0 720 208",
        "Column-level lineage runs from a raw table through a staging column into a "
        "feature table. DataHub's ML graph holds the model and its training run. Nothing "
        "joins the two until janus link writes the edge from feature to source column.",
        b,
    )


def pipeline() -> str:
    """What a scan does, and the one place a language model is allowed to be."""
    b = markers("p")
    b += text(102, 18, "1  READ THE GRAPH", FAINT, 11)
    b += text(299, 18, "2  DECIDE", FAINT, 11)
    b += text(558, 18, "3  WRITE BACK", FAINT, 11)

    b += panel(6, 26, 192, 122)
    for index, item in enumerate(["column lineage", "schemas", "timestamps", "tags and terms"]):
        b += box(20, 38 + index * 27, 164, 22, item, size=10)

    b += panel(216, 26, 166, 122)
    b += text(299, 66, "six checks", INK, 14)
    b += text(299, 86, "deterministic Python", FAINT, 10)
    b += line("M240,102 L358,102", RULE, width=1)
    b += text(299, 124, "no model runs here", ACCENT, 10)

    b += panel(400, 26, 314, 122)
    writes = [
        ("incident", "model-at-risk tag"),
        ("trust score", "guarding assertion"),
        ("impact report", "process run"),
    ]
    for row, (left, right) in enumerate(writes):
        b += box(412, 38 + row * 37, 142, 28, left, size=10)
        b += box(562, 38 + row * 37, 142, 28, right, size=10)

    b += line("M198,87 L212,87", INK, "ap")
    b += line("M382,87 L396,87", INK, "ap")

    # The model, hanging off to one side and reaching only the wording.
    b += box(216, 178, 166, 34, "language model", fill=PANEL)
    b += line("M382,192 L470,192 L470,152", ACCENT, "wp", dash="6 5", width=2)
    b += text(478, 186, "wording only", ACCENT, 10, "start")

    return svg(
        "0 0 720 224",
        "A scan reads column lineage, schemas, timestamps and tags from the graph, "
        "decides with six deterministic checks and no language model, and writes back an "
        "incident, a model-at-risk tag, a trust score, a guarding assertion, an impact "
        "report and a process run. A language model is attached only to the wording of "
        "what is written, never to the decision.",
        b,
    )


def precision() -> str:
    """Why the column edge is the whole difference between the two readings."""
    b = markers("c")
    b += text(140, 22, "ONE LABELLED TABLE", FAINT, 11)
    b += panel(30, 34, 220, 112, "LOANS_LABELLED")
    b += box(44, 62, 192, 30, "default_flag", stroke=ACCENT, colour=ACCENT, size=10)
    b += text(140, 108, "the label the model predicts", FAINT, 9)
    b += box(44, 114, 192, 26, "region", size=10)

    b += text(475, 22, "TWO FEATURES OF ONE MODEL", FAINT, 11)
    b += box(380, 40, 190, 46, "applicant_income", "derives from the label", stroke=DANGER)
    b += box(380, 100, 190, 46, "zip_code", "derives from region")

    b += line("M236,77 L305,77 L305,63 L374,63", DANGER, "bc", width=2)
    b += line("M236,127 L305,127 L305,123 L374,123", INK, "ac")

    b += panel(30, 176, 320, 110, "TABLE LEVEL")
    b += text(46, 216, "sees loans_labelled, not its columns", FAINT, 10, "start")
    b += text(46, 238, "flags applicant_income", INK, 11, "start")
    b += text(46, 256, "flags zip_code", DANGER, 11, "start")
    b += text(46, 276, "one of the two is wrong", DANGER, 10, "start")

    b += panel(370, 176, 320, 110, "COLUMN LEVEL")
    b += text(386, 216, "sees default_flag &#8594; applicant_income", FAINT, 10, "start")
    b += text(386, 238, "flags applicant_income", INK, 11, "start")
    b += text(386, 256, "leaves zip_code alone", OK, 11, "start")
    b += text(386, 276, "names the one somebody has to fix", OK, 10, "start")

    return svg(
        "0 0 720 298",
        "Two features of one model both descend from the same labelled table. Only "
        "applicant_income descends from the label column itself. Table-level lineage "
        "cannot see the columns, so it flags both features and is wrong about one of "
        "them; column-level lineage flags applicant_income and leaves zip_code alone.",
        b,
    )


def counterfactual() -> str:
    """Why cutting the path an incident quotes is not always the fix."""
    b = markers("x")
    b += box(20, 66, 150, 34, "default_flag", "the label", stroke=ACCENT)
    b += box(258, 20, 160, 34, "income_band")
    b += box(258, 112, 160, 34, "default_flag_backfill", size=10)
    b += box(506, 66, 178, 34, "applicant_income", "the feature", stroke=DANGER)

    b += line("M170,78 L214,78 L214,37 L252,37", INK, "ax")
    b += line("M418,37 L462,37 L462,78 L500,78", INK, "ax")
    b += line("M170,90 L214,90 L214,129 L252,129", INK, "ax")
    b += line("M418,129 L462,129 L462,90 L500,90", INK, "ax")
    b += text(352, 168, "two derivations, one feature", FAINT, 10)

    b += panel(30, 188, 320, 96, "CUT THE PATH THE INCIDENT QUOTES")
    b += text(46, 222, "income_band       cut", FAINT, 10, "start")
    b += text(46, 240, "backfill          still there", FAINT, 10, "start")
    b += text(46, 266, "the finding still fires", DANGER, 12, "start")

    b += panel(370, 188, 320, 96, "CUT EVERY FIRST EDGE")
    b += text(386, 222, "income_band       cut", FAINT, 10, "start")
    b += text(386, 240, "backfill          cut", FAINT, 10, "start")
    b += text(386, 266, "the finding clears", OK, 12, "start")

    return svg(
        "0 0 720 296",
        "The feature applicant_income reaches the label default_flag by two separate "
        "derivations, through income_band and through default_flag_backfill. Cutting "
        "only the path the incident quotes leaves the finding standing; it clears only "
        "when the first edge of every path is cut.",
        b,
    )


def dropped() -> str:
    """The silent failure `watch --events` exists for, as four steps."""
    b = markers("t")
    steps = [
        (4, "janus link", "the model declares", "its features", EDGE, INK),
        (189, "datahub ingest", "mlModelProperties", "upserted whole", EDGE, INK),
        (374, "mlFeatures gone", "three checks report", "not evaluated", DANGER, DANGER),
        (559, "watch --events", "reads the change log,", "re-applies the link", ACCENT, ACCENT),
    ]
    for x, title, first, second, stroke, colour in steps:
        b += box(x, 30, 157, 108, "", stroke=stroke, fill=PANEL if colour == INK else PAPER)
        b += text(x + 78, 58, title, colour, 11)
        b += text(x + 78, 84, first, FAINT, 9)
        b += text(x + 78, 98, second, FAINT, 9)
    for x in (161, 346, 531):
        b += line(f"M{x},84 L{x + 24},84", INK, "at")

    b += text(452, 160, "nothing errors, and nothing in the catalog looks wrong", DANGER, 10)
    b += text(
        452, 176, "a model that was fully checked yesterday is simply no longer checked", FAINT, 10
    )

    return svg(
        "0 0 720 190",
        "A link is declared, then an ordinary mlflow ingest upserts the whole "
        "mlModelProperties aspect and drops the features it attached. Three checks then "
        "report not evaluated on a model that was fully checked the day before, and "
        "nothing errors. watch --events reads the change log and re-applies the link.",
        b,
    )


def selfrun() -> str:
    """The agent, as entities inside the graph it is writing to."""
    b = markers("r")
    b += box(24, 40, 176, 44, "dataFlow", "the Janus agent")
    b += box(24, 118, 176, 44, "dataJob", "the scan")
    b += box(
        264, 118, 200, 44, "dataProcessInstance", "one per run, keyed by run_id", stroke=ACCENT
    )
    b += box(264, 26, 200, 40, "inputs", "the entities it read")
    b += box(264, 194, 200, 40, "outputs", "the entities it wrote")
    b += box(520, 118, 176, 44, "run event", "started, then complete")

    b += line("M112,84 L112,114", INK, "ar")
    b += line("M200,140 L260,140", INK, "ar")
    b += line("M364,114 L364,70", INK, "ar")
    b += line("M364,162 L364,190", INK, "ar")
    b += line("M464,140 L516,140", INK, "ar")
    b += text(608, 178, "or failed, which is not silence", DANGER, 10)

    return svg(
        "0 0 720 246",
        "Janus emits itself as a dataFlow for the agent, a dataJob for the scan, and one "
        "dataProcessInstance per run keyed by the same run id every write is stamped "
        "with. The run carries the entities it read as inputs and the entities it wrote "
        "as outputs, and a run event that ends complete or failed.",
        b,
    )


#: Each diagram, keyed by a run of its own aria-label long enough to be unique.
#: Matching on the label rather than on position is what lets this be re-run.
DIAGRAMS: dict[str, Callable[[], str]] = {
    "Column-level lineage runs from a raw table": join,
    "A scan reads column lineage": pipeline,
    "Two features of one model": precision,
    "The feature applicant_income reaches": counterfactual,
    "A link is declared": dropped,
    "Janus emits itself": selfrun,
}


def render(page: str) -> str:
    """Return `page` with every diagram in it replaced by a freshly drawn one."""
    for needle, draw in DIAGRAMS.items():
        pattern = re.compile(
            r'<svg viewBox="[^"]*" role="img"\s+aria-label="' + re.escape(needle) + r".*?</svg>",
            re.S,
        )
        page, count = pattern.subn(lambda _match, build=draw: build(), page, count=1)
        assert count == 1, f"no diagram on the page matches: {needle}"
    return page


def main() -> None:
    """Redraw the diagrams in place, and say whether anything moved."""
    before = PAGE.read_text()
    after = render(before)
    PAGE.write_text(after)
    print(f"{len(DIAGRAMS)} diagrams redrawn, {'changed' if after != before else 'unchanged'}")


if __name__ == "__main__":
    main()
