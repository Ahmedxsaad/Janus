"""The README's join diagram is generated, and may not be edited in place.

Same joint as `tests/test_site.py`'s diagram check: run the generator against
the committed file and fail if it moves anything.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAM = ROOT / "assets" / "join-diagram.svg"


def test_the_readme_diagram_matches_the_generator_that_draws_it():
    spec = importlib.util.spec_from_file_location(
        "_make_readme_diagram", ROOT / "assets" / "make_readme_diagram.py"
    )
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    assert DIAGRAM.read_text() == generator.standalone(), (
        "the README diagram is stale: run python assets/make_readme_diagram.py"
    )
