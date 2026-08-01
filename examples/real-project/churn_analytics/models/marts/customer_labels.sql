-- The training label: did this customer churn in the observed period.
select
    customer_id,
    case when churn = 'Yes' then 1 else 0 end as churned
from {{ ref('stg_customers') }}
