"""Draw the Roman ornaments the documentation page is decorated with.

Why a script rather than hand-typed grids
-----------------------------------------
The same reason `argos/ui/sprites/make_sprites.py` exists (docs/11-argos.md rule
6): a temple front, a column and an arch are symmetrical, and symmetry typed by
hand is symmetry that is one pixel out on the third try. Here a shape is drawn
on the left half and mirrored, so it is exact by construction, and the dark
edge is computed from the silhouette rather than authored, so it is even
everywhere.

Run it from anywhere; it rewrites `ornaments.txt` beside itself and can render
a preview sheet for looking at the art rather than reading it:

    python site/art/make_ornaments.py            # rewrite ornaments.txt
    python site/art/make_ornaments.py --preview  # and write preview.png

`ornaments.txt` is the committed artifact. `site/ornaments.js` reads it as an
inlined string, because the deployed site is served with `site/` as its root
and anything outside that directory is unreachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "ornaments.txt"

#: One character per pixel. Stone under a warm light, on the page's own ivory.
#: These are the page's palette rather than a scheme of the art's own, so an
#: ornament reads as carved into the document instead of pasted onto it.
PALETTE = {
    "k": "#2b1d13",  # ink: the outline, the same brown the body text is set in
    "l": "#f3e9d6",  # stone, lit
    "s": "#e0d0b0",  # stone, mid
    "d": "#c6ae8b",  # stone, in shade
    "c": "#c08a4a",  # caramel: bronze and gold fittings
    "b": "#9a5d21",  # bronze, deep
    "r": "#6e1f26",  # oxblood: used on almost nothing, on purpose
}

Grid = list[list[str]]


def blank(width: int, height: int) -> Grid:
    """A transparent canvas."""
    return [["." for _ in range(width)] for _ in range(height)]


def rect(grid: Grid, x: int, y: int, w: int, h: int, ch: str) -> None:
    """Fill an axis-aligned rectangle, clipped to the canvas."""
    for row in range(y, y + h):
        if 0 <= row < len(grid):
            for col in range(x, x + w):
                if 0 <= col < len(grid[0]):
                    grid[row][col] = ch


def disc(grid: Grid, cx: int, cy: int, radius: float, ch: str) -> None:
    """Fill a circle by distance test, which is round enough at this size."""
    for row in range(len(grid)):
        for col in range(len(grid[0])):
            if (col - cx) ** 2 + (row - cy) ** 2 <= radius * radius:
                grid[row][col] = ch


def ring(grid: Grid, cx: int, cy: int, outer: float, inner: float, ch: str) -> None:
    """Fill an annulus: the volute's coil, and the wreath's body."""
    for row in range(len(grid)):
        for col in range(len(grid[0])):
            distance = (col - cx) ** 2 + (row - cy) ** 2
            if inner * inner <= distance <= outer * outer:
                grid[row][col] = ch


def taper(grid: Grid, y0: int, y1: int, cx: int, half0: float, half1: float, ch: str) -> None:
    """A vertical shaft whose half-width runs from `half0` to `half1`.

    Real columns are not parallel-sided; the swell is what stops a drawn one
    reading as a pipe. Interpolated per row so the edge steps evenly.
    """
    span = max(1, y1 - y0)
    for row in range(y0, y1 + 1):
        half = half0 + (half1 - half0) * ((row - y0) / span)
        left = round(cx - half)
        width = round(half * 2)
        rect(grid, left, row, width, 1, ch)


def flute(grid: Grid, y0: int, y1: int, ch: str = "d", step: int = 4) -> None:
    """Cut vertical flutes into whatever solid stone is already on these rows.

    Every `step` columns of an existing run becomes a shade line, so the shaft
    reads as channelled rather than flat. Only stone is cut, so the outline and
    the background are left alone.
    """
    for row in range(y0, min(y1 + 1, len(grid))):
        for col in range(len(grid[0])):
            if grid[row][col] in "lsd" and col % step == 0:
                grid[row][col] = ch


def light(grid: Grid) -> None:
    """Turn flat stone into lit stone, from the upper left.

    Each horizontal run of stone gets a lit column on its left and a shaded one
    on its right. One pass over runs rather than a per-pixel gradient: at this
    size two extra tones are all the form a shape can carry.
    """
    for row in range(len(grid)):
        col = 0
        width = len(grid[0])
        while col < width:
            if grid[row][col] != "s":
                col += 1
                continue
            start = col
            while col < width and grid[row][col] == "s":
                col += 1
            run = col - start
            if run >= 3:
                grid[row][start] = "l"
                grid[row][col - 1] = "d"
            if run >= 7:
                grid[row][start + 1] = "l"
                grid[row][col - 2] = "d"


def mirror(grid: Grid) -> None:
    """Copy the left half onto the right, so a symmetrical form is exact."""
    width = len(grid[0])
    for row in grid:
        for col in range(width // 2):
            row[width - 1 - col] = row[col]


def outline(grid: Grid, ch: str = "k") -> None:
    """Give the silhouette its dark edge, computed rather than authored.

    Any transparent pixel sharing a side with a drawn one becomes the edge. The
    canvas needs a spare pixel of margin all round or the edge is clipped, which
    is why every piece below is authored inside its own border.
    """
    filled = [[cell != "." for cell in row] for row in grid]
    height, width = len(grid), len(grid[0])
    for row in range(height):
        for col in range(width):
            if filled[row][col]:
                continue
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                near_row, near_col = row + dy, col + dx
                if 0 <= near_row < height and 0 <= near_col < width and filled[near_row][near_col]:
                    grid[row][col] = ch
                    break


# --------------------------------------------------------------------------
# The pieces. Each returns a finished grid, already outlined.
#
# There is deliberately no two-faced head of Janus here, which is the obvious
# thing to draw for a project with this name. Three drafts of one were made and
# all three read as a jar: two mirrored profiles at this resolution leave the
# middle of the skull a blank slab, and every attempt to fill it (hair texture,
# a beard mass) turned into a comb or a blob. The arch is the better answer
# anyway and it is not a consolation prize: Janus is the god of doorways and
# gates before he is a face, the Arch of Janus is a real monument, and a good
# arch says more than a bad portrait.
# --------------------------------------------------------------------------


def volute(grid: Grid, cx: int, cy: int) -> None:
    """One scroll of an Ionic capital: a coil, not a disc.

    Drawn as two rings and an eye. A filled circle here reads as a bolt head;
    the hole in the middle is the whole difference between the two.
    """
    disc(grid, cx, cy, 5.2, "s")
    ring(grid, cx, cy, 5.2, 4.0, "d")
    ring(grid, cx, cy, 3.0, 1.8, "d")
    disc(grid, cx, cy, 1.2, "c")


def column() -> Grid:
    """An Ionic column: abacus, volutes, fluted shaft with entasis, base."""
    width, height = 36, 116
    grid = blank(width, height)
    cx = width // 2

    rect(grid, 3, 1, 30, 3, "s")  # abacus, the slab the load sits on
    rect(grid, 5, 4, 26, 2, "s")  # fillet under it
    volute(grid, 8, 11)
    volute(grid, 27, 11)
    rect(grid, 12, 7, 12, 9, "s")  # echinus, between the two coils
    rect(grid, 13, 16, 10, 2, "c")  # a bronze astragal at the neck

    taper(grid, 18, 96, cx, 8.5, 10.0, "s")
    rect(grid, 6, 97, 24, 3, "s")  # torus
    rect(grid, 5, 100, 26, 3, "s")
    rect(grid, 3, 103, 30, 4, "s")  # plinth
    rect(grid, 2, 107, 32, 5, "s")

    light(grid)
    flute(grid, 20, 94)
    outline(grid)
    return grid


def arch() -> Grid:
    """A gate: the thing Janus is actually the god of.

    A semicircular arch carried on two piers, its opening transparent so the
    page shows through it. Built by subtraction, which is how the geometry stays
    honest: lay the solid mass, then cut the soffit and the doorway out of it,
    so the springing line lands where the curve genuinely meets the pier.
    """
    width, height = 44, 62
    grid = blank(width, height)
    cx = width // 2
    springing = 26  # where the curve stops and piers start

    disc(grid, cx, springing, 16.0, "s")  # the arch mass
    rect(grid, 0, springing + 1, width, height - springing - 1, ".")
    rect(grid, 6, springing, 8, 27, "s")  # the two piers, under the springing
    rect(grid, 30, springing, 8, 27, "s")
    disc(grid, cx, springing, 9.5, ".")  # cut the soffit
    rect(grid, 14, springing, 16, 27, ".")  # and carry the opening down

    rect(grid, 1, 3, 42, 6, "s")  # entablature the arch runs up into
    rect(grid, 0, 0, width, 3, "s")  # cornice

    rect(grid, 2, 53, 40, 4, "s")  # the step the piers stand on
    rect(grid, 0, 57, width, 4, "s")

    light(grid)
    rect(grid, cx - 2, 9, 4, 7, "c")  # the keystone, in bronze
    outline(grid)
    return grid


def temple() -> Grid:
    """A temple front: pediment, architrave, four columns, stylobate."""
    width, height = 46, 52
    grid = blank(width, height)

    for step in range(11):  # the raking cornice, one row at a time
        rect(grid, 2 + step * 2, 13 - step, width - 4 - step * 4, 1, "s")
    rect(grid, 1, 14, width - 2, 4, "s")  # tympanum floor and architrave
    rect(grid, 3, 18, width - 6, 3, "s")

    for index in range(4):  # four columns, evenly spaced
        left = 5 + index * 10
        rect(grid, left, 21, 6, 24, "s")
        rect(grid, left - 1, 21, 8, 2, "s")  # capital
        rect(grid, left - 1, 43, 8, 2, "s")  # base

    rect(grid, 1, 45, width - 2, 3, "s")  # stylobate
    rect(grid, 0, 48, width, 3, "s")

    light(grid)
    for index in range(4):
        left = 5 + index * 10
        rect(grid, left + 2, 24, 1, 18, "d")
    outline(grid)
    return grid


def amphora() -> Grid:
    """A two-handled jar, drawn on its left half and mirrored."""
    width, height = 34, 56
    grid = blank(width, height)
    cx = width // 2

    rect(grid, cx - 5, 3, 10, 3, "s")  # lip
    rect(grid, cx - 3, 6, 6, 6, "s")  # neck
    for row in range(12, 22):  # shoulder, opening out
        half = 3 + (row - 11) * 0.9
        rect(grid, int(cx - half), row, int(half * 2), 1, "s")
    for row in range(22, 40):  # belly, closing back in
        half = 12 - (row - 22) * 0.52
        rect(grid, int(cx - half), row, int(half * 2), 1, "s")
    rect(grid, cx - 3, 40, 6, 6, "s")  # foot
    rect(grid, cx - 6, 46, 12, 3, "s")

    for row in range(11, 24):  # the handle, an arc from neck to belly
        offset = 4 + abs(row - 17) * 0.35
        rect(grid, int(cx - 5 - offset), row, 2, 1, "s")
    rect(grid, cx - 10, 11, 6, 2, "s")

    rect(grid, cx - 9, 26, 18, 3, "b")  # a painted band, where a black-figure
    rect(grid, cx - 7, 29, 14, 1, "c")  # vase carries its frieze

    mirror(grid)
    light(grid)
    outline(grid)
    return grid


def laurel() -> Grid:
    """A victory wreath: two boughs of pointed leaves, tied at the foot.

    The first attempt set round blobs around a ring and read as a knotted rope.
    A laurel leaf is a lance, and it sweeps: each one is drawn as a short
    tapering stroke from the bough outward, tilted along the direction the bough
    grows, which is the whole difference between a wreath and a doughnut.
    """
    from math import cos, pi, sin

    width, height = 42, 42
    grid = blank(width, height)
    cx, cy = width // 2, height // 2
    bough = 12.5

    #: The crown stays open, as a wreath is. Angles run from the foot up both
    #: sides and stop short of the top.
    gap = 0.42
    steps = 11
    for side in (-1, 1):
        for index in range(steps):
            along = gap + (pi - gap * 2.0) * (index / (steps - 1))
            base_x = cx + side * sin(along) * bough
            base_y = cy + cos(along) * bough
            # The leaf leans towards the crown, so both boughs sweep upward.
            lean = along - 0.30
            tip_x = cx + side * sin(lean) * (bough + 6.5)
            tip_y = cy + cos(lean) * (bough + 6.5)
            for step in range(9):  # taper: fat at the bough, a point out
                travel = step / 8
                px = base_x + (tip_x - base_x) * travel
                py = base_y + (tip_y - base_y) * travel
                disc(grid, round(px), round(py), 1.9 - travel * 1.4, "s")

    ring(grid, cx, cy, bough + 0.9, bough - 0.9, "b")  # the bough itself

    light(grid)
    rect(grid, cx - 2, height - 9, 4, 4, "c")  # the ribbon that ties it
    rect(grid, cx - 7, height - 6, 5, 3, "c")  # and its two loose ends
    rect(grid, cx + 2, height - 6, 5, 3, "c")
    outline(grid)
    return grid


def helmet() -> Grid:
    """A galea: bowl, cheek pieces, nasal, and a crest standing over it.

    The crest is a profile computed per column rather than a stacked slab. A
    plume is tall in the middle and falls away at both ends, and drawn as a
    rectangle it read as a dark brick balanced on a hat.
    """
    width, height = 40, 50
    grid = blank(width, height)
    cx = width // 2
    brow = 30  # where the bowl stops and the face is

    # The crest stands clear above the bowl rather than wrapping it. The first
    # draft drew it across the same rows the bowl then painted over, which is
    # how a helmet turned into a bonnet.
    for offset in range(-14, 15):
        rise = 12.0 * (1.0 - (offset / 15.0) ** 2)
        top = round(brow - 12 - rise)
        rect(grid, cx + offset, top, 1, brow - 12 - top, "r")
        if offset % 4 == 0:  # a few strands catch the light
            rect(grid, cx + offset, top, 1, 2, "c")

    disc(grid, cx, brow, 12.0, "s")  # the bowl, of which only the top half
    rect(grid, 0, brow + 1, width, height - brow - 1, ".")  # is the helmet

    rect(grid, cx - 13, brow + 1, 26, 4, "s")  # the brow band
    rect(grid, cx - 13, brow + 5, 4, 10, "s")  # cheek pieces, hanging either side
    rect(grid, cx + 9, brow + 5, 4, 10, "s")

    light(grid)

    # The void where the face is. Without it the piece is a dome on two legs,
    # which is a mushroom; a helmet is read by the dark it surrounds.
    rect(grid, cx - 9, brow + 5, 18, 10, "k")
    rect(grid, cx - 2, brow + 1, 4, 14, "s")  # the nasal, bright down the middle
    rect(grid, cx + 1, brow + 1, 1, 14, "d")
    rect(grid, cx - 13, brow + 2, 26, 2, "c")  # a bronze band across the brow
    outline(grid)
    return grid


def owl() -> Grid:
    """Athena's owl, as the Athenian coin drew it: front on, wide eyed."""
    width, height = 34, 40
    grid = blank(width, height)
    cx = width // 2

    rect(grid, cx - 9, 6, 18, 14, "s")  # head
    rect(grid, cx - 11, 8, 2, 6, "s")  # ear tufts
    rect(grid, cx + 9, 8, 2, 6, "s")
    rect(grid, cx - 8, 20, 16, 14, "s")  # body
    rect(grid, cx - 10, 22, 2, 8, "s")  # wings
    rect(grid, cx + 8, 22, 2, 8, "s")
    rect(grid, cx - 5, 34, 3, 3, "c")  # feet
    rect(grid, cx + 2, 34, 3, 3, "c")

    light(grid)
    disc(grid, cx - 4, 12, 3.2, "l")  # the eyes, which are the whole bird
    disc(grid, cx + 4, 12, 3.2, "l")
    disc(grid, cx - 4, 12, 1.6, "k")
    disc(grid, cx + 4, 12, 1.6, "k")
    rect(grid, cx - 1, 14, 2, 4, "c")  # beak
    outline(grid)
    return grid


def tripod() -> Grid:
    """A Delphic tripod, burning.

    This slot held an oil lamp for three drafts. A lamp is a low round body with
    a spout at one end and a ring at the other, and at this size that silhouette
    is a fish however the curve is drawn: the reading depends on detail the grid
    has no room for. A tripod is legs, a bowl and a flame, all of which survive
    being small, so the motif was changed rather than the drawing polished.
    """
    width, height = 38, 48
    grid = blank(width, height)
    cx = width // 2

    rect(grid, cx - 1, 20, 3, 22, "d")  # the far leg, behind the others
    for row in range(20, 43):  # the two near legs, splaying out
        travel = (row - 20) / 22
        left = round(11 - travel * 8)
        rect(grid, left, row, 3, 1, "s")
        rect(grid, width - 3 - left, row, 3, 1, "s")
    rect(grid, 1, 43, 6, 3, "s")  # feet
    rect(grid, width - 7, 43, 6, 3, "s")
    rect(grid, cx - 2, 42, 4, 3, "s")

    for row in range(12, 21):  # the bowl, a shallow taper
        half = 13.0 - (row - 12) * 0.85
        rect(grid, round(cx - half), row, round(half * 2), 1, "s")
    rect(grid, cx - 14, 10, 28, 3, "s")  # its rim

    light(grid)
    rect(grid, 6, 33, width - 12, 2, "c")  # the stretcher tying the legs

    # The fire. Three tongues, the middle one tallest, so the shape reads as
    # flame rather than as a plume.
    for offset, tall in ((-6, 5), (6, 5), (0, 9)):
        for step in range(tall):
            span = max(1, (tall - step) // 2 + 1)
            rect(grid, cx + offset - span // 2, 9 - step, span, 1, "c" if step < tall - 2 else "l")
    outline(grid)
    return grid


def scroll() -> Grid:
    """A volumen, part unrolled: the shape a document had."""
    width, height = 42, 34
    grid = blank(width, height)

    rect(grid, 6, 10, 30, 14, "l")  # the open sheet
    for row in range(13, 22, 3):  # ruled lines, standing in for text
        rect(grid, 10, row, 22, 1, "d")
    rect(grid, 2, 6, 6, 22, "s")  # the two rollers
    rect(grid, 34, 6, 6, 22, "s")
    rect(grid, 1, 4, 8, 3, "c")  # and their bronze caps
    rect(grid, 33, 4, 8, 3, "c")
    rect(grid, 1, 27, 8, 3, "c")
    rect(grid, 33, 27, 8, 3, "c")

    light(grid)
    outline(grid)
    return grid


def aqueduct() -> Grid:
    """Two tiers of arches: the engineering the whole empire ran on."""
    width, height = 46, 44
    grid = blank(width, height)

    rect(grid, 0, 2, width, 6, "s")  # the channel along the top
    for index in range(3):  # upper arcade
        left = 2 + index * 15
        rect(grid, left, 8, 13, 12, "s")
        disc(grid, left + 6, 16, 4.2, ".")
        rect(grid, left + 2, 16, 9, 5, ".")
    rect(grid, 0, 20, width, 4, "s")
    for index in range(2):  # lower arcade, wider spans
        left = 3 + index * 21
        rect(grid, left, 24, 19, 16, "s")
        disc(grid, left + 9, 34, 6.2, ".")
        rect(grid, left + 3, 34, 13, 7, ".")
    rect(grid, 0, 40, width, 3, "s")

    light(grid)
    outline(grid)
    return grid


def key() -> Grid:
    """Janus holds the key as well as the staff: the god of doors carries one."""
    width, height = 40, 22
    grid = blank(width, height)

    ring(grid, 8, 11, 7.2, 4.0, "c")  # the bow
    rect(grid, 12, 9, 22, 4, "c")  # the shank
    rect(grid, 30, 13, 3, 5, "c")  # the wards
    rect(grid, 25, 13, 3, 4, "c")

    outline(grid)
    return grid


def meander() -> Grid:
    """One unit of a running Greek key, drawn to tile edge to edge.

    The bottom rail runs the full width, so a row of these reads as one
    continuous border rather than as a row of stamps. Everything above it is the
    key itself: up, along the top, back down the inside, and in to the eye.
    Stroke and gap are both 3, which is what keeps the channel between the turns
    the same width as the line and stops the whole thing reading as a smear.
    """
    width, height = 24, 21
    grid = blank(width, height)

    rect(grid, 0, 18, width, 3, "c")  # the rail every unit shares
    rect(grid, 3, 3, 3, 15, "c")  # up from the rail, at the left
    rect(grid, 3, 3, 18, 3, "c")  # along the top, to the right
    rect(grid, 18, 6, 3, 6, "c")  # back down the inside
    rect(grid, 9, 9, 12, 3, "c")  # in along the middle, to the left
    rect(grid, 9, 12, 3, 3, "c")  # and the short drop that ends it
    return grid


def stylobate() -> Grid:
    """A tileable course of masonry: the step the whole page stands on.

    Two courses with the perpends offset, because a wall whose vertical joints
    line up is not a wall anybody built. No outline: this one repeats edge to
    edge and an outline would draw a border through the middle of the run.
    """
    width, height = 24, 16
    grid = blank(width, height)

    rect(grid, 0, 0, width, height, "s")
    rect(grid, 0, 0, width, 1, "l")  # the lit top of the step
    rect(grid, 0, 7, width, 1, "d")  # the bed joint between the courses
    rect(grid, 0, 0, 1, 7, "d")  # perpends, upper course
    rect(grid, 12, 0, 1, 7, "d")
    rect(grid, 6, 8, 1, 8, "d")  # and the lower one, half a block over
    rect(grid, 18, 8, 1, 8, "d")
    rect(grid, 0, height - 1, width, 1, "d")
    return grid


#: What gets written, in the order a reader of the file meets them.
PIECES = {
    "column": column,
    "arch": arch,
    "temple": temple,
    "amphora": amphora,
    "laurel": laurel,
    "helmet": helmet,
    "owl": owl,
    "tripod": tripod,
    "scroll": scroll,
    "aqueduct": aqueduct,
    "key": key,
    "meander": meander,
    "stylobate": stylobate,
}


def render(grids: dict[str, Grid], path: Path) -> None:
    """Write a contact sheet, so the art can be looked at rather than read.

    A repeating piece is shown repeated: a meander unit on its own says nothing
    about whether a row of them joins up, which is the only thing that matters
    about it.
    """
    from PIL import Image

    shown = dict(grids)
    unit = shown["meander"]
    shown["meander"] = [row * 4 for row in unit]

    scale = 4
    pad = 14
    columns = 4
    cells = list(shown.items())
    rows = [cells[i : i + columns] for i in range(0, len(cells), columns)]

    row_heights = [max(len(g) for _, g in row) * scale + pad * 2 for row in rows]
    width = max(sum(len(g[0]) * scale + pad * 2 for _, g in row) for row in rows)
    sheet = Image.new("RGB", (width, sum(row_heights)), "#faf5ea")

    oy = 0
    for row, row_height in zip(rows, row_heights, strict=True):
        ox = 0
        for _, grid in row:
            for y, line in enumerate(grid):
                for x, cell in enumerate(line):
                    if cell == ".":
                        continue
                    colour = PALETTE[cell]
                    rgb = tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))
                    for sy in range(scale):
                        for sx in range(scale):
                            sheet.putpixel(
                                (ox + pad + x * scale + sx, oy + pad + y * scale + sy), rgb
                            )
            ox += len(grid[0]) * scale + pad * 2
        oy += row_height
    sheet.save(path)


def main() -> None:
    """Draw every piece, write the character file, optionally the preview."""
    grids = {name: draw() for name, draw in PIECES.items()}

    lines = [
        "# Roman ornaments for the documentation page, one character per pixel.",
        "#",
        "# GENERATED by make_ornaments.py in this directory. Edit the drawing",
        "# functions there and re-run it; editing the rows below is lost.",
        "#",
        "# Palette, which is the page's own rather than the art's:",
        "#   .  transparent      k  ink #2B1D13      l  stone lit #F3E9D6",
        "#   s  stone #E0D0B0    d  stone shade #C6AE8B",
        "#   c  caramel #C08A4A  b  bronze #9A5D21    r  oxblood #6E1F26",
        "#",
        "# A `# name` line opens a piece and every line after it is a row.",
        "",
    ]
    for name, grid in grids.items():
        lines.append(f"# {name}")
        lines.extend("".join(row) for row in grid)
        lines.append("")
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(grids)} pieces)")

    if "--preview" in sys.argv:
        preview = HERE / "preview.png"
        render(grids, preview)
        print(f"wrote {preview}")


if __name__ == "__main__":
    main()
