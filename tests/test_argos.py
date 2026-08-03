"""The protocol, the art, the transport, and the log-driven states.

What CI can check without a display, which is everything except whether the
window is actually on top of the other windows (docs/plan/08 section 11).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from rich.console import Console

from modelguard.agent.pipeline import ScanReport
from modelguard.argos import events
from modelguard.argos.handler import ArgosHandler
from modelguard.argos.producer import ArgosProducer
from modelguard.argos.protocol import (
    COMMANDS,
    MAX_ARGUMENT_LENGTH,
    STATES,
    Command,
    Event,
    Hop,
)
from modelguard.argos.terminal import TerminalArgos
from modelguard.argos.window import ArgosWindow, resolve_binary
from modelguard.logs import phase
from modelguard.models import Finding, TrustBand, TrustScore

from .conftest import make_finding

UI = Path(__file__).resolve().parent.parent / "argos" / "ui"
PALETTE_CHARS = set(".kwabdr")


def _frames() -> dict[str, list[str]]:
    """Parse the sprite file the same way the browser and the icon script do."""
    frames: dict[str, list[str]] = {}
    name: str | None = None
    for raw in (UI / "sprites" / "argos.txt").read_text().splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            stripped = line.lstrip("#").strip()
            name = stripped if stripped.replace("_", "").isalpha() else None
        elif line.strip() and name:
            frames.setdefault(name, []).append(line)
    return frames


# --- the art -----------------------------------------------------------------


def test_every_frame_is_sixteen_rows_of_sixteen_palette_characters():
    frames = _frames()
    assert frames, "no frames parsed out of the sprite file"
    for name, rows in frames.items():
        assert len(rows) == 16, f"{name} has {len(rows)} rows"
        assert {len(row) for row in rows} == {16}, f"{name} has a row of the wrong width"
        assert set("".join(rows)) <= PALETTE_CHARS, f"{name} uses a colour outside the palette"


def test_no_frame_paints_red_because_red_is_state_not_decoration():
    # The renderer repaints the collar red when a finding is live. Art that was
    # already red would make a healthy graph look like a failing one.
    for name, rows in _frames().items():
        assert "r" not in "".join(rows), f"{name} paints red into the art"


def test_the_state_machine_names_a_frame_that_exists():
    frames = _frames()
    script = (UI / "argos.js").read_text()
    for state in STATES:
        assert f"{state}:" in script, f"argos.js has no rule for the {state} state"
    for quoted in ("idle_a", "idle_b", "walk_a", "walk_b", "sniff", "alert_a", "sleep"):
        assert quoted in frames, f"argos.js draws {quoted}, which the sprite file does not define"


# --- the protocol ------------------------------------------------------------


def test_an_event_round_trips_through_json():
    event = Event(
        state="barking",
        title="loans_raw is 14h stale",
        entity="urn:li:dataset:(x,loans_raw,PROD)",
        severity="high",
        link="http://localhost:9002/dataset/urn",
        path=(Hop(urn="urn:li:dataset:(x,loans_raw,PROD)", column="income"), Hop(urn="urn:li:m")),
    )
    restored = Event.from_dict(json.loads(event.to_json()))
    assert restored == event


def test_an_event_omits_the_fields_that_carry_nothing():
    assert Event(state="patrolling").to_dict() == {"v": 1, "state": "patrolling"}


def test_a_producer_cannot_invent_a_state():
    # The window renders an unknown state as patrolling, which would look like
    # health. A producer must therefore fail at the source instead.
    with pytest.raises(ValueError, match="unknown Argos state"):
        Event(state="somersaulting")


def test_the_fixture_replays_every_state_the_window_can_draw():
    lines = [
        line
        for line in (UI / "fixture.jsonl").read_text().splitlines()
        if line.strip() and not line.startswith("//")
    ]
    seen = set()
    unknown = 0
    for line in lines:
        payload = json.loads(line)
        try:
            seen.add(Event.from_dict(payload).state)
        except ValueError:
            unknown += 1
    assert seen == STATES, f"fixture misses {sorted(STATES - seen)}"
    assert unknown == 1, "the fixture must carry exactly one unknown state, for the tolerance demo"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "Gtk-WARNING **: cannot open display",  # a library on the wrong stream
        '{"cmd": "scan_now"',  # a truncated write
        "[1, 2, 3]",
        '{"cmd": "rm_rf"}',  # a name we do not implement
        '{"cmd": "scan_now", "args": {"shell": "id"}}',  # an argument key we do not expect
        '{"cmd": "scan_now", "args": {"entity": 42}}',
        '{"cmd": "drop", "args": {"path": "' + "x" * (MAX_ARGUMENT_LENGTH + 1) + '"}}',
    ],
)
def test_the_command_channel_drops_everything_that_is_not_ours(line: str):
    assert Command.parse(line) is None


def test_a_valid_command_parses_with_its_arguments():
    command = Command.parse('{"cmd": "open_datahub", "args": {"entity": "urn:li:dataset:(a,b,c)"}}')
    assert command == Command(name="open_datahub", args={"entity": "urn:li:dataset:(a,b,c)"})


def test_every_command_the_window_offers_is_one_the_protocol_accepts():
    html = (UI / "index.html").read_text()
    offered = {part.split('"')[0] for part in html.split('data-cmd="')[1:]}
    assert offered <= COMMANDS, f"the window offers {offered - COMMANDS}, which nothing handles"


# --- events built from findings ----------------------------------------------


def test_a_finding_becomes_a_barking_event_carrying_its_walk():
    finding = make_finding()
    event = events.from_report(_report_with(finding))
    assert event.state == "barking"
    assert event.title == finding.title
    assert event.severity == str(finding.severity)
    assert event.path[0].urn == finding.blast_radius.failing_table_urn
    assert event.path[-1].column, "the model hop must name the feature that put it at risk"


def test_a_clean_scan_patrols():
    assert events.from_report(_report_with()).state == "patrolling"


def test_a_dropped_trust_band_makes_the_dog_sick_even_with_no_finding():
    from modelguard.agent.pipeline import ScanReport, TrustWrite

    report = ScanReport(
        run_id="r",
        dry_run=True,
        trust=(
            TrustWrite(
                model_urn="urn:li:mlModel:(x,credit_risk_v3,PROD)",
                model_name="credit_risk_v3",
                score=TrustScore(value=35, band=TrustBand.AT_RISK, deductions={}),
            ),
        ),
    )
    event = events.from_report(report)
    assert event.state == "sick"
    assert "credit_risk_v3" in (event.title or "")


def test_no_link_is_offered_when_the_ui_url_is_not_configured(monkeypatch):
    monkeypatch.delenv(events.ENV_UI_URL, raising=False)
    urn = "urn:li:dataset:(urn:li:dataPlatform:postgres,analytics.loans_raw,PROD)"
    assert events.entity_link(urn) is None


def test_a_configured_ui_url_builds_a_link_for_the_entity_type(monkeypatch):
    monkeypatch.setenv(events.ENV_UI_URL, "http://localhost:9002/")
    urn = "urn:li:dataset:(urn:li:dataPlatform:postgres,analytics.loans_raw,PROD)"
    assert events.entity_link(urn) == f"http://localhost:9002/dataset/{urn}"


def _report_with(*findings: Finding) -> ScanReport:
    """Build a ScanReport around zero or more findings, as the pipeline would."""
    from modelguard.agent.narrate import Narrative, NarrativeSource
    from modelguard.agent.pipeline import FindingWrites, ScanReport

    return ScanReport(
        run_id="r",
        dry_run=True,
        writes=tuple(
            FindingWrites(finding=finding, narrative=Narrative("x", NarrativeSource.TEMPLATE))
            for finding in findings
        ),
    )


# --- the transport -----------------------------------------------------------


STUB = """#!{python}
import json
import sys
# What the real window does, minus the pixels: read events on stdin, and send a
# command back on stdout. It answers the event rather than speaking first, so a
# producer that stops sending events is a test that goes red.
for line in sys.stdin:
    event = json.loads(line)
    sys.stdout.write(json.dumps({{"cmd": "scan_now", "args": {{"entity": event["state"]}}}}))
    sys.stdout.write("\\n")
    sys.stdout.flush()
"""


@pytest.fixture
def stub_window(tmp_path: Path) -> Path:
    """A stand-in binary: speaks the protocol, opens no window."""
    path = tmp_path / "stub-argos"
    path.write_text(STUB.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_the_window_delivers_a_command_and_accepts_events(stub_window: Path):
    received: list[Command] = []
    window = ArgosWindow.open(received.append, binary=stub_window)
    assert window is not None
    try:
        assert window.send(Event(state="patrolling")) is True
        deadline = time.monotonic() + 5
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        # The argument echoes the event's state, so this asserts the whole round
        # trip rather than only that the child said something.
        assert received == [Command(name="scan_now", args={"entity": "patrolling"})]
    finally:
        window.close()
    assert window.alive is False


def test_sending_to_a_closed_window_is_false_rather_than_an_exception(stub_window: Path):
    window = ArgosWindow.open(lambda _: None, binary=stub_window)
    assert window is not None
    window.close()
    assert window.send(Event(state="patrolling")) is False


def test_a_handler_that_raises_does_not_kill_the_reader(stub_window: Path, caplog):
    def explode(_: Command) -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        window = ArgosWindow.open(explode, binary=stub_window)
        assert window is not None
        # The stub answers events rather than speaking first, so there has to be
        # one to answer.
        window.send(Event(state="patrolling"))
        deadline = time.monotonic() + 5
        while "command handler failed" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.05)
        window.close()
    assert "command handler failed" in caplog.text


def test_a_missing_binary_is_not_an_error(monkeypatch):
    monkeypatch.delenv("MODELGUARD_ARGOS_BIN", raising=False)
    monkeypatch.setenv("PATH", "")
    assert resolve_binary() is None
    assert ArgosWindow.open(lambda _: None) is None


def test_a_configured_binary_that_is_not_there_fails_loudly(monkeypatch, tmp_path: Path):
    from modelguard.env import ConfigError

    monkeypatch.setenv("MODELGUARD_ARGOS_BIN", str(tmp_path / "nope"))
    with pytest.raises(ConfigError, match="MODELGUARD_ARGOS_BIN"):
        resolve_binary()


# --- the log-driven states ---------------------------------------------------


def test_a_phase_log_line_becomes_an_event():
    seen: list[Event] = []
    handler = ArgosHandler(seen.append)
    logger = logging.getLogger("modelguard.test.argos")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("lineage walk urn=x", extra=phase("sniffing", urn="x"))
        logger.info("something else entirely")
    finally:
        logger.removeHandler(handler)
    assert [event.state for event in seen] == ["sniffing"]


def test_the_bubble_never_carries_a_log_message():
    # logs.py forbids prose on the log channel, so the handler must not lift the
    # record's message onto the screen.
    seen: list[Event] = []
    handler = ArgosHandler(seen.append)
    logger = logging.getLogger("modelguard.test.argos.prose")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("a secret-looking sentence", extra=phase("scribbling"))
    finally:
        logger.removeHandler(handler)
    assert "secret-looking" not in (seen[0].title or "")


# --- the terminal fallback and the producer ----------------------------------


def test_the_terminal_fallback_prints_a_change_once():
    console = Console(record=True, width=100)
    terminal = TerminalArgos(console)
    terminal.send(Event(state="patrolling", title="clean"))
    terminal.send(Event(state="patrolling", title="clean"))
    terminal.send(Event(state="barking", title="stale"))
    text = console.export_text()
    assert text.count("clean") == 1
    assert "barking" in text


def test_scan_now_wakes_the_poll_and_mute_stops_the_writes(monkeypatch):
    monkeypatch.setenv("PATH", "")
    producer = ArgosProducer.start(Console(record=True), window=False)
    try:
        assert producer.muted is False
        producer.handle(Command(name="mute"))
        assert producer.muted is True
        assert producer.wake.is_set() is False
        producer.handle(Command(name="scan_now"))
        assert producer.wake.is_set() is True
    finally:
        producer.close()


def test_approve_says_so_when_nothing_is_pending(monkeypatch):
    console = Console(record=True, width=100)
    producer = ArgosProducer.start(console, window=False)
    try:
        producer.handle(Command(name="approve"))
        assert "nothing is waiting" in console.export_text()
        producer.send(Event(state="tugging", title="1 write waiting"))
        producer.handle(Command(name="approve"))
        assert producer.wake.is_set() is True
    finally:
        producer.close()


def test_a_dropped_file_polls_now_and_never_reaches_a_shell(monkeypatch):
    console = Console(record=True, width=200)
    producer = ArgosProducer.start(console, window=False)
    try:
        producer.handle(Command(name="drop", args={"path": "/tmp/train.py; rm -rf /"}))
        assert producer.wake.is_set() is True
        # The name is echoed, never executed: nothing in this path builds a
        # command line, and the parse step already bounded the value.
        assert "train.py" in console.export_text()
    finally:
        producer.close()


def test_the_producer_detaches_its_handler_on_close():
    before = len(logging.getLogger("modelguard").handlers)
    producer = ArgosProducer.start(Console(record=True), window=False)
    assert len(logging.getLogger("modelguard").handlers) == before + 1
    producer.close()
    assert len(logging.getLogger("modelguard").handlers) == before


def test_the_environment_is_only_read_through_env_py():
    # The window module resolves a path from the environment; it must do it the
    # way every other module does (root CLAUDE.md rule 6). This mirrors the
    # repo-wide check in test_env.py, kept close to the code that could break it.
    source = Path(__file__).resolve().parent.parent / "modelguard" / "argos"
    for module in source.glob("*.py"):
        code = module.read_text()
        assert "os.environ" not in code and "os.getenv" not in code, module.name
    assert os.environ is not None  # the import is used, and nothing here mutates it
