"""Attach tags to the entities a finding implicates.

An incident cannot live on an mlModel, so the tag is how a model announces that
something upstream of it broke. It is the first thing a human sees in the UI
search results, and it is what makes "which models are at risk right now" a
one-click filter.

Idempotency by read-merge-emit
------------------------------
``datahub.specific`` ships patch builders for datasets, charts, dashboards, and
data jobs, but **not** for mlModel, so there is no patch-based add-tag for the
entity we need to tag. Emitting a ``globalTags`` aspect is an upsert that
replaces the whole aspect, which would silently drop tags somebody else applied.
So every write here reads the current aspect, merges, and re-emits, exactly as
:func:`modelguard.writeback.properties.assign_properties` does. Adding a tag that
is already present writes nothing at all.
"""

from __future__ import annotations

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass
from datahub.metadata.urns import TagUrn
from datahub.sdk.tag import Tag

from modelguard.client import DataHubConnection

#: Colour the model-at-risk tag renders with in the UI. Amber: a warning, not an
#: outage. The model still serves; its inputs are no longer trustworthy.
AT_RISK_TAG_COLOR = "#EAB308"


def ensure_tag(
    conn: DataHubConnection,
    name: str,
    description: str,
    *,
    color: str = AT_RISK_TAG_COLOR,
) -> str:
    """Create the tag entity if it does not exist, so it renders with a name.

    A tag URN can be attached to an entity without the tag itself existing, but
    then the UI shows a bare URN and the tag has no description. Upserting the
    entity first is what makes the tag legible.

    Returns:
        The tag URN.
    """
    conn.client.entities.upsert(Tag(name=name, description=description, color=color))
    return str(TagUrn(name))


def add_tag(conn: DataHubConnection, entity_urn: str, tag_urn: str) -> bool:
    """Attach a tag to an entity, preserving every tag already on it.

    Args:
        conn: A connection with write credentials.
        entity_urn: The entity to tag.
        tag_urn: The tag to attach. The tag entity should already exist; see
            :func:`ensure_tag`.

    Returns:
        True when the tag was added, False when it was already present and
        nothing was written.
    """
    existing = conn.graph.get_aspect(entity_urn, GlobalTagsClass)
    current = list(existing.tags) if existing else []

    if any(association.tag == tag_urn for association in current):
        return False

    conn.graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=entity_urn,
            aspect=GlobalTagsClass(tags=[*current, TagAssociationClass(tag=tag_urn)]),
        )
    )
    return True


def read_tags(conn: DataHubConnection, entity_urn: str) -> tuple[str, ...]:
    """Return the tag URNs currently attached to an entity.

    Used by the write-back layer's read-before-write and by the tests to prove a
    tag landed on the graph rather than only in a local object.
    """
    aspect = conn.graph.get_aspect(entity_urn, GlobalTagsClass)
    if aspect is None:
        return ()
    return tuple(association.tag for association in aspect.tags)
