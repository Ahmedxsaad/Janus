"""Read a model's feature-to-column join out of a declaration that already exists.

Why this package exists
-----------------------
Every detector in ModelGuard reads the join between a model and the warehouse
columns it trained on, and nothing in the ecosystem writes that join (D-074).
``modelguard link`` is the bridge, and it asks a human to type the arguments.

For a large class of real ML stacks nobody has to: the mapping is already
declared, in a file the team already maintains and keeps correct because their
training pipeline reads it. A Feast repo declares each feature view's source and
its per-field mapping. A dbt semantic model declares its entities, dimensions
and measures against a model's columns. Where that is true, ``link`` should
import the declaration rather than ask for it a second time, in a second
notation, with a second chance to get it wrong.

The local rules (adapters/CLAUDE.md) are the whole design: an adapter parses a
declaration on disk, offline, read-only, and returns exactly what ``link``
takes. It never connects to a vendor's service, never writes to DataHub, and
never decides anything the declaration does not say. What it could not read is
reported as unread, never guessed at.

What an adapter returns, and what the caller does with it
---------------------------------------------------------
A :class:`DeclaredLink` names the source table, the features and their source
columns, and the label where the declaration carries one. It does *not* name the
excluded columns, because exclusion is the complement of the declaration against
the table's real schema, and the schema lives in DataHub rather than in the
declaration. :func:`excluded_columns` and :func:`missing_columns` do that join,
as pure functions, once the caller has read the schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: The values ``modelguard link --from`` accepts, in the order the CLI lists
#: them. Kept here rather than in cli.py so a new adapter is one module and one
#: entry, and so the help text cannot fall out of step with what is dispatched.
ADAPTERS: tuple[str, ...] = ("feast", "dbt")


class AdapterError(ValueError):
    """A declaration could not be read, or does not say enough to link a model.

    Every message names the file or the declaration it read, and the argument
    that would resolve it. An adapter that fails vaguely is worse than one that
    does not exist: the user has no way to tell a repo it cannot parse from a
    repo that declares nothing.
    """


@dataclass(frozen=True)
class DeclaredFeature:
    """One feature, and the source column the declaration says it comes from."""

    name: str
    """The feature's name, as the declaration names it."""
    source_column: str
    """The column of :attr:`DeclaredLink.source_table` it is read from. Often the
    same string as :attr:`name`, and the cases where it is not are exactly why
    reading the declaration beats matching on names."""
    declared_in: str
    """The declaration this line came from (a feature view, a semantic model).
    Printed next to the line, so a reviewer checks a proposal against the file
    they already know rather than against a tool's summary of it."""


@dataclass(frozen=True)
class DeclaredLink:
    """What one declaration says a model is trained on.

    The adapter's whole output, and the input to the arguments ``link`` takes.
    Nothing here is inferred: every field is a value the declaration states, and
    a field the declaration does not state is None rather than a guess.
    """

    adapter: str
    """Which adapter read this, quoted in the proposal."""
    name: str
    """The declaration's own name (a feature service, a semantic model), so the
    proposal says which one of several was read."""
    source_table: str
    """The feature table, named the way the declaration names it. Resolving that
    to a dataset URN is the caller's job: only DataHub knows how the catalog
    spells it."""
    features: tuple[DeclaredFeature, ...]
    """Every feature the declaration names, in declaration order."""
    label_column: str | None
    """The label, where the declaration carries one. Feast's label views do;
    a plain feature view does not, and a dbt semantic model does not."""
    label_table: str | None
    """The table the label lives in, when it is not :attr:`source_table`."""
    reasons: tuple[str, ...]
    """One line per decision, naming the declaration it was read from. Printed
    before the proposal, exactly as ``link --infer`` prints its own reasons: a
    proposal accepted without reading the reasoning is a guess with a
    confirmation prompt stapled to it."""

    @property
    def source_columns(self) -> frozenset[str]:
        """The distinct warehouse columns the declaration names as features."""
        return frozenset(feature.source_column for feature in self.features)


def excluded_columns(declared: Iterable[str], table_columns: Iterable[str]) -> frozenset[str]:
    """Return the table's columns that the declaration does not name as features.

    ``link`` takes the complement (everything that is *not* a feature) because
    that is what a human types: the feature table is the unit, and the join key
    and the partition column are the exceptions. A declaration states the
    positive side, so the two are joined here rather than in either of them.

    Args:
        declared: The source columns the declaration names as features.
        table_columns: Every column the linked dataset actually has, from its
            ``schemaMetadata``.

    Returns:
        The columns to exclude. A column the declaration names but the table does
        not have is not in the result, because it is not a column of the table;
        :func:`missing_columns` is what reports that disagreement.
    """
    named = set(declared)
    return frozenset(column for column in table_columns if column not in named)


def missing_columns(declared: Iterable[str], table_columns: Iterable[str]) -> tuple[str, ...]:
    """Return the declared source columns the linked table does not have.

    A non-empty result means the declaration and the catalog disagree about the
    table: a renamed column, a stale declaration, or the wrong table resolved.
    The caller stops rather than linking the intersection, because a silently
    partial link is a model whose leaking feature was never declared and whose
    scan therefore reports it clean.

    Returns:
        The missing columns, sorted, so the message reads the same every run.
    """
    present = set(table_columns)
    return tuple(sorted({column for column in declared if column not in present}))


def read_declaration(adapter: str, path: Path, select: str | None = None) -> DeclaredLink:
    """Read one declaration with the named adapter.

    Args:
        adapter: One of :data:`ADAPTERS`.
        path: The declaration root: a Feast feature repo, or a dbt project (or
            the manifest inside one).
        select: Which declaration to read, when the root holds more than one. A
            root holding exactly one needs no answer; a root holding several is
            a question, and the adapter asks it by name rather than picking.

    Returns:
        What the declaration says.

    Raises:
        AdapterError: The adapter is unknown, its optional dependency is not
            installed, the path holds no declaration, or the declaration does not
            say enough to link a model.
    """
    # Imported here, not at module import: each adapter carries an optional
    # dependency, and `link --from dbt` must not need feast installed to run.
    if adapter == "feast":
        from modelguard.adapters.feast import read_repo

        return read_repo(path, select)
    if adapter == "dbt":
        from modelguard.adapters.dbt import read_manifest

        return read_manifest(path, select)
    raise AdapterError(f"unknown adapter {adapter!r}; --from takes {', '.join(ADAPTERS)}")


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "DeclaredFeature",
    "DeclaredLink",
    "excluded_columns",
    "missing_columns",
    "read_declaration",
]
