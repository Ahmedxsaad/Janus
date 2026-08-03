# CLAUDE.md - argos

The window half of the watchdog: a Tauri v2 binary that renders the JSON event
stream a producer writes to its stdin. Design and rationale in
docs/plan/08-watchdog-mascot.md. The Python half lives in modelguard/argos/.

Build it with `cargo build --release` here, or `cargo tauri build` for the
installers. Run the frontend alone with `python -m http.server` inside `ui/`;
`fetch` of a local file is blocked under `file://`, so double-clicking
`index.html` does not work.

## Local rules

1. **stdout is the command channel.** Everything this process says about itself
   goes to stderr, and the parent drops any line that is not a JSON object.
   A `println!` for debugging is a protocol violation.
2. **No npm, no bundler, no framework.** `frontendDist` is `ui/`, static files,
   and there is no `beforeBuildCommand`. That only holds while
   `app.withGlobalTauri` stays true: with it false the frontend needs the
   `@tauri-apps/api` package and the whole node toolchain comes with it.
3. **`app.macOSPrivateApi` is required for transparency on macOS** and makes the
   app ineligible for the Mac App Store. That is a decision, not an accident:
   Argos ships through pip and GitHub Releases.
4. The binary is named `modelguard-argos`, never `argos`: the short name is
   taken on PyPI and is too generic to claim on somebody's PATH.
5. No detection, no DataHub calls, no credentials, no network. This process
   draws and forwards clicks. Anything else belongs to the producer.
6. Art is text. Frames live in `ui/sprites/argos.txt`, 16 rows of 16 characters,
   and `tests/test_argos.py` checks the geometry and the palette. No frame
   paints red: red is state, applied by the renderer when a finding is live.
   `icons/icon.png` is generated from that file by `icons/make_icon.py`; a PNG
   is unavoidable there because every OS bundler demands one.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-08-03 | Claude (for Ghassen Naouar) | Initial version: the Tauri v2 window, the stdio contract, the no-npm rule and the sprite format (D-098) |
