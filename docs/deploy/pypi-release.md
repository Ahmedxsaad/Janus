# Releasing janus-datahub to PyPI

`.github/workflows/publish-pypi.yml` builds and publishes the wheel and sdist
on a `v*.*.*` tag. It authenticates with **Trusted Publishing** (OIDC), so
there is no API token stored in this repository. That means one piece of
setup has to happen on PyPI's side, once, before the first release.

**Status, 2026-08-06:** janus-datahub 0.1.0 is published;
`https://pypi.org/simple/janus-datahub/` returns 200. The one-time setup below
is done for it and does not need repeating. `janus-argos` is a second, separate
project and is **not** published: its `/simple/` index still returns 404, which
is what makes `pip install "janus-datahub[pet]"` fail to resolve on macOS and
Windows. Its own one-time setup is in
[The second distribution](#the-second-distribution-janus-argos).

Do not check whether a project exists by loading `pypi.org/project/<name>/` in
a script: PyPI answers automated requests with a bot-challenge page that
returns HTTP 200 regardless, which reads as "it exists" when it does not. The
`/simple/` index is the honest check.

## One-time setup, on PyPI

`janus-datahub` does not exist on PyPI yet, so use the **pending
publisher** flow, which reserves the name and the publisher together and
requires no pre-existing project.

1. Sign in at [pypi.org](https://pypi.org) (create an account if needed, and
   enable 2FA, which PyPI requires for publishing).
2. Go to **Your account -> Publishing -> Add a new pending publisher**.
3. Fill in exactly:
   - PyPI project name: `janus-datahub`
   - Owner: `Ahmedxsaad`
   - Repository name: `DataHub`
   - Workflow name: `publish-pypi.yml`
   - Environment name: `pypi`
4. Save.

The environment name matters: the workflow's `publish` job declares
`environment: name: pypi`, and PyPI will reject the upload if the identity it
receives does not match the publisher exactly.

## Optional, recommended: require an approval on the environment

The tag gate already means a release is deliberate. Adding a reviewer on the
GitHub environment adds a second pair of eyes on something that cannot be
undone (a yanked release still burns its version number forever).

In the repository: **Settings -> Environments -> New environment -> `pypi`**,
then tick **Required reviewers** and add whoever should approve a release.

## Before the first tag

Checked mechanically on 2026-08-02 against the built wheel, except where noted.

- [x] `python -m build --wheel` succeeds, and `twine check` passes on the result,
      which is what says the long description will render on PyPI rather than
      landing as a wall of unformatted text.
- [x] The README's links are absolute `https://github.com/...` URLs. They used to
      be repository-relative, and PyPI resolves a relative link against
      `pypi.org`, so all 22 of them 404'd for the first person to arrive from
      `pip install`. GitHub renders absolute links identically, so one form
      serves both.
- [x] `janus/writeback/props/*.yaml` is inside the wheel: without it,
      `define_properties` fails on a fresh install and no scan can write a trust
      score.
- [x] All four console scripts (`janus`, `janus-seed`,
      `janus-scenario`, `janus-mcp`) are installed and run.
- [x] `import janus` exposes the public API (`link_model`, `scan_model` and
      their result types) from a clean install.
- [x] `janus.__version__` equals `pyproject.toml`'s `version`. A test
      enforces it (`tests/test_api.py`), because a wheel whose two versions
      disagree is one nobody can report a bug against: the user reads one, the
      resolver reads the other.
- [x] **Repository rename decided and done** (P1-1, D-144): `Ahmedxsaad/DataHub`
      is now `Ahmedxsaad/janus`. No pending publisher existed yet to edit (the
      one referenced by the 2026-08-01 status line further up was itself a
      casualty of the product rename, D-142), so create it fresh, once, with
      `Repository name: janus` matching what the OIDC claim will actually
      carry. Do this before cutting the first release, not after.

The install check above must be done in a **throwaway** virtualenv, never the
development one: installing the published package over an editable install of
the same project silently shadows it, and a broken wheel would still look fine
because the local source tree is what actually ran.

## Cutting a release

The workflow refuses to publish when the tag and `pyproject.toml` disagree, so
bump the version first, in its own commit, through the normal PR flow:

```bash
# 1. bump `version` in pyproject.toml, commit, PR, merge to main
git checkout main && git pull origin main

# 2. tag the merge commit and push the tag
git tag v0.1.0
git push origin v0.1.0
```

Pushing the tag fires both publish workflows: this one, and
`publish-image.yml`, which pushes the container image to GHCR.

## The second distribution: janus-argos

`janus-datahub[pet]` depends on `janus-argos`, a **separate PyPI project**
built by `.github/workflows/build-argos.yml`. It has to be separate: the
window is a Rust binary, so it is one platform wheel per OS, while
janus-datahub is a single pure wheel for all of them
(`argos/pyproject.toml`). Two distributions means two pending publishers and
two release routes.

Linux is not on PyPI at all. The binary links the system webkit2gtk, which no
manylinux tag permits, so the `.deb` and the `.AppImage` are attached to the
GitHub release instead and the extra carries a
`platform_system != 'Linux'` marker.

### One-time setup, on PyPI

Same pending-publisher flow as above, with different values:

- PyPI project name: `janus-argos`
- Owner: `Ahmedxsaad`
- Repository name: `janus`
- Workflow name: `build-argos.yml`
- Environment name: `pypi`

The environment is deliberately the same one janus-datahub publishes from, so
a reviewer requirement configured once covers both. PyPI still treats these as
two distinct publishers, because the workflow filename differs.

### Cutting an Argos release

The crate versions independently, so it has its own tag namespace and a
`v0.2.0` product release never has to move `argos/Cargo.toml`:

```bash
# 1. bump `version` in argos/Cargo.toml, commit, PR, merge to main
git checkout main && git pull origin main

# 2. tag the merge commit and push the tag
git tag argos-v0.1.0
git push origin argos-v0.1.0
```

A product `v*.*.*` tag builds and publishes the window too, so the wheel exists
on the day the Python package goes out. In that case the crate version is
usually already on PyPI, which is the normal case and not a failure: the
publish step runs with `skip-existing`, uploads nothing, and passes.

## Verifying a release

```bash
pip install janus-datahub==0.1.0
janus --help
```

On macOS or Windows, the window is its own check:

```bash
pip install "janus-datahub[pet]"
python -c "import shutil; print(shutil.which('janus-argos'))"
```

Do not reach for `janus-argos --help`: it takes no arguments and opens a
window. What the wheel has to prove is that the binary landed on PATH, which
is what `janus watch --pet` looks for.

Install into a throwaway virtualenv, not the development one: installing the
published package over an editable install of the same project silently
shadows it, and a broken wheel would still look fine because the local source
tree is what actually ran.

## If a release is broken

A version number on PyPI cannot be reused, even after deletion. Yank the bad
release (**Manage project -> Releases -> Yank**), which leaves it installable
for anyone who pinned it exactly but hides it from resolvers, then fix
forward with a new patch version. Do not delete it outright: that breaks
anyone who already pinned it.
