"""Argos: the desktop watchdog, and the wire that drives it.

Five modules, one job each:

* :mod:`~modelguard.argos.protocol` is the contract, events out and commands in.
* :mod:`~modelguard.argos.events` turns what a detector found into what the dog
  does.
* :mod:`~modelguard.argos.window` owns the window process and its two pipes.
* :mod:`~modelguard.argos.terminal` is the fallback when there is no window.
* :mod:`~modelguard.argos.handler` drives the mid-scan states from the log.

The renderer itself is not Python: it is the Tauri binary built from argos/ in
the repository root, which reads these events on stdin (docs/plan/08).
"""

from modelguard.argos.events import from_report, unreachable
from modelguard.argos.handler import ArgosHandler
from modelguard.argos.protocol import Command, Event, Hop
from modelguard.argos.terminal import TerminalArgos
from modelguard.argos.window import ArgosWindow, install_hint, resolve_binary

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
