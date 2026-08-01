-- One clean row per customer: types fixed, the churn outcome kept as it came.
select
    customerid                                          as customer_id,
    case when tenure = 0 then null else tenure end      as tenure_months,
    monthlycharges                                      as monthly_charges,
    nullif(trim(totalcharges), '')::numeric             as total_charges,
    contract                                            as contract_type,
    internetservice                                     as internet_service,
    paymentmethod                                       as payment_method,
    churn                                               as churn
from raw.telco_customers
