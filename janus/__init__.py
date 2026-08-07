"""Janus: a data-to-model reliability agent built on DataHub.

Janus sits on the boundary between the warehouse graph and the ML graph.
It reads column-level and ML lineage from DataHub to detect silent
data-to-model failures (target leakage, upstream blast radius, schema drift,
trust decay) and writes findings back into the graph as incidents, structured
properties, tags, documents, and guarding assertions.

Package layout:
    seed/       builds the ML graph that the sample datapacks lack
    detect/     deterministic detectors (pure functions, no LLM)
    writeback/  idempotent DataHub mutations
    agent/      LangGraph orchestration with a human approval gate

Using it from Python
--------------------
The command line is the main interface, but the one place Janus belongs
*inside* somebody's code is the script that trains the model, because that is the
only moment when the feature table, the label column, and the training-time
schema are all known::

    from janus import link_model, scan_model

    link_model(model="churn_model", features="analytics.customer_features",
               label_column="churned", exclude=["customer_id"])
    report = scan_model(model="churn_model", dry_run=True)

Both raise :class:`TableResolutionError` when a name they were given matches no
dataset or more than one, which is the first failure a script hits on a real
catalog: a relation named the way the warehouse names it usually exists on more
than one platform. It is exported here so catching it does not mean importing
the CLI.

Those two names, plus their result types and the two errors they raise, are the
supported public surface: they are what a script may pin to, and they are thin
wrappers over exactly the functions the CLI calls (:mod:`janus.api`). Everything
else in this package is importable and documented, but its shape is free to
change; import a submodule directly when you need more, knowingly.

See docs/plan/architecture.md for the full design.
"""

from janus.api import LinkError, LinkResult, ScanReport, link_model, scan_model
from janus.cli import TableResolutionError

__version__ = "0.1.0"

__all__ = [
    "LinkError",
    "LinkResult",
    "ScanReport",
    "TableResolutionError",
    "__version__",
    "link_model",
    "scan_model",
]
