"""Land the raw churn extract in the warehouse, the way an EL job would.

Public dataset: IBM's Telco customer churn (7043 customers, one row each).
Nothing here is Janus-specific; it is the "we have a warehouse" starting
point every real project already has before any of this matters.

Loaded through SQLAlchemy Core directly, not ``DataFrame.to_sql()``: this
project's own ``feast`` extra pins SQLAlchemy 1.4, and pandas's ``to_sql``
stopped reliably detecting a 1.4 connectable somewhere around pandas 2,
raising ``'Engine' object has no attribute 'cursor'`` on the exact
combination `pip install -e ".[dev]"` produces. Reproduced directly: a
bare SQLAlchemy 1.4 ``Connection`` or a raw ``psycopg2`` DBAPI connection
both fail the same way, the second one because pandas's legacy DBAPI2
fallback path is SQLite-only and answers a Postgres connection with
``sqlite_master`` syntax. Core's ``Table``/``insert()`` have no such
version sensitivity.
"""

import os

import pandas as pd
from sqlalchemy import BigInteger, Column, Float, MetaData, String, Table, create_engine, text

# No default: a warehouse URL identifies a system, and a fallback in a
# committed file is somebody else's database.
ENGINE = create_engine(os.environ["WAREHOUSE_URL"])

frame = pd.read_csv(os.environ.get("TELCO_CSV", "telco.csv"))
frame.columns = [c.lower() for c in frame.columns]

#: Read off the frame rather than hand-typed, so a column pandas infers as
#: numeric is never silently loaded as text. ``totalcharges`` is genuinely
#: ``object`` here, not a missed float: a handful of rows carry a blank
#: string instead of 0 for a customer with no billing history yet, which is
#: itself real warehouse behaviour rather than a cleaning step to fix here.
_SQL_TYPE = {"int64": BigInteger, "float64": Float}


def _column(name: str, dtype: str) -> Column:
    return Column(name, _SQL_TYPE.get(dtype, String)())


with ENGINE.begin() as connection:
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS raw"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))

metadata = MetaData()
table = Table(
    "telco_customers",
    metadata,
    *(_column(name, str(dtype)) for name, dtype in frame.dtypes.items()),
    schema="raw",
)
with ENGINE.begin() as connection:
    table.drop(connection, checkfirst=True)
    table.create(connection)
    connection.execute(table.insert(), frame.to_dict(orient="records"))

print(f"loaded {len(frame)} rows into raw.telco_customers")
