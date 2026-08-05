# Model Impact Report: ecommerce.public.loans_raw

**Severity: critical** | Models at risk: 1 (live: 1) | Run: `scan-10f6b20fd2d2`

## What happened

`ecommerce.public.loans_raw` last changed 30.0 hours ago, against a freshness SLA of 6.0 hours. Freshness was measured from the dataset's `operation` aspect, which is DataHub's own record of when the table last changed.

## Assessment

ecommerce.public.loans_raw has been stale for 30.0 hours, and 1 model(s) behind a live endpoint (Credit Risk v3) are scoring production traffic on features derived from it. Predictions are being served against inputs that no longer reflect the source system, and nothing in the serving path would surface that. Refresh the upstream table, then confirm the affected endpoints recover before trusting their output.

## Blast radius

Traversed downstream from the failing table across column-level warehouse lineage and into the ML graph.

- Downstream datasets: 1
- Downstream features: 2
- Models reached: 1

### Credit Risk v3

- URN: `urn:li:mlModel:(urn:li:dataPlatform:mlflow,credit_risk_v3,PROD)`
- Severity: **critical**
- Distance from the failing table: 3 lineage hops
- Serving status: **Live**, serving through 1 deployment(s).
- Ownership: **Unowned**: nobody is on the hook to fix this.
- Features fed by the failing table:
  - `urn:li:mlFeature:(credit_risk,applicant_income)`
  - `urn:li:mlFeature:(credit_risk,prior_default_flag)`

## What Janus did

1. Raised a `FRESHNESS` incident on the failing table.
2. Tagged every at-risk model so it surfaces in search.
3. Recorded the risk flags as structured properties on each model.
4. Left a guarding freshness assertion on the failing table, with the result of this evaluation attached, so the next stale load is caught rather than discovered.

## Caveats

Freshness here is derived from metadata DataHub already holds. Janus did not query the warehouse. Scheduled evaluation of assertions and anomaly detection are DataHub Cloud features; the check logic above is Janus's own.
