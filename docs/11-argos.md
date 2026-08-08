# Argos, the desktop watchdog

Argos is a 32 by 32 pixel dog who lives in the corner of your screen and shows
what the metadata graph is doing. He patrols while a poll finds nothing, sniffs
while a lineage walk is in flight, barks with a red collar the moment a finding
lands, and turns into a translucent ghost when he cannot reach DataHub.

```bash
pip install "janus-datahub[pet]"     # macOS and Windows
janus watch --table loans_raw --pet  # Janus's own findings
janus companion                      # everything wrong with the assets you own
```

On Linux the window ships as a `.deb` or `.AppImage` on the GitHub release rather
than as a wheel. With no window binary installed, both commands print one line per
change in the terminal instead, which is also what runs over SSH.

## Why it exists

Everything else Janus produces lands in a terminal, a CI summary, or a DataHub
page somebody has to remember to open. DataHub has no desktop presence at all: it
is a browser tab people forget.

Argos is a second **surface**, not a second product. It adds no detection and no
new source of truth.

## The design law

**No animation exists without a real event behind it.**

This is the deterministic-detection rule applied to pixels. The sprite depicts only
what a detector already measured. It never animates on a timer to look busy, and a
state with no event source is not drawn.

Twelve states, and every one names the code that fires it:

| State | Fires when |
|---|---|
| Patrolling | A poll completed and found nothing |
| Sniffing | A lineage traversal is in flight |
| Head tilt | The narrator is drafting prose |
| Scribbling | An aspect was actually written |
| Barking, red collar | A finding was emitted |
| Tugging a sleeve | An approval is pending |
| Recovered, wagging | A finding that was open stopped reproducing |
| Sick, dimmed | The trust score fell below its healthy band |
| Unchecked, looking around | A check could not run |
| Asleep | Several polls with no change |
| Muted, sitting | You muted him from the menu |
| Ghost, translucent | He cannot reach DataHub |

The four mid-scan states are phases of a call that has not returned yet, so nothing
returns them. They arrive through the log channel rather than through a callback
threaded into a detector's signature, which keeps the detectors' signatures free of
a renderer.

**The ghost row is the one that earns trust.** A cheerful pet on a silently
disconnected watch is a lie, and it is exactly how ambient status displays get
switched off. Blind has to look different from healthy.

**The renderer applies no thresholds.** A trust band arrives on the event because a
detector decided it. Re-deriving the band in the window would let the window
disagree with the catalog it is reporting on, and it did: a window that re-applied
the band thresholds painted a `watch` model healthy, because a critical finding caps
the band below what its points alone would give.

## The interaction worth building it for

**Double-click a finding and the dog walks the blast radius across the screen**, one
screen hop per graph hop, with the column name floating over each jump.

That is the column-level traversal the benchmark measures, rendered as motion
instead of a paragraph. The detector already returns the path, so the cost is
animation, not analysis.

Also: click for a speech bubble with the top finding and a link into DataHub;
right-click for a small pixel menu (scan now, approve pending, mute, open DataHub);
drag to move him; pet him and he wags before resuming the state he was in; throw a
toy and he fetches it, but only from the states that already roam, because a dog
that trotted off to play mid-finding would be the sprite contradicting the event.

## The protocol

One versioned event shape going out, one command shape coming back, both
newline-delimited JSON over a pipe. **Any process that can print these events
drives the dog**, which is what makes Argos a general DataHub companion rather than
a Janus pet.

Two asymmetries are deliberate:

- **Events are trusted, commands are not.** An event is built in this process from a
  finding we detected. A command arrives from a window a user was clicking on, so it
  is validated against a closed set of names and a closed set of argument keys, and
  anything else is dropped.
- **Events are forgiving on the way in.** An unknown state is accepted and rendered
  as patrolling, because a newer producer must never break an older reader. A
  producer may not invent a state for the opposite reason: an unknown state
  rendering as patrolling would silently look like health.

`stdout` is the command channel and nothing else. Everything the window says about
itself goes to stderr, and the parent drops any line that is not a JSON object. A
stray print for debugging is a protocol violation.

The window process does no detection, makes no DataHub calls, holds no credentials
and opens no network connection. It draws and forwards clicks.

## The companion, which is not about Janus at all

`janus companion` runs no detector. It sweeps the assets one owner owns for open
incidents, failing assertion runs and deprecations, and drives the same window.

It is the general-purpose half, and it exists because the DataHub-shaped gap here is
a desktop presence, not an ML-specific one. The owner identifies an account, so it
gets no default: the variable is declared, and a missing value fails loudly naming
it.

## The art is text, and generated text

`argos/ui/sprites/argos.txt` holds 24 frames of 32 rows by 32 characters. It is
written by a generator that composes each frame from a handful of parts (head,
torso, tail, legs) and finds the outline itself. Edit the parts and rerun it; never
edit the committed file by hand. The same source produces the README animation, the
documentation page's dog, and the application icons, so there is exactly one copy of
the art.

Two rules the pixels themselves obey:

- **No frame carries red.** Red is state, and the renderer is the only thing that
  applies it, repainting both rows of the collar while a finding is live.
- **The sprite carries a one-pixel light rim outside its own dark outline.** The
  outline is DataHub's near-black and it vanishes against a dark wallpaper, taking
  the silhouette with it. A desktop pet cannot choose its background.

## The shell

Tauri v2, static files, no npm, no bundler, no framework. Transparency on macOS
requires a private API, which makes the app ineligible for the Mac App Store: that
is a decision, not an accident, since Argos ships through pip and GitHub Releases.

The binary is named `janus-argos` rather than `argos`, which is too generic to claim
on somebody's PATH.

## Inside the Python half

Six modules in `janus/argos/`, one job each:

| Module | What it owns |
|---|---|
| `protocol.py` | The contract: events out, commands in, and the parsing of both |
| `events.py` | Turning a detector's own output into a state and a sentence |
| `window.py` | Spawning the child process and owning its two pipes |
| `terminal.py` | The fallback when there is no window |
| `handler.py` | The four mid-scan states, read off the log channel |
| `producer.py` | The four things any producer needs, in one object |

**`window.py` holds the one rule that makes stdio safe**, in code rather than in
a comment somewhere: the parent reads the child's stdout on its own thread,
always. A parent that only writes eventually blocks, because the child's stdout
pipe fills, the child blocks writing into it, it stops reading its stdin, and the
parent blocks writing there. Both processes then sleep forever. The reader thread
removes the whole failure mode.

**`producer.py` exists because both producers need the same four things**: a
surface to draw on, the log handler that turns phases into states, a way for a
click to interrupt the poll interval, and a mute the user can set from the menu.
A window belongs to exactly one process and there is no shared bus, so running
both commands gives two dogs with no shared state, which is honest and cheap.

**The speech-bubble sentence comes from a finding's `title`**, not from the log
and not from the narrator. The title is a pure function of stable graph facts;
the log channel is forbidden to carry prose; and language-model output must never
reach a key or a state. A title is the honest, stable thing to show.

**`terminal.py` is deliberately not pixel art.** The sprite files live next to the
window's frontend, which the Python wheel does not ship, and duplicating the art
into the package to draw it in a terminal would create a second copy to keep in
step with the first. A state and a sentence are what a person reading a log wants
anyway.

## The blast-radius walk

`argos/ui/walk.js` runs in its own maximised, click-through window rather than in
the pet window, because repositioning a 176-pixel always-on-top window once per
frame is jank some window managers rate-limit.

**The path is not computed there and never could be.** It arrives on the event as
`path`, straight out of the blast-radius detector, which is the column-level
traversal the benchmark measures. The file only animates it.

## The sprite generator

`argos/ui/sprites/argos.txt` holds 24 frames of 32 rows by 32 characters, written
by `make_sprites.py`.

A script rather than 24 hand-typed grids, because the frames share almost
everything: one dog, one silhouette, a handful of parts that move. Typed out 24
times, a change to the muzzle is 24 edits and the frames drift apart on the first
one somebody misses. Here a pose is a choice of head, tail and legs, each
authored once.

**The fills are authored; the outline is not.** Every frame gets its dark edge
computed from the shape, which keeps the silhouette even. Author the colour, let
the script find the border.

`icons/make_icon.py` renders both application icons from that same file, standard
library only. Two formats, because Windows will not take the PNG: `tauri-build`
compiles a Windows resource file into the executable and fails `cargo build`
outright when `icons/icon.ico` is absent, which makes the `.ico` a build
dependency rather than a packaging nicety.

`assets/make_demo.py` renders the README's animation from the same file, reusing
the frame writer from `make_icon.py` rather than carrying a second copy. So one
text file feeds the window, the icons, the README animation and the documentation
page, and a redraw updates all four.

## Where the name comes from

Odysseus's dog Argos, who waited twenty years and still recognised his master. Argus
was also the hundred-eyed watchman of the same mythology. A watchdog and a watcher
in one word, for a process whose whole job is to keep waiting and still notice.

## What is not built

- The speech bubble uses scaled-down system mono rather than an authored bitmap
  font.
- A `datahub-actions` plugin that would forward change-log events to the companion
  and remove the poll latency for anyone already running that framework.
- Code signing for the release artifacts.
- The window's interactions are exercised by hand rather than by a test, because a
  test would need a display and a synthetic pointer.
