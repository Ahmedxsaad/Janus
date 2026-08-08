"""Check that the built Argos binary reads its stdin and stays up.

The transport is the part of this that a compile cannot prove and that differs
most between platforms: a Windows release build has no console, so the whole
thing rests on the parent's inherited pipe handles reaching a GUI-subsystem
process (docs/11-argos.md).

This is not a rendering test. CI has no display, so the window may well fail to
open; what must hold is that the process accepts an event without dying on it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "argos" / "target" / "release"
EVENT = '{"v": 1, "state": "patrolling", "title": "ci smoke"}\n'

#: Long enough for the process to reach its stdin loop, short enough that a CI
#: job never waits on it.
SETTLE_SECONDS = 5.0


def binary() -> Path:
    """Return the built executable for this platform."""
    for name in ("janus-argos", "janus-argos.exe"):
        candidate = RELEASE / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"no Argos binary in {RELEASE}")


def main() -> int:
    """Feed one event in and report whether the process survived it."""
    executable = binary()
    process = subprocess.Popen(
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    try:
        process.stdin.write(EVENT)
        process.stdin.flush()
        time.sleep(SETTLE_SECONDS)
        if process.poll() is not None:
            # A headless runner with no display is allowed to refuse a window;
            # what it must not do is refuse the event before it gets there.
            print(f"argos exited with {process.returncode} after one event")
            return 1
        print(f"argos accepted an event and is running: {executable}")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
