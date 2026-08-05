"""Read the link out of a Feast feature repository.

A Feast repo is the cleanest declaration of this join in common use. A feature
view names its source and its schema; the source's ``field_mapping`` names the
warehouse column behind a renamed feature; a label view names the column the
model predicts; and a feature service names the exact set one model is trained
on. That is every argument ``modelguard link`` asks a human for, already written
down, already kept correct because the training pipeline reads it.

Offline and read-only, per adapters/CLAUDE.md. ``feast.repo_operations.parse_repo``
imports the repo's own Python modules and returns the declared objects; it does
not open the registry, contact an online store, or run ``feast apply``. This
module is imported only when ``--from feast`` is used, so ``feast`` stays an
optional extra.

What it deliberately does not do
--------------------------------
It does not resolve the source table against DataHub, and it does not infer a
feature that the declaration does not name. A repo whose feature service spans
two source tables is reported as such rather than merged: ``link`` declares one
feature table per model, and picking one of two tables here would silently drop
half a model's features.
"""

from __future__ import annotations

import re
import sys
from contextlib import chdir
from pathlib import Path
from typing import Any

from modelguard.adapters import AdapterError, DeclaredFeature, DeclaredLink

#: Attributes a Feast data source may carry its table under, in the order they
#: are tried. Warehouse sources (BigQuery, Snowflake, Redshift) use ``table``;
#: file sources use ``path``; anything else falls back to the source's declared
#: name, which is at least a string the user chose.
_TABLE_ATTRIBUTES = ("table", "path")

#: A bare relation, optionally schema-qualified. What ``get_table_query_string``
#: returns for a source declared on a table, and what distinguishes that from the
#: same method's answer for a source declared on a query (a parenthesised SELECT)
#: or for a dialect that quotes its identifiers.
_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _feast_repo(path: Path) -> Any:
    """Parse the repo, or say precisely why it could not be parsed.

    Raises:
        AdapterError: feast is not installed, the path is not a feature repo, or
            the repo's own Python raised while being imported. The third is the
            interesting one and it is passed through verbatim: a repo that does
            not import is the user's file to fix, and paraphrasing their
            traceback would take away the line number.
    """
    try:
        from feast.repo_operations import parse_repo
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise AdapterError(
            "reading a Feast repo needs the feast package, which is an optional "
            'extra: pip install "modelguard-datahub[feast]"'
        ) from exc

    if not path.is_dir():
        raise AdapterError(
            f"{path} is not a directory. --repo takes the Feast feature repo "
            "itself, the directory holding feature_store.yaml."
        )
    # parse_repo derives each module's import name *relative to the working
    # directory* and then imports it, so a repo anywhere else fails with "not in
    # the subpath of ..." and, once that is fixed, with ModuleNotFoundError.
    # Feast's own CLI never meets either, because it runs inside the repo with
    # the interpreter's implicit "" on sys.path. So this runs the same way. Both
    # changes are process-global, which is safe here (one CLI invocation, no
    # threads) and is why the window is kept to the parse call alone.
    resolved = str(path.resolve())
    try:
        with chdir(path):
            sys.path.insert(0, resolved)
            try:
                return parse_repo(Path())
            finally:
                sys.path.remove(resolved)
    except Exception as exc:
        raise AdapterError(f"feast could not parse the repo at {path}: {exc}") from exc


def _join_keys(repo: Any, entity_names: list[str]) -> tuple[str, ...]:
    """Return the columns a view's entities join on, which are never features."""
    by_name = {entity.name: entity for entity in repo.entities}
    keys: dict[str, None] = {}
    for name in entity_names:
        entity = by_name.get(name)
        if entity is None:
            continue
        for key in getattr(entity, "join_keys", None) or [entity.join_key]:
            keys.setdefault(key, None)
    return tuple(keys)


def _source_table(source: Any) -> str:
    """Return the table a Feast data source reads, as the declaration spells it.

    The SQL contrib sources (postgres, and the others built on the same options
    object) keep the table private and expose it only through
    ``get_table_query_string``, so an attribute scan alone reports such a repo's
    source table as the source's Feast *name*: a string that resolves against no
    catalog, on the stack this reader is most likely to meet. Found by running
    the adapter against the ingested real project (T-14).

    That method is asked second and its answer is taken only when it is a bare
    relation. A source declared on a query returns a parenthesised SELECT, and a
    dialect that quotes its identifiers returns them quoted; neither is a table
    name, and both fall through to the declared name rather than being handed to
    a catalog lookup as one.

    Never asked of a Spark source: unlike the SQL contrib sources' pure string
    build, a ``SparkSource`` with neither ``table`` nor ``path`` set answers by
    calling ``SparkSession.builder.getOrCreate()`` and loading a dataframe, a
    live side effect this offline, read-only reader must never trigger
    (adapters/CLAUDE.md rule 1). Checked by module name rather than imported,
    so this reader never has to import pyspark to stay safe without it.
    """
    for attribute in _TABLE_ATTRIBUTES:
        value = getattr(source, attribute, None)
        if value:
            return str(value)
    if "spark" in type(source).__module__.lower():
        return str(source.name)
    relation = getattr(source, "get_table_query_string", None)
    if callable(relation):
        try:
            candidate = str(relation()).strip()
        except Exception:  # noqa: BLE001 - a vendor source's own failure, not ours
            candidate = ""
        if _RELATION.match(candidate):
            return candidate
    return str(source.name)


def _source_column(source: Any, feature_name: str) -> str:
    """Return the warehouse column behind a feature name.

    Feast's ``field_mapping`` is declared source column to feature name, so the
    lookup this needs is the reverse of the dict as written. A feature no mapping
    mentions is read from a column of its own name, which is Feast's own default
    and not an assumption made here.
    """
    mapping = getattr(source, "field_mapping", None) or {}
    for column, mapped in mapping.items():
        if mapped == feature_name:
            return str(column)
    return feature_name


def _selected(repo: Any, select: str | None) -> tuple[str, list[Any], list[Any]]:
    """Return the name read, the feature views in it, and the label views in it.

    A repo holding exactly one declaration needs no answer. A repo holding
    several is a question, and it is asked by name rather than answered by
    picking the first: linking a model to the wrong feature service is a scan
    that reports on somebody else's model.

    Raises:
        AdapterError: Nothing is declared, the name is unknown, or several
            declarations exist and none was named.
    """
    services = {service.name: service for service in repo.feature_services}
    views = {view.name: view for view in repo.feature_views}
    labels = {view.name: view for view in repo.label_views}

    def in_service(service: Any) -> tuple[str, list[Any], list[Any]]:
        # A projection carries the selected fields but not the view's source, so
        # the view itself is looked up by name. A projection naming neither a
        # feature view nor a label view is an on-demand view: it computes its
        # features in Python from other views, so it names no warehouse column
        # and there is nothing here to read.
        chosen = [views[p.name] for p in service.feature_view_projections if p.name in views]
        chosen_labels = [
            labels[p.name] for p in service.feature_view_projections if p.name in labels
        ]
        if not chosen:
            raise AdapterError(
                f"feature service {service.name!r} names no feature view with a "
                "batch source (on-demand views compute their features in Python, "
                "so they name no warehouse column). Nothing to link."
            )
        return service.name, chosen, chosen_labels

    if select is not None:
        if select in services:
            return in_service(services[select])
        if select in views:
            return select, [views[select]], []
        known = ", ".join(sorted(services) + sorted(views)) or "nothing"
        raise AdapterError(f"{select!r} is not declared in this repo. It declares: {known}.")

    if len(services) == 1:
        return in_service(next(iter(services.values())))
    if services:
        raise AdapterError(
            f"this repo declares {len(services)} feature services "
            f"({', '.join(sorted(services))}). Name the one this model trains on "
            "with --select."
        )
    if len(views) == 1:
        name, view = next(iter(views.items()))
        return name, [view], list(labels.values())
    if views:
        raise AdapterError(
            f"this repo declares {len(views)} feature views "
            f"({', '.join(sorted(views))}) and no feature service to group them. "
            "Name the one this model trains on with --select."
        )
    raise AdapterError(f"this repo declares no feature view at all: {sorted(labels) or 'nothing'}")


def _label(repo: Any, label_views: list[Any]) -> tuple[str | None, str | None, str]:
    """Return the label column, its table, and the line explaining either answer.

    Feast's label view is the only place in a feature repo that names the column
    a model predicts. A repo without one is the common case, not an error: the
    label usually arrives in the entity dataframe at training time, so the
    declaration genuinely does not hold it and ``--label-column`` still has to be
    supplied by the person who knows.
    """
    if not label_views:
        return (
            None,
            None,
            "label: this declaration names none (a Feast repo carries one only "
            "when it declares a label view), so pass --label-column",
        )
    if len(label_views) > 1:
        names = ", ".join(sorted(view.name for view in label_views))
        return (
            None,
            None,
            f"label: {len(label_views)} label views are declared ({names}), so "
            "which one this model predicts is a question this file does not "
            "answer; pass --label-column",
        )

    view = label_views[0]
    keys = set(_join_keys(repo, list(view.entities)))
    # The entity's join key is inserted into a label view's schema, so the label
    # is what is left after the keys are taken out. More than one remainder means
    # the view declares several labels and nothing says which this model predicts.
    columns = [field.name for field in view.schema if field.name not in keys]
    if len(columns) != 1:
        return (
            None,
            None,
            f"label: label view {view.name!r} declares {len(columns)} non-key "
            "columns, so which one this model predicts is a question this file "
            "does not answer; pass --label-column",
        )
    source = view.batch_source
    column = _source_column(source, columns[0])
    table = _source_table(source)
    return (
        column,
        table,
        f"label: {column} of {table}, from label view {view.name!r}",
    )


def read_repo(path: Path, select: str | None = None) -> DeclaredLink:
    """Read a Feast feature repo and return the link it declares.

    Args:
        path: The feature repo directory, the one holding ``feature_store.yaml``.
        select: A feature service or feature view name, when the repo declares
            more than one.

    Returns:
        The declaration: the source table, one entry per declared feature with
        the warehouse column behind it, and the label where a label view names
        one.

    Raises:
        AdapterError: feast is not installed, the repo cannot be parsed, the
            selection is ambiguous, or the selected views span more than one
            source table.
    """
    repo = _feast_repo(path)
    name, views, label_views = _selected(repo, select)

    tables = {view.name: _source_table(view.batch_source) for view in views}
    if len(set(tables.values())) > 1:
        listed = ", ".join(f"{view} -> {table}" for view, table in sorted(tables.items()))
        raise AdapterError(
            f"{name!r} spans {len(set(tables.values()))} source tables ({listed}). "
            "`link` declares one feature table per model, so link them one at a "
            "time with --select, or pass --features to name the one you mean."
        )

    features: list[DeclaredFeature] = []
    keys: dict[str, None] = {}
    timestamps: dict[str, None] = {}
    for view in views:
        source = view.batch_source
        for key in _join_keys(repo, list(view.entities)):
            keys.setdefault(key, None)
        for column in (source.timestamp_field, source.created_timestamp_column):
            if column:
                timestamps.setdefault(str(column), None)
        for field in view.features:
            features.append(
                DeclaredFeature(
                    name=field.name,
                    source_column=_source_column(source, field.name),
                    declared_in=f"feature view {view.name!r}",
                )
            )

    if not features:
        raise AdapterError(
            f"{name!r} declares no feature at all, so there is nothing to link. "
            "Feature views infer their schema from the source during `feast "
            "apply`; this reads the declaration, so the schema has to be in it."
        )

    label_column, label_table, label_reason = _label(repo, label_views)
    table = next(iter(tables.values()))
    renamed = sum(1 for feature in features if feature.name != feature.source_column)
    not_features = sorted(keys) + sorted(timestamps)
    reasons = [
        f"read {name!r} from the Feast repo at {path}",
        f"feature table: {table}, the batch source of "
        + ", ".join(f"{view.name!r}" for view in views),
        f"features: {len(features)} declared, of which {renamed} name a warehouse "
        "column different from the feature (from the source's field_mapping)",
        label_reason,
    ]
    if not_features:
        reasons.append(
            "not features: " + ", ".join(not_features) + " (entity join keys and "
            "event timestamps), so they are excluded from the link"
        )
    return DeclaredLink(
        adapter="feast",
        name=name,
        source_table=table,
        features=tuple(features),
        label_column=label_column,
        label_table=label_table,
        reasons=tuple(reasons),
    )
