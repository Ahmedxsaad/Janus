# Argos: identity rework and desktop companion

Written 2026-08-02 from the session that produced draft 1 of the character,
revised 2026-08-03 twice: once to settle the identity and the shell, once to
check every technical claim in it against the installed SDK, the Tauri v2
configuration reference and the code in this repository. The dog is **Argos**,
the shell is **Tauri v2**, the transport is **stdio**, the scope is a general
DataHub companion, and distribution is pip-first.

Built the same day, on `feat/argos-companion` (D-098), redrawn and run against
a live DataHub Quickstart the same evening (D-099). Section 10 says what is
done, section 8 what is not, and the places the build corrected this document
say so where they sit.

Claims in this document are marked `[verified]` when they were checked on
2026-08-03 against acryl-datahub 1.6.0.13 in `.venv`, the Tauri v2 config
reference, or this repository's own code, and `[confirm]` when they still need
a live GMS or a platform this machine is not.

Draft 1 of the character sheet (live sprite, state bindings, palette, asset
sheet, name shortlist):
<https://claude.ai/code/artifact/4dc8a35f-97f8-4cb9-bd97-b65bfeb0ea39>

## 1. Why

The hackathon is crowded and several teams are building near-identical
"agent reads DataHub, writes findings back" projects. The detection work is
already differentiated (column-level lineage, measured in benchmarks/RESULTS.md);
what is not differentiated is the identity and the surface a person actually
touches. Everything ModelGuard produces today lands in a terminal, a CI summary,
or a DataHub page somebody has to remember to open.

So the proposal is a second surface, not a second product: an always-on pixel
watchdog that gives the agent a body and makes the state of the ML supply chain
readable from peripheral vision.

## 2. What existing code this rests on

The pet is a renderer. It adds no detection and no new source of truth.

| Already exists | What the pet uses it for |
|---|---|
| `cli._watch_once` | the poll loop that drives every state change |
| `render.py` `--format json` | a stable report shape, already a public interface |
| `agent/narrate.py` | the sentence that goes in the speech bubble |
| `detect/blast_radius.py` | the hop path the dog physically walks |
| `detect/trust_score.py` | the dog's health, mapped to the trust band |
| `scan --review` | the pending-approval state |
| `client.connect` | the disconnected state |

## 3. Design law

**No animation exists without a real event behind it.**

This is root CLAUDE.md rule 4 applied to pixels: detection stays deterministic
Python, and the sprite only depicts what a detector already measured. It never
animates on a timer to look busy. A state with no event source does not get
drawn.

Twelve states, and every one of them names the code that fires it.

| Sprite state | Fires when | Source |
|---|---|---|
| Patrolling | poll completed, nothing found | `cli._watch_once` |
| Barking, red collar | a Finding was emitted | `agent/pipeline.py` |
| Recovered, wagging | a finding that was open stopped reproducing | the transition in `cli._watch_once` |
| Asleep | N polls, no change | `cli._finding_signature` |
| Sick, dimmed | trust score below its healthy band | `detect/trust_score.py` |
| Unchecked, looking around | a check could not run | `detect/coverage.py` |
| Muted, sitting | the user muted from the menu | `argos/producer.py` |
| Ghost, translucent | cannot reach DataHub | `client.connect` |
| Sniffing | lineage traversal in flight | `detect/blast_radius.py` log line |
| Head tilt | narrator drafting prose | `agent/narrate.py` log line |
| Scribbling | an aspect was actually written | `writeback/incidents.py` log line |
| Tugging sleeve | an approval is pending | `agent/graph.py` log line |

The last four are phases of a call that has not returned yet, so nothing returns
them and the log is where a reader learns they started. They arrive through
`logs.phase()` and `argos/handler.py` rather than through a callback threaded
into a detector's signature.

The ghost row is the one that earns trust. A cheerful pet that is silently
disconnected is a lie, and it is how ambient status displays get switched off.
Blind must look different from healthy.

## 4. The interaction worth building it for

**Double-click an incident bubble and the dog walks the blast radius across the
desktop.** It hops from the affected table to the model that consumes it, one
screen-hop per graph-hop, with the column name floating over each jump.

That is the project's differentiator (column-level lineage traversal, scored
1.00 precision and 1.00 recall in RESULTS.md against 0.25 for table-level)
rendered as motion instead of a paragraph. `blast_radius.py` already returns
the path, so the cost is animation, not analysis.

Supporting interactions, in cost order:

- Click: speech bubble with the top finding, plus a link that opens the entity
  in DataHub.
- Right-click: small pixel menu. Scan now, Approve pending, Mute 1h,
  Open DataHub.
- Drag: move the dog.
- Pet him: a click is also a hand, and he wags for a moment before the state he
  was in resumes.
- Click the floor beside him: the toy lands there and he goes and gets it,
  carries it a second, then picks the patrol back up (D-102). It is a reaction
  to a hand, like the petting, and it is gated on the states that already roam:
  a dog that trotted off to play mid-finding would be the sprite contradicting
  the event. The toy stays inside the pet window's own strip; a fetch across the
  whole desktop means moving the window every animation frame, which some window
  managers rate-limit, and the overlay window section 4's walk already uses is
  the upgrade path if it is ever worth it.
- Drop a file on it: the drop triggers a poll now and names the file in the
  bubble. It does *not* retarget the watch, and this is a correction to what
  draft 1 assumed: `link --infer` infers from the model in the graph, not from
  a training script on disk, so there is nothing in a dropped file to infer
  from, and `watch` is defined by the target it was started with.

## 5. The character

**The dog is Argos.** Odysseus's dog, who waited twenty years and still
recognised his master; Argus was also the hundred-eyed watchman of the same
mythology. A watchdog and a watcher in one word, for a process whose whole job
is to keep waiting and still notice. The two rejected alternatives, kept so the
reasoning survives: *Cerb* (Cerberus guards a crossing, and `modelguard gate` is
literally a gate, but it reads as menacing unless drawn as a puppy) and *Scout*
(warmest to a non-technical judge, least distinctive).

A side-profile watchdog, facing right, with a blue collar at the throat and a
tail that lifts when it wags. Draft 1 was 16x16 and too small to carry a snout,
an eye with a highlight, or a four-frame walk; 24x24 (D-099) carried those but
still read as a generic dog rather than a specific one. D-101 redraws it again,
at 32x32, as a German Shepherd (erect ears, a black saddle over a tan coat, a
low bushy tail): the extra size is spent on a breed a viewer can name, not on
more of the same silhouette.

**Colour rule: red is state, not decoration.** The dog is blue and amber while
the graph is healthy. Red enters only on a live finding, on the collar tag. That
is what makes the health readable at a glance without reading anything. No frame
of the art carries red at all (D-102): the bark's open mouth was drawn red once,
being the one place red occurs naturally on a dog, and at 32 pixels it read as
an injured animal rather than a barking one. It is the outline colour now, which
is what an open mouth is anyway: a hole.

**The two poses a viewer looks at longest got redrawn (D-102).** Asleep is held
for minutes and the bark is the one state that must land in peripheral vision.
Asleep is no longer the standing rig pushed down three rows (that read as a dog
standing in a hole) but its own lying pose: haunch, a back sloping to a head laid
on outstretched forepaws, a tail curled on the ground, one row of ribcage rising
between the two frames, and a Z drifting up off the head. The bark is a jump: the
second frame tucks the legs and the renderer lifts the sprite off the floor for
exactly that frame while the shadow shrinks under it, with an exclamation mark
that slams in over his shoulder the way the courtroom games do it. The mark is
punctuation on a state the producer already sent, never a claim of its own, so
only two states carry one: `!` for a bark and `?` for a check that could not run.

Palette (D-101; the collar keeps the original DataHub blue, everything else is
the breed's own colouring rather than the logo's):

| Hex | Role |
|---|---|
| `#160F0A` | outline, near-black |
| `#2668E8` | DataHub blue, collar |
| `#16408F` | deep blue, collar shade |
| `#D99347` | coat |
| `#A96B2C` | coat shade, the far pair of legs |
| `#2A2119` | the black saddle and ears |
| `#15100C` | saddle shade |
| `#F22525` | red, findings only |

### Sprite as data

A frame is 32 strings of 32 characters, one character per pixel, indexing a
named palette. All of a character's frames live in one plain text file,
`argos/ui/sprites/argos.txt`, each opened by a `# name` line and loaded by
`fetch` at startup, so the same file feeds the desktop window, the browser demo
and the icon generator. One file per character rather than one per frame: it is
still a reviewable text diff, and it is one request instead of eleven with no
index to keep in step.

Since D-101 the file itself is generated, not hand-typed: `argos/ui/sprites/
make_sprites.py` composes each frame from a head, a torso, a tail and a pair of
leg clusters, and computes the outline from the resulting silhouette rather
than having it typed alongside the fill. A change to the muzzle is one edit to
the head part, not 24 edits to 24 frames that are then one keystroke away from
drifting apart. The command is `python argos/ui/sprites/make_sprites.py`, run
from anywhere, and it rewrites argos.txt in place.

```
# idle_a
................................
.................kk...kk........
................kaak.kaak.......
...............kaaaakaaaak......
...............kaaaakaaaak......
..............kaaawwwaaawk......
```

Each stance is two leg clusters (fore, hind), and each cluster is a near leg
beside a far leg in the shade colour rather than one leg with a shaded edge.
That is what makes the animal read as four legs instead of two; an earlier cut
of this redraw drew one leg per cluster and it stood like a kangaroo. The near
leg's foot lands a row lower than the far leg's, which is what makes the
four-frame walk read as a gait instead of a shuffle.

One consequence to keep in mind while developing: `fetch` of a local file is
blocked under `file://`, so the browser demo is served with
`python -m http.server` from `argos/ui/` rather than opened by double-clicking
`index.html`. Inside Tauri the same code works untouched, because assets are
served over the custom protocol rather than the filesystem.

Why text and not PNG, all of them the reason to do it this way:

- No binary assets in git.
- Recolouring to a different theme is one map, not a redraw.
- A contributor adds a character as a small text file, reviewable in a diff.
- The art can be authored and verified in-session, with frames rendered and
  inspected, rather than blocking on an asset pipeline.

Twenty-four frames exist: idle and blink, a four-frame walk, sniff, bark, tilt,
sleep, sit, scribble, tug, wag and search, most of them in two-frame pairs. The
two health modifiers need no art of their own: `sick` dims the palette and
`ghost` drops the alpha, both applied by the renderer.

Animation is a timeline of frame-and-hold rather than a frame rate. The life is
in the uneven timing: a two-second hold and a 130ms blink is a dog, four frames
at 3fps is a flipbook.

`argos/icons/icon.png` is generated from this same file by
`argos/icons/make_icon.py`, standard library only. A PNG is unavoidable there
because every OS bundler demands one; it is the single binary asset in the
repository, and it is reproducible from text.

## 6. The shell: Tauri v2

Draft 1 rejected Tauri on deadline grounds and proposed `pywebview`. That
rejection is reversed. Tauri gives the window behaviour this thing lives or dies
on (frameless, transparent, always-on-top, no taskbar entry, native drag and
drop) in a small binary, and the Rust toolchain is a build-machine cost, not a
user cost: nobody installing Argos ever sees it.

Verified on this machine 2026-08-03, so none of this is aspirational:
`cargo` and `rustc` present, `webkit2gtk-4.1` 2.52.3 present (Tauri v2's Linux
webview), session X11.

### No bundler, no node, and the one config key that decides it

The frontend is plain static files: `argos/ui/index.html`, `argos.js`, and the
sprite text files. `tauri.conf.json` points `frontendDist` at that directory and
declares no `beforeBuildCommand`. The Tauri CLI comes from
`cargo install tauri-cli`, so npm never enters the build even on a machine that
has it.

That only holds because of one key:

```json
{ "app": { "withGlobalTauri": true, "macOSPrivateApi": true } }
```

`withGlobalTauri` **defaults to false** [verified against the v2 config
reference], and with it false there is no `window.__TAURI__`, the frontend has
to import `@tauri-apps/api` from npm, and the whole node toolchain arrives with
it. It is one line and it is load-bearing.

`macOSPrivateApi` is what "enables the transparent background API" on macOS
[verified, same source]. Without it the window is opaque there. It uses a
private API, so the app could never ship on the Mac App Store. That is a
decision, not an accident: Argos ships through pip and GitHub Releases, and
neither cares.

Window configuration, all of it declarative:

```json
{ "decorations": false, "transparent": true, "alwaysOnTop": true,
  "skipTaskbar": true, "shadow": false, "resizable": false,
  "width": 160, "height": 160, "dragDropEnabled": true }
```

`dragDropEnabled` is what makes section 4's drop-a-file interaction possible;
the rest is what makes a pet a pet rather than a small application window.

### IPC is stdio, not a socket

The producer spawns the binary as a child process and writes newline-delimited
JSON to its **stdin**. Rust reads stdin on a thread and emits each line to the
webview. Commands from the window (scan now, approve, mute, open in DataHub) go
back as one JSON line on **stdout**, which the producer reads.

This is the whole transport. What it buys, and the reason a local HTTP server
with SSE was rejected: no port is bound, no shared secret is invented, there is
no CORS configuration and no auth path to review, the window cannot be reached
by anything else on the machine, the child dies with its parent, and GMS
credentials never leave the producer process. It is roughly thirty lines of Rust
and twenty of Python against a server, a token, an origin check and a lifecycle.

Three rules come with it, and the first one is the difference between a working
prototype and a hang:

1. **The parent must drain the child's stdout on its own thread.** A parent that
   only writes to stdin will eventually block: the child's stdout pipe fills, the
   child blocks writing into it, it stops reading its stdin, and the parent
   blocks writing there. Both processes are then asleep forever. One reader
   thread removes the entire failure mode.
2. **stdout is a data channel.** Everything the Rust side logs goes to stderr,
   and so does GTK and webkit chatter, which is not always polite about it. The
   parent's reader skips any line that does not parse as JSON rather than
   treating it as a protocol violation.
3. **Flush every write, both directions.** Line buffering is the contract.

`[confirm]` on Windows, which is the one platform this repository cannot test
from here: a release build sets `windows_subsystem = "windows"` and so has no
console, but inherited stdin and stdout handles supplied by the parent's
`CreateProcess` should still work. Check it before promising the Windows wheel.

### The protocol

One versioned event shape, and one command shape. This is the contract, and it
is the thing that makes Argos general (section 7): any producer that can print
JSON to stdout drives the dog.

```json
{"v": 1, "state": "barking", "entity": "urn:li:dataset:(...,loans_raw,PROD)",
 "title": "loans_raw is 14h stale", "severity": "HIGH",
 "path": [{"urn": "urn:li:dataset:(...)", "column": "income"},
          {"urn": "urn:li:mlModel:(...)", "column": null}],
 "link": "http://localhost:9002/dataset/urn:li:dataset:(...)"}
```

```json
{"cmd": "scan_now", "args": {}}
```

`state` is one of the nine rows in section 3. `path` is present only when the
event carries a blast radius, and it is what section 4's walk animates.
`severity` and `link` are optional. An unknown `state` renders as patrolling
rather than crashing, because a newer producer must not break an older window.

### The command channel is a trust boundary

Everything above flows out of the process. This is the one thing that flows in,
and it can trigger writes to the catalogue, so it is not simplified:

- An explicit mapping of command name to handler. No dynamic dispatch off a
  string, no `getattr`, and nothing that reaches a shell.
- Arguments validated against a small schema before a handler sees them. An
  unknown command or a malformed argument is logged and dropped.
- A dropped file path is checked (exists, is a regular file, under a size cap)
  before `link --infer` opens it.
- Any write a command triggers goes through the same `run_scan` and agent path
  the CLI uses, so root CLAUDE.md rule 5 still holds: idempotent, keyed by
  (resourceUrn, finding_type, run_id), read before write.

### Where the fine-grained states come from

Section 3 lists four states with no source. They arrive as about five new
structured log lines at phase boundaries in `detect/graph_reads.py`,
`agent/narrate.py` and `writeback/`, each one a
`logger.info(..., extra={LOG_FIELDS: fields})` in the existing style, plus an
`ArgosHandler(logging.Handler)` that maps records to protocol events.

Logging rather than a progress callback because nothing has to be threaded
through a detector's signature for a rendering concern, and because the lines
are worth having on their own: an operator tailing `modelguard watch` wants to
know a lineage walk started and an aspect was written.

The constraint that comes with that channel, from `modelguard/logs.py`: log
lines carry identifiers, counts and durations only. No prose, no aspect content,
no credential. So the speech bubble's sentence is **not** on the log channel;
the producer takes it from the `ScanReport` it already holds and puts it in the
event's `title`.

### One window, one parent

A consequence of stdio worth stating plainly: a window belongs to exactly one
process. There is no shared bus, so two producers cannot drive one dog.

- `modelguard companion` is the long-lived producer. It owns the window, polls
  the catalogue (section 7), and can also run ModelGuard scans on a configured
  target, so one dog can show everything.
- `modelguard watch --pet` is the development and demo path. It spawns its own
  window.
- Running both gives two dogs with no shared state. That is honest and cheap;
  making it one dog means a broker process, which reintroduces the port, the
  token and the lifecycle that choosing stdio removed.

### Lifecycle and failure

The producer owns the child: it terminates it on exit, and it survives the child
exiting. A user who closes the window does not kill the watch; the CLI prints
one line saying how to bring it back. A producer that cannot spawn the binary at
all falls back to the terminal sprite and says why, once.

A command must not wait for the poll interval. `cli.py:1075` currently sleeps
with `time.sleep(interval)`, which would make "Scan now" do nothing for up to
thirty seconds. It becomes a `threading.Event.wait(interval)` that the command
reader sets, which is a contained change to one loop.

### Wayland, stated honestly

Tauri does not fix Wayland. Always-on-top, absolute window positioning and
click-through are compositor policy there, and GNOME's compositor does not grant
them to ordinary clients. X11, macOS and Windows behave as configured. This is
not a Tauri regression against `pywebview` (which has the same problem through
the same GTK layer), it is the platform, and the terminal fallback is what a
Wayland user gets until it changes.

### Two known ceilings

- **The transparent corners of the pet window still eat clicks.** The window is
  kept tight to the sprite's bounding box so the dead square is small.
  Per-pixel hit testing is the upgrade path if anyone complains.
- **The blast-radius walk needs its own window.** Repositioning a 160px
  always-on-top window once per animation frame is janky, and some window
  managers rate-limit it. The walk creates a full-screen transparent window with
  `set_ignore_cursor_events(true)`, animates inside it, and destroys it when the
  path ends.

### What makes it worth leaving on screen

Four things, and each one exists because its absence was visible:

- **A rim.** One light pixel outside the sprite's own dark outline. DataHub's
  near-black outline disappears against a dark wallpaper and takes the
  silhouette with it, so the tail stopped looking attached to the dog. A
  desktop pet cannot choose its background, so it carries its own separation.
- **A contact shadow**, so the dog is on the desktop rather than floating above
  it, and an entry squash on every state change, so a change reads as the dog
  reacting rather than the sprite being swapped.
- **A trust meter**, ten segments under the bubble. Its colour comes from the
  band the detector decided, never from a threshold re-applied in JavaScript: a
  model can score 70 and still be on watch, because a critical finding caps its
  band, and a meter that re-derived that would paint it healthy while the
  catalogue calls it watch. That bug was live for one build and is the reason
  the band now rides on the event.
- **A bubble that leaves.** It fades in with a pointer and a severity chip, and
  hides after nine seconds. A bubble that never leaves is a sticker.

### The fallback that costs nothing

`rich` is already a dependency. A terminal line inside `modelguard watch` works
over SSH, needs no binary, and is what runs when the window is unavailable for
any reason. It renders the same event stream, one line per change of state, and
deduplicates a steady state so a one-second poll does not scroll.

Not pixel art, which is a correction to draft 1: the sprite file lives beside
the window's frontend in `argos/ui/`, which the Python wheel does not ship, and
copying the art into the package to draw it in a terminal would create a second
copy to keep in step with the first.

## 6b. Packaging with Python

The requirement is that a Python user types one command. The complication is
that a Tauri binary is per-platform and `modelguard-datahub` is a pure wheel.

**Where the code lives.** `argos/` at the repository root, holding
`src-tauri/` and `ui/`. Not under `modelguard/`, because a Rust crate inside the
import package fouls `[tool.setuptools.packages.find]` and the sdist.

**How the binary becomes a wheel.** maturin with `bindings = "bin"` builds a
Rust binary into a platform wheel; maturin packages binaries as wheel scripts,
so they land on `PATH` in the environment's `bin` directory [verified against
maturin's bindings documentation]. Tauri embeds the frontend assets into the
binary on a release build, so the wheel is one self-contained file with no data
directory to locate at runtime.

**Names, and why not the obvious one.** The distribution is `modelguard-argos`
and the installed executable is `modelguard-argos` too. Not `argos`: that name
is already taken on PyPI [verified, HTTP 200 on the JSON API 2026-08-03], and it
is far too generic to claim on a user's `PATH`. `modelguard-argos` is free
[verified, 404] and matches the `modelguard-seed`, `modelguard-mcp`,
`modelguard-scenario` convention already in `[project.scripts]`.

**The extra.**

```toml
pet = ["modelguard-argos; platform_system != 'Linux'"]
```

**The Linux caveat, which the marker is the honest form of.** The binary links
the system webkit2gtk. That is not an allowlisted external library under
manylinux policy, so the wheel cannot be tagged manylinux and PyPI will not
accept a bare `linux_x86_64` wheel. Vendoring webkit into the wheel to force a
tag is rejected: it is enormous, it breaks against the host's GTK, and it would
make ModelGuard the maintainer of a browser engine. Linux users install the
`.deb` or `.AppImage` that `cargo tauri build` already produces, from GitHub
Releases. `[confirm]` the exact maturin flag that skips the auditwheel repair
for the local Linux build, since that path is used for the bundle even though
its wheel is never published.

**How Python finds the binary**, in order:

1. an explicit override, read only through `modelguard/env.py` (root CLAUDE.md
   rule 6: one module touches the environment),
2. `shutil.which("modelguard-argos")`, which covers the wheel, the `.deb` and a
   `cargo install`,
3. nothing found: one message naming this platform's install command, then the
   terminal sprite. It never silently degrades anything else.

**CI.** A `build-argos.yml` matrix (macos-14 for arm64, macos-13 for x86_64,
windows-latest, ubuntu-22.04) publishes wheels and bundles to the GitHub
release. `publish-pypi.yml` keeps publishing the pure-Python wheel and does not
learn about Rust.

**Code signing, and which path actually needs it.** macOS quarantine and Windows
mark-of-the-web are applied by browsers and download tools, not by pip, so a
binary installed from a wheel normally starts without a Gatekeeper or SmartScreen
prompt. Signing matters for the GitHub Releases artefacts (`.dmg`, `.msi`,
`.AppImage`), which is the Linux route and the non-Python route. Signing
identities are a paid ownership decision; until one exists, the release notes
document the override for that path rather than pretending the warning does not
appear.

## 7. What Argos is, beyond ModelGuard

Argos ships as a **general DataHub companion**, not a ModelGuard pet with a
ModelGuard-shaped event stream. DataHub has no desktop presence at all today; it
is a browser tab people forget to open. That gap is the give-back, and it is the
day-one shape rather than a later extraction.

`modelguard companion` is the producer that makes it general. It knows nothing
about ML lineage and polls the assets you own for three things. All three reads
are verified to exist:

| Source | What it calls | Status |
|---|---|---|
| Owned assets | `DataHubGraph.get_urns_by_filter(entity_types=..., extraFilters=[{"field": "owners", "values": [owner]}])` | [verified] against a live GMS on 2026-08-03 |
| Open incidents | `writeback/incidents.py:142` `attached_incident_urns` and `:165` `find_active_incident`, already written for read-before-write | [verified], reuse as-is |
| Deprecations | `get_aspect(urn, DeprecationClass)`, the pattern at `detect/governance.py:212` | [verified] |
| Failing assertions | `get_latest_timeseries_value(assertion_urn, AssertionRunEventClass, {"asserteeUrn": dataset_urn})` | [verified] against a live GMS on 2026-08-03 |

The companion needs to know whose assets to watch, and an owner identifies an
account, so per root CLAUDE.md rule 6a it gets no default and no fallback: the
variable is declared in `modelguard/env.py`, added to `.env` and `.env.example`
in the same position, and a missing value fails loudly naming the variable.

Done, and checked against a running Quickstart on 2026-08-03: an owned table
carrying an open incident, a failing assertion run and a deprecation produced
all three issues in one sweep, ranked incident first, with the dog barking
"Stale upstream data in ecommerce.public.loans_raw (+2 more)" and no ModelGuard
scan running anywhere.

The rest of the give-back, ranked by payoff per hour:

2. **The sprite-as-data format.** Characters are text files, so the community
   themes it and DataHub could ship its own mascot through it, with nothing
   binary entering a repository.
   Done when: a second character exists, contributed as text only, and the
   palette swap is documented in one place.
3. **A `datahub-actions` plugin** forwarding MetadataChangeLog events to the
   companion, which removes the poll latency for anyone running the Actions
   framework. That is DataHub's real extension point, so it is upstreamable
   rather than a side-car.
   Done when: the plugin emits a protocol event on a real MCL and the polling
   producer can be switched off. [confirm] the plugin API against the installed
   package first.
4. **The mascot itself, permissively licensed.** Docs, the 404 page, a CLI
   easter egg. Costs nothing and a community keeps that kind of thing.
   Done when: the sprite files and palette carry an explicit licence and a short
   usage note.
5. **Agent visibility over MCP.** When something writes to the catalogue through
   `mcp_ext`, the dog acts it out. Governance by making the agent's hands
   visible, which is on-theme for an agent hackathon.
   Done when: an `mcp_ext` write produces a scribbling animation naming the
   aspect written.

## 8. What is not done

Sections 5, 6, 6b and 7's first item are built and on the branch
`feat/argos-companion` (D-098, D-099), and both producers have been run against
a live DataHub Quickstart. What is still open:

- The bubble uses scaled-down system mono rather than an authored bitmap font.
- Give-back items 2 to 5 in section 7 (the sprite format as a contribution
  path, the `datahub-actions` plugin, the licensed mascot, MCP visibility).
- The wheel build itself has not been run: maturin is not installed on the
  development machine, so `maturin build --release` in `argos/` is unverified
  and the CI matrix is the first thing that will run it.
- Windows and macOS are unverified entirely. Both are in the build matrix, and
  the Windows stdio question in section 6 is the one that could still bite.
- The window's interactions (click, right-click, drag, drop) have been exercised
  by hand rather than by a test, because a test would need a display and a
  synthetic pointer.

## 9. What is still open

Answered: the name, the shell, the transport, the producer topology, the source
of the fine-grained states, the scope, and the distribution. Closed by running
it: both live-GMS reads in section 7.

Genuinely open, and each one blocks only the piece it names:

1. Whether stdio pipes reach a Windows GUI-subsystem build. Needs Windows.
2. The `datahub-actions` plugin API. Needs the installed package.
3. The maturin flag that skips auditwheel repair for the local Linux build.
4. Code signing identities for the Releases artefacts, an ownership decision.
5. Whether the speech bubble gets an authored bitmap font or keeps system mono.

## 10. Build order

Ordered so the parts most likely to be wrong are verifiable first, and so the
art is finished before any Rust exists.

| Phase | Ships | State |
|---|---|---|
| P0 | protocol, `argos/ui/`, sprite file, a recorded event fixture | done, checked in a browser over `python -m http.server` |
| P1 | Tauri shell and the stdio bridge | done, window screenshotted rendering a fixture event |
| P2 | `modelguard watch --pet`, the log lines and `ArgosHandler` | done, and run against a live Quickstart: the seeded leaking model produced a barking dog reading "CRITICAL Target leakage: prior_default_flag derives from label default_status" |
| P3 | `modelguard companion` producer | done, and run live against all three sources |
| P4 | maturin wheels, bundles, CI | files land; the build itself runs first in CI |
| P5 | the blast-radius walk, overlay window included | done, checked in a browser |
| P6 | give-back items 2 to 5 | not started |

P0 was the phase that de-risked everything else: the fixture makes every one of
the nine states in section 3 reproducible without DataHub, without Rust, and
without waiting for a real incident, and it is what the browser demo replays.

## 11. What the tests cover

CI has no display, so the split is deliberate. 44 tests land with this, each one
confirmed red against a deliberate break of the behaviour it covers
(tests/CLAUDE.md rule 6).

Python, in `tests/test_argos.py` and `tests/test_companion.py`:

- the art: every frame is 16 rows of 16 palette characters, and no frame paints
  red, because red is state and art that was already red would make a healthy
  graph look like a failing one;
- a protocol round-trip, and the asymmetry that matters: a producer cannot build
  an unknown state (it would render as patrolling, which reads as health) while
  the window tolerates one;
- the fixture carries all nine states plus exactly one unknown, so the browser
  demo and the tolerance rule are checked by the same file;
- the command channel drops a blank line, a GTK warning, a truncated write, an
  unimplemented name, an unexpected argument key and an oversized value;
- the transport: a real child process round-trips an event to a command, a
  closed window returns False rather than raising, and a handler that throws
  does not kill the reader thread;
- the companion's three sources, each with its negative case (a passing
  assertion, a lifted deprecation) and the cap that stops a catalogue sweep.

Rust, in the crate: the stdin line parser, including a blank line, a GTK warning
and a partial write.

Not tested in CI: window behaviour, transparency, always-on-top. The matrix
compiles, runs the Rust test, and smokes the transport by feeding one event to
the built binary (`.github/scripts/smoke_argos.py`), which is the part a compile
cannot prove. The rest is checked by hand on each platform before a release.
