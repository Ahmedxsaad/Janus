"""Read the link out of a dbt semantic model.

A dbt project that has adopted the semantic layer already declares, per semantic
model: the dbt model it is built on, the entities that join it, the dimensions it
groups by, and the measures it aggregates. Dimensions and measures name the
column they read through ``expr``, which is exactly the feature-to-column mapping
``modelguard link`` otherwise asks a human to retype.

Needs no dependency at all: ``target/manifest.json`` is JSON, and this reads it
with the standard library. Unlike the Feast adapter there is no extra to install,
which also means it works against a manifest somebody sent you, with no dbt on
the machine.

[verified] against dbt-core 1.12.0, manifest schema v12
(``https://schemas.getdbt.com/dbt/manifest/v12.json``), by parsing a project that
declares a semantic model and reading the artifact it produced. The fields used
here (``semantic_models`` keyed by unique id, each with ``name``,
``node_relation``, ``entities``, ``dimensions``, ``measures``) are stable across
v9 to v12; a manifest whose ``dbt_schema_version`` is older is read anyway, and
the version it reported is quoted in the reasons so a surprise is attributable.

What it deliberately does not do
--------------------------------
It does not run dbt, and it does not read the project's SQL. A measure whose
``expr`` is an expression rather than a column name (``amount * rate``,
``case when ...``) names no single source column, so it is reported as unread
rather than parsed: guessing which column an expression is "really" about is how
a detector ends up walking the lineage of a column that does not exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from modelguard.adapters import AdapterError, DeclaredFeature, DeclaredLink

#: Where dbt writes its manifest inside a project, relative to the project root.
_MANIFEST_PATH = Path("target") / "manifest.json"

#: A bare column reference. Anything else in an ``expr`` is an expression, and an
#: expression is not a column this can hand to a lineage walk.
_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _manifest_file(path: Path) -> Path:
    """Return the manifest to read, from a project root or a direct file path.

    Raises:
        AdapterError: Neither reading is a file that exists. The message names
            the dbt command that produces one, because a project that has never
            been compiled is the common case and it is fixed in one step.
    """
    if path.is_file():
        return path
    candidate = path / _MANIFEST_PATH
    if candidate.is_file():
        return candidate
    raise AdapterError(
        f"no dbt manifest at {candidate}. --repo takes a dbt project root or a "
        "manifest.json; run `dbt parse` (or `dbt compile`) in the project to "
        "produce one."
    )


def _load(path: Path) -> tuple[dict[str, Any], str]:
    """Return the manifest and the schema version it declares.

    Raises:
        AdapterError: The file is not JSON, or is JSON that is not a manifest.
    """
    file = _manifest_file(path)
    try:
        loaded = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError(f"{file} could not be read as JSON: {exc}") from exc
    # Checked rather than assumed: this file comes from outside the process, and
    # a wrong one (another tool's manifest, a truncated artifact) must fail here
    # with its own name in the message rather than as an AttributeError later.
    if not isinstance(loaded, dict) or not isinstance(loaded.get("semantic_models"), dict):
        raise AdapterError(
            f"{file} is not a dbt manifest, or is one from a dbt version with no "
            "semantic layer: it carries no semantic_models mapping."
        )
    metadata = loaded.get("metadata") or {}
    version = str(metadata.get("dbt_schema_version") or "an unstated schema version")
    return loaded, version


def _select(models: dict[str, dict[str, Any]], select: str | None) -> dict[str, Any]:
    """Return the one semantic model to read.

    Matched on the declared name or on the full unique id, because a manifest
    keys on the second and a person reads the first.

    Raises:
        AdapterError: The manifest declares none, the name is unknown, or several
            are declared and none was named.
    """
    if not models:
        raise AdapterError(
            "this manifest declares no semantic model. `--from dbt` reads the dbt "
            "semantic layer (semantic_models in a schema YAML), not the models "
            "themselves: a project without it declares no feature-to-column "
            "mapping for this to import."
        )
    by_name = {model["name"]: model for model in models.values()}

    if select is not None:
        if select in models:
            return models[select]
        if select in by_name:
            return by_name[select]
        raise AdapterError(
            f"{select!r} is not a semantic model in this manifest. It declares: "
            f"{', '.join(sorted(by_name))}."
        )

    if len(models) == 1:
        return next(iter(models.values()))
    raise AdapterError(
        f"this manifest declares {len(models)} semantic models "
        f"({', '.join(sorted(by_name))}). Name the one this model trains on with "
        "--select."
    )


def _source_table(model: dict[str, Any]) -> str:
    """Return the warehouse table a semantic model is built on.

    ``node_relation`` is the compiled relation, which is what the warehouse (and
    therefore DataHub's ingestion of it) actually holds. Rendered as
    ``schema.table`` rather than as the fully qualified ``relation_name``,
    because that is how a dataset is named in DataHub for the warehouse sources
    this is used with, and the caller resolves it against the catalog anyway.
    """
    relation = model.get("node_relation") or {}
    alias = relation.get("alias") or model["name"]
    schema = relation.get("schema_name")
    return f"{schema}.{alias}" if schema else str(alias)


def _column(entry: dict[str, Any]) -> str | None:
    """Return the column an entity, dimension or measure reads, or None.

    None means the declaration names an expression rather than a column, which
    the caller reports as unread. dbt's own default is that a missing ``expr``
    means the column is named after the declaration itself.
    """
    expression = entry.get("expr") or entry["name"]
    return str(expression) if _COLUMN.match(str(expression)) else None


def read_manifest(path: Path, select: str | None = None) -> DeclaredLink:
    """Read a dbt manifest and return the link its semantic model declares.

    Args:
        path: A dbt project root, or a ``manifest.json`` directly.
        select: A semantic model's name or unique id, when the manifest declares
            more than one.

    Returns:
        The declaration: the compiled relation as the source table, one entry per
        dimension and measure with the column behind it, and no label (the dbt
        semantic layer has no notion of one).

    Raises:
        AdapterError: There is no manifest, it is not readable as one, the
            selection is ambiguous, or the selected model declares nothing that
            names a column.
    """
    manifest, schema_version = _load(path)
    model = _select(manifest["semantic_models"], select)
    declared_in = f"semantic model {model['name']!r}"

    features: list[DeclaredFeature] = []
    computed: list[str] = []
    for kind in ("dimensions", "measures"):
        for entry in model.get(kind) or []:
            column = _column(entry)
            if column is None:
                computed.append(f"{entry['name']} ({kind[:-1]})")
                continue
            features.append(
                DeclaredFeature(name=entry["name"], source_column=column, declared_in=declared_in)
            )

    if not features:
        raise AdapterError(
            f"semantic model {model['name']!r} declares no dimension or measure "
            "that names a column, so there is nothing to link."
        )

    entities = [name for entry in model.get("entities") or [] if (name := _column(entry))]
    table = _source_table(model)
    renamed = sum(1 for feature in features if feature.name != feature.source_column)
    reasons = [
        f"read {model['name']!r} from the dbt manifest at {path} ({schema_version})",
        f"feature table: {table}, the relation the semantic model is built on (node_relation)",
        f"features: {len(features)} declared as dimensions and measures, of which "
        f"{renamed} read a column named differently through expr",
        "label: the dbt semantic layer declares no label column, so pass --label-column",
    ]
    if entities:
        reasons.append(
            "not features: " + ", ".join(entities) + " (entity key columns), so "
            "they are excluded from the link"
        )
    if computed:
        reasons.append(
            "not read: " + ", ".join(computed) + " compute an expression rather "
            "than naming a column, so no source column was taken from them"
        )
    return DeclaredLink(
        adapter="dbt",
        name=model["name"],
        source_table=table,
        features=tuple(features),
        label_column=None,
        label_table=None,
        reasons=tuple(reasons),
    )
