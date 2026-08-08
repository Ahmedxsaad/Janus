# Contributions back to DataHub

Three things built alongside Janus and offered back to the DataHub ecosystem.
Each fills a gap that was verified against the shipped software rather than
assumed.

## 1. The `datahub-ml-guard` skill

**Where:** [`skill/datahub-ml-guard/`](../skill/datahub-ml-guard)
**Destined for:** [datahub-project/datahub-skills](https://github.com/datahub-project/datahub-skills)

A skill that traces a model's features back to source columns to catch leakage,
drift and blast radius, and guides the write-back. It carries its own reference
material on the DataHub write surface, the detectors, and how to compose it with
DataHub's own MCP server.

**What makes it different from what is already in the registry.** Several
ML-reliability skills were submitted upstream while this one was being built, so
"first ML skill" is not the claim. The difference is what sits behind it: this
skill is a thin operator's guide over a real, tested, deterministic detection
engine, not a language model asked to eyeball a lineage graph. It never asks a
model whether a finding exists.

It is also explicit about **when not to use it**, which is unusual for a skill and
matters in a registry where several overlap: general metadata edits belong to
`datahub-enrich`, catalog discovery to `datahub-search`, warehouse-only lineage to
`datahub-lineage`. Reach for this one only when the question is about a model's
inputs.

The skill invokes a CLI, so its prerequisite is that the CLI is installable. That
is why the package was published before the skill was submitted: a prerequisite a
reviewer cannot run is worse than a clone they can.

## 2. A `raise_incident` tool for DataHub's MCP server, and an RFC

**Where:** [`mcp_ext/raise_incident_tool.py`](../mcp_ext/raise_incident_tool.py)
and [`mcp_ext/RFC-ml-incidents.md`](../mcp_ext/RFC-ml-incidents.md)
**Targets:** [acryldata/mcp-server-datahub](https://github.com/acryldata/mcp-server-datahub)

Two gaps make it impossible for an agent to close the loop on a reliability
failure today, and the RFC addresses both.

**The MCP server is read-only for reliability signals.** It ships read tools and,
behind a flag, metadata-edit tools (tags, descriptions, glossary terms, domains,
owners, documents). There is no tool to raise an incident, create an assertion, or
write lineage. So an agent can traverse column-level lineage, find that a stale
table feeds a live model, draft an explanation, and then have nowhere to put it.
Incidents are OSS-native (the `raiseIncident` and `updateIncidentStatus` GraphQL
mutations exist and work), so the only missing piece is the tool wrapper. That is
what `raise_incident_tool.py` is.

**DataHub will not let an incident attach to a model.** `incidentInfo.entities`
declares dataset, chart, dashboard, dataFlow, dataJob and schemaField. The one
entity a reliability incident is most naturally about, the model in production,
cannot carry one; GMS answers 500. Janus works around it by attaching findings to
the data asset and carrying model risk as structured properties. The RFC proposes
first-class ML incidents as the larger follow-up.

## 3. Sixteen reproducible findings from building on DataHub

**Where:** [16-most-valuable-feedback.md](16-most-valuable-feedback.md)

Every bug and documentation gap hit while building Janus, each with the affected
version, the symptom, a minimal reproduction, and the workaround that shipped.
Ordered roughly by impact. A selection, to show what kind of finding these are:

- Incidents cannot attach to an `mlModel` (GMS 500).
- `incidentsSummary` is never written by GMS, so a dedup based on it silently
  finds nothing and duplicates on every scan.
- Searching incidents by their `entities` field returns a GraphQL non-null
  violation, through every route the SDK offers.
- `LineageResult.urn` is the upstream *dataset*, not the column, for a
  column-scoped query.
- `operation` is a timeseries aspect but reads like a versioned one, so
  `get_aspect` raises a `TypeError`.
- `graph.exists()` returns `False` for every `schemaField`.
- `DataHubClient.assertions` is Cloud-only and is not documented as such.
- `BaseEntityAssertion.get_assertion_info_aspect()` restamps `source.created` with
  the current time, so the aspect never converges and cannot be used idempotently.
- The MLflow source overwrites `mlModelProperties`, silently dropping another
  tool's `mlFeatures`. This is the one that shapes the product most: it is why
  `link --all` and `watch --events` exist.
- A dbt semantic model silently overwrites the dbt model it is built on.
- `datahub-agent-context` pins one exact `acryl-datahub` patch, so the Agent
  Context Kit cannot be installed beside any project pinning a different one.

Several of these are why parts of Janus look the way they do; see
[13-design-decisions.md](13-design-decisions.md) under "What DataHub would not let us
do".
