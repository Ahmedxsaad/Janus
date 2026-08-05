# CLAUDE.md - site

The documentation and landing page for what ships to a user: the PyPI package,
the CLI, the Python API, the MCP server, and Argos. Three static files, no
build step, no dependency.

Run it with `python -m http.server` **from the repository root**, then open
<http://localhost:8000/site/>. Not from inside this directory: the page reads
the art out of `../argos/`, and `fetch` is blocked under `file://`.

## Local rules

1. **No second copy of the art.** `argos-guide.js` reads
   `argos/ui/sprites/argos.txt` through `argos/ui/sprites.js`, the same two
   files the Tauri window, the app icon and the README animation read. A
   vendored copy here goes stale the first time somebody redraws a leg, and
   nothing would fail. `tests/test_site.py` enforces this.
2. **No framework, no bundler, no web font, no CDN.** The bubble text is a
   glyph table drawn as rects, so it is made of the same pixels the dog is.
3. **A beat is markup, not code.** Argos says what a `<canvas class="beat">`
   carries in `data-say`, in the pose named by `data-pose`. Adding a beat is
   adding one element; the tests check that the pose exists and that every
   character has a glyph.
4. **Red is a live finding here too.** `data-collar="r"` belongs on the beat
   about a finding and nowhere else, for the same reason the window only paints
   it off an event (argos/CLAUDE.md rule 6).
5. This page documents the shipped product, so what it claims has to be true of
   the released package. When a command, a flag or an extra changes, this page
   changes in the same commit as the README.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-08-03 | Claude (for Ghassen Naouar) | Initial version: the documentation page, its beats, and the rule against a second copy of the art (D-104) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The NIST AI RMF crosswalk section lands. The page holds the only hand-written copy of a table the code generates, so tests/test_site.py asserts it carries a row for every detector: a check added to ModelGuard cannot go missing here (D-109, T-02) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The crosswalk table gains the degraded mode's row, which the page cannot generate and a test asserts it carries: a detector that exists in the CLI's generated crosswalk and not on the page is the gap the test was written for (D-113, T-07) |
| 2026-08-05 | Claude (for Ahmed Saad) | Phase 5 lands on the page: `model-card` and `evidence-pack` get a section, and the proxy-attribute detector gets the prose it never had (the page already carried its crosswalk row, because a test enforces that row and nothing enforced the rest). "The five checks" and "the two that read the governance graph" were both stale by one. Rule 5 is now a test rather than a promise: tests/test_docs.py fails if a registered command is missing from this page or the README, which is how these two shipped undiscoverable (D-123) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The page gains `coverage` beside `inventory`: the same sweep as one catalog figure, and what it does not claim (it measures declaring, not health) stated on the page rather than only in --help (D-126, T-15) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The watch section gains the OTLP export and, as prominently, what it deliberately is not: three instruments and no traces, with the instrumentation package that does provide them named rather than implied (D-128, T-17) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The page gains `finops` and, in the same paragraph, the two things that keep it safe to act on: one live consumer excludes a table, and an undated model is never called unused (D-129, T-18) |
