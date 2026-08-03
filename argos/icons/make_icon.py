"""Render the application icon from the sprite file, with no image library.

The icon has to be a PNG: every OS bundler wants one, and neither the wheel nor
the .deb can carry a text file there. So it is generated from the same art
everything else reads, and regenerating it after a redraw is one command:

    python argos/icons/make_icon.py

Standard library only (zlib and struct write a PNG in about thirty lines), so
this runs in a clean clone without installing anything.
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
from pathlib import Path

#: Matches the palette legend at the top of the sprite file.
PALETTE: dict[str, tuple[int, int, int, int]] = {
    "k": (0x12, 0x23, 0x3F, 255),
    "w": (0xF7, 0xF7, 0xF7, 255),
    "a": (0xF3, 0x9F, 0x19, 255),
    "b": (0x18, 0x57, 0xD2, 255),
    "d": (0x1B, 0x49, 0xA0, 255),
    "r": (0xE9, 0x01, 0x01, 255),
    ".": (0x00, 0x00, 0x00, 0),
}

HERE = Path(__file__).resolve().parent
SPRITES = HERE.parent / "ui" / "sprites" / "argos.txt"
OUTPUT = HERE / "icon.png"

#: 512 is the largest size the bundlers ask for, and every smaller one is a
#: clean integer downscale of it because the art is 16x16.
SIZE = 512


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


def png_bytes(rows: list[str], size: int) -> bytes:
    """Encode the frame as an RGBA PNG scaled up to ``size`` pixels square."""
    scale = size // len(rows)
    raw = bytearray()
    for row in rows:
        line = bytearray()
        for char in row:
            line.extend(bytes(PALETTE[char]) * scale)
        # Filter type 0 (None) per scanline: the image is tiny and flat, so
        # nothing here is worth the complexity of a real filter choice.
        for _ in range(scale):
            raw.append(0)
            raw.extend(line)

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


def main() -> int:
    """Write icon.png next to this script. Returns a process exit code."""
    OUTPUT.write_bytes(png_bytes(read_frame("idle_a"), SIZE))
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
