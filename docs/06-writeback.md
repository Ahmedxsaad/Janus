# Everything Janus writes into DataHub

The point of Janus is not a report on a terminal. It is that a run's output lands
in the catalog, where the data lives, on the entities the finding is about. This
page is the complete write surface.

The rules governing it are in [02-architecture.md](02-architecture.md); the
threat model is [10-security.md](10-security.md).

## The rules every write obeys

1. **Fixed and parameterized.** `writeback/` exposes a closed set of functions
   with validated arguments. URNs must resolve, incident types are read from
   `IncidentTypeClass` rather than hardcoded, scores are clamped. GraphQL uses
   bound variables with no string interpolation. There is no code path that sends
   a GraphQL string a caller supplied.
2. **Read before write, always.** Every function reads the current state and
   merges. Nothing is blind-written.
3. **Idempotent.** A rerun converges. The benchmark measures this by rerunning a
   scan and counting duplicates in the graph.
4. **Never a value no detector computed.** The number in a property comes from a
   finding's evidence or from a detector. Never from a language model, never from
   thin air.
5. **Approval belongs to `agent/`, not here.** Functions in this package assume
   the write was already approved.

## Incidents

`writeback/incidents.py`. `raiseIncident` and `updateIncidentStatus` through
`execute_graphql`, because incidents have no Python SDK wrapper in the pinned
version.

**Deduplication** is on `(resource_urn, incident_type, title)` across the
resource's *active* incidents. Existing incidents are found by traversing the
`IncidentOn` relationship **inbound**. `incidentsSummary` is never read: GMS does
not write it, so a dedup based on it silently finds nothing and duplicates on
every scan. That was found by having it happen.

Two DataHub constraints shape this:

- **An incident cannot attach to an `mlModel`.** `incidentInfo.entities` accepts
  dataset, chart, dashboard, dataFlow, dataJob and schemaField; GMS answers 500
  for a model. Findings therefore land on the data asset, and model risk is
  carried as structured properties. Filed upstream as an RFC.
- **Incidents cannot be searched.** `scrollAcrossEntities(types: [INCIDENT])`
  fails with a GraphQL non-null violation through every route the SDK offers,
  which is why they are always reached inbound from their resources.

The allowed types are OPERATIONAL, FRESHNESS, VOLUME, FIELD, SQL, DATA_SCHEMA and
CUSTOM. There is no COLUMN type; the column-scoped one is FIELD. `graph.exists()`
is always False for a `schemaField`, so a column is resolved through its parent
dataset's `schemaMetadata`.

**Resolution.** A finding that has cleared closes its incident, and that has to
work whether the recovery is noticed by a long-running `watch`, a fresh `scan`,
or a restarted process, so reconciliation reads from the graph on every run
rather than from in-process memory. It keys on the **finding type**, not on
`(resource, incident_type)` alone: leakage and a sensitive source both raise a
FIELD incident on the same column, and a flatter key would leave a fixed leak
open forever.

One leakage case needs its own property. The most common way a leak is actually
fixed is deleting the offending column outright, which leaves no lineage to walk
back through, so `janus.open_leak_columns` records which columns a model has an
open leak incident on and lets the incident be closed anyway.

## Structured properties

`writeback/properties.py`, with the definitions in
`writeback/props/janus_props.yaml` rather than in code, so a reviewer can read
the graph contract without reading Python.

DataHub separates a property's **definition** (name, type, cardinality, which
entity types may carry it) from its **assignment** (a value on one entity). Both
are written here, by emitting the `structuredPropertyDefinition` and
`structuredProperties` aspects directly rather than shelling out to the CLI.
Assignment is read-before-write: values for properties this run does not touch
are preserved, and rewriting the same value is a no-op.

Twelve properties are defined. Seven describe a model's risk, three record what
`link` was told, one describes the agent's own reach, and one lives on a feature.
The full table is in [03-components.md](03-components.md).

Two are facts about the code rather than knobs, and are therefore not overridable
from the environment:

- **`janus.scoring_version`.** Two scores are comparable only when the same
  weights, band boundaries and contributing findings produced them. Without the
  version, a drop caused by a release that added a detector looks exactly like a
  drop caused by a bug.
- **`TABLE_LEVEL_PRECISION`**, the measured precision the degraded mode quotes
  about itself. A tool that let an operator dial up the confidence it claims for
  itself would be worse than one that claimed none.

## Tags and glossary terms

`writeback/labels.py` and `writeback/terms.py`.

A tag is how a model announces that something upstream broke, because an incident
cannot live on it. `model-at-risk` is the first thing a human sees in search
results and makes "which models are at risk right now" a one-click filter.

Both write by **read-merge-emit**, and that is not a stylistic choice.
`datahub.specific` ships patch builders for datasets, charts, dashboards and data
jobs but **not for `mlModel`**, so there is no patch-based add-tag for the entity
that needs tagging. A `globalTags` or `glossaryTerms` aspect is an upsert of the
whole list, so a blind write would silently drop tags somebody else applied.

Terms carry meaning tags do not. A tag says "look at this"; a term says "this
column *is* the label". Janus both reads terms (the label declaration) and writes
them (marking a feature it proved leaks), which closes the loop: the finding
lands in the vocabulary a human would have used. If your catalog already has a
label term, `JANUS_LABEL_TERM_URN` points at it and Janus honours yours instead
of creating one.

## Knowledge documents

Three modules, four document types, all read entirely out of the catalog and all
first-class searchable graph entities linked to their subject by
`related_assets`. `Document` is an OSS SDK entity, verified against a live
Quickstart before the code was written.

**The Model Impact Report** (`documents.py`). One per finding. The incident says
a table broke; the report says what that means for the model: which features it
reaches, whether the endpoint is live, the trust waterfall, the trend, the
counterfactual, and what to do. It is the artifact a human actually reads, and
attaching it to the model is what leaves institutional memory behind.

**The model card and the evidence pack** (`model_documents.py`). Per model, and
neither reports a problem. Described in [07-reports.md](07-reports.md).

**The Data Card** (`feature_documents.py`). One per feature.

Document ids fold a hash of the **full URN**, not the bare name, so two entities
sharing a name cannot overwrite each other's card. A document's dedup key is the
entity plus the finding type, so regenerating replaces rather than leaving a
second copy behind.

## Guarding assertions

`writeback/assertions.py`. Detecting a stale table once is worth little; leaving
behind a check that fires next time is the point. Janus writes the freshness
assertion in two forms:

- **Open-assertions YAML**, DataHub's own portable declarative spec, validated by
  parsing it back through `AssertionsConfigSpec`. Committed at
  `examples/guarding-assertion-loans-raw.yml`.
- **The assertion entity plus a run event**, so it appears on the dataset's
  Quality tab with the result Janus actually measured.

**What is OSS and what is not, stated rather than hidden.** `DataHubClient.assertions`
is DataHub Cloud only: the property imports the Cloud package. Smart and
anomaly-detection assertions and scheduled evaluation are Cloud features too. So
detection is Janus's own and `assertionInfo` is emitted directly.
`get_assertion_info_aspect()` is never called: it restamps `source.created` with
the current time, so the aspect never converges and cannot be used idempotently.

A run event **always reports what a detector actually measured on that run**,
never a fabricated pass or fail, and `nativeResults` names the source of the
number. A fresh table writes SUCCESS. A run event is a timeseries aspect, so
emitting it appends: an undo is a newer event, not a delete. That is also why
DataHub truncates any freshness lookback of a day or more, which Janus refuses
rather than silently accepting.

## The input data contract

`writeback/contract.py`. A pure renderer: it reads the graph and returns text,
writing nothing back. `scan --contract-out` decides where the YAML lands.

An incident says what went wrong. A contract says what was expected to be true in
the first place: for every table the model trains on, the schema it should have
and how fresh it should be. It is standards-based, **ODCS v3.1.0** (Linux
Foundation Bitol), so anything that speaks ODCS can enforce it, not just Janus.

Every column and native type is quoted verbatim from the current
`schemaMetadata`; `required` comes from the field's own `nullable` flag. The
freshness expectation is the SLA Janus would guard, rendered as an ODCS
`slaProperties` entry so the contract and the emitted assertion declare the same
check. A `logicalType` is mapped from the native type only where the mapping is
unambiguous and omitted otherwise: a wrong logical type is worse than an absent
one, and `physicalType` still carries the truth.

**Nothing no detector measures is invented.** There is no volume or distribution
expectation in the contract, because Janus does not measure one.

The committed example validates against datacontract-cli's bundled ODCS 3.1.0
JSON schema.

## The link

`writeback/link.py` and `link_infer.py`. Covered in full in
[05-the-link.md](05-the-link.md).

## Trend histories

`trust_history.py` and `coverage_history.py`. A trust score of 82 is neither good
nor bad until you know it was 95 last Tuesday. The direction is the signal.

Each scan that scores a model appends one pipe-separated line, oldest first, to a
**capped** MULTIPLE structured property. Capped because this is a recent trend a
human reads on the model in the DataHub UI, not an audit log: the full audit
trail is the `janus.run_id` stamped on every write of that run, which resolves to
an entity.

A structured property and not a new timeseries aspect, because a new timeseries
aspect is a change to DataHub's own metadata model and belongs in the RFC lane
rather than in a tool that has to work against a stock GMS.

`coverage_history` is the same mechanism for the catalog-wide figure, and it is
the only property that does not describe somebody else's asset. It therefore
hangs on Janus's own `dataFlow`, the entity every scan run already belongs to.
A `dataFlow` carrying a structured property was verified against a live GMS, not
assumed.

## The agent, as an entity in the graph it guards

`writeback/process_instance.py`. Every scan emits a `dataProcessInstance` under a
`dataJob` under a `dataFlow`, with the entities it read as inputs, the entities
it wrote as outputs, and a run event carrying whether it finished.

The argument for this is the product's own thesis turned on itself: a pipeline
nobody catalogued is a pipeline nobody can reason about, so an agent that writes
incidents into a graph while remaining invisible in that graph is exempting
itself from its own case. It closes three gaps at once:

- An incident stamped "Raised by Janus run scan-abc123" used to send a reader to
  grep a log. Now the `run_id` is an entity to open.
- The agent's own reach across the catalog becomes queryable.
- A half-finished run is visible as a run that did not complete.

A dry run emits nothing, keeping the no-write contract intact. A
`dataProcessInstance`'s inputs and outputs accept dataset and `mlModel` only,
which is what the implementation was corrected to.

## What the seeder writes

`janus/seed/` is not part of the product surface and production code never
imports it, but it is what makes the demo and the benchmark reproducible. It
writes, idempotently: two warehouse tables with real schemas so `schemaField`
URNs resolve; column-level lineage between them including the edge that carries
the label into a feature; `mlFeature`, `mlPrimaryKey` and `mlFeatureTable`
entities; a training run consuming the feature table; a model, its group, and a
deployment.

There are no SDK entity classes for `MLFeature`, `MLPrimaryKey`, `MLFeatureTable`
or `MLModelDeployment` in the pinned version, so those aspects are emitted as
MCPs. Only `MLModel` and `MLModelGroup` have classes.

`graph_spec.py` is the single source of truth for every seeded URN and value.
Nothing hardcodes a URN string, and a test asserts the spec is self-consistent.

## Scenarios: the failures, planted and reverted

`janus/seed/scenarios.py`. Nine of them, each writing one fact a deterministic
detector will find, and **each reversible**, so a run can prove both directions:
planted produces a finding, reverted produces none.

| Scenario | What it plants |
|---|---|
| Stale source | Backdates the `operation` aspect on the raw table |
| Schema drift | Retypes a column after the training snapshot was taken |
| Target leakage | Derives a feature from the label column |
| Second leak path | A second derivation, so cutting one is not enough |
| Common-ancestor label | A label reachable only through a shared ancestor |
| Proxy attribute | A feature and a protected attribute sharing an ancestor |
| Label lookalike | A column named like a label but carrying no term (a confusable negative) |
| Sensitive source | Classifies a column three joins upstream of a feature |
| Deprecated input | Marks a training input deprecated |

Every scenario stamps `janus.scenario` into the aspect's custom properties, so a
reader can tell a planted failure from a real one. Scenarios take the current
instant as an argument, so tests can fix it. Reverting the stale-source scenario
emits a *newer* operation event announcing a refresh rather than deleting
anything, because `operation` is a timeseries aspect.

## The complete list of aspects touched

Read: `upstreamLineage` and column-level lineage, `operation`, `schemaMetadata`,
`glossaryTerms`, `globalTags`, `deprecation`, `mlModelProperties`,
`mlFeatureProperties`, `dataProcessInstanceInput`, `dataProcessInstanceProperties`,
`mlModelDeploymentProperties`, `ownership`, `versionProperties`, `incidentInfo`,
`assertionRunEvent`, `structuredProperties`, `datasetProperties`.

Written: `incidentInfo` (via GraphQL), `structuredPropertyDefinition`,
`structuredProperties`, `globalTags`, `glossaryTerms`, `assertionInfo`,
`assertionRunEvent`, `mlFeatureProperties`, `mlPrimaryKeyProperties`,
`mlFeatureTableProperties`, `mlModelProperties`, `dataProcessInstanceInput`,
`dataProcessInstanceProperties`, `dataProcessInstanceRunEvent`, `dataFlowInfo`,
`dataJobInfo`, plus the `Document` entity.

Janus reads the metadata graph and nothing else. It never connects to a warehouse
and never issues a query against a table.
