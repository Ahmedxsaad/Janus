# Janus - Judge Readiness Checklist

> The remaining work between the merged codebase and a submission a judge can
> read, run, and score. Everything here is delivery and presentation. No new
> product features: if an item asks for one, it is out of scope and belongs in
> a separate plan doc.
>
> Deadline for all of it: **Aug 10, 2026, 5:00pm ET** (docs/hackathon-specs/06).
>
> Tasks carry a stable id (`J-01`). Use it in commit bodies and decision-log
> entries so a task traces from plan to commit to decision without a search.

## How to use this file

- Phases are ordered by dependency. J-01 unblocks J-02; J-05 gates J-06.
- Tick a box only when it has actually been run, not when it looks right.
- Items marked **[decision]** are not work, they are a choice someone has to
  make. They block the items under them. Log each one in decision-log.md.
- The runbooks already in `docs/deploy/` are the source of truth for the
  mechanical steps. This file does not repeat them, it sequences them.

---

## Standing definition of done

Applies to every task below.

- [ ] `pre-commit run --all-files` clean (ruff lint, ruff format, mypy strict).
- [ ] `git status` clean before and after each commit: no build artifacts, no
      caches, no `.env`, no personal files (root rule, git rules).
- [ ] No em dashes, no emojis, English only (root formatting rules).
- [ ] Every claim in a judge-facing doc is either verified against the code or
      marked `[confirm]`. A doc for judges that overstates is worse than one
      that omits.
- [ ] Any decision taken while doing the task gets a decision-log entry the
      same day (root workflow rule 4).

---

## Phase 1: Publish the package (J-01)

Runbook: `docs/deploy/pypi-release.md`. Its pre-tag checklist is already
mechanically verified except for one item, which is a decision, not work.

- [ ] **[decision] Settle the repository rename (P1-1, D-076).** This is the
      single open box in the runbook's pre-tag checklist and it has been
      deferred twice. The PyPI pending publisher matches on
      `Repository name: DataHub`; GitHub's redirect does not help because the
      OIDC claim carries the new name. Renaming after publishing is strictly
      worse than before. Decide yes or no, and if yes, edit the pending
      publisher on PyPI **before** tagging.
- [ ] Re-run the runbook's pre-tag checklist against a freshly built wheel.
      It was checked on 2026-08-02; the tree has moved since (D-136 renamed
      identifiers repo-wide, which is exactly the class of change that breaks
      console scripts and packaged data files).
- [ ] Confirm `janus.__version__` still equals `pyproject.toml`'s `version`
      (`tests/test_api.py` enforces it, so this is a test run, not a read).
- [ ] Bump `version` in `pyproject.toml` in its own commit, through the normal
      PR flow. Decide the number: `0.1.0` as written, or a version that says
      "this is the submitted state".
- [ ] Tag the merge commit on main and push the tag. Note this fires
      `publish-image.yml` as well, so the GHCR image ships at the same moment.
- [ ] Verify with the honest check, not the project page:
      `curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/simple/janus-datahub/`
      must return `200`.
- [ ] Approve the `pypi` GitHub environment if required reviewers are enabled.

Blocks: J-02, and the install line in every judge-facing doc.

---

## Phase 2: Land the OSS contributions (J-02)

Plan: `docs/plan/05-oss-delivery.md`. Runbook: `docs/deploy/skills-pr-runbook.md`.

- [ ] Open the `datahub-ml-guard` PR to `datahub-project/datahub-skills`. The
      runbook is prepared and its only blocker is J-01: the skill declares
      `allowed-tools: Bash(janus *)` and every script shells out to that CLI,
      so a reviewer who cannot `pip install janus-datahub` gets nothing.
      Do not submit against a 404.
- [ ] Work the runbook's section 5: diff against `skills/datahub-enrich/`,
      confirm the frontmatter field set, and decide whether the
      `using-datahub/` routing table and the `.claude-plugin` manifest need an
      entry for discoverability.
- [ ] `pre-commit run --all-files` inside the fork (prettier, markdownlint-cli2,
      ruff) before opening the PR.
- [ ] Check on `acryldata/mcp-server-datahub` PR #188 (the `raise_incident`
      mutation tool). It is already open. If review has moved, respond; if it
      has stalled, that is fine and is stated as upside, not a gate.
- [ ] Reconcile the finding count in `docs/most-valuable-feedback.md` before
      submitting it: the doc's header says 16 findings and
      `05-oss-delivery.md` section 8.3 says 12. One of them is wrong, and a
      feedback submission that miscounts its own contents is an easy thing for
      a judge to notice.
- [ ] **[decision]** Whether to file the three sharpest findings as individual
      upstream GitHub issues and link them from the survey (05-oss-delivery
      section 8.3 lists them). Strengthener, not required.

---

## Phase 3: Rewrite docs/plan/ as explanatory documents (J-03)

**Decided 2026-08-06:** the plan docs are rewritten in place into documents
whose only job is to explain the built product to a reader who has never seen
the repository. They stop being a build plan.

This conflicts with docs/CLAUDE.md rule 1 ("never let the plan silently rot")
and with the convention from `10-depth-implementation.md` that a wrong task is
struck through rather than deleted. Both exist to preserve the trail that
`decision-log.md` references by section number. Resolve it explicitly rather
than by accident:

- [ ] **[decision] Where the build history goes.** The decision log cites plan
      docs by section (`plan/09 section 3.1`, `plan/10 phase 7`, and dozens
      more). Rewriting in place breaks every one of those references unless
      the originals survive somewhere. Options: move the internal docs to
      `docs/plan/history/` untouched and rewrite the top level; or accept the
      broken references and say so in the decision-log entry. Pick one, log it,
      and update docs/CLAUDE.md rule 1 to match whichever it is.

Then, doc by doc. The target reader is a judge with fifteen minutes and no
clone.

- [ ] `01-strategy-janus.md` -> what problem Janus solves and why it is not
      solved by table-level lineage or by a data-quality tool. Strip the
      category analysis and the Week 1 fallback: those are decisions we made,
      not information a reader needs.
- [ ] `architecture.md` -> the reference explanation of how the built system
      works. It is already closest to the target shape. Verify every layer,
      component and flow against the code as it stands today rather than as
      planned; anything that drifted gets corrected, not deleted.
- [ ] `02-implementation-plan.md` -> either a description of what was built
      (phases become capabilities) or it is retired to history. It is 880
      lines of build schedule and is the least useful doc for a judge in its
      current form.
- [ ] `03-production-hardening.md` -> the security, scaling and benchmark
      story, stated as what the product does, not as a plan to do it.
- [ ] Retire to history (or delete, per the decision above): `04-improvements.md`,
      `06-judge-review-and-improvements.md`, `07-weaknesses-and-remedies.md`,
      `08-watchdog-mascot.md`, `09-depth-axes.md`, `10-depth-implementation.md`,
      and this file once it is spent. Every one of these documents is a record
      of internal deliberation. `07` in particular lists 18 known weaknesses,
      which is exactly the document a judge should not read first.
- [ ] Update the README's Documentation table to match. It currently links six
      plan docs by name, including `04`, `06` and `07`.
- [ ] Update `docs/CLAUDE.md` (its layer description, rules 1 and 3, and the
      change log) to describe the new layout.
- [ ] Re-read `docs/submission-description.md` against the rewritten docs so
      the Devpost text and the repo tell the same story.

---

## Phase 4: Architecture diagrams (J-04)

**Decided 2026-08-06:** PlantUML source inside the markdown, per docs/CLAUDE.md
rule 4. Mainly sequence diagrams.

- [ ] **[decision] How a judge sees the picture.** GitHub does not render
      PlantUML: a judge reading the repo in a browser sees raw source, which is
      worse than no diagram. `architecture.md`'s existing diagrams already have
      this problem. Options: commit rendered SVGs beside the source; use the
      public `plantuml.com/plantuml/svg/...` proxy as an image link (no build
      step, but an external dependency in the doc); or accept raw source.
      Nothing below is worth doing until this is settled.
- [ ] `plantuml` is **not installed** on this machine (checked 2026-08-06).
      If rendering is the chosen option, install it
      (`sudo apt install plantuml`, which pulls a JRE) or render in CI.

Sequences to draw. One per diagram, each showing the actors that actually
exist in the code, not a conceptual sketch:

- [ ] **`scan`, end to end.** CLI -> client -> lineage read -> deterministic
      detectors -> LLM (wording only) -> idempotent write-back. This is the
      one diagram that proves design law 4: the model is wired to the text and
      to nothing else.
- [ ] **`link`, including `--infer` and `--from`.** How the model-to-column
      join is obtained: declared, inferred, or imported from a Feast repo or
      dbt semantic model by the read-only adapters.
- [ ] **Idempotent write-back.** Read-before-write against active incidents,
      the `(resource_urn, incident_type, title)` key, and why `run_id` is
      deliberately outside it (D-013). A rerun must visibly produce zero new
      writes in the diagram.
- [ ] **`gate`.** The CI path: what is read, what makes it exit non-zero, and
      what the developer sees.
- [ ] **`watch`, the reactive loop.** The long-running entry point, including
      the graph-event trigger rather than the timer.
- [ ] **Argos.** The event stream `janus/argos/` writes to the window's stdin,
      and the state it drives. Optional: it is charm, not mechanism, and it is
      already explained on the site.
- [ ] Keep diagrams conceptual, no file names inside them (docs/CLAUDE.md
      rule 4).
- [ ] Place them in the rewritten `architecture.md` next to the prose they
      replace, and delete the paragraphs that now say the same thing twice.

---

## Phase 5: Test the package as a user (J-05)

Nothing here runs in the development tree. Every step is a throwaway
virtualenv: installing over an editable install silently shadows the wheel,
and a broken package still looks fine because the local source is what ran.

- [ ] Fresh venv, `pip install janus-datahub` from PyPI (not from the repo).
- [ ] All four console scripts resolve and run: `janus`, `janus-seed`,
      `janus-scenario`, `janus-mcp`. `--help` on each.
- [ ] `import janus` exposes the documented public API (`link_model`,
      `scan_model` and their result types) with nothing missing and nothing
      that only works from a source checkout.
- [ ] Packaged data is present: `janus/writeback/props/*.yaml` inside the
      installed package, or `define_properties` fails and no scan can write a
      trust score.
- [ ] Walk the README's "Try it" section verbatim, as a first-time reader,
      against a live DataHub. Every command in it, in order, copy-pasted. Any
      command that needs a step the README does not state is a bug in the
      README.
- [ ] Walk "Use it on your own project" the same way, including the
      `link --from` adapter paths.
- [ ] Confirm the failure modes are legible: run with a missing `.env` key and
      check the error names the variable (root rule 6a) and leaks no secret
      (rule 6d).
- [ ] Repeat the install on a second Python version. `pyproject.toml` claims
      3.11, 3.12 and 3.13; 3.12 is verified, 3.13 is claimed. Either verify it
      or narrow the claim.
- [ ] Test the container path too: `docker run` the GHCR image published by
      the tag, since the README offers it as the no-Python-install route.

Blocks J-06: do not record a demo against a package that has not been
installed clean.

---

## Phase 6: Demo video (J-06)

Requirements, from `docs/hackathon-specs/03-submission-requirements.md`:
under 3 minutes, shows the project actually functioning, publicly visible on
YouTube/Vimeo/Youku, English, no third-party trademarks or copyrighted music.

- [ ] **[decision] The one story the video tells.** Three minutes is one
      narrative, not a feature tour. The obvious candidate is the thing nothing
      else does: a silent data-to-model failure that table-level lineage cannot
      see, caught, scored, and written back into DataHub as an incident a human
      then opens in the UI. Confirm or replace it.
- [ ] Write the script and time it read aloud before recording. Aim for 2:30 so
      the cut is not frantic.
- [ ] Prepare the environment: which DataHub, which seeded scenario, which
      terminal, which font size. A judge watching on a laptop cannot read a
      12pt terminal.
- [ ] Record the DataHub UI showing the write-back, not just the CLI. The
      write into the graph is the differentiator; a terminal-only video shows a
      linter.
- [ ] Show Argos briefly if the story has room. It is memorable and costs
      seconds.
- [ ] No copyrighted music. Silence with narration is safer and reads as more
      serious anyway.
- [ ] Upload publicly and confirm the link opens in a private browser window.
      An unlisted-but-broken link is the classic failure here.

---

## Phase 7: Submission gate (J-07)

Every box in `docs/hackathon-specs/03-submission-requirements.md`, checked
against the real submission form, on the day.

- [ ] Project URL that gives judges easy access: the repo with clear setup
      instructions, and the Azure demo VM if it is up
      (`docs/deploy/azure-vm.md`).
- [ ] Public repository, Apache 2.0 license file present and **visible in the
      GitHub About section**. Confirm it shows there, not just that `LICENSE`
      exists.
- [ ] Text description submitted (`docs/submission-description.md`, refreshed
      against the rewritten docs from J-03).
- [ ] Demo video link, public, under 3 minutes.
- [ ] Sample outputs in `examples/` are current and readable
      (recommended item, cheap to satisfy, and we already have them).
- [ ] Most Valuable Feedback survey submitted, opted in, within the Feedback
      Period, one per entrant.
- [ ] If the demo VM is part of the submission, confirm it is reachable and
      stays up through the judging period, and that any credentials the judges
      need are in the testing instructions.

---

## What is deliberately not in this checklist

- New detectors, new adapters, new depth axes. `09` and `10` are closed; work
  that is not delivery does not belong in the last four days.
- Fixing the open weaknesses in `07`. They are known, documented, and honest.
  Rewriting them into the product now risks breaking what is measured in
  `benchmarks/RESULTS.md`.
- Re-running the benchmark, unless J-05 finds the package behaves differently
  from the source tree. If it does, that is a release bug, not a benchmark
  task.
