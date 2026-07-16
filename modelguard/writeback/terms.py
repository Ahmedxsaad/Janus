"""Attach glossary terms to the entities a finding implicates.

Terms carry meaning that tags do not. A tag says "look at this"; a term says
"this column *is* the label", and it is the vocabulary a data team already uses.
ModelGuard both reads terms (a label declaration, see detect/leakage.py) and
writes them (marking a feature it proved leaks), which is what closes the loop:
the finding lands in the same vocabulary a human would have used.

Idempotency by read-merge-emit
------------------------------
A ``glossaryTerms`` aspect is an upsert of the whole term list, so a blind write
would drop terms somebody else attached. Every write here reads the current
aspect, merges, and re-emits, exactly as
:func:`modelguard.writeback.labels.add_tag` does. Attaching a term that is already
present writes nothing at all, so a rerun converges.

Where a term can live
---------------------
Both routes were emitted and read back against a live GMS:

* directly on a ``schemaField``, which is how ModelGuard declares a label, and
* in ``editableSchemaMetadata`` on the parent dataset, which is the aspect the
  DataHub UI writes when a human tags a column.

This module writes the first. The detector reads both, so a human's UI
declaration counts exactly as much as ModelGuard's own (detect/leakage.py).
"""

from __future__ import annotations

import time

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    AuditStampClass,
    GlossaryTermAssociationClass,
    GlossaryTermInfoClass,
    GlossaryTermsClass,
)

from modelguard.client import DataHubConnection

#: The actor credited with attaching a term.
TERM_ACTOR = "urn:li:corpuser:datahub"

#: Glossary terms need a source, and DataHub's own enum for "we defined it here"
#: rather than importing it from an external business glossary.
INTERNAL_SOURCE = "INTERNAL"


def ensure_term(
    conn: DataHubConnection,
    term_urn: str,
    name: str,
    definition: str,
) -> str:
    """Create the term entity if absent, so it renders with a name and meaning.

    A term URN can be attached to an entity without the term existing, but then
    the UI shows a bare URN and a reader has no way to learn what it means. The
    definition is the whole point of a term, so it is required, not optional.

    Idempotent: the term's info aspect is a pure function of its arguments, so
    re-emitting an unchanged term leaves the aspect byte-for-byte identical.

    Returns:
        The term URN, for convenience at the call site.
    """
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=term_urn,
            aspect=GlossaryTermInfoClass(
                name=name,
                definition=definition,
                termSource=INTERNAL_SOURCE,
            ),
        )
    )
    return term_urn


def add_term(
    conn: DataHubConnection,
    entity_urn: str,
    term_urn: str,
    *,
    now_ms: int | None = None,
) -> bool:
    """Attach a term to an entity, preserving every term already on it.

    Args:
        conn: A connection with write credentials.
        entity_urn: The entity to term. A schemaField or an mlFeature, typically.
        term_urn: The term to attach. It should already exist; see
            :func:`ensure_term`.
        now_ms: The audit stamp instant. Defaults to now. Only used when the term
            is actually added, so a no-op rerun writes no new stamp.

    Returns:
        True when the term was added, False when it was already present and
        nothing was written.
    """
    existing = conn.graph.get_aspect(entity_urn, GlossaryTermsClass)
    current = list(existing.terms) if existing else []

    if any(association.urn == term_urn for association in current):
        return False

    stamp = AuditStampClass(
        time=now_ms if now_ms is not None else int(time.time() * 1000),
        actor=TERM_ACTOR,
    )
    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=entity_urn,
            aspect=GlossaryTermsClass(
                terms=[*current, GlossaryTermAssociationClass(urn=term_urn)],
                auditStamp=stamp,
            ),
        )
    )
    return True


def read_terms(conn: DataHubConnection, entity_urn: str) -> tuple[str, ...]:
    """Return the term URNs currently attached to an entity.

    Used by the read-before-write above, and by the tests to prove a term landed
    on the graph rather than only in a local object.
    """
    aspect = conn.graph.get_aspect(entity_urn, GlossaryTermsClass)
    if aspect is None:
        return ()
    return tuple(association.urn for association in aspect.terms)
