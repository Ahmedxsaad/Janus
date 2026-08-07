"""Argos: the desktop watchdog, and the wire that drives it.

Five modules, one job each:

* :mod:`~janus.argos.protocol` is the contract, events out and commands in.
* :mod:`~janus.argos.events` turns what a detector found into what the dog
  does.
* :mod:`~janus.argos.window` owns the window process and its two pipes.
* :mod:`~janus.argos.terminal` is the fallback when there is no window.
* :mod:`~janus.argos.handler` drives the mid-scan states from the log.

The renderer itself is not Python: it is the Tauri binary built from argos/ in
the repository root, which reads these events on stdin (docs/11-argos.md).
"""

from janus.argos.events import from_report, unreachable
from janus.argos.handler import ArgosHandler
from janus.argos.protocol import Command, Event, Hop
from janus.argos.terminal import TerminalArgos
from janus.argos.window import ArgosWindow, install_hint, resolve_binary

__all__ = [
    "ArgosHandler",
    "ArgosWindow",
    "Command",
    "Event",
    "Hop",
    "TerminalArgos",
    "from_report",
    "install_hint",
    "resolve_binary",
    "unreachable",
]
