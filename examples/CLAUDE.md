# CLAUDE.md - examples

Sample outputs for judges, so they can evaluate quality without running
anything (recommended by the official rules). Planned artifacts: a Model
Impact Report, a guarding-assertion YAML, an ODCS input data contract, an
incident payload JSON.

## Local rules

1. Every artifact here is real generated output from a ModelGuard run, never
   handwritten mockups. Regenerate all of them whenever output formats change.
2. Validate the ODCS contract with datacontract-cli before committing it
   (check the tool is installed first).
3. No secrets, no tokens, no personal URLs in any artifact. URNs must come
   from the seeded demo graph only.
4. Name files by content, kebab-case, for example
   impact-report-credit-risk-model.md.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: real-output-only and validation rules |
