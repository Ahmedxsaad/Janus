# janus-argos

Argos, the DataHub watchdog: the desktop window for
[janus-datahub](https://pypi.org/project/janus-datahub/).

A small always-on-top window that renders the state of your data-to-model
supply chain as a desktop pet. It owns pixels and nothing else: it reads
newline-delimited JSON events on stdin, and writes the commands you click back
on stdout. It never talks to DataHub, holds no credentials, and binds no port.
The producer that spawned it does all of that.

## Install

```
pip install "janus-datahub[pet]"
```

That puts the `janus-argos` binary on your PATH for macOS and Windows. Linux
ships as a `.deb` and an `.AppImage` on
[GitHub Releases](https://github.com/Ahmedxsaad/janus/releases): the binary
links the system webkit2gtk, which no manylinux tag permits, so PyPI will not
accept a Linux wheel.

## Use

`janus watch --pet` spawns it. To drive it yourself, write one JSON object per
line to its stdin:

```
{"v": 1, "state": "patrolling", "title": "all clear"}
```

Design and the full event contract:
[docs/11-argos.md](https://github.com/Ahmedxsaad/janus/blob/main/docs/11-argos.md).
