# CLAUDE.md - site

The documentation and landing page for what ships to a user: the PyPI package,
the CLI, the Python API, the MCP server, and Argos. Static files, no build step
and no dependency; `art/` holds the two generators that write the pixels.

Open `index.html`. It needs no server and no build step: every asset it loads
is in this directory, and the art is inlined rather than fetched.

Everything outside `site/` is unreachable once deployed, because the deployment
is served with this directory as its root. That is not a preference, it is the
constraint the whole file layout here answers to (D-139).

## Local rules

1. **Nothing here may load anything from outside this directory, ever.** Not
   `../argos/`, not a CDN. Such a reference works locally and 404s in
   production, where the page still renders and the missing thing is simply
   absent: that is how a page with no dog on it passed review and shipped
   (D-139). `tests/test_site.py` fails on any `../` reference and on any
   `fetch(`.
2. **The art is generated into this directory, never authored in it.**
   `pixels.js` is built by `art/make_pixels.py` from `argos/ui/sprites.js` and
   `argos/ui/sprites/argos.txt`, the files the window and the README animation
   read. It is a second copy, which the old rule forbade outright, so the
   guarantee moved from a rule somebody remembers to a test: `test_site.py`
   reruns the generator and compares. Redraw the dog, run the generator, commit
   both. The ornaments work the same way: draw in `art/make_ornaments.py`, run
   it with `--preview` and *look* at the sheet, never hand-edit `ornaments.txt`.
3. **No framework, no bundler, no web font, no CDN.** The bubble text is a
   glyph table drawn as rects, so it is made of the same pixels the dog is.
4. **A beat is markup, not code.** Argos says what a section carries in
   `data-say`, in the pose named by `data-pose`; its ornament is `data-relic`.
   Adding a beat is adding attributes; the tests check that the pose exists,
   that every character has a glyph, and that the ornament is one the art
   defines. There is no `data-x`: he is parked, and the sections move past him.
5. **Argos never stands where the document is laid out.** He is fixed in the
   corner and `body` reserves that corner with a `padding-right`. A speech
   bubble is opaque, so anywhere else it covers a paragraph; that is what the
   old walking strip did, and no amount of positioning fixes it while the two
   share the same space (D-140).
6. **Red is a live finding here too.** `data-collar="r"` belongs on the beat
   about a finding and nowhere else, for the same reason the window only paints
   it off an event (argos/CLAUDE.md rule 6). The ornaments' oxblood is held to
   the same line: it is the crest on one helmet and nothing else.
7. **An ornament never shares a column with content.** The rail is its own grid
   column. Floated into the text it sits politely beside a paragraph and then
   lands on top of the next wide table, because the prose is capped at a reading
   measure and the tables and code blocks are not.
8. This page documents the shipped product, so what it claims has to be true of
   the released package. When a command, a flag or an extra changes, this page
   changes in the same commit as the README.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-08-03 | Claude (for Ghassen Naouar) | Initial version: the documentation page, its beats, and the rule against a second copy of the art (D-104) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The NIST AI RMF crosswalk section lands. The page holds the only hand-written copy of a table the code generates, so tests/test_site.py asserts it carries a row for every detector: a check added to Janus cannot go missing here (D-109, T-02) |
| 2026-08-04 | Claude (for Ghassen Naouar) | The crosswalk table gains the degraded mode's row, which the page cannot generate and a test asserts it carries: a detector that exists in the CLI's generated crosswalk and not on the page is the gap the test was written for (D-113, T-07) |
| 2026-08-05 | Claude (for Ahmed Saad) | Phase 5 lands on the page: `model-card` and `evidence-pack` get a section, and the proxy-attribute detector gets the prose it never had (the page already carried its crosswalk row, because a test enforces that row and nothing enforced the rest). "The five checks" and "the two that read the governance graph" were both stale by one. Rule 5 is now a test rather than a promise: tests/test_docs.py fails if a registered command is missing from this page or the README, which is how these two shipped undiscoverable (D-123) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The page gains `coverage` beside `inventory`: the same sweep as one catalog figure, and what it does not claim (it measures declaring, not health) stated on the page rather than only in --help (D-126, T-15) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The watch section gains the OTLP export and, as prominently, what it deliberately is not: three instruments and no traces, with the instrumentation package that does provide them named rather than implied (D-128, T-17) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The page gains `finops` and, in the same paragraph, the two things that keep it safe to act on: one live consumer excludes a table, and an undated model is never called unused (D-129, T-18) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The documents section becomes three, gaining `feature-card` and the sentence that keeps it honest: its freshness figures are measured now, not at training time (D-130, T-19) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The watch section gains `--events`, and states the failure it exists for rather than the feature: an ingest silently drops a link, every check then reports not-evaluated on a model that was checked yesterday, and nothing errors (D-132, T-20) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Package and brand identifiers renamed repo-wide: paths, imports, and prose all match the current name and distribution name (D-136) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The page is set in autumn (ivory, dark brown, caramel, oxblood) and one Argos walks it: position, pose, collar and line are declared per section as `data-x` / `data-pose` / `data-collar` / `data-say`, replacing the nine stacked canvases. `vercel.json` at the repository root keeps rule 1 workable in the deployment, where a `site/` root would put `../argos/` outside it and the dog would silently never appear (D-137) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Rule 8's promise extended below the command level: a `#flags` section covers every option of every command, the configuration section covers all 28 `.env` keys rather than 13, and the versioned-model sweep behaviour gets the prose it never had. `tests/test_docs.py` enforces a command, nothing enforces an option, so this one is still kept by remembering (D-138) |
| 2026-08-05 | Claude (for Ghassen Naouar) | The dog was missing in production and broken everywhere: the page fetched `../argos/`, which the `site/`-rooted deployment cannot reach, and `<canvas id="argos">` collided with `<section id="argos">` so querySelector returned the section. Art is now generated into `pixels.js` and rule 1 forbids reaching outside this directory at all. The bottom strip's translucent wash is gone, replaced by a drawn masonry course, and the page is decorated with pixel Roman ornaments placed where no content reaches (D-139) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Argos is parked in the bottom right instead of walking a strip across the window, because a walking dog puts an opaque speech bubble wherever he stops and it covered the documentation behind it. The page reserves that corner so the bubble has somewhere to be that is not on top of a paragraph, and the full-width masonry course shrinks to a short ledge under his feet. He still changes pose and line with the section being read (D-140) |
