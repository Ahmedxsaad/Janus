"""The MCP server's pure parts, checked offline.

Connecting is not tested here: every tool opens a real DataHub connection, which
is exactly what ``tests/integration/test_mcp_server.py`` is for. What is worth
pinning offline is the one function with no network in it: turning a finding into
the plain text an MCP client hands to its own model. A markup character or a
missing field here would silently degrade every conversation the server has.
"""

from __future__ import annotations

import asyncio

import pytest

from janus import mcp_server
from janus.mcp_server import _render_finding, create_server
from tests.conftest import make_finding, make_leakage_finding, make_schema_drift_finding


def test_a_leakage_finding_names_the_path_and_the_model():
    text = _render_finding(make_leakage_finding(live=True))

    assert "leak path:" in text
    assert "prior_default_flag" in text
    assert "default_status" in text
    assert "Credit Risk v3" in text
    assert "LIVE" in text


def test_a_non_live_leakage_finding_does_not_claim_live():
    text = _render_finding(make_leakage_finding(live=False))

    assert "LIVE" not in text


def test_a_freshness_finding_states_the_lag_and_the_sla():
    text = _render_finding(make_finding(lag_hours=30.0, sla_hours=6.0))

    assert "30.0h" in text
    assert "6.0h" in text
    assert "blast radius:" in text


def test_a_freshness_finding_with_no_model_still_renders():
    text = _render_finding(make_finding(with_model=False))

    assert "0 model(s)" in text


def test_a_schema_drift_finding_lists_every_change():
    text = _render_finding(make_schema_drift_finding())

    assert "Credit Risk v3" in text
    assert "applicant_income" in text
    assert "debt_to_income" in text
    assert "updated_at" in text


def test_rendered_text_carries_no_rich_markup():
    """An MCP client's model reads this raw; a stray [bold] tag would confuse it."""
    for finding in (
        make_finding(),
        make_leakage_finding(),
        make_schema_drift_finding(),
    ):
        text = _render_finding(finding)
        assert "[" not in text, f"markup leaked into: {text!r}"


def _registered_tools() -> list:
    """The tools as an MCP client would actually see them, over the real API."""
    return asyncio.run(create_server().list_tools())


def test_every_tool_is_read_only():
    """The one property the whole module exists to guarantee.

    Checked at the server's actual registration, over the same ``list_tools``
    call a client makes, not by re-reading the constant: a tool registered
    without the read-only annotation would pass a check that only inspected the
    constant's own value.
    """
    for tool in _registered_tools():
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.readOnlyHint is True, f"{tool.name} is not read-only"


def test_the_missing_extra_is_reported_as_a_command_not_a_traceback(monkeypatch):
    """`janus-mcp` on a plain install used to die with an ImportError (D-155).

    The console script is registered unconditionally, so somebody who ran
    `pip install janus-datahub` and typed `janus-mcp` got a traceback quoting a
    site-packages path. Every other optional extra here names itself and the
    command that installs it, and this one is the likeliest to be reached by
    accident: an MCP client launches it, so the traceback lands in that
    client's log rather than on a terminal.
    """
    import builtins

    real_import = builtins.__import__

    def _no_mcp(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("mcp"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_mcp)

    with pytest.raises(SystemExit) as caught:
        mcp_server.main()

    assert '"janus-datahub[mcp]"' in str(caught.value)


def test_the_server_exposes_exactly_the_three_detectors():
    """Pins the exact set of tools.

    A tool added later without a test here would still be caught: this fails the
    moment the set changes, forcing whoever added it to update the assertion and,
    with it, decide whether the new tool is read-only.
    """
    names = {tool.name for tool in _registered_tools()}

    assert names == {"check_leakage", "check_freshness", "check_gate"}
