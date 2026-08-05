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

- Phases are ordered by dependency. J-00 unblocks almost everything; J-01
  unblocks J-02; J-05 gates J-08.
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

## Phase 0: Rename the repository (J-00)

**Decided: it happens** (P1-1, deferred twice since D-076). It goes first
because it is the item with the widest blast radius, and every one of the
consequences below gets strictly worse if it happens after the thing it
touches. Do it in one sitting, in this order.

- [ ] **[decision] The name.** One call, and it is final in practice: GitHub
      redirects the old URL, but the OIDC claim, any external link a judge
      saves, and the Cloudflare project connection all carry the new one.
- [ ] Edit the **PyPI pending publisher first**, before the rename and before
      any tag. It matches on `Repository name: DataHub`, and GitHub's redirect
      does not help because the OIDC claim carries the new name. A mismatch
      here does not warn, it rejects the upload.
- [ ] Rename on GitHub (Settings -> General -> Repository name).
- [ ] Fix the absolute URLs. The README's links were deliberately made absolute
      `https://github.com/Ahmedxsaad/DataHub/...` so PyPI would resolve them
      (all 22 of them 404'd once already). Every one now points at the old
      name. Sweep the whole tree, not just the README:
      `grep -rn "Ahmedxsaad/DataHub" --exclude-dir=.git .`
- [ ] Update the clone URL in `deploy/` cloud-init, which is what the demo VM
      provisions from, and in `docs/deploy/skills-pr-runbook.md`, whose
      fallback install line is a `git+https://` URL.
- [ ] Re-point local remotes and any open branches:
      `git remote set-url origin <new>`.
- [ ] Confirm the two publish workflows still resolve: push a no-op commit and
      check `publish-pypi.yml` and `publish-image.yml` are green, before a tag
      makes it expensive to find out.
- [ ] Note the GHCR image path changes with the repository name. Anywhere the
      README or the site tells a judge to `docker run` a `ghcr.io/...` path,
      that path is now wrong.

Blocks: J-01 (the publisher must match), J-06 (the VM clones from it), J-07
(Cloudflare connects to it), and every doc link in J-03.

---

## Phase 1: Publish the package (J-01)

Runbook: `docs/deploy/pypi-release.md`. Its pre-tag checklist is already
mechanically verified except for the rename, which is now J-00.

- [ ] J-00 complete, including the pending-publisher edit. The runbook's one
      open pre-tag box is closed by that phase, not by this one.
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
- [ ] **Rewrite the README so it is concise.** It is the first and often only
      thing a judge reads, and right now it is 25 sections and roughly 850
      lines, opening on the product and then absorbing the entire user manual:
      install, every command, the Python API, MCP, Argos, Helm, security, a
      governance mapping. That material is not wrong, it is misplaced, because
      `site/` already is the manual. The README's job is to answer, above the
      fold, what this is, what nothing else does, and how to run it once. Move
      the reference material to the site (J-07 publishes it), keep a short
      table of links, and cut anything that survives only because it was true
      when it was written.
- [ ] Update the README's Documentation table to match. It currently links six
      plan docs by name, including `04`, `06` and `07`.
- [ ] Re-check every README link after J-00 renamed the repository, and after
      this rewrite. The links are absolute so PyPI can resolve them, which
      means nothing in the tooling catches one that is stale.
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

Blocks J-08: do not record a demo against a package that has not been
installed clean.

---

## Phase 6: Reconfigure the demo VM (J-06)

Runbook: `docs/deploy/azure-vm.md`. The VM was verified end to end on
2026-07-30 and **has not picked up the codebase since**. Everything landed
after that date is absent from the machine a judge will open: the depth-axes
work, the adapters, Argos, and D-136's repo-wide rename of package and brand
identifiers. That last one is the dangerous case, because a stale checkout
plus renamed identifiers does not fail loudly, it fails as a service that
starts and finds nothing.

- [ ] Establish what is actually running before changing anything: SSH in and
      record the commit the working tree is on, the installed package version,
      and whether `janus-watch` is alive or has been dead since a crash.
      `/var/log/cloud-init-output.log` is where every provisioning step's real
      output landed.
- [ ] **[decision] Update in place, or re-provision from cloud-init.** In place
      is faster; re-provisioning is the only thing that proves the runbook
      still works, which matters because the runbook is what a judge would
      follow. Given D-136 renamed identifiers and D-063 was a provisioning race,
      re-provisioning is the honest choice and it also tests J-00's clone URL.
- [ ] Pull the current main (post J-00, so the new remote), reinstall the
      package, and restart the stack.
- [ ] Re-verify the checks the runbook's "Verify the demo works" section
      already lists, rather than inventing new ones: swap is present (D-071:
      without it OpenSearch OOM-crashes and search silently dies while the UI
      keeps answering), OpenSearch `RestartCount` is not climbing, and a real
      GMS search query returns real data.
- [ ] Re-seed so the graph shows current output: live incidents, tags, trust
      scores and impact reports produced by today's detectors, not July's.
- [ ] Confirm the frontend password is still the changed one and that a login
      actually works, from a machine that is not the provisioning one.
- [ ] Confirm the custom domain and HTTPS still resolve, and that the
      certificate has not expired inside the judging window (Aug 17-31).
- [ ] Check the NSG is still the intended shape: 9002 open, 8080 never, SSH
      scoped rather than open to the world.
- [ ] Decide the uptime plan. The runbook documents `deallocate` to stop the
      compute meter between now and judging. Whatever is chosen, the VM must be
      up and seeded for the whole judging period, and someone has to own
      starting it.

---

## Phase 7: Deploy the site to Cloudflare (J-07)

`site/` is a static page with no build step. It was deliberately made
self-contained in D-139: the sprite art is inlined into `site/pixels.js`
rather than fetched, precisely because a deployment serves `site/` as its
root and `../argos/` would 404. So this is a configuration task, not a
porting one.

- [ ] Create the Cloudflare Pages project connected to the renamed repository
      (J-00 first, or the connection points at a name that is about to change).
      Root directory `site`, no build command, no framework preset.
- [ ] Verify the deployed page against the one real hazard: `ArgosSprites.load()`
      in `site/pixels.js` still fetches `sprites/argos.txt`, a path that does
      not exist under `site/`. It is documented as unused and nothing calls it,
      but confirm the deployed page's console is clean, because the failure
      mode here is a page that renders perfectly with no dog on it, which is
      how it shipped that way once already.
- [ ] Confirm `tests/test_site.py` still passes: it fails if the generated
      art in `site/pixels.js` and the originals under `argos/ui/sprites/`
      disagree, which is what keeps the page honest.
- [ ] Check the page in both a cold browser and a phone. Judges browse on
      whatever is in front of them.
- [ ] **[decision] The domain.** A `*.pages.dev` URL is free and instant; a
      subdomain of the domain already used for the demo VM reads as more
      finished. Either is fine, but the choice determines what goes on the
      Devpost form.
- [ ] Point the README and the Devpost Project URL at the deployed site, and
      make the site link the demo VM and the repository so all three are
      reachable from any one of them.
- [ ] If J-03 moved reference material out of the README and into the site,
      confirm it actually landed there before deleting it from the README.

---

## Phase 8: Demo video (J-08)

Requirements, from `docs/hackathon-specs/03-submission-requirements.md`:
under 3 minutes, shows the project actually functioning, publicly visible on
YouTube/Vimeo/Youku, English, no third-party trademarks or copyrighted music.

**Decided: the video runs against a real project, not a seeded scenario.** A
judge discounts a demo on fixture data, and rightly. `examples/real-project/`
already exists for this reason: the dbt + MLflow + postgres stack Janus was
validated against as an ordinary user would (D-074), and scored on the graph
its own ingestion builds (D-121). Using it means the video shows the same
thing the benchmark measured.

- [ ] **[decision] Which real project.** `examples/real-project/` is the
      obvious answer and it is already wired, benchmarked and reproducible. An
      outside project would be more persuasive still, but it has to be
      ingestible, non-confidential, and working before Aug 10, which is four
      days. Pick one and stop.
- [ ] Run the real project end to end **before** scripting, and record what
      actually happens. The script is written from the run, not the run staged
      to match a script. If the detectors find something unflattering on real
      data, that is the video, not a problem with it.
- [ ] Keep the narrative single: a silent data-to-model failure that
      table-level lineage cannot see, caught on this real stack, scored, and
      written back into DataHub as an incident a human then opens in the UI.
      Three minutes is one story, not a feature tour.
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

## Phase 9: Submission gate (J-09)

Every box in `docs/hackathon-specs/03-submission-requirements.md`, checked
against the real submission form, on the day.

- [ ] Project URL that gives judges easy access. There are now three surfaces
      and they must agree: the Cloudflare site (J-07), the Azure demo VM
      (J-06), and the renamed repository (J-00). Decide which one is the
      Project URL and make sure it links the other two.
- [ ] Public repository, Apache 2.0 license file present and **visible in the
      GitHub About section**. Confirm it shows there after the rename, not just
      that `LICENSE` exists.
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
