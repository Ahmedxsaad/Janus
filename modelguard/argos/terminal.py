"""The fallback that costs nothing: Argos as one line in the terminal.

There is no binary on a Wayland session that refuses always-on-top, on a server
somebody reached over SSH, or on a machine where the wheel is not installed. The
watch still has something to say, and `rich` is already a dependency, so it says
it here.

Deliberately not pixel art: the sprite files live next to the window's frontend
in argos/ui/, which the Python wheel does not ship, and duplicating the art into
the package to draw it in a terminal would create a second copy to keep in step
with the first. A state and a sentence are what a person reading a log wants
anyway.
"""

from __future__ import annotations

from rich.console import Console

from modelguard.argos.protocol import Event

#: Colour per state, chosen so the two that mean "act now" are the only loud
#: ones and the two that mean "not working" are visibly not healthy.
_STYLES = {
    "patrolling": "green",
    "sniffing": "cyan",
    "narrating": "cyan",
    "barking": "bold red",
    "scribbling": "blue",
    "tugging": "bold yellow",
    "asleep": "dim",
    "sick": "yellow",
    "ghost": "dim red",
}


class TerminalArgos:
    """Render events as status lines, one per change of state or title."""

    def __init__(self, console: Console) -> None:
        """Write to the given console, which is the caller's, not a new one."""
        self._console = console
        self._last: tuple[str, str | None] | None = None

    def send(self, event: Event) -> bool:
        """Print this event unless it repeats the last one.

        Deduplicating is what makes this usable at a one-second poll: the
        window animates a steady state, a terminal would just scroll it.

        Returns:
            True always, so this is a drop-in for
            :meth:`~modelguard.argos.window.ArgosWindow.send`.
        """
        current = (event.state, event.title)
        if current == self._last:
            return True
        self._last = current
        style = _STYLES.get(event.state, "white")
        title = f" {event.title}" if event.title else ""
        # No square brackets around the state: rich reads those as markup and
        # would eat the one word this line exists to show.
        self._console.print(f"[{style}]argos {event.state}[/{style}]{title}")
        return True

    def close(self) -> None:
        """Nothing to close. Present so callers need not care which one they hold."""
