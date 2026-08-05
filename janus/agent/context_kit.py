"""Organizational context for a finding, read through DataHub's Agent Context Kit.

A detector answers whether something is wrong. It does not answer who to tell,
which domain the asset belongs to, or what its owners wrote down about it, and
those are the first three questions a person asks on reading an incident. That
context already exists in DataHub; nothing in the detection path collects it,
because none of it changes whether the finding exists.

This module fetches it and hands it to the narrator as additional grounded fact.
The read goes through `datahub-agent-context`, DataHub's own Agent Context Kit,
using its read-only toolset (``build_langchain_tools`` defaults
``include_mutations=False``, and this module never passes True). The kit is an
optional dependency: without it installed every function here returns ``None``
and a scan is unchanged.

Where this sits relative to the design law (docs/plan/architecture.md section 2):

* Detection never sees any of this. It runs, and finishes, before this is read.
* Nothing here can create, suppress, or re-rank a finding. It is fetched for a
  finding that already exists, and it reaches the model inside the same
  delimited untrusted block the rest of the catalog text does, because owners
  and descriptions are editable by anybody with catalog access.
* No language model chooses what is fetched. The URNs come from the finding, and
  the tool called is fixed. The kit supplies the read, not the decision to read.

That last point is why this is a library call and not an agent loop. The Agent
Context Kit's tools are also the ones an LLM tool-caller would drive, and handing
them to a model here would let it decide which parts of the catalog an incident
gets to mention, which is exactly the class of decision this project keeps out of
a model's hands.
"""

from __future__ import annotations

import logging
from typing import Any

from janus.client import DataHubConnection
from janus.models import Finding

logger = logging.getLogger(__name__)

#: How many entities to describe for one finding. The resource the incident
#: attaches to, plus the models it puts at risk, is already the whole of what a
#: reader asks about, and an unbounded list would put a wide blast radius's worth
#: of catalog prose into a prompt whose useful part is three lines long.
MAX_ENTITIES = 4

#: Truncation for a single description. Owners write essays; a narrator needs the
#: first sentence or two, and the incident body is not the place to reproduce a
#: table's full documentation.
MAX_DESCRIPTION_CHARS = 240


def available() -> bool:
    """Whether the Agent Context Kit is installed.

    Import-only, so it is safe to call when the extra is absent: that is the
    ordinary state for anyone who installed the package without ``[context]``.
    """
    try:
        import datahub_agent_context  # noqa: F401
    except ImportError:
        return False
    return True


def _read_only_tool(conn: DataHubConnection, name: str) -> Any | None:
    """Return one named tool from the kit's read-only toolset, or None.

    ``include_mutations`` is left at its default of False rather than passed
    explicitly as False, so that a future kit release which changed that default
    would fail this project's tests rather than silently arm a write tool: the
    assertion in ``tests/agent/test_context_kit.py`` checks the built set
    contains no mutation, which is a stronger check than the argument.
    """
    try:
        from datahub_agent_context.langchain_tools import build_langchain_tools
    except ImportError:
        return None

    tools = build_langchain_tools(conn.client)
    for candidate in tools:
        if getattr(candidate, "name", None) == name:
            return candidate
    logger.debug("agent context kit has no %s tool; skipping catalog context", name)
    return None


def _entity_urns(finding: Finding) -> tuple[str, ...]:
    """The URNs worth describing for this finding, resource first.

    Deduplicated with the order kept, because the resource the incident attaches
    to is the one a reader looks at first and a dict would not promise that.
    """
    urns = [finding.resource_urn]
    urns.extend(model.urn for model in finding.models_at_risk)
    seen: dict[str, None] = {}
    for urn in urns:
        seen.setdefault(urn, None)
    return tuple(seen)[:MAX_ENTITIES]


def _description(entity: dict[str, Any]) -> str | None:
    """The human description, from wherever this entity type keeps it.

    A dataset carries an edited description under ``editableProperties`` and an
    ingested one under ``properties``; an mlModel carries it at the top level.
    Verified against a live GMS rather than assumed, because the shapes differ
    per entity type and a wrong guess here reads as "nobody documented this".
    """
    for candidate in (
        entity.get("description"),
        (entity.get("editableProperties") or {}).get("description"),
        (entity.get("properties") or {}).get("description"),
    ):
        if candidate:
            text = " ".join(str(candidate).split())
            if len(text) > MAX_DESCRIPTION_CHARS:
                text = f"{text[:MAX_DESCRIPTION_CHARS]}..."
            return text
    return None


def _health(entity: dict[str, Any]) -> str | None:
    """What else is already wrong with this asset, per the catalog's own health.

    The most useful line this module produces. A detector knows the one thing it
    measured; the catalog knows the asset also has two other active incidents and
    a failing assertion, which is what tells a reader whether this finding is the
    story or a symptom of one.
    """
    entries = entity.get("health")
    if not isinstance(entries, list):
        return None
    failing = [
        f"{e.get('type')}={e.get('message')}"
        for e in entries
        if isinstance(e, dict) and e.get("status") == "FAIL" and e.get("message")
    ]
    return "; ".join(failing) if failing else None


def _owners(entity: dict[str, Any]) -> str | None:
    """Owner URNs, when the catalog names any.

    Read defensively across both shapes the SDK returns for ownership, because a
    catalog that names owners is exactly the one where this line matters and it
    is not the catalog this was developed against.
    """
    raw = entity.get("ownership") or entity.get("owners") or []
    if isinstance(raw, dict):
        raw = raw.get("owners", [])
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            owner = item.get("owner")
            names.append(str(owner.get("urn") if isinstance(owner, dict) else owner))
        elif item:
            names.append(str(item))
    names = [n for n in names if n and n != "None"]
    return ",".join(names[:3]) if names else None


def _describe(entity: dict[str, Any]) -> str | None:
    """Render one entity's organizational context as a single line.

    Returns None when the catalog holds nothing about it, so an undocumented
    entity contributes nothing rather than a line of empty fields. A narrator
    told "owners: none, domain: none" would dutifully write a sentence about it.
    """
    urn = entity.get("urn")
    if not urn:
        return None

    parts: list[str] = []

    owners = _owners(entity)
    if owners:
        parts.append(f"owners={owners}")

    domain = entity.get("domain")
    if isinstance(domain, dict):
        domain = domain.get("urn") or domain.get("name")
    if domain:
        parts.append(f"domain={domain}")

    health = _health(entity)
    if health:
        parts.append(f"catalog_health=({health})")

    description = _description(entity)
    if description:
        parts.append(f'description="{description}"')

    if not parts:
        return None
    return f"{urn}: {' '.join(parts)}"


def catalog_context(conn: DataHubConnection, finding: Finding) -> str | None:
    """Describe the entities a finding names, as grounded fact for the narrator.

    Args:
        conn: The live DataHub connection. Read from, never written to.
        finding: The already-decided finding. Never modified.

    Returns:
        One line per documented entity, or None when the kit is not installed,
        the read failed, or the catalog says nothing about any of them. None is
        an ordinary answer here and not an error: most catalogs are sparse, and a
        scan against one must be identical to a scan against a rich one except
        for the prose.

    This function does not raise. Context is a nicety on top of a finding that is
    already complete, and failing a scan because an optional enrichment call
    timed out would trade the whole result for a garnish.
    """
    tool = _read_only_tool(conn, "get_entities")
    if tool is None:
        return None

    urns = _entity_urns(finding)
    if not urns:
        return None

    try:
        raw = tool.invoke({"urns": list(urns)})
    except Exception as exc:  # noqa: BLE001 - see the docstring: never fatal
        logger.debug("agent context kit read failed (%s); continuing", type(exc).__name__)
        return None

    entities = raw.get("entities", raw) if isinstance(raw, dict) else raw
    if not isinstance(entities, list):
        return None

    lines = [
        line for line in (_describe(e) for e in entities if isinstance(e, dict)) if line is not None
    ]
    if not lines:
        return None
    return "\n".join(lines)
