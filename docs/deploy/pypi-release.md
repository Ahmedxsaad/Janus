# Releasing modelguard-datahub to PyPI

`.github/workflows/publish-pypi.yml` builds and publishes the wheel and sdist
on a `v*.*.*` tag. It authenticates with **Trusted Publishing** (OIDC), so
there is no API token stored in this repository. That means one piece of
setup has to happen on PyPI's side, once, before the first release.

**Status, 2026-08-01:** the pending publisher has been created on PyPI, so the
one-time setup below is done. Nothing is published yet: the authoritative
check, `https://pypi.org/simple/modelguard-datahub/`, still returns 404. The
first release is deliberately deferred until closer to submission, so the
published version matches the final submitted state rather than an
intermediate one. Skip to [Cutting a release](#cutting-a-release).

Do not check whether a project exists by loading `pypi.org/project/<name>/` in
a script: PyPI answers automated requests with a bot-challenge page that
returns HTTP 200 regardless, which reads as "it exists" when it does not. The
`/simple/` index is the honest check.

## One-time setup, on PyPI

`modelguard-datahub` does not exist on PyPI yet, so use the **pending
publisher** flow, which reserves the name and the publisher together and
requires no pre-existing project.

1. Sign in at [pypi.org](https://pypi.org) (create an account if needed, and
   enable 2FA, which PyPI requires for publishing).
2. Go to **Your account -> Publishing -> Add a new pending publisher**.
3. Fill in exactly:
   - PyPI project name: `modelguard-datahub`
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
pip install modelguard-datahub==0.1.0
modelguard --help
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
