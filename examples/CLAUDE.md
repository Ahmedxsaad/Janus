# CLAUDE.md - examples

Sample outputs for judges, so they can evaluate quality without running
anything (recommended by the official rules). Artifacts, all generated and
committed: a Model Impact Report, a guarding-assertion YAML, an ODCS input
data contract, an incident payload JSON.

`real-project/` is different in kind: not an artifact but a runnable stack
(dbt, MLflow, scikit-learn, DataHub recipes) that ModelGuard was validated
against, kept because every other number here comes from a seeded graph.

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

7. `real-project/` states what was verified and when, and nothing more. It is
   committed code somebody may run against their own warehouse, so it carries
   no absolute path, no host, and no credential: every one of those comes from
   an environment variable with no default, exactly as the package itself does
   (root CLAUDE.md rules 5 and 6a). Its claims (the AUC figures, what each
   ingestion source did and did not produce) are measurements from the run in
   D-074; if the product changes, re-run it or delete the claim.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-08 | Claude (for Ahmed Saad) | Initial version: real-output-only and validation rules |
| 2026-07-10 | Claude (for Ghassen Naouar) | Record the exact command that regenerates the Phase 1 artifacts |
| 2026-07-16 | Claude (for Ghassen Naouar) | Add the ODCS input contract regeneration + lint command (D-038) |
| 2026-07-22 | Claude (for Ahmed Saad) | Reword "Planned artifacts" to "Artifacts...generated and committed": all four have existed since 2026-07-13/16 |
| 2026-08-01 | Claude (for Ghassen Naouar) | real-project/ lands: the dbt + MLflow + postgres stack ModelGuard was validated against on a real graph, with rule 7 on what it may claim and what it may not contain (D-074) |
| 2026-08-04 | Claude (for Ghassen Naouar) | feature-repo/ lands: an ordinary Feast repo (entity, feature view, label view, feature service) that `modelguard link --from feast` imports. Unlike the artifacts above it is an input rather than generated output, and it is also the test fixture, so rule 1 does not apply to it and it must keep parsing (D-112, T-05) |
