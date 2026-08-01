# CLAUDE.md - deploy

Infrastructure-as-code for the judge-facing demo VM. `docs/deploy/azure-vm.md`
is the runbook; the files here are what it actually installs. Only Azure
exists for now (`azure/`); a second target gets a sibling directory, not a
branch inside this one.

## Local rules

1. Nothing in this directory has been run against a real target. State that
   plainly wherever a file makes a claim about what it does, the same way
   `benchmarks/CLAUDE.md` rule 6 states the benchmark's own limits. A script
   that silently overclaims what was verified is worse than one that says so.
2. Validate everything that can be validated without the real target, and
   name the tool that did it: `bash -n` for shell fragments,
   `systemd-analyze verify` for unit files, a real YAML parser for
   cloud-init's structure. "Looks right" is not a substitute for a tool that
   actually checked.
3. GMS (port 8080) never gets an inbound network rule, at any layer, for any
   reason. A Quickstart's metadata-service authentication is disabled by
   default (the judge's out-of-the-box path everywhere else in this repo),
   which makes an internet-reachable GMS an unauthenticated write API. The
   frontend (9002) is the only thing meant to be public, and it has its own
   login.
4. Prefer a service failing fast and retrying (`Restart=always` with a real
   backoff) over getting startup ordering between independent systems exactly
   right. `modelguard watch` already fails loudly and specifically when
   DataHub is not yet reachable (modelguard/client.py); leaning on that
   instead of a fragile `After=`/`Requires=` chain is reuse of a boundary the
   code already draws correctly, not a shortcut.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-23 | Claude (for Ahmed Saad) | Initial version: azure/cloud-init.yaml, azure/modelguard-watch.service (D-057) |
| 2026-07-29 | Claude (for Ahmed Saad) | docs/deploy/azure-vm.md's defaults change to Standard_B2ms + 64GB disk + provision-now-pause-until-judging, to actually fit a $60 budget; B4ms run continuously priced out to roughly double it (D-059) |
| 2026-07-29 | Claude (for Ahmed Saad) | docs/deploy/azure-vm.md's size/region change to Standard_D2as_v5 in francecentral, real Azure Portal pricing beats the earlier web-search estimate by roughly 3-4x for identical specs (D-060) |
| 2026-07-29 | Claude (for Ahmed Saad) | Reverted: D-060's price was an Azure Spot bid, not standard, and D2as_v5 was quota-blocked. Back to Standard_B2as_v2 in francecentral, Spot never used (D-061) |
| 2026-07-29 | Claude (for Ahmed Saad) | cloud-init.yaml's git clone uses a __GITHUB_CLONE_TOKEN__ placeholder (repo stays private for now); real token substituted only outside git and revoked after first provision (D-062) |
| 2026-07-29 | Claude (for Ahmed Saad) | A real VM boot found write_files racing azureuser's own creation, cascading into a Permission Denied git clone. .env write moves into runcmd, write_files removed (D-063) |
| 2026-07-29 | Claude (for Ahmed Saad) | Custom domain + HTTPS via Caddy verified live (`https://modelguard.ahmedxsaad.me`); fixed the frontend password-change instructions to the real user.props mechanism; azure/Caddyfile.template added (D-064) |
| 2026-07-30 | Claude (for Ahmed Saad) | Found OpenSearch had OOM-crashed and stayed dead 6h with no restart policy, silently breaking search while health checks kept passing; cloud-init.yaml now sets restart: unless-stopped on all datahub-* containers (D-065) |
| 2026-07-30 | Claude (for Ahmed Saad) | Deleted and recreated the VM from the current cloud-init.yaml to prove it works from a genuine cold boot, not just a patched running VM; zero errors, all containers healthy with the D-065 fix already applied (D-066) |
| 2026-07-30 | Claude (for Ahmed Saad) | Redeployed the live VM onto D-067's fixes (it had cloned before that PR merged); found modelguard-watch.service's ExecStop does not actually stop docker compose run containers, a minor restart race left as a known gap (D-068) |
| 2026-08-01 | Claude (for Ahmed Saad) | The VM had no swap at all, which is what was actually failing OpenSearch's native thread-stack allocation (not a thread or PID ceiling: 1150 threads against a 62881 max). cloud-init.yaml now creates a guarded 4GB swapfile and fstab entry (D-071) |
| 2026-08-01 | Claude (for Ghassen Naouar) | D-068's ExecStop gap is not the minor async race it was logged as: `docker compose stop` silently stops nothing for a `compose run` container, so the container outlives the unit and every later ExecStart dies on a name conflict, with Restart=always retrying forever. Observed wedging the live VM. ExecStop targets the container with plain `docker stop`, and a new `ExecStartPre=-docker rm -f` makes the unit genuinely self-healing (D-073) |
