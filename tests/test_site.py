"""The documentation page reads the same art the window reads, so it can drift.

Nothing here renders anything: these check the three joints where `site/` names
something that lives elsewhere. A page whose dog silently fails to appear, or
whose bubble drops half a sentence because a character has no glyph, looks fine
in review and broken to a reader.
"""

from __future__ import annotations

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


def test_the_page_reads_the_one_copy_of_the_art():
    # If somebody vendors a second argos.txt into site/, the window and the page
    # start drifting apart and neither review nor the tests above would notice.
    assert '"../argos/ui/sprites/argos.txt"' in GUIDE
    assert '"../argos/ui/sprites.js"' in PAGE
    assert not list(SITE.glob("**/*.txt"))


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
