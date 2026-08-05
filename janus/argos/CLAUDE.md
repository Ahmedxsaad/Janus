# CLAUDE.md - argos (producer side)

The Python half of the desktop watchdog. The window itself is the Rust binary in
`argos/` at the repository root; nothing here draws anything.

Five modules, one job each: `protocol` (the wire), `events` (findings to
states), `window` (the child process and its pipes), `terminal` (the fallback),
`handler` (mid-scan states from the log). `producer` ties them together for the
two commands that own a window, `watch --pet` and `companion`.

## Local rules

1. **Drain the child's stdout on a thread, always.** A parent that only writes
   deadlocks: the child's stdout pipe fills, it stops reading its stdin, and
   both processes block forever.
2. **Events out are trusted, commands in are not.** `Command.parse` is the trust
   boundary: a closed set of names, a closed set of argument keys, bounded
   values, and no dynamic dispatch on anything a user clicked. It never raises.
3. An `Event` cannot be built in a state the window does not know. The window
   renders an unknown state as patrolling, which would read as health, so a
   producer must fail at the source instead.
4. **No prose from the log channel.** `logs.py` allows identifiers and counts
   only, so the speech bubble's sentence comes from the finding's `title`, never
   from a log record's message.
5. This package renders; it never detects. Every event is a pure function of
   something a detector already measured (docs/plan/08 section 3).
6. Configuration is read through `env.py` like everywhere else. Two variables,
   both identity, both without defaults: `JANUS_ARGOS_BIN` and
   `JANUS_DATAHUB_UI_URL`.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-08-03 | Claude (for Ghassen Naouar) | Initial version: the protocol, the stdio transport, the terminal fallback and the log-driven states (D-098) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Package and brand identifiers renamed repo-wide: paths, imports, and prose all match the current name and distribution name (D-136) |
