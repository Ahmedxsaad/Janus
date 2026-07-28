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
