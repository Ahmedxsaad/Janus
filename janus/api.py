"""The two calls a training script actually needs, as Python rather than a shell.

Everything else in this package is arranged for the CLI, which is right: a person
auditing a catalog types a command. But the one place Janus has to be called
from *inside* somebody's code is the script that trains the model, because that
is the only moment when the feature table, the label column and the training-time
schema are all known. Telling that script to shell out to its own CLI is a worse
interface than a function call, and it is what the README used to say to do.

So::

    from janus import link_model, scan_model

    link_model(
        model="churn_model",
        features="analytics.customer_features",
        label_column="churned",
        exclude=["customer_id"],
    )
    report = scan_model(model="churn_model", dry_run=True)

Two functions, and deliberately only two. This is a public surface: every name
exported here is one somebody will pin a script to, so it holds exactly what the
CLI's own commands are built from and nothing speculative. Anything richer is
already available by importing the modules these call, which is the supported way
to go deeper and the reason there is no client class wrapping them.

What these are not
------------------
Not a second implementation. ``scan_model`` calls
:func:`~janus.agent.pipeline.run_scan` and ``link_model`` calls
:func:`~janus.writeback.link.link_model`, which are the same functions
``janus scan`` and ``janus link`` call, so a finding found here is
found identically at the command line: every trigger shares one core
(docs/02-architecture.md).

Not a place where configuration is invented either. Both connect through
:func:`~janus.client.connect` and read :class:`~janus.config.ScanConfig`
from the environment, so a script and a shell in the same project behave the
same way, and neither one gets a default the other does not.
"""

from __future__ import annotations

from collections.abc import Sequence

from datahub.metadata.urns import SchemaFieldUrn

from janus.agent.pipeline import ScanReport, run_scan
from janus.client import DataHubConnection, connect
from janus.config import ScanConfig
from janus.writeback.link import LinkError, LinkResult
from janus.writeback.link import link_model as _link_model


def _resolve(conn: DataHubConnection, name: str, *, model: bool) -> str:
    """Turn a name or URN into exactly one URN, the way the CLI does.

    Imported lazily from the CLI module so importing :mod:`janus` does not
    import Typer and rich for a caller that only wants two functions.
    """
    from janus.cli import resolve_model, resolve_table

    return resolve_model(conn, name) if model else resolve_table(conn, name)


def scan_model(
    *,
    model: str | None = None,
    table: str | None = None,
    dry_run: bool = False,
    conn: DataHubConnection | None = None,
    config: ScanConfig | None = None,
) -> ScanReport:
    """Audit a model, a table, or both, and return what was found.

    The same core ``janus scan`` runs. No LLM is used: prose is what a
    command-line report wants and a caller holding a
    :class:`~janus.agent.pipeline.ScanReport` has the findings themselves,
    with every measured fact on them.

    Args:
        model: The model to audit, as a name or an mlModel URN.
        table: The table to audit, as a name or a dataset URN.
        dry_run: Detect and return, writing nothing to the graph.
        conn: An open connection. One is opened from the environment if omitted,
            and a caller scanning many models should pass one rather than
            reconnecting per call.
        config: Thresholds and hop caps. Read from the environment if omitted.

    Returns:
        The report: findings, trust scores, the checks that could not run, and
        what was written.

    Raises:
        ValueError: Neither a model nor a table was named, or a name did not
            resolve to exactly one entity.
    """
    if model is None and table is None:
        raise ValueError("scan_model needs model=, table=, or both.")

    connection = conn or connect()
    return run_scan(
        connection,
        config or ScanConfig.from_env(),
        table_urn=_resolve(connection, table, model=False) if table else None,
        model_urn=_resolve(connection, model, model=True) if model else None,
        llm=None,
        dry_run=dry_run,
    )


def link_model(
    *,
    model: str,
    features: str,
    label_column: str,
    label_table: str | None = None,
    exclude: Sequence[str] = (),
    dry_run: bool = False,
    conn: DataHubConnection | None = None,
    config: ScanConfig | None = None,
) -> LinkResult:
    """Declare a model's features, its label, and its training-time schema.

    Call it from the script that trains the model, right after training, which is
    when the arguments are known and when "the schema right now" is the schema
    the model actually saw.

    Args:
        model: The trained model, as a name or an mlModel URN. It must already
            exist in DataHub.
        features: The table the model trains on, as a name or a dataset URN.
        label_column: The column the model predicts, by name.
        label_table: The table holding the label column, when it is not the
            feature table.
        exclude: Feature-table columns that are not features (a join key, a
            partition column).
        dry_run: Work everything out and write nothing.
        conn: An open connection. Opened from the environment if omitted.
        config: Read from the environment if omitted.

    Returns:
        What was declared.

    Raises:
        LinkError: The model or the table is not in DataHub, or the label column
            is not one of a dataset's columns.
    """
    connection = conn or connect()
    feature_urn = _resolve(connection, features, model=False)
    label_dataset_urn = (
        _resolve(connection, label_table, model=False) if label_table else feature_urn
    )

    return _link_model(
        connection,
        config or ScanConfig.from_env(),
        model_urn=_resolve(connection, model, model=True),
        feature_dataset_urn=feature_urn,
        label_column_urn=str(SchemaFieldUrn(label_dataset_urn, label_column)),
        exclude=frozenset(exclude),
        dry_run=dry_run,
    )


__all__ = ["LinkError", "LinkResult", "ScanReport", "link_model", "scan_model"]
