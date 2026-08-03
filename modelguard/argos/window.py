"""Owning the Argos window: spawn it, feed it, read what the user clicked.

The window is a child process, and the transport is its own stdin and stdout.
No port is bound, no shared secret is invented, there is no CORS configuration
and no auth path to review, the window cannot be reached by anything else on the
machine, it dies with its parent, and the DataHub token never leaves this
process (docs/plan/08 section 6).

The one rule that makes stdio safe is here rather than in a comment somewhere:
the parent reads the child's stdout on its own thread, always. A parent that
only writes eventually blocks, because the child's stdout pipe fills, the child
blocks writing into it, it stops reading its stdin, and the parent blocks
writing there. Both processes then sleep forever. The reader thread removes the
whole failure mode.
"""

from __future__ import annotations

import contextlib
import logging
import platform
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from modelguard.argos.protocol import Command, Event
from modelguard.env import ConfigError, optional_value

logger = logging.getLogger(__name__)

#: The executable the wheel, the .deb and `cargo install` all install. Not the
#: bare name "argos": that is taken on PyPI and is far too generic to claim on
#: somebody's PATH (docs/plan/08 section 6b).
BINARY_NAME = "modelguard-argos"

#: An explicit path to the binary, for a build that is not on PATH. Read here
#: and nowhere else, through env.py, which is the only module that touches the
#: environment (root CLAUDE.md rule 6).
ENV_BINARY = "MODELGUARD_ARGOS_BIN"

#: How long the child gets to exit on its own before it is killed.
_SHUTDOWN_GRACE_SECONDS = 3.0


def install_hint() -> str:
    """Return the install instruction for this platform, in one line.

    Linux is the odd one out, and the reason is policy rather than effort: the
    binary links the system webkit2gtk, which no manylinux tag permits, so PyPI
    will not accept the wheel and the bundle from GitHub Releases is the route.
    """
    if platform.system() == "Linux":
        return (
            "Install the Argos window from the .deb or .AppImage on the GitHub "
            "release (the Linux wheel cannot be published: the binary links "
            "system webkit2gtk, which manylinux does not allow), or build it "
            "with `cargo build --release` in argos/ and point "
            f"{ENV_BINARY} at the result."
        )
    return 'Install the Argos window with: pip install "modelguard-datahub[pet]"'


def resolve_binary() -> Path | None:
    """Return the Argos executable, or None when it is not installed.

    Looks at the explicit override first, then PATH, which covers the wheel, the
    .deb and a `cargo install` equally. None is not an error: the caller falls
    back to the terminal sprite, which needs no binary at all.

    Raises:
        ConfigError: The override is set but does not point at a file. A
            configured path that is wrong is a typo to fix, not a reason to
            silently use something else.
    """
    override = optional_value(ENV_BINARY)
    if override is not None:
        path = Path(override).expanduser()
        if not path.is_file():
            raise ConfigError(f"{ENV_BINARY} does not point at a file. {install_hint()}")
        return path
    found = shutil.which(BINARY_NAME)
    return Path(found) if found else None


class ArgosWindow:
    """A running Argos window, and the two pipes that talk to it."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        on_command: Callable[[Command], None],
    ) -> None:
        """Wrap an already-spawned window. Use :meth:`open` instead."""
        self._process = process
        self._on_command = on_command
        self._reader = threading.Thread(
            target=self._read_commands, name="argos-reader", daemon=True
        )
        self._reader.start()

    @classmethod
    def open(
        cls,
        on_command: Callable[[Command], None],
        *,
        binary: Path | None = None,
    ) -> ArgosWindow | None:
        """Spawn the window, or return None when there is no binary to spawn.

        Args:
            on_command: Called on the reader thread for every valid command the
                user clicked. It must not raise; if it does, the exception is
                logged and the reader keeps going, because one bad handler must
                not cost the user their window.
            binary: Override the resolved path. For tests, which spawn a stub
                rather than a real window.

        Returns:
            The window, or None when no binary is installed.
        """
        executable = binary or resolve_binary()
        if executable is None:
            return None
        # A resolved path, no shell, and no user input in the argument list.
        process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # stderr is deliberately not captured: the child logs there, and so
            # do GTK and WebKit. Inheriting it puts those lines in the terminal
            # the operator is already watching, and keeps them out of the
            # command channel, which is what stdout is for.
            text=True,
            bufsize=1,
        )
        logger.info("argos window started pid=%s", process.pid)
        return cls(process, on_command)

    @property
    def alive(self) -> bool:
        """True while the window process is still running."""
        return self._process.poll() is None

    def send(self, event: Event) -> bool:
        """Write one event to the window. Returns False once it is gone.

        A closed window is an ordinary outcome, not an error: the user is
        allowed to close it, and the producer keeps working without it.
        """
        stdin = self._process.stdin
        if stdin is None or not self.alive:
            return False
        try:
            stdin.write(event.to_json() + "\n")
            stdin.flush()
        except (BrokenPipeError, ValueError):
            # ValueError: the pipe was closed by close() while we were writing.
            return False
        return True

    def _read_commands(self) -> None:
        """Drain the child's stdout forever, dispatching what parses."""
        stdout = self._process.stdout
        if stdout is None:
            return
        for line in stdout:
            command = Command.parse(line)
            if command is None:
                # Not a protocol violation worth shouting about: anything that
                # is not one of ours lands here, including a library writing to
                # the wrong stream.
                logger.debug("argos: dropped a line that is not a command")
                continue
            try:
                self._on_command(command)
            except Exception:  # noqa: BLE001 - a handler must never kill the reader
                logger.warning("argos: command handler failed for %s", command.name, exc_info=True)

    def close(self) -> None:
        """Close the window and wait for the process to go.

        Terminate, then kill: a window that ignores SIGTERM must not keep a
        `modelguard watch` from exiting, and there is nothing in it worth
        saving.
        """
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._process.kill()
        for pipe in (self._process.stdin, self._process.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()

    def __enter__(self) -> ArgosWindow:
        """Return self, so a producer can own the window with `with`."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the window however the block ended."""
        self.close()
