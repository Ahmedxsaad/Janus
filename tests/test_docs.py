"""The two hand-written documents that claim to cover the whole CLI.

`site/CLAUDE.md` rule 5 says the page changes in the same commit as the README
when a command changes, and the README says of `site/` that it covers "every
command". Both are promises a human keeps by remembering, which is how T-12 and
T-13 shipped two commands that appeared in neither: nothing failed, the phase
looked done, and the artifact most likely to matter to a governance reader was
reachable only by reading `--help`.

This is the same joint `test_site.py` covers for the crosswalk table, applied to
the command list: a command cannot be added to ModelGuard without being written
down where a user would look for it.
"""

from __future__ import annotations

from pathlib import Path

from modelguard.cli import app

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
PAGE = (ROOT / "site" / "index.html").read_text()


def _command_names() -> list[str]:
    """Every subcommand `modelguard` exposes, as a user would type it."""
    names = [
        command.name or command.callback.__name__
        for command in app.registered_commands
        if command.callback is not None
    ]
    assert names, "no commands found: this test cannot check anything"
    return sorted(names)


def test_the_readme_shows_every_command() -> None:
    """A command absent from the README is one nobody discovers."""
    missing = [name for name in _command_names() if f"modelguard {name}" not in README]
    assert not missing, f"README.md never shows: {missing}"


def test_the_documentation_page_shows_every_command() -> None:
    """The page claims to cover every command; hold it to that."""
    missing = [name for name in _command_names() if f"modelguard {name}" not in PAGE]
    assert not missing, f"site/index.html never shows: {missing}"
