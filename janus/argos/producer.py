"""What a producer needs to drive Argos, in one object.

A producer is any long-running process that has something to say about a
catalogue: `janus watch` (one target, Janus's own detectors) and
`janus companion` (owned assets, the general DataHub view) are the two in
this repository, and the protocol lets there be others.

Both need exactly the same four things, which is why they are here and not
copied into two commands: a surface to draw on (the window, or the terminal when
there is no window), the log handler that turns mid-scan phases into states, a
way for a click to interrupt the poll interval, and a mute the user can set from
the menu.

A window belongs to exactly one process. There is no shared bus, so two
producers cannot drive one dog: running both commands gives two dogs with no
shared state, which is honest and cheap (docs/plan/08 section 6).
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from pathlib import Path
from types import TracebackType

from rich.console import Console
from rich.markup import escape

from janus.argos.handler import ArgosHandler
from janus.argos.protocol import Command, Event
from janus.argos.terminal import TerminalArgos
from janus.argos.window import ArgosWindow, install_hint

logger = logging.getLogger(__name__)

#: How long "Mute 1h" mutes for.
MUTE_SECONDS = 3600.0


class ArgosProducer:
    """The window (or its terminal stand-in), plus the state a click changes."""

    def __init__(
        self,
        surface: ArgosWindow | TerminalArgos,
        *,
        console: Console,
    ) -> None:
        """Wrap a surface. Use :meth:`start`, which chooses the surface."""
        self._surface = surface
        self._console = console
        self._muted_until = 0.0
        self._pending_approval = False
        #: Set by a command that wants the caller's poll to happen now. The
        #: caller waits on this instead of sleeping, so "Scan now" means now and
        #: not "in up to thirty seconds".
        self.wake = threading.Event()
        self._handler = ArgosHandler(self.send)
        logging.getLogger("janus").addHandler(self._handler)

    @classmethod
    def start(cls, console: Console, *, window: bool = True) -> ArgosProducer:
        """Open the best available surface.

        Args:
            console: Where the terminal fallback writes, and where the one line
                explaining a missing binary goes.
            window: False to skip the window entirely and use the terminal, for
                a session that has no display at all.

        Returns:
            A producer. Never None: with no binary and no window, the terminal
            surface still renders every event.
        """
        # The window has to be spawned before we know whether there is one, and
        # spawning it needs the callback that only the producer can serve. One
        # cell closes the circle; a command cannot arrive in the microseconds
        # between the two lines, and if one did it would be dropped, not lost.
        bound: list[ArgosProducer] = []

        def sink(command: Command) -> None:
            if bound:
                bound[0].handle(command)

        surface: ArgosWindow | TerminalArgos | None = None
        if window:
            surface = ArgosWindow.open(sink)
            if surface is None:
                # escape: the non-Linux hint ends in "janus-datahub[pet]", and
                # rich would read [pet] as a style tag and drop it (D-151).
                console.print(f"[dim]argos: no window binary found. {escape(install_hint())}[/dim]")
        if surface is None:
            surface = TerminalArgos(console)
        producer = cls(surface, console=console)
        bound.append(producer)
        return producer

    @property
    def muted(self) -> bool:
        """True while the user has muted writes from the window's menu."""
        return time.monotonic() < self._muted_until

    def send(self, event: Event) -> None:
        """Draw one event, and remember whether it left an approval pending."""
        if event.state == "tugging":
            self._pending_approval = True
        elif event.state in {"patrolling", "barking", "sick", "recovered"}:
            self._pending_approval = False
        self._surface.send(event)

    def handle(self, command: Command) -> None:
        """Act on one validated command from the window.

        An explicit branch per name, no dispatch table keyed by user input and
        nothing that reaches a shell: this is the one channel that flows into
        the process and it can trigger a write (docs/plan/08 section 6).
        """
        if command.name == "scan_now":
            self.wake.set()
        elif command.name == "mute":
            self._muted_until = time.monotonic() + MUTE_SECONDS
            self.send(Event(state="muted", title="muted for 1h, still watching"))
        elif command.name == "approve":
            self._approve()
        elif command.name == "open_datahub":
            self._open(command.args.get("entity"))
        elif command.name == "drop":
            self._dropped(command.args.get("path"))

    def _approve(self) -> None:
        """Answer the Approve button honestly.

        `watch` and `companion` are unattended and approve their own writes, so
        there is normally nothing here to approve. Saying so beats a button that
        looks like it did something.
        """
        if self._pending_approval:
            self.wake.set()
            return
        self.send(Event(state="patrolling", title="nothing is waiting for approval"))

    def _open(self, entity: str | None) -> None:
        """Open an entity in the browser, when the event carried a link."""
        if not entity:
            return
        from janus.argos.events import entity_link

        url = entity_link(entity)
        if url is None:
            self.send(Event(state="patrolling", title="set JANUS_DATAHUB_UI_URL to open"))
            return
        webbrowser.open(url)

    def _dropped(self, path: str | None) -> None:
        """Acknowledge a dropped file and poll now.

        ponytail: a drop triggers a poll of the target this producer already
        watches; it does not retarget. Retargeting a running watch would change
        what `watch` means (it is defined by the target it started with), and
        `link --infer` works from the model in the graph rather than from a
        file on disk, so there is nothing in the dropped file to infer from.
        """
        if not path:
            return
        name = Path(path).name
        logger.info("argos: file dropped name=%s", name)
        self.send(Event(state="sniffing", title=f"dropped {name}, polling now"))
        self.wake.set()

    def close(self) -> None:
        """Detach the log handler and close the surface."""
        logging.getLogger("janus").removeHandler(self._handler)
        self._surface.close()

    def __enter__(self) -> ArgosProducer:
        """Return self, so a command can own the producer with `with`."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close however the block ended."""
        self.close()
