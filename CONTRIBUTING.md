# Contributing to Janus

Janus is a data-to-model reliability agent built on DataHub. It reads
column-level and ML lineage to catch silent data-to-model failures, and writes
incidents, trust scores, reports and guarding assertions back into the graph.

Before changing anything, read [docs/02-architecture.md](docs/02-architecture.md) for
the layer boundaries and [docs/13-design-decisions.md](docs/13-design-decisions.md)
for the choices those boundaries encode. This file holds the conventions.

## Getting set up

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # DATAHUB_GMS_URL=http://localhost:8080
pre-commit install
```

Python 3.11 exactly. The offline suite needs nothing else:

```bash
pytest -m "not integration"   # unit tests, no network
pytest -m integration         # needs a local DataHub Quickstart and janus-seed
pre-commit run --all-files    # ruff, mypy, and the formatting rules below
```

## Code rules

1. **Clean and modular.** Single-responsibility modules, small functions,
   explicit names, type hints on every public function, docstrings on every
   module, class and function.
2. **Comments explain intent.** Say why, wherever the code is not self-evident.
   Never restate the line below.
3. **No placeholders.** No empty functions, no `pass` stubs, no TODO stubs, no
   dead code. Code lands when it is implemented and tested.
4. **Detection is deterministic Python.** The LLM only explains, ranks and
   drafts prose. It never decides whether a finding exists and never composes
   raw GraphQL. See [docs/10-security.md](docs/10-security.md).
5. **Every DataHub write is idempotent**, with read-before-write, and reruns
   must never duplicate. An incident's key is
   `(resource_urn, incident_type, title)` over the resource's active incidents;
   a document's is the entity plus the finding type. `run_id` is deliberately
   not in any key: it changes every run, so including it would make each scan
   raise a fresh copy of the same finding. It is stamped into the body as
   provenance instead.
6. **Configuration enters the process in exactly one module, `janus/env.py`.**
   It is the only place that calls `load_dotenv` and the only place that touches
   `os.environ`. Two tests enforce this; do not weaken them.
   - Anything that identifies a system, an account or a vendor gets no default
     and no fallback: server URLs, tokens, API keys, LLM provider names, model
     ids. A fallback is a machine-specific value in tracked code. It turns a
     missing `.env` into a silent connection to the wrong place, or a silent
     call to the wrong vendor billed to whatever key is in the ambient
     environment. Missing means missing, and it fails loudly, naming the
     variable.
   - Algorithm parameters (thresholds, hop caps, score weights) are not
     identity. They may keep a documented default in `janus/config.py`.
   - A group of related settings is all-or-nothing: set every one or none. A
     half-configured feature fails loudly, it never downgrades in silence.
   - Secrets never appear in a log line, an exception message, a repr or a CLI
     flag. Carry them as pydantic `SecretStr`. Text that came back from someone
     else's SDK goes through `env.scrub()` before it reaches a log.
   - `.env` and `.env.example` carry the identical key set, in the same order.
     Copying `.env.example` to `.env` must produce a working run. Add a key to
     one, add it to the other with an empty value and a comment.
7. **Verify every SDK symbol against the installed package before using it**
   (`pip show <pkg>`, then introspect). Never trust a documentation snippet over
   the installed signature.
8. **The agent is provider-agnostic.** Never import a vendor's SDK outside
   `janus/llm.py`, and never name a vendor's model anywhere else.

## Testing rules

1. Unit tests are offline: detectors run against fixture graphs, no network, no
   live DataHub. A known-leakage fixture must flag exactly the seeded feature; a
   clean fixture must flag nothing.
2. Integration tests assume a local DataHub Quickstart plus the seeded graph.
   Mark them `@pytest.mark.integration` so unit runs stay fast, and skip cleanly
   when DataHub is unreachable. Stop any `janus watch` pointed at the same graph
   first: the suite reads back the latest value of timeseries aspects it just
   wrote, and a concurrent watcher makes its own event the latest one.
3. Idempotency is a test: run a scan twice, assert exactly one incident per
   finding.
4. LLM-dependent behaviour is not unit-tested. Detection is LLM-free by design;
   test that instead. Generated-text quality belongs to the benchmark.
5. Mirror the package layout: `tests/detect/test_leakage.py` tests
   `janus/detect/leakage.py`.
6. **A green suite proves nothing until a fault kills it.** Before landing tests
   for a behaviour, break that behaviour on purpose and confirm the suite goes
   red. Assertions over values the test itself constructs (fixed URNs,
   constants) are not tests; assert on what the code sent to DataHub or wrote to
   the graph.

## Formatting rules

Strict, and they apply everywhere: code, docs, comments, commit messages.

- No em dashes. Use a hyphen, comma, colon or parentheses instead.
- No emojis. Use text markers like `[verified]`.
- Everything in English.

## Git rules

- Commit messages follow Conventional Commits: `type(scope): summary`.
  - Types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `bench`.
  - Scope: the directory or module touched (`seed`, `detect`, `writeback`,
    `agent`, `skill`, `bench`, `docs`). Omit scope only for repo-wide changes.
  - Summary: imperative, lowercase, no trailing period, at most 60 characters.
- One logical change per commit. Never mix scaffolding, features and docs in a
  single commit.
- Keep the repo clean: no build artifacts, caches, `.env` or personal files in
  git. Check `git status` before and after every commit.
- Branch names: `type/short-topic`, for example `feat/leakage-detector`. Never
  commit directly to `main`.
- Never put personal or machine-specific values (absolute paths, tokens,
  usernames, editor config) in tracked files. Personal settings belong in `.env`
  and `.claude/settings.local.json`, both git-ignored.

## Documentation rules

Three surfaces, and each owns its subjects:

| Surface | Owns |
|---|---|
| `README.md` | The whole narrative once: problem, differentiator, quickstart, what each command is for |
| [docs.ahmedxsaad.me](https://docs.ahmedxsaad.me) (`site/`) | The reference manual: every command, every flag, every configuration key |
| `docs/*.md` | The engineering explanation: architecture, detectors, evaluation, security, limitations, decisions |

A subject is explained in one place and linked from the others. When a command,
a flag or an extra changes, `site/index.html` changes in the same commit as the
README: `tests/test_docs.py` fails if a command appears in neither.

License: [Apache 2.0](LICENSE).
