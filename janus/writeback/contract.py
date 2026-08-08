"""Render a model's input tables as an Open Data Contract Standard (ODCS) YAML.

The incident, the trust score, and the impact report say what went wrong. A data
contract says what is expected to be true in the first place: for every table the
model is trained on, the schema it should have and how fresh it should be. It is
the machine-readable version of "these are my model's input requirements", and it
is standards-based (ODCS v3.1.0, Linux Foundation Bitol), so anything that speaks
ODCS can enforce it, not just Janus.

Pure renderer
-------------
Like :func:`~janus.writeback.assertions.render_assertion_yaml` and
:func:`~janus.writeback.documents.render_impact_report`, this reads the graph
and returns text; it writes nothing back to DataHub. The caller decides where the
YAML lands (the CLI's ``--contract-out`` writes it to ``examples/``).

What is derived, and what is not
--------------------------------
Every column and its native type come from the input dataset's current
``schemaMetadata``: real facts DataHub holds, quoted verbatim as ODCS
``physicalType``. The freshness expectation is the SLA Janus would guard
(``config.freshness_sla_hours`` against ``config.freshness_field``), rendered as an
ODCS ``slaProperties`` entry so the contract and the emitted guarding assertion
declare the same check. Nothing that no detector measures is invented: there is no
volume or distribution "expectation" here, because Janus does not measure one
(docs/02-architecture.md). A column's ODCS ``logicalType`` is mapped from its
native type where the mapping is unambiguous and omitted otherwise, rather than
guessed.

Validation
----------
The emitted YAML validates against datacontract-cli's bundled ODCS 3.1.0 JSON
Schema. Regenerate the committed example and lint it with::

    janus scan --model credit_risk_v3 --no-llm --dry-run --contract-out
        examples/input-data-contract.odcs.yaml
    datacontract lint examples/input-data-contract.odcs.yaml

``--no-llm`` keeps the committed file reproducible by anyone, with or without an
API key, and ``--dry-run`` is safe here because rendering a contract is a read.
``datacontract-cli`` is a validation tool only; install it with
``pip install datacontract-cli`` if ``datacontract`` is not on PATH.
"""

from __future__ import annotations

import yaml
from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
)
from datahub.metadata.urns import DatasetUrn, MlModelUrn

from janus.client import DataHubConnection
from janus.config import ScanConfig

#: The ODCS revision the emitted contract targets, and the one datacontract-cli
#: validates it against. Bumping this means re-verifying against the new schema.
ODCS_API_VERSION = "v3.1.0"


class ContractError(Exception):
    """The model has no input table whose schema Janus can contract.

    Raised when the model records no training run, no training run declares an
    input dataset, or no input dataset has a current schema. A contract with no
    columns would assert nothing, so this is surfaced rather than emitted empty.
    """


def _logical_type(native_type: str) -> str | None:
    """Map a warehouse native type to an ODCS logical type, or None if unclear.

    ODCS ``logicalType`` is a small closed vocabulary; the exact native type is
    preserved separately as ``physicalType``. When the native type does not map
    cleanly, the logical type is omitted rather than guessed: a wrong logical type
    is worse than an absent one, and the physical type still carries the truth.
    Order matters where names overlap (``DATETIME`` is a timestamp, not a date).
    """
    name = native_type.upper()
    if any(token in name for token in ("CHAR", "TEXT", "STRING", "UUID")):
        return "string"
    if "BOOL" in name:
        return "boolean"
    if "TIMESTAMP" in name or "DATETIME" in name:
        return "timestamp"
    if "DATE" in name:
        return "date"
    if "TIME" in name:
        return "time"
    if "INT" in name or "SERIAL" in name:
        return "integer"
    if any(token in name for token in ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "NUMBER")):
        return "number"
    return None


def _input_dataset_urns(conn: DataHubConnection, model_urn: str) -> tuple[str, ...]:
    """Return the datasets a model was trained on, deduplicated and sorted.

    Read the same way the schema-drift detector reads them: the model's training
    runs, then each run's declared inputs. A model with no training run, or runs
    that declare no inputs, yields nothing.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.trainingJobs:
        return ()

    urns: set[str] = set()
    for run_urn in properties.trainingJobs:
        inputs = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
        if inputs and inputs.inputs:
            urns.update(inputs.inputs)
    return tuple(sorted(urns))


def _schema_object(
    conn: DataHubConnection, dataset_urn: str, freshness_field: str
) -> dict[str, object] | None:
    """Render one input dataset as an ODCS schema object, or None if it has no schema.

    Columns are sorted by name so the contract reads identically on every run.
    ``required`` is derived from the field's own ``nullable`` flag, another fact
    the schema already carries, not an assumption.
    """
    schema = conn.graph.get_aspect(dataset_urn, SchemaMetadataClass)
    if schema is None or not schema.fields:
        return None

    dataset_name = DatasetUrn.from_string(dataset_urn).name

    properties: list[dict[str, object]] = []
    for field in sorted(schema.fields, key=lambda f: f.fieldPath):
        prop: dict[str, object] = {
            "name": field.fieldPath,
            "physicalType": field.nativeDataType,
            "required": not getattr(field, "nullable", True),
        }
        logical = _logical_type(field.nativeDataType)
        if logical is not None:
            prop["logicalType"] = logical
        properties.append(prop)

    return {
        "name": dataset_name,
        "physicalName": dataset_name,
        "logicalType": "object",
        "description": (
            f"Input table consumed by the model. Freshness is expected on `{freshness_field}`."
        ),
        "properties": properties,
    }


def render_input_contract(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> str:
    """Render a model's input tables as an ODCS v3.1.0 data-contract YAML.

    Deterministic and read-only. For every table the model trains on, the current
    schema becomes an ODCS schema object and the freshness SLA becomes an ODCS SLA
    property, so the contract declares exactly the checks Janus would guard.

    Args:
        conn: An open connection.
        model_urn: The model whose input boundary is being contracted.
        config: Supplies the freshness SLA and the freshness field.

    Returns:
        The ODCS YAML, ready to write to disk and validate with datacontract-cli.

    Raises:
        ContractError: The model has no input table with a readable schema.
    """
    dataset_urns = _input_dataset_urns(conn, model_urn)
    schema_objects = [
        obj
        for urn in dataset_urns
        if (obj := _schema_object(conn, urn, config.freshness_field)) is not None
    ]
    if not schema_objects:
        raise ContractError(
            f"{model_urn} has no input table with a readable schema; nothing to contract."
        )

    model_name = MlModelUrn.from_string(model_urn).name
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    display_name = (properties.name if properties and properties.name else None) or model_name

    # One freshness SLA entry per input table, all sharing the SLA Janus
    # guards. `element` names the field a warehouse-side evaluator would read;
    # Janus itself measures freshness from the operation aspect, but the
    # contract speaks the warehouse's language.
    sla_properties = [
        {
            "property": "frequency",
            "value": config.freshness_sla_hours,
            "unit": "h",
            "element": f"{obj['name']}.{config.freshness_field}",
            "driver": "operational",
            "description": "Janus freshness SLA; matches the emitted guarding assertion.",
        }
        for obj in schema_objects
    ]

    contract = {
        "apiVersion": ODCS_API_VERSION,
        "kind": "DataContract",
        "id": f"janus.input-contract.{model_name}",
        "name": f"Input data contract for {display_name}",
        "version": "1.0.0",
        "status": "active",
        "description": {
            "purpose": (
                f"Input requirements Janus derived for the model {display_name}: "
                "the schema of every table it trains on, and the freshness those tables "
                "must hold. Derived from column-level lineage and schema metadata in DataHub."
            ),
            "usage": f"Generated by Janus for {model_urn}.",
        },
        "schema": schema_objects,
        "slaProperties": sla_properties,
    }

    return yaml.safe_dump(contract, sort_keys=False, default_flow_style=False, width=100)
