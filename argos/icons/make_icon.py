"""Render the application icons from the sprite file, with no image library.

The icon has to be a binary image: every OS bundler wants one, and neither the
wheel nor the .deb can carry a text file there. So both are generated from the
same art everything else reads, and regenerating them after a redraw is one
command:

    python argos/icons/make_icon.py

Two formats, because Windows will not take the PNG. `tauri-build` compiles a
Windows resource file into the executable and fails the build outright when
`icons/icon.ico` is absent, which is not a bundling nicety: it stops
`cargo build` on that platform.

Standard library only (zlib and struct write a PNG in about thirty lines, and
an ICO is a header and a bottom-up DIB), so this runs in a clean clone without
installing anything.
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
from pathlib import Path

#: Matches the palette legend at the top of the sprite file.
PALETTE: dict[str, tuple[int, int, int, int]] = {
    "k": (0x16, 0x0F, 0x0A, 255),
    "w": (0xD9, 0x93, 0x47, 255),
    "g": (0xA9, 0x6B, 0x2C, 255),
    "a": (0x2A, 0x21, 0x19, 255),
    "o": (0x15, 0x10, 0x0C, 255),
    "b": (0x26, 0x68, 0xE8, 255),
    "d": (0x16, 0x40, 0x8F, 255),
    "r": (0xF2, 0x25, 0x25, 255),
    ".": (0x00, 0x00, 0x00, 0),
}

HERE = Path(__file__).resolve().parent
SPRITES = HERE.parent / "ui" / "sprites" / "argos.txt"
PNG_OUTPUT = HERE / "icon.png"
ICO_OUTPUT = HERE / "icon.ico"

#: A multiple of the sprite's own size, so every pixel scales to an exact
#: square and nothing is resampled. 768 is 32 x 24.
SIZE = 768

#: The sizes inside the .ico, every one an exact power-of-two multiple of the
#: 32-row sprite, so these are scaled and never resampled either. 16 is left
#: out deliberately: it would be the one entry that had to throw pixels away,
#: and Windows downscales the 32 for a small slot perfectly well.
ICO_SIZES = (32, 64, 128, 256)


def read_frame(name: str) -> list[str]:
    """Return the named frame's rows from the sprite file."""
    rows: list[str] = []
    current: str | None = None
    for raw in SPRITES.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            match = re.fullmatch(r"#\s*([a-z_]+)\s*", line)
            current = match.group(1) if match else None
        elif line.strip() and current == name:
            rows.append(line)
    if not rows:
        raise SystemExit(f"frame {name!r} not found in {SPRITES}")
    return rows


def scaled_rows(rows: list[str], size: int) -> list[list[tuple[int, int, int, int]]]:
    """Return ``size`` rows of ``size`` RGBA pixels, nearest-neighbour.

    ``size`` must be a whole multiple of the sprite's own row count, which is
    what keeps every sprite pixel an exact square.
    """
    scale = size // len(rows)
    out: list[list[tuple[int, int, int, int]]] = []
    for row in rows:
        line = [PALETTE[char] for char in row for _ in range(scale)]
        out.extend([line] * scale)
    return out


def png_bytes(rows: list[str], size: int) -> bytes:
    """Encode the frame as an RGBA PNG scaled up to ``size`` pixels square."""
    raw = bytearray()
    for line in scaled_rows(rows, size):
        # Filter type 0 (None) per scanline: the image is tiny and flat, so
        # nothing here is worth the complexity of a real filter choice.
        raw.append(0)
        for pixel in line:
            raw.extend(pixel)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def dib_bytes(rows: list[str], size: int) -> bytes:
    """Encode one .ico entry: a BITMAPINFOHEADER, BGRA pixels, and a mask.

    Uncompressed DIB rather than an embedded PNG. Both are legal in a modern
    .ico, but the DIB is what every resource compiler back to the ones in older
    Windows SDKs will read, and this file is consumed by whichever `rc.exe`
    happens to be on the build machine.
    """
    pixels = scaled_rows(rows, size)
    # biHeight is doubled because the DIB holds the colour image and the mask
    # stacked; both are stored bottom-up, hence the reversed() below.
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)

    colour = bytearray()
    for line in reversed(pixels):
        for red, green, blue, alpha in line:
            colour.extend((blue, green, red, alpha))

    # The 1-bit AND mask predates the alpha channel and is redundant with it,
    # but leaving it blank makes the transparent border opaque black wherever
    # something ignores alpha, so it is filled from alpha anyway. A set bit
    # means transparent. Rows are padded to a 4-byte boundary.
    stride = ((size + 31) // 32) * 4
    mask = bytearray()
    for line in reversed(pixels):
        bits = bytearray(stride)
        for x, pixel in enumerate(line):
            if pixel[3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask.extend(bits)

    return header + bytes(colour) + bytes(mask)


def ico_bytes(rows: list[str], sizes: tuple[int, ...]) -> bytes:
    """Encode the frame as a multi-resolution Windows .ico."""
    images = [dib_bytes(rows, size) for size in sizes]
    directory = struct.pack("<HHH", 0, 1, len(images))
    offset = len(directory) + 16 * len(images)
    entries = bytearray()
    for size, image in zip(sizes, images, strict=True):
        # 256 is written as 0: the width and height fields are single bytes.
        side = 0 if size >= 256 else size
        entries.extend(struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    return directory + bytes(entries) + b"".join(images)


def main() -> int:
    """Write icon.png and icon.ico next to this script. Returns an exit code."""
    frame = read_frame("idle_a")
    PNG_OUTPUT.write_bytes(png_bytes(frame, SIZE))
    ICO_OUTPUT.write_bytes(ico_bytes(frame, ICO_SIZES))
    for path in (PNG_OUTPUT, ICO_OUTPUT):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
