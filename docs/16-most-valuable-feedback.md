# Janus: Most Valuable Feedback

Concrete, reproducible findings from building a data-to-model reliability agent on
DataHub OSS. Each is a real bug or a real documentation gap hit while writing
Janus, with the version, the symptom, a minimal repro, and the workaround we
shipped. They are ordered roughly by impact.

Versions: `acryl-datahub` 1.6.0.13 (CLI + Python SDK), DataHub GMS 1.5.0.6 (local
Quickstart), `mcp-server-datahub` v0.6.0.

---

## 1. Incidents cannot attach to an mlModel

**Package:** DataHub GMS 1.5.0.6.
**Symptom:** raising an incident with an `mlModel` resource URN returns a GMS 500.
`incidentInfo.entities` declares
`entityTypes: [dataset, chart, dashboard, dataFlow, dataJob, schemaField]`; the one
entity a reliability incident is most naturally about, the model in production,
cannot carry one. This makes the ML-incident story impossible today.
**Repro:** `raiseIncident(input:{ resourceUrn:"urn:li:mlModel:(...)", type: CUSTOM,
title:"x", description:"y" })`.
**Workaround:** attach the incident to the offending dataset or `schemaField` and
carry model-level risk as structured properties on the model. Filed as an upstream
RFC (see `mcp_ext/RFC-ml-incidents.md`).

## 2. No incident/assertion write tools in the MCP server

**Package:** `mcp-server-datahub` v0.6.0.
**Symptom:** the server ships read tools and metadata-edit tools, but no tool to
raise an incident, create an assertion, or write lineage. An agent can find a
data-to-model failure but cannot record it.
**Workaround:** a thin `raise_incident` tool wrapping the OSS-native `raiseIncident`
mutation, contributed in `mcp_ext/raise_incident_tool.py`.

## 3. `incidentsSummary` is never written by GMS

**Package:** DataHub GMS 1.5.0.6.
**Symptom:** after `raiseIncident` succeeds, neither the dataset nor the
`schemaField` carries the `incidentsSummary` aspect, so the documented way to list a
resource's incidents returns nothing. A summary-based dedup silently finds nothing
and duplicates every finding on every run.
**Workaround:** traverse the `IncidentOn` relationship inbound
(`graph.get_related_entities`) to enumerate a resource's incidents.

## 4. Searching incidents by their `entities` field 500s

**Package:** DataHub GMS 1.5.0.6.
**Symptom:** a GraphQL non-null violation: "The field at path
`/scrollAcrossEntities/searchResults[0]/entity` was declared as a non null type, but
the code involved in retrieving data has wrongly returned a null value."
**Workaround:** the `IncidentOn` relationship traversal above; do not search
incidents by `entities`.

## 5. `updateIncidentStatus` takes `IncidentStatusInput`, not `UpdateIncidentStatusInput`

**Package:** DataHub GMS 1.5.0.6 (docs mismatch).
**Symptom:** the docs and the GraphQL mutation reference name
`UpdateIncidentStatusInput`. GMS answers
`Validation error (VariableTypeMismatch@[updateIncidentStatus])`. The real type is
`IncidentStatusInput`.
**Workaround:** introspect `Mutation` on a live server; there is no other way to find
the right name.

## 6. No Python SDK wrapper for incidents, and no SDK entity classes for several ML entities

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** incidents have no Python SDK wrapper (the writes go through raw
GraphQL). There are also no SDK entity classes for `MLFeature`, `MLPrimaryKey`,
`MLFeatureTable`, or `MLModelDeployment`; only `MLModel` and `MLModelGroup` have
classes.
**Workaround:** emit the aspects directly (`MLFeaturePropertiesClass`,
`MLPrimaryKeyPropertiesClass`, `MLFeatureTablePropertiesClass`,
`MLModelDeploymentPropertiesClass`) via `MetadataChangeProposalWrapper`.

## 7. `graph.exists()` returns False for every `schemaField`

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** `graph.exists(<schemaField urn>)` is always False, with no documented
alternative. Columns are not materialized as standalone entities.
**Workaround:** resolve a column through its parent dataset's `schemaMetadata`
aspect, matching on `fieldPath`. This also catches a misspelled column name, which
an entity-level check never would.

## 8. `FixedIntervalFreshnessAssertion` truncates any lookback of a day or more

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** `get_assertion_info()` builds
`FixedIntervalSchedule(multiple=self.lookback_interval.seconds)`; it must be
`total_seconds()`. `timedelta(hours=30).seconds == 21600`, so
`lookback_interval: "30 hours"` silently emits an assertion of **6 hours**. A
one-word fix, and a silently wrong data-quality check today.
**Workaround:** refuse any SLA of a day or more, or bypass the builder and construct
the schedule with `total_seconds()`.

## 9. `BaseEntityAssertion.get_assertion_info_aspect()` cannot be used idempotently

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** it calls `_ensure_source_created` -> `make_assertion_source()`, which
stamps `source.created` with the current time, so re-upserting an unchanged
assertion rewrites the aspect on every run. There is no way to pass a creation stamp
in. The aspect never converges.
**Workaround:** call `get_assertion_info()` (not `..._aspect()`), build
`AssertionSource` yourself, and preserve any existing stamp.

## 10. `DataHubClient.assertions` is Cloud-only but is not documented as such

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** `DataHubClient.assertions` is discoverable as a plain property on the
OSS client but raises `SdkUsageError` telling you to `pip install
acryl-datahub-cloud`, a paid product (it imports `acryl_datahub_cloud`). The OSS
path is undocumented.
**Workaround:** on OSS, parse the open-assertions YAML back through
`AssertionsConfigSpec` and emit `assertionInfo` yourself.

## 11. `operation` is a timeseries aspect but reads like a versioned one

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** `graph.get_aspect(urn, OperationClass)` raises `TypeError: Cannot get a
timeseries aspect using "get_aspect"`. The required
`get_latest_timeseries_value(urn, aspect, filter_criteria_map)` has a mandatory
third positional argument that is almost always `{}`, undiscoverable without reading
the source.
**Workaround:** `graph.get_latest_timeseries_value(urn, OperationClass, {})`.

## 12. `LineageResult.urn` is the upstream dataset, not the column, for a column-scoped query

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** `client.lineage.get_lineage(source_urn=t, source_column=c,
direction="upstream")` returns a result whose `.urn` is the upstream table, not the
`schemaField` the query asked about, even at one hop. The column identity is carried
only in `LineageResult.paths` (a list of `LineagePath(urn, entity_name,
column_name)`), which is undocumented as the source of column granularity. A caller
who compares `.urn` against a target column, the obvious thing to do, silently gets
zero matches on a graph where the column-level edge plainly exists. For a leakage
detector, that is the worst way to be wrong: it pronounces a leaking graph clean.
**Workaround:** read `LineageResult.paths` and never `.urn` for column-level
comparisons.

## 13. The `setuptools<82.0.0` pin holds users on a version with a published advisory

**Package:** `acryl-datahub` 1.6.0.13.
**Symptom:** the SDK declares `setuptools<82.0.0`. setuptools 81.0.0 carries
PYSEC-2026-3447 (`MANIFEST.in` exclusions bypassed by Unicode normalization when
building an sdist on a normalization-preserving filesystem, so a file meant to be
excluded can ship inside one; fixed in 83.0.0). Any environment with the SDK
installed therefore reports a known vulnerability that its own user cannot clear:
upgrading is refused as a dependency conflict, and pinning down is not an option
because there is no fixed version below the ceiling. Teams running `pip-audit` in
CI, which is common in regulated settings and is exactly where DataHub is pitched,
get a finding they can neither fix nor honestly dismiss.
**Repro:** `pip install acryl-datahub==1.6.0.13 && pip install "setuptools>=83"`
reports `acryl-datahub 1.6.0.13 requires setuptools<82.0.0, but you have
setuptools 83.0.0 which is incompatible`. Then `pip-audit` reports
PYSEC-2026-3447 against the 81.0.0 it left in place.
**Workaround:** none that keeps the declared dependency set intact. The advisory
is not reachable for most consumers (it affects sdist *building*, not use of the
SDK), so the practical answer is to record it as accepted rather than to pretend
it was fixed. Raising the ceiling to admit setuptools 83 would remove the finding
outright.

## 14. The mlflow source overwrites `mlModelProperties`, silently dropping another tool's `mlFeatures`

**Package:** `acryl-datahub` 1.6.0.13, `datahub ingest` with `source: mlflow`.
**Symptom:** the source emits `mlModelProperties` as a whole-aspect upsert built
only from what MLflow knows. Any field MLflow has no concept of is written back as
empty, and `mlFeatures` is the one that matters: a model linked to the columns it
trains on is silently unlinked by the next scheduled ingestion run. The same
happens to `dataProcessInstanceProperties.customProperties` on the training run,
so anything recorded against the run (for us, the training-time input schema)
disappears too. Nothing warns, and the model still renders fine in the UI, so the
loss is invisible until whatever depended on those links reports that it can no
longer see anything.

This is not specific to us. `mlFeatures`, `mlFeatureTables`, `groups` and
`trainingJobs` are exactly the fields an organization fills in from somewhere
other than MLflow, and an ingestion source that treats "MLflow does not know this"
as "this is not true" makes DataHub's ML entities unusable as a place where two
systems meet.

**Repro:**
```bash
datahub ingest -c mlflow.yml                       # model appears, mlFeatures empty
# attach features to the model with the SDK (emit mlModelProperties with mlFeatures)
datahub ingest -c mlflow.yml                       # same recipe, unchanged MLflow
# read mlModelProperties: mlFeatures is None again
```
Verified against a real MLflow tracking server (2026-08-01), twice, on two
model versions.

**Workaround:** keep anything that has to survive ingestion in an aspect the
source does not write. Janus records what it was told in
`structuredProperties` on the model, so the link can be replayed with one command
after each ingest. The fix upstream is a patch-style emit (`ChangeType.PATCH`, or
read-merge-emit) for the fields the source does not own, which is what
DataHub's own docs already recommend to *users* of the SDK for exactly this
reason.

## 15. A dbt semantic model silently overwrites the dbt model it is built on

**Package:** `acryl-datahub` 1.6.0.13, `datahub ingest` with `source: dbt` and
`include_column_lineage: true`; dbt-core 1.12.0, manifest schema v12.
**Symptom:** the source emits a dbt semantic model as a dataset keyed by the
semantic model's `name`. A semantic model built on a dbt model of the same name
(`- name: customer_features` over `model: ref('customer_features')`, which reads
as the obvious thing to type) therefore lands on the *same* URN as the model, and
the last writer wins. What survives is the semantic model's view of the table:
its entities become the columns, so `customer_id` is replaced by the entity name
`customer`, and the `upstreamLineage` the compiled SQL produced (including every
fine-grained column edge) is replaced by nothing.

Nothing warns. Both entities are legitimately named, the dataset still renders in
the UI with a schema and a description, and the only visible symptom is that
column-level lineage for that table is gone. For us that meant a target-leakage
detector that reads exactly those edges reported a leaking model as having
nothing to check, on a graph that still held the leak.

**Repro:**
```bash
# a dbt project with models/marts/customer_features.sql and a schema yml declaring
#   semantic_models: [{name: customer_features, model: ref('customer_features'), ...}]
dbt run && dbt docs generate
datahub ingest -c dbt.yml
# read upstreamLineage on urn:li:dataset:(urn:li:dataPlatform:dbt,<db>.<schema>.customer_features,PROD)
#   -> no upstream but the warehouse sibling, no fineGrainedLineages
#   read schemaMetadata -> the entity name, not the key column
# rename the semantic model to `customers`, regenerate, re-ingest: both come back
```
Verified on a postgres + dbt + MLflow stack, 2026-08-04.

**Workaround:** never name a semantic model after the dbt model it is built on,
which is a rule nobody can be expected to know and which dbt itself does not
require. The fix upstream is to key semantic models on a URN that cannot collide
with a model's (they are different kinds of node in the same manifest), or, at
minimum, to warn when two manifest nodes resolve to one dataset URN.

---

## 16. `datahub-agent-context` pins one exact `acryl-datahub` patch, so the Agent Context Kit cannot be installed beside any project that pins a different one

**Package:** `datahub-agent-context` 1.6.0.6 through 1.7.0 (every release checked
from 1.6.0.6 on). **Severity:** high, and it is the reason a project that wanted
to use the Agent Context Kit ended up unable to declare it as a dependency.

**Symptom:** the kit declares `acryl-datahub[datahub-rest]==1.6.0.6`, an exact
pin, and has kept declaring that same patch as its own version moved on.
`datahub-agent-context==1.7.0` still requires `acryl-datahub==1.6.0.6`. Any
project pinning a different `acryl-datahub` patch (this one pins 1.6.0.13, the
version its whole suite and benchmark are verified against) cannot install the
kit at all: pip answers `ResolutionImpossible`.

The pin used to track the release. `datahub-agent-context==1.5.0.19` requires
`acryl-datahub==1.5.0.19`, matching. From 1.6.0.6 onward the version bump stopped
propagating into the dependency, which reads as release automation that lost a
step rather than a deliberate compatibility statement.

**Repro:**
```bash
pip install "acryl-datahub[datahub-rest]==1.6.0.13" "datahub-agent-context==1.7.0"
# ERROR: ResolutionImpossible
#   The user requested acryl-datahub==1.6.0.13
#   datahub-agent-context 1.7.0 depends on acryl-datahub==1.6.0.6

# and the pin, per release:
#   1.7.0     -> acryl-datahub[datahub-rest]==1.6.0.6
#   1.6.0.17  -> acryl-datahub[datahub-rest]==1.6.0.6
#   1.6.0.13  -> acryl-datahub[datahub-rest]==1.6.0.6
#   1.5.0.19  -> acryl-datahub[datahub-rest]==1.5.0.19   <- tracked, then stopped
```
Verified 2026-08-05 against PyPI in a clean venv.

**Why it matters more than an ordinary pin:** the kit's whole purpose is to be
added to somebody else's agent, and an agent that talks to DataHub almost
certainly already depends on `acryl-datahub`. An exact pin on a single patch
means the kit is installable only into a project that happens to have chosen the
same patch, which for a library is close to being installable nowhere. It is the
same failure mode this project had to fix in its own packaging: an exact `==`
pin in a library makes it uninstallable next to anything else.

**Workaround:** none that keeps a verified `acryl-datahub` version. Either pin
the whole project down to 1.6.0.6 and re-verify against it, or do not install the
kit. This project chose the latter and wrote the integration against the kit's
public API anyway, so it starts working the moment the pin is loosened.

**Suggested fix:** a compatible-release specifier rather than an exact one
(`acryl-datahub[datahub-rest]~=1.6.0`), which still excludes an incompatible
major while letting the kit sit beside an ordinary DataHub project. Failing that,
restore whatever step used to keep the pin in step with the release, so 1.7.0
requires a 1.7.x.
