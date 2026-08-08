"""The protocol, the art, the transport, and the log-driven states.

What CI can check without a display, which is everything except whether the
window is actually on top of the other windows (docs/11-argos.md).
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import struct
import sys
import time
from pathlib import Path

import pytest
from rich.console import Console

from janus.agent.pipeline import ScanReport
from janus.argos import events
from janus.argos.handler import ArgosHandler
from janus.argos.producer import ArgosProducer
from janus.argos.protocol import (
    COMMANDS,
    MAX_ARGUMENT_LENGTH,
    STATES,
    Command,
    Event,
    Hop,
)
from janus.argos.terminal import TerminalArgos
from janus.argos.window import ArgosWindow, resolve_binary
from janus.logs import phase
from janus.models import Finding, TrustBand

from .conftest import make_finding, make_trust_score

UI = Path(__file__).resolve().parent.parent / "argos" / "ui"
PALETTE_CHARS = set(".kwgaobdr")
SPRITE = 32


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


def test_every_frame_is_a_square_of_palette_characters():
    frames = _frames()
    assert frames, "no frames parsed out of the sprite file"
    for name, rows in frames.items():
        assert len(rows) == SPRITE, f"{name} has {len(rows)} rows"
        assert {len(row) for row in rows} == {SPRITE}, f"{name} has a row of the wrong width"
        assert set("".join(rows)) <= PALETTE_CHARS, f"{name} uses a colour outside the palette"


def test_the_windows_icon_is_a_committed_ico_that_parses():
    # tauri-build compiles a Windows resource file into the executable and
    # fails `cargo build` outright when icons/icon.ico is missing, so this is a
    # build dependency and not a bundling nicety. It is written by hand out of
    # struct in make_icon.py, which is exactly the kind of code that can emit a
    # file no resource compiler will read while still looking fine on disk.
    ico = UI.parent / "icons" / "icon.ico"
    assert ico.is_file(), "icons/icon.ico is missing; run argos/icons/make_icon.py"
    blob = ico.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", blob, 0)
    assert (reserved, kind) == (0, 1), "not an icon directory"
    assert count, "no images in the icon"
    sides = []
    for index in range(count):
        side, _, _, _, planes, depth, length, offset = struct.unpack_from(
            "<BBBBHHII", blob, 6 + 16 * index
        )
        assert (planes, depth) == (1, 32), "entries must be 32-bit BGRA"
        assert offset + length <= len(blob), "an entry points past the end of the file"
        # A DIB entry stores the colour image and the 1-bit mask stacked, so
        # its declared height is twice the icon's.
        _, width, height = struct.unpack_from("<Iii", blob, offset)
        assert height == width * 2, "entry is not a stacked colour-plus-mask DIB"
        sides.append(256 if side == 0 else side)
    assert sides == sorted(sides) and len(set(sides)) == len(sides)


def test_no_frame_paints_red_because_red_is_state_the_renderer_applies():
    # Red means one thing here: a live finding, painted onto the collar by the
    # renderer for exactly as long as the finding is up. Art that carried its
    # own red would make a healthy graph look like a failing one. The bark's
    # open mouth used to be the exception; at 32 pixels it read as an injured
    # dog rather than a barking one, and it is drawn with the outline colour now.
    reddened = {name for name, rows in _frames().items() if "r" in "".join(rows)}
    assert not reddened, f"red leaked into {reddened}"


def test_the_state_machine_names_a_frame_that_exists():
    frames = _frames()
    script = (UI / "argos.js").read_text()
    for state in STATES:
        assert f"{state}:" in script, f"argos.js has no rule for the {state} state"
    # Every frame the state machine names must exist in the art, and every frame
    # in the art must be reachable: an orphan on either side is a bug nobody
    # sees until the state it belongs to fires.
    named = set(re.findall(r'"([a-z]+_?[a-z]*)", \d+', script)) | set(
        re.findall(r'"(walk_[a-d])"', (UI / "walk.js").read_text())
    )
    named.discard("v")
    assert named <= set(frames), f"the window draws frames that do not exist: {named - set(frames)}"
    assert set(frames) <= named, f"frames nothing draws: {set(frames) - named}"


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
    from janus.agent.pipeline import ScanReport, TrustWrite

    report = ScanReport(
        run_id="r",
        dry_run=True,
        trust=(
            TrustWrite(
                model_urn="urn:li:mlModel:(x,credit_risk_v3,PROD)",
                model_name="credit_risk_v3",
                score=make_trust_score(35, band=TrustBand.AT_RISK, deductions={}),
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
    from janus.agent.narrate import Narrative, NarrativeSource
    from janus.agent.pipeline import FindingWrites, ScanReport

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
    monkeypatch.delenv("JANUS_ARGOS_BIN", raising=False)
    monkeypatch.setenv("PATH", "")
    assert resolve_binary() is None
    assert ArgosWindow.open(lambda _: None) is None


def test_a_configured_binary_that_is_not_there_fails_loudly(monkeypatch, tmp_path: Path):
    from janus.env import ConfigError

    monkeypatch.setenv("JANUS_ARGOS_BIN", str(tmp_path / "nope"))
    with pytest.raises(ConfigError, match="JANUS_ARGOS_BIN"):
        resolve_binary()


# --- the log-driven states ---------------------------------------------------


def test_a_phase_log_line_becomes_an_event():
    seen: list[Event] = []
    handler = ArgosHandler(seen.append)
    logger = logging.getLogger("janus.test.argos")
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
    logger = logging.getLogger("janus.test.argos.prose")
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
    before = len(logging.getLogger("janus").handlers)
    producer = ArgosProducer.start(Console(record=True), window=False)
    assert len(logging.getLogger("janus").handlers) == before + 1
    producer.close()
    assert len(logging.getLogger("janus").handlers) == before


def test_the_environment_is_only_read_through_env_py():
    # The window module resolves a path from the environment; it must do it the
    # way every other module does (CONTRIBUTING.md). This mirrors the
    # repo-wide check in test_env.py, kept close to the code that could break it.
    source = Path(__file__).resolve().parent.parent / "janus" / "argos"
    for module in source.glob("*.py"):
        code = module.read_text()
        assert "os.environ" not in code and "os.getenv" not in code, module.name
    assert os.environ is not None  # the import is used, and nothing here mutates it


def test_a_recovery_wags_rather_than_going_quiet():
    """The transition the caller owns: failing a moment ago, clean now."""
    clean = _report_with()
    assert events.from_report(clean).state == "patrolling"
    assert events.from_report(clean, recovered=True).state == "recovered"


def test_a_check_that_could_not_run_is_not_rendered_as_health():
    """detect/coverage.py's whole point, applied to pixels."""
    from janus.agent.pipeline import ScanReport
    from janus.detect.coverage import Unevaluated

    report = ScanReport(
        run_id="r",
        dry_run=True,
        not_evaluated=(
            Unevaluated(
                check="target leakage",
                target_urn="urn:li:mlModel:(x,m,PROD)",
                reason="no label term on any column",
                remedy="apply the label term",
            ),
        ),
    )
    event = events.from_report(report)
    assert event.state == "unchecked"
    assert "target leakage" in (event.title or "")


def test_the_trust_score_rides_along_so_the_meter_has_something_to_draw():
    from janus.agent.pipeline import ScanReport, TrustWrite

    report = ScanReport(
        run_id="r",
        dry_run=True,
        trust=(
            TrustWrite(
                model_urn="urn:li:mlModel:(x,m,PROD)",
                model_name="m",
                score=make_trust_score(64, band=TrustBand.WATCH, deductions={}),
            ),
        ),
    )
    assert events.from_report(report).trust == 64


def test_the_band_is_sent_rather_than_recomputed_from_the_score():
    """The band is the detector's, not a threshold applied twice.

    A model can score 70 and still be on watch, because a critical finding caps
    its band (detect/trust_score.py). A meter that re-derived it would paint
    that model healthy while the catalogue calls it watch. Found by running
    this against a live graph, where exactly that happened.
    """
    from janus.agent.pipeline import ScanReport, TrustWrite

    report = ScanReport(
        run_id="r",
        dry_run=True,
        trust=(
            TrustWrite(
                model_urn="urn:li:mlModel:(x,m,PROD)",
                model_name="m",
                score=make_trust_score(70, band=TrustBand.WATCH, deductions={}),
            ),
        ),
    )
    event = events.from_report(report)
    assert (event.trust, event.band) == (70, "watch")
    # And nothing in the window re-derives it.
    script = (UI / "argos.js").read_text()
    assert "score >= 70" not in script and "score >= 40" not in script
