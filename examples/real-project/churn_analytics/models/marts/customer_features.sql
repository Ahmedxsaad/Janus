-- The feature table the churn model trains on.
--
-- contract_renewed_flag is the mistake this example is here to be caught making,
-- and it is the ordinary kind: an analyst wants "did they stay with us", and the
-- only column in the warehouse that answers it is the churn outcome itself. It
-- reads as a perfectly reasonable account-health feature and it is the label
-- inverted, so the model scores beautifully offline and is worthless in
-- production, where nobody knows yet whether the customer will churn.
--
-- Delete that last line to play the fix: rebuild, re-ingest, re-link, rescan,
-- and Janus closes the incident it raised.
select
    customer_id,
    tenure_months,
    monthly_charges,
    total_charges,
    contract_type,
    internet_service,
    payment_method,
    case when churn = 'No' then 1 else 0 end as contract_renewed_flag
from {{ ref('stg_customers') }}
