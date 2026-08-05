"""The documentation page reads the same art the window reads, so it can drift.

Nothing here renders anything: these check the three joints where `site/` names
something that lives elsewhere. A page whose dog silently fails to appear, or
whose bubble drops half a sentence because a character has no glyph, looks fine
in review and broken to a reader.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from janus.render import AI_RMF_SUBCATEGORIES, CROSSWALK

from .test_argos import _frames

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
GUIDE = (SITE / "argos-guide.js").read_text()
PAGE = (SITE / "index.html").read_text()


def _font() -> dict[str, str]:
    """The glyph table, as the drawing routine indexes it: character to bitmap."""
    table = re.search(r"const FONT = \{(.*?)\n  \};", GUIDE, re.S)
    assert table, "the glyph table moved: this test cannot check the bubbles"
    # Quoted keys ("?", " ") and bare ones (A, 7, _) both index the same table.
    pairs = re.findall(r'(?:"(.)"|\b([A-Z0-9_])):\s*"([01]+)"', table.group(1))
    return {quoted or bare: bitmap for quoted, bare, bitmap in pairs}


def _pose_frames() -> dict[str, list[str]]:
    """The frame names each pose cycles through, plus the walk cycle."""
    block = re.search(r"const POSES = \{(.*?)\n  \};", GUIDE, re.S)
    assert block, "the pose table moved"
    poses = {
        name: re.findall(r'"([a-z_]+)"', frames)
        for name, frames in re.findall(r"(\w+): \[(.*?)\],", block.group(1))
    }
    walk = re.search(r"const WALK = \[(.*?)\];", GUIDE, re.S)
    assert walk, "the walk cycle moved"
    poses["walk"] = re.findall(r'"([a-z_]+)"', walk.group(1))
    return poses


def test_every_frame_the_page_animates_exists_in_the_sprite_file():
    frames = _frames()
    for pose, names in _pose_frames().items():
        assert names, f"pose {pose} names no frames"
        for name in names:
            assert name in frames, f"pose {pose} names a frame the art does not have: {name}"


def test_every_pose_the_page_asks_for_is_one_the_guide_defines():
    poses = _pose_frames()
    for pose in re.findall(r'data-pose="([^"]+)"', PAGE):
        assert pose in poses, f"the page asks for pose {pose}, which the guide does not define"


def test_every_character_the_dog_says_has_a_glyph():
    # Uppercased first, because that is what the drawing routine does before it
    # looks a character up: the font has no lowercase by design.
    characters = _font()
    for line in re.findall(r'data-say="([^"]+)"', PAGE):
        missing = {c for c in line.upper() if c not in characters}
        assert not missing, f"no glyph for {sorted(missing)} in: {line}"


def test_every_glyph_is_a_rectangle_five_rows_tall():
    # The drawing routine divides the bitmap by five to learn the glyph's width,
    # so a bitmap of any other length silently shears the letter into diagonal
    # noise rather than failing.
    for character, bitmap in _font().items():
        assert len(bitmap) % 5 == 0, f"glyph {character!r} is {len(bitmap)} pixels"


def test_the_bundle_matches_the_art_it_was_generated_from():
    """The page carries a copy of the art now, and it may not drift (D-139).

    It used to read `../argos/` directly, which is the right source and an
    unreachable path in production: the deployment is served with `site/` as its
    root, so the fetch 404s and the page renders perfectly with no dog on it.

    So the copy is generated rather than forbidden, and this is what the old
    "no second copy" rule becomes: run the generator and compare. A redrawn leg
    that never reaches the page now fails here instead of shipping.
    """
    # Loaded by path, not imported: `site` is a standard library module, so
    # `import site.art` finds the interpreter's own and not this directory.
    spec = importlib.util.spec_from_file_location("_make_pixels", SITE / "art" / "make_pixels.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    assert (SITE / "pixels.js").read_text() == generator.build(), (
        "site/pixels.js is stale: run python site/art/make_pixels.py"
    )


def test_the_page_loads_nothing_from_outside_its_own_directory():
    """Anything above `site/` is unreachable once deployed, so nothing may ask.

    The failure this exists for is silent in both directions: the asset 404s and
    the page still renders, so neither a reviewer nor a smoke test notices.
    """
    for reference in re.findall(r'(?:src|href)="([^"]+)"', PAGE):
        assert not reference.startswith("../"), f"{reference} is outside site/"
    assert "fetch(" not in GUIDE, "the page fetches art again: inline it instead"


def test_every_ornament_the_page_asks_for_is_one_the_art_defines():
    """A missing piece is a blank rectangle, which reads as a layout bug."""
    pieces = set(re.findall(r'data-(?:piece|relic)="([a-z]+)"', PAGE))
    assert pieces, "the page places no ornaments: this test cannot check anything"
    drawn = set(re.findall(r"^# ([a-z]+)$", (SITE / "art" / "ornaments.txt").read_text(), re.M))
    assert pieces <= drawn, f"the page asks for pieces nothing draws: {pieces - drawn}"


def test_the_page_carries_a_crosswalk_row_for_every_detector():
    """A fourth joint: the page names a table the code generates (T-02).

    The CLI's copy cannot go stale, because it is rendered from the registry.
    The page's copy is HTML somebody wrote once, so this is what stops a new
    detector from being absent there while present everywhere else.
    """
    for row in CROSSWALK.values():
        assert f"<td>{row.detector}</td>" in PAGE, row.detector
        for sub_id in row.subcategory_ids:
            assert f"<code>{sub_id}</code>" in PAGE, sub_id


def test_the_page_quotes_every_subcategory_it_cites():
    for sub_id, text in AI_RMF_SUBCATEGORIES.items():
        assert f"<code>{sub_id}</code> {text}" in PAGE, sub_id


def test_the_page_says_the_crosswalk_is_not_a_conformity_claim():
    assert "not a conformity claim" in PAGE
