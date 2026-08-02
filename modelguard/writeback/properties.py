"""Define and assign the structured properties ModelGuard owns.

DataHub separates a property's *definition* (its name, type, cardinality, and
which entity types may carry it) from its *assignment* (a value on one entity).
Both are written here.

The definitions live in ``props/modelguard_props.yaml`` rather than in code, so a
reviewer can read the graph contract without reading Python. The plan reached for
``datahub properties upsert -f ...`` and a GraphQL mutation; emitting the
``structuredPropertyDefinition`` and ``structuredProperties`` aspects directly is
the same write without a subprocess or a hand-built query, and it is verifiable
offline against the installed SDK.

Assignment is read-before-write: existing values for properties this run does not
touch are preserved, and rewriting the same value is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    PropertyCardinalityClass,
    StructuredPropertiesClass,
    StructuredPropertyDefinitionClass,
    StructuredPropertyValueAssignmentClass,
)
from datahub.metadata.urns import DataTypeUrn, EntityTypeUrn, StructuredPropertyUrn

from modelguard.client import DataHubConnection

PROPS_FILE = Path(__file__).parent / "props" / "modelguard_props.yaml"

TRUST_SCORE = "modelguard.trust_score"
TRUST_BAND = "modelguard.trust_band"
RISK_FLAGS = "modelguard.risk_flags"
RUN_ID = "modelguard.run_id"

#: Source columns this model currently has an open leakage incident on. Written
#: by the scan that raised them and read by the next scan's reconciliation, so a
#: leak fixed by deleting the column outright, which leaves nothing to walk back
#: through, still gets its incident closed (D-069's gap, hit for real in D-074).
OPEN_LEAK_COLUMNS = "modelguard.open_leak_columns"

#: One entry per scan that scored a model, oldest first. A trust score on its own
#: says nothing a reader can act on: 82 matters only next to the 95 it was last
#: week, and the direction is what tells somebody that a change shipped.
TRUST_HISTORY = "modelguard.trust_history"

_VALID_CARDINALITIES = frozenset(
    {PropertyCardinalityClass.SINGLE, PropertyCardinalityClass.MULTIPLE}
)

#: A structured property value may only be a string or a number (DataHub's
#: PrimitivePropertyValue). Booleans are numbers in Python, so they are rejected
#: explicitly rather than silently stored as 0 or 1.
StructuredPropertyValue = str | float | int


class PropertyDefinitionError(ValueError):
    """The YAML declaration file is malformed."""


@dataclass(frozen=True)
class PropertyDefinition:
    """One structured property, as declared in the YAML file."""

    qualified_name: str
    display_name: str
    description: str
    value_type: str
    cardinality: str
    entity_types: tuple[str, ...]

    @property
    def urn(self) -> StructuredPropertyUrn:
        """Return the URN this property is addressed by."""
        return StructuredPropertyUrn(self.qualified_name)

    def as_aspect(self) -> StructuredPropertyDefinitionClass:
        """Render the declaration as the aspect DataHub stores."""
        return StructuredPropertyDefinitionClass(
            qualifiedName=self.qualified_name,
            displayName=self.display_name,
            description=self.description,
            valueType=str(DataTypeUrn(f"datahub.{self.value_type}")),
            entityTypes=[str(EntityTypeUrn(f"datahub.{et}")) for et in self.entity_types],
            cardinality=self.cardinality,
        )


def load_definitions(path: Path = PROPS_FILE) -> tuple[PropertyDefinition, ...]:
    """Parse the YAML declaration file.

    Raises:
        PropertyDefinitionError: A property is missing a field or declares a
            cardinality DataHub does not support.
    """
    raw: Any = yaml.safe_load(path.read_text())
    try:
        entries = raw["properties"]
    except (TypeError, KeyError) as exc:
        raise PropertyDefinitionError(f"{path} has no top-level 'properties' list") from exc

    definitions: list[PropertyDefinition] = []
    for entry in entries:
        try:
            definition = PropertyDefinition(
                qualified_name=entry["qualified_name"],
                display_name=entry["display_name"],
                description=entry["description"].strip(),
                value_type=entry["value_type"],
                cardinality=entry["cardinality"],
                entity_types=tuple(entry["entity_types"]),
            )
        except KeyError as exc:
            raise PropertyDefinitionError(f"property {entry!r} is missing field {exc}") from exc

        if definition.cardinality not in _VALID_CARDINALITIES:
            raise PropertyDefinitionError(
                f"{definition.qualified_name} declares cardinality "
                f"{definition.cardinality!r}; allowed: {sorted(_VALID_CARDINALITIES)}"
            )
        definitions.append(definition)
    return tuple(definitions)


def define_properties(conn: DataHubConnection) -> tuple[str, ...]:
    """Create or update every declared property definition.

    Idempotent: the definition aspect is keyed by the property URN, so
    re-emitting an unchanged declaration leaves the graph untouched.

    Returns:
        The URNs of the defined properties.
    """
    definitions = load_definitions()
    conn.graph.emit_mcps(
        [
            MetadataChangeProposalWrapper(entityUrn=str(d.urn), aspect=d.as_aspect())
            for d in definitions
        ]
    )
    return tuple(str(d.urn) for d in definitions)


def _validated_values(values: list[StructuredPropertyValue]) -> list[str | float]:
    """Coerce assignment values, rejecting types DataHub cannot store.

    Raises:
        ValueError: A value is a bool, or is neither a string nor a number.
    """
    coerced: list[str | float] = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError(
                "structured property values may not be booleans; "
                "encode the flag as a string instead"
            )
        if isinstance(value, str):
            coerced.append(value)
        elif isinstance(value, int | float):
            coerced.append(float(value))
        else:
            raise ValueError(f"unsupported structured property value {value!r}")
    return coerced


def assign_properties(
    conn: DataHubConnection,
    entity_urn: str,
    values: dict[str, list[StructuredPropertyValue]],
) -> None:
    """Assign structured property values to one entity, preserving the others.

    Reads the entity's current ``structuredProperties`` aspect, replaces only the
    properties named in ``values``, and writes the merged result back. Assigning
    the same values twice is a no-op.

    Merging in Python is only safe while one writer is active. DataHub exposes no
    conditional write, so a second writer whose read predates this one's emit
    rewrites the whole aspect from stale data and drops whatever landed in
    between, silently and whichever property each of them touched (F3). Every
    caller therefore batches a model's properties into one call, and the
    supported deployment is one writer per graph (charts/modelguard-watch).

    ponytail: read-merge-write, one writer per graph. The installed SDK carries
    `datahub.specific.aspect_helpers.structured_properties.HasStructuredPropertiesPatch`,
    which emits a server-side JSON patch per property and would remove the merge
    window entirely. Not adopted here because whether this project's GMS accepts
    a PATCH on `structuredProperties` has not been verified against a live server,
    and an unverified change to every write path is a worse trade than a
    documented single-writer rule. Upgrade path: run the integration suite
    against a Quickstart with the patch builder in place, then delete the read.

    Args:
        conn: A connection with write credentials.
        entity_urn: The entity receiving the values.
        values: Qualified property name to its list of values. A SINGLE
            cardinality property takes a one-element list.

    Raises:
        ValueError: A value has an unsupported type.
    """
    incoming = {
        str(StructuredPropertyUrn(name)): _validated_values(vals) for name, vals in values.items()
    }

    existing = conn.graph.get_aspect(entity_urn, StructuredPropertiesClass)
    kept = [
        assignment
        for assignment in (existing.properties if existing else [])
        if assignment.propertyUrn not in incoming
    ]

    merged = kept + [
        StructuredPropertyValueAssignmentClass(propertyUrn=urn, values=vals)
        for urn, vals in sorted(incoming.items())
    ]

    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=entity_urn,
            aspect=StructuredPropertiesClass(properties=merged),
        )
    )


def read_properties(conn: DataHubConnection, entity_urn: str) -> dict[str, list[str | float]]:
    """Return the entity's structured properties, keyed by qualified name.

    Used by the write-back layer's read-before-write and by the gate to prove a
    property landed.
    """
    aspect = conn.graph.get_aspect(entity_urn, StructuredPropertiesClass)
    if aspect is None:
        return {}
    return {
        StructuredPropertyUrn.from_string(a.propertyUrn).id: list(a.values)
        for a in aspect.properties
    }


def remove_properties(conn: DataHubConnection, entity_urn: str, names: set[str]) -> bool:
    """Remove named structured-property assignments without touching other values."""
    existing = conn.graph.get_aspect(entity_urn, StructuredPropertiesClass)
    if existing is None:
        return False

    urns = {str(StructuredPropertyUrn(name)) for name in names}
    kept = [assignment for assignment in existing.properties if assignment.propertyUrn not in urns]
    if len(kept) == len(existing.properties):
        return False

    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=entity_urn,
            aspect=StructuredPropertiesClass(properties=kept),
        )
    )
    return True
