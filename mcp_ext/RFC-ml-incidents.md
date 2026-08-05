# RFC: incident write tools and first-class ML incidents

**Status:** proposal
**Affects:** `acryldata/mcp-server-datahub` (v0.6.0), DataHub GMS (observed 1.5.0.6)
**Author:** Janus (Build with DataHub: The Agent Hackathon)

## Summary

Two gaps make it impossible for an agent to close the loop on a data-to-model
reliability failure today: the MCP server has no incident-write tool, and DataHub
itself will not let an incident attach to an `mlModel`. This RFC proposes a thin
`raise_incident` mutation tool (implemented in `raise_incident_tool.py`) and, as a
larger follow-up, a first-class ML-incident workflow.

## Problem 1: the MCP server is read-only for reliability signals

`mcp-server-datahub` v0.6.0 ships read tools (`search`, `get_lineage`,
`get_lineage_paths_between`, `get_dataset_queries`, `get_entities`,
`list_schema_fields`) and, with `TOOLS_IS_MUTATION_ENABLED=true`, metadata-edit
tools (`add_tags`, `update_description`, `add_glossary_terms`, `set_domains`,
`add_owners`, `save_document`). There is **no** tool to raise an incident, create
an assertion, or write lineage.

So an agent can traverse column-level lineage, find that a stale table feeds a live
model, and draft an explanation, but it cannot record the incident. The finding has
nowhere to land in the graph. Incidents are OSS-native (the `raiseIncident` /
`updateIncidentStatus` GraphQL mutations exist and work), so the only thing missing
is the tool wrapper.

### Proposal

Add a `raise_incident` mutation tool, gated behind `TOOLS_IS_MUTATION_ENABLED` and
annotated `readOnlyHint: false`, mirroring the existing mutation tools. A thin
implementation is provided in `raise_incident_tool.py`: it wraps the `raiseIncident`
GraphQL mutation, derives the allowed incident types and target entity types from
the installed metadata model (so it cannot drift from the server), and refuses when
mutations are disabled. A companion `create_assertion` tool (open-assertions YAML to
`assertionInfo`) is the natural next tool; it is out of scope for this first PR.

## Problem 2: incidents cannot attach to an mlModel

`incidentInfo.entities` declares
`entityTypes: [dataset, chart, dashboard, dataFlow, dataJob, schemaField]`. Raising
an incident with an `mlModel` resource URN returns a **GMS 500**. ML entities are
first-class in the graph (models, model groups, features, feature tables,
deployments) but second-class in the incident model: the one entity a reliability
incident is most naturally *about*, the model in production, cannot carry one.

The current workaround (which Janus uses) is to attach the incident to the
offending dataset or `schemaField` and carry model-level risk as structured
properties on the model. That split is defensible, an incident is about a broken
data asset while a trust score is a property of the model, but it should be a
choice, not a constraint imposed by a 500.

### Proposal

Add `mlModel` (and, arguably, `mlModelGroup`) to the `entityTypes` of
`incidentInfo.entities`, so an incident can name the model it endangers directly.
This is a metadata-model change in GMS, larger than the tool above, hence a separate
track. If accepted, the MCP `raise_incident` tool needs no change: it reads the
allowed set from the schema.

## Related findings

While building against a local Quickstart we hit adjacent issues worth noting
alongside this RFC (full list in the project's Most Valuable Feedback survey):

- `incidentsSummary` is never written by GMS, so the documented way to list a
  resource's incidents returns nothing; the `IncidentOn` relationship must be
  traversed instead.
- Searching incidents by their `entities` field returns a GraphQL non-null
  violation (500).
- `updateIncidentStatus` takes `IncidentStatusInput`, not the
  `UpdateIncidentStatusInput` the docs name.

## Scope of the accompanying PR

Just Problem 1: the `raise_incident` tool file, its registration hook, and an
offline self-check. Problem 2 (the GMS metadata-model change) is filed here as the
RFC it needs to be.
