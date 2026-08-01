"""Land the raw churn extract in the warehouse, the way an EL job would.

Public dataset: IBM's Telco customer churn (7043 customers, one row each).
Nothing here is ModelGuard-specific; it is the "we have a warehouse" starting
point every real project already has before any of this matters.
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text

# No default: a warehouse URL identifies a system, and a fallback in a
# committed file is somebody else's database.
ENGINE = create_engine(os.environ["WAREHOUSE_URL"])

frame = pd.read_csv(os.environ.get("TELCO_CSV", "telco.csv"))
frame.columns = [c.lower() for c in frame.columns]

with ENGINE.begin() as connection:
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS raw"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))

frame.to_sql("telco_customers", ENGINE, schema="raw", if_exists="replace", index=False)
print(f"loaded {len(frame)} rows into raw.telco_customers")
