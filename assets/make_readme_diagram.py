"""Render the README's join diagram from the same generator the page uses.

The second thing generated for the README rather than authored in it, after
`argos.gif`. `site/art/make_diagrams.py` owns the diagram's actual coordinates;
this script only adds what a page embedded in `site/` never had to think about:
an explicit background.

    python assets/make_readme_diagram.py

Why the background is added here and nowhere upstream: `site/index.html`'s
diagrams sit on the page's own ivory paper, so they never draw one for
themselves. A README has no page behind it, and GitHub renders it on white or
on GitHub's own near-black depending on the reader's theme (`make_demo.py`
solves the identical problem for the GIF, with an opaque rim for the same
reason). Without a background here, the diagram's dark ink and pale rules would
read fine on one theme and vanish on the other. One flat paper-coloured rect
under everything fixes it on both, since this diagram, unlike the dog, is not
meant to change with the theme at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "site" / "art"))

import make_diagrams  # noqa: E402  (the path above is what makes this importable)

OUTPUT = HERE / "join-diagram.svg"


def standalone() -> str:
    """Return `make_diagrams.join()`'s SVG, with a background under it.

    The one thing a diagram embedded in `site/` never has to draw for itself:
    see the module docstring for why a README needs it drawn here instead.
    """
    body = make_diagrams.join()
    view_box = body.split('viewBox="', 1)[1].split('"', 1)[0]
    width, height = view_box.split()[2:]
    inner = body.split(">", 1)[1].rsplit("</svg>", 1)[0]
    background = f'<rect width="{width}" height="{height}" fill="{make_diagrams.PAPER}"></rect>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" role="img" '
        f'aria-label="Column-level lineage runs from a raw table through a staging '
        f"column into a feature table. DataHub's ML graph holds the model and its "
        f"training run. Nothing joins the two until janus link writes the edge from "
        f'feature to source column.">{background}{inner}</svg>\n'
    )


def main() -> int:
    """Write join-diagram.svg next to this script."""
    svg = standalone()
    OUTPUT.write_text(svg)
    print(f"wrote {OUTPUT} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
