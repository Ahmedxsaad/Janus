# Security Policy

## Supported versions

Janus is pre-1.0 (currently `0.1.x`). Only the latest release gets security
fixes; there is no parallel maintenance of older versions.

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability. Instead,
use GitHub's private reporting for this repository:

**[Report a vulnerability](https://github.com/Ahmedxsaad/Janus/security/advisories/new)**

This opens a private conversation with the maintainer only, until a fix is
ready to disclose. If you'd rather not use GitHub, email
ahmedsaadcontactpro@gmail.com with the same information.

Include what you'd include in any report: the affected version, a
description of the issue, and steps to reproduce it if you have them.

There's no bug bounty program. You'll get a response acknowledging the
report, and credit in the fix's changelog/release notes unless you'd rather
stay anonymous.

## Scope

Janus reads from and writes to a DataHub instance you point it at; it holds
no credentials of its own beyond what you configure. Reports about Janus's
own code (the CLI, the detectors, Argos, the write-back paths) are in scope.
Reports about DataHub itself belong in
[datahub-project/datahub](https://github.com/datahub-project/datahub).
