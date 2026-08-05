# Releasing janus-datahub to PyPI

`.github/workflows/publish-pypi.yml` builds and publishes the wheel and sdist
on a `v*.*.*` tag. It authenticates with **Trusted Publishing** (OIDC), so
there is no API token stored in this repository. That means one piece of
setup has to happen on PyPI's side, once, before the first release.

**Status, 2026-08-01:** the pending publisher has been created on PyPI, so the
one-time setup below is done. Nothing is published yet: the authoritative
check, `https://pypi.org/simple/janus-datahub/`, still returns 404. The
first release is deliberately deferred until closer to submission, so the
published version matches the final submitted state rather than an
intermediate one. Skip to [Cutting a release](#cutting-a-release).

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
- [ ] **Decide the repository rename** (P1-1), and if it goes ahead, edit the
      PyPI pending publisher *first*. The publisher matches on
      `Repository name: DataHub`, and GitHub's redirect does not help, because
      the OIDC claim carries the new name. Renaming after publishing is strictly
      worse than before, so this is a decision that has to be made now rather
      than deferred again (D-076).

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

## Verifying a release

```bash
pip install janus-datahub==0.1.0
janus --help
```

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
