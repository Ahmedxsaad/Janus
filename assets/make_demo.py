"""Render the README's animation from the same sprite file the window reads.

The GIF is the third thing generated out of `argos/ui/sprites/argos.txt`, after
the window's own frames and `argos/icons/icon.png`, and for the same reason: a
hand-made copy of the art goes stale the first time somebody redraws a leg. Run
this after `make_sprites.py` and the README shows what the window actually does:

    python assets/make_demo.py

The frame writer comes from `argos/icons/make_icon.py` rather than being written
again here, so the palette lives in exactly one place. GIF assembly is
ImageMagick's job: encoding an animated GIF by hand means an LZW encoder, and
`convert` is already on any machine that can build the docs.

What this reproduces from the window and what it does not:

* The **rim** is here, and it has to be. A README renders on white or on
  GitHub's near-black depending on the reader's theme, and on the dark one the
  saddle, the ears and the outline all disappear into the page: the dog loses
  its back and its head. This is docs/11-argos.md with a different
  background it cannot choose. The window's rim is 30% alpha and this one is
  opaque, because GIF has one transparent index and nothing in between; opaque
  costs nothing here, since a near-white rim on a white page is invisible and on
  a dark page it is the whole point.
* The **red collar** is here. The window paints it from a live finding, and the
  bark frames are that finding, so painting it any other colour would advertise
  the product doing the one thing it is built to make visible, in the wrong
  state.
* The **top-down light** and the **shadow** are not. Both need partial alpha to
  read as anything other than banding and a grey blob.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE.parent / "argos" / "icons"
sys.path.insert(0, str(ICONS))

import make_icon  # noqa: E402  (the path above is what makes this importable)

OUTPUT = HERE / "argos.gif"

#: The rim colour, added to the writer's palette rather than to the sprite file:
#: no frame of the art contains it, because it is not part of the character. It
#: is what the renderer puts *around* the character so a background nobody chose
#: cannot swallow it, and this GIF has to do the same job for a dark README.
make_icon.PALETTE["l"] = (0xE8, 0xED, 0xF5, 255)

#: Frames the window draws with a red collar, because a finding is live for as
#: long as they are on screen. There is no event stream behind a GIF, so the
#: repaint the renderer does off the event is done off the frame name here.
RED_COLLAR = frozenset({"alert_a", "alert_b"})

#: Blank pixels kept around the sprite, so the bark can leave the ground without
#: the ears going over the top edge. The frame stays square because the PNG
#: writer borrowed from make_icon.py takes one size for both dimensions.
MARGIN = 4

#: Sprite pixels per GIF pixel. Five keeps the whole thing at 200px, which is
#: about as wide as an inline README image wants to be.
SCALE = 5

#: The tour: a frame, how long to hold it in centiseconds (GIF's own unit, and
#: browsers round anything under 2 up), and how far off the floor it sits.
#:
#: It is the window's own timing, not a flipbook: the long idle holds and the
#: single-frame blink are what make the dog read as alive, and a GIF that ran
#: every state at one even rate would misrepresent the thing it is advertising.
#: The order tells the product's story once through, patrol to finding to rest.
TOUR: list[tuple[str, int, int]] = [
    ("idle_a", 100, 0),
    ("blink", 13, 0),
    ("idle_a", 70, 0),
    ("idle_b", 90, 0),
    # Walking his beat.
    *[(name, 11, 0) for _ in range(2) for name in ("walk_a", "walk_b", "walk_c", "walk_d")],
    # Nose down: a lineage traversal is in flight.
    *[(name, hold, 0) for _ in range(3) for name, hold in (("sniff_a", 32), ("sniff_b", 30))],
    # A finding. The second frame is the one off the ground.
    *[
        (name, hold, lift)
        for _ in range(4)
        for name, hold, lift in (("alert_a", 15, 0), ("alert_b", 17, 3))
    ],
    # Writing the incident back to the catalogue.
    *[(name, hold, 0) for _ in range(2) for name, hold in (("scribble_a", 26), ("scribble_b", 28))],
    # It stopped reproducing.
    *[(name, hold, 0) for _ in range(3) for name, hold in (("wag_a", 15), ("wag_b", 16))],
    # Nothing has changed in a while.
    ("sleep_a", 150, 0),
    ("sleep_b", 160, 0),
]


def padded(rows: list[str], lift: int) -> list[str]:
    """Return the frame centred in a larger square, raised ``lift`` pixels."""
    width = len(rows[0]) + MARGIN * 2
    blank = "." * width
    body = ["." * MARGIN + row + "." * MARGIN for row in rows]
    # The margin is split above and below, then the lift moves rows from under
    # the sprite to over it, which is what raising something on a fixed floor is.
    above = MARGIN - lift
    return [blank] * above + body + [blank] * (MARGIN + lift)


def rimmed(rows: list[str]) -> list[str]:
    """Return the frame with one light pixel outside everything solid.

    Orthogonal neighbours only, the same rule `make_sprites.py` outlines with:
    including diagonals doubles the rim at every corner, and at this size the
    corners are most of the silhouette.
    """
    solid = [[char != "." for char in row] for row in rows]
    height, width = len(rows), len(rows[0])

    def touches(y: int, x: int) -> bool:
        return any(
            0 <= y + dy < height and 0 <= x + dx < width and solid[y + dy][x + dx]
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))
        )

    return [
        "".join("l" if not solid[y][x] and touches(y, x) else rows[y][x] for x in range(width))
        for y in range(height)
    ]


def main() -> int:
    """Write argos.gif next to this script. Returns a process exit code."""
    if shutil.which("convert") is None:
        print("assets: ImageMagick is missing (try: sudo apt install imagemagick)", file=sys.stderr)
        return 1

    frames = {name: make_icon.read_frame(name) for name, _, _ in TOUR}
    with tempfile.TemporaryDirectory() as workspace:
        command = ["convert", "-loop", "0", "-dispose", "background"]
        for index, (name, hold, lift) in enumerate(TOUR):
            art = frames[name]
            if name in RED_COLLAR:
                # Both rows of the collar, exactly as the renderer repaints them.
                art = [row.replace("b", "r").replace("d", "r") for row in art]
            rows = rimmed(padded(art, lift))
            path = Path(workspace) / f"{index:03d}.png"
            path.write_bytes(make_icon.png_bytes(rows, len(rows) * SCALE))
            command += ["-delay", str(hold), str(path)]
        command.append(str(OUTPUT))
        subprocess.run(command, check=True)

    print(f"wrote {OUTPUT} ({len(TOUR)} frames, {OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
