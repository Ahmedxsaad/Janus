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
5. Regenerate the Phase 1 artifacts with a real scan against a seeded Quickstart:

       modelguard-seed
       modelguard-scenario --lag-hours 30
       modelguard scan --table loans_raw --no-llm \
         --report-out examples/impact-report-credit-risk-model.md \
         --assertion-out examples/guarding-assertion-loans-raw.yml

   Use --no-llm so the committed report is reproducible by anyone, with or
   without an API key. The run_id changes on every regeneration; that is real
   output, not churn to suppress.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: real-output-only and validation rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | Record the exact command that regenerates the Phase 1 artifacts |
