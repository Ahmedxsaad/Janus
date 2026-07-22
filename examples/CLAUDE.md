# CLAUDE.md - examples

Sample outputs for judges, so they can evaluate quality without running
anything (recommended by the official rules). Artifacts, all generated and
committed: a Model Impact Report, a guarding-assertion YAML, an ODCS input
data contract, an incident payload JSON.

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

6. Regenerate the ODCS input contract from a seeded model scan, then lint it:

       modelguard scan --model credit_risk_v3 --no-llm --dry-run \
         --contract-out examples/input-data-contract.odcs.yaml
       datacontract lint examples/input-data-contract.odcs.yaml

   --dry-run is fine here: the contract is read-and-render, never a graph write.
   datacontract-cli is a validation tool only; install it with
   `pip install datacontract-cli` if `datacontract` is not on PATH (rule 2).

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: real-output-only and validation rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | Record the exact command that regenerates the Phase 1 artifacts |
| 2026-07-16 | Claude (for Ghassen Naouar) | Add the ODCS input contract regeneration + lint command (D-038) |
| 2026-07-22 | Claude (for Ahmed Saad) | Reword "Planned artifacts" to "Artifacts...generated and committed": all four have existed since 2026-07-13/16 |
