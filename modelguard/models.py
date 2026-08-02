"""The typed findings that travel between ModelGuard's layers.

Detectors return these; the narrator reads them to draft prose; the write-back
layer turns them into incidents, tags, properties, assertions, and reports.
Nothing passes dicts across a layer boundary (modelguard/CLAUDE.md rule 1).

Everything here is a frozen dataclass computed from graph facts. No value in this
module is ever produced by an LLM: the narrative text an LLM writes is carried
separately, in :class:`~modelguard.agent.narrate.Narrative`, and is never allowed
to influence a dedup key or a severity.

Why the title is deterministic
------------------------------
An incident is deduplicated on ``(resource_urn, incident_type, title)``. If the
title were reworded on each scan, every scan would raise a duplicate incident.
:meth:`Finding.title` is therefore built from the failing table's name alone: it
contains no timestamp, no lag, and no LLM output. The measured numbers, which do
change between runs, live in :attr:`Finding.evidence` and in the description.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

#: Milliseconds in one hour, as a float so lag arithmetic stays fractional.
_MS_PER_HOUR = 3_600_000.0


class Severity(StrEnum):
    """How much damage a finding can do, worst first."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: Severity order, most severe first. Used to rank models and to pick the
#: severity of a blast radius from the models inside it.
_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
)


def severity_rank(severity: Severity) -> int:
    """Return a sort key for a severity, where 0 is the most severe."""
    return _SEVERITY_ORDER.index(severity)


class FindingType(StrEnum):
    """The kind of problem a detector found.

    One value per detector that raises a finding. The trust score has no value
    here: it is a rollup of these findings, not a finding of its own.
    """

    UPSTREAM_FRESHNESS = "upstream-freshness"
    TARGET_LEAKAGE = "target-leakage"
    INPUT_SCHEMA_DRIFT = "input-schema-drift"
    SENSITIVE_SOURCE = "sensitive-source"
    DEPRECATED_INPUT = "deprecated-input"


@dataclass(frozen=True)
class FreshnessSignal:
    """How stale a dataset is, measured against its freshness SLA.

    Read from the dataset's ``operation`` aspect, which is DataHub's own record
    of when the dataset last changed. This is metadata the graph already holds:
    ModelGuard does not query the warehouse.
    """

    dataset_urn: str
    last_updated_ms: int
    """When the dataset last changed, per its operation aspect."""
    observed_at_ms: int
    """When ModelGuard read the aspect. Lag is measured against this instant."""
    sla_hours: float

    @property
    def lag_hours(self) -> float:
        """Return how many hours have passed since the dataset last changed."""
        return (self.observed_at_ms - self.last_updated_ms) / _MS_PER_HOUR

    @property
    def is_stale(self) -> bool:
        """Whether the lag has exceeded the SLA. This is the whole predicate."""
        return self.lag_hours > self.sla_hours


@dataclass(frozen=True)
class ModelRef:
    """Enough of a model to judge how much a finding about it matters.

    Every detector needs the same three things about a model: who it is, whether
    it is serving traffic, and whether anyone owns it. What a detector adds on
    top (how many hops away the failure is, which feature leaks) is specific to
    that detector and lives on its own finding.
    """

    urn: str
    name: str
    deployments: tuple[str, ...]
    live_deployments: tuple[str, ...]
    """The subset of deployments whose status is IN_SERVICE."""
    has_owner: bool

    @property
    def is_live(self) -> bool:
        """Whether this model is currently serving predictions."""
        return bool(self.live_deployments)


@dataclass(frozen=True)
class ModelAtRisk(ModelRef):
    """One model that consumes, transitively, a dataset that has gone stale."""

    hops: int = 0
    """Lineage hops from the failing table to this model."""
    features_at_risk: tuple[str, ...] = ()
    """The model's own MLFeatures that sit downstream of the failing table."""

    @property
    def severity(self) -> Severity:
        """Rank the model by what its staleness would actually break.

        A model behind a live endpoint is scoring real traffic on stale inputs,
        which is a production incident. A model that is deployed but not in
        service is a release away from the same problem. A model with no
        deployment at all is a training-time concern.
        """
        if self.is_live:
            return Severity.CRITICAL
        if self.deployments:
            return Severity.HIGH
        return Severity.MEDIUM

    def sort_key(self) -> tuple[int, int, int, int, str]:
        """Order models by severity, then by blast size, then deterministically.

        Fan-out and ownership do not change the severity band, they order models
        inside one: a stale model feeding five features is worse than one feeding
        a single feature, and an unowned model is worse than an owned one because
        nobody is on the hook to fix it. The URN breaks any remaining tie so the
        report reads identically on every run.
        """
        return (
            severity_rank(self.severity),
            -len(self.live_deployments),
            -len(self.features_at_risk),
            int(self.has_owner),
            self.urn,
        )


@dataclass(frozen=True)
class BlastRadius:
    """Everything downstream of one failing table that the failure can reach."""

    signal: FreshnessSignal
    failing_table_urn: str
    failing_table_name: str
    downstream_datasets: tuple[str, ...]
    downstream_features: tuple[str, ...]
    models: tuple[ModelAtRisk, ...]
    """At-risk models, most severe first."""
    truncated: bool = False
    """True when the downstream traversal saw exactly the lineage result cap,
    so a model beyond it may exist and was never checked (F1, docs/plan/07).
    The staleness finding is certain either way; what is uncertain is whether
    ``models`` names every model this table endangers, which matters most when
    it is empty: a truncated, empty result cannot claim no model consumes this
    table, only that none was found within what the walk could see."""

    @property
    def severity(self) -> Severity:
        """Return the severity of the worst model in the radius.

        A stale table with no model downstream is still a data problem, but it is
        not the data-to-model failure ModelGuard exists to catch, so it lands at
        MEDIUM rather than inheriting a model's CRITICAL.
        """
        if not self.models:
            return Severity.MEDIUM
        return min((m.severity for m in self.models), key=severity_rank)

    @property
    def live_models(self) -> tuple[ModelAtRisk, ...]:
        """Return only the models currently serving predictions."""
        return tuple(m for m in self.models if m.is_live)


class Finding(ABC):
    """A detected problem, ready to be written back to the graph.

    One subclass per detector. The write-back layer and the narrator consume this
    interface and nothing wider, so a new detector reaches the graph without
    either of them growing a special case.

    ``resource_urn`` is where the incident attaches. It is never an mlModel:
    DataHub's incident model rejects that entity type (D-017), so a finding
    attaches to the data asset that is actually wrong (a dataset, or a column),
    and model-level risk is carried by a tag and structured properties on the
    model instead.
    """

    @property
    @abstractmethod
    def finding_type(self) -> FindingType:
        """Which detector produced this."""

    @property
    @abstractmethod
    def resource_urn(self) -> str:
        """The dataset or schemaField the incident attaches to."""

    @property
    @abstractmethod
    def incident_type(self) -> str:
        """A DataHub incident type, validated by the write-back layer."""

    @property
    @abstractmethod
    def severity(self) -> Severity:
        """How much damage this finding can do."""

    @property
    @abstractmethod
    def title(self) -> str:
        """The incident title, and part of the dedup key.

        Every implementation must return a pure function of stable graph facts:
        no measurement that drifts between scans, no timestamp, and never any
        LLM output. The dedup key is ``(resource_urn, incident_type, title)``, so
        a title that changes between runs raises a duplicate incident on every
        run (D-013, D-027).
        """

    @property
    @abstractmethod
    def evidence(self) -> Mapping[str, str]:
        """The measured facts behind the finding.

        The only input the narrator is allowed to speak from. Every value is
        computed from the graph, never generated.
        """

    @property
    @abstractmethod
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """The models this finding endangers, most severe first.

        The write-back layer tags these, records risk flags on them, and writes
        each one an impact report. A finding that endangers no model returns an
        empty tuple and no model is touched.
        """


@dataclass(frozen=True)
class FreshnessFinding(Finding):
    """An upstream table stopped refreshing, and models downstream consume it."""

    blast_radius: BlastRadius

    @property
    def finding_type(self) -> FindingType:
        """An upstream table stopped refreshing."""
        return FindingType.UPSTREAM_FRESHNESS

    @property
    def resource_urn(self) -> str:
        """The failing table. The incident lands on the asset that is actually wrong."""
        return self.blast_radius.failing_table_urn

    @property
    def incident_type(self) -> str:
        """DataHub's incident type for a table that stopped changing."""
        return "FRESHNESS"

    @property
    def severity(self) -> Severity:
        """Inherited from the worst model the staleness reaches."""
        return self.blast_radius.severity

    @property
    def title(self) -> str:
        """Deliberately carries no measurement.

        The lag drifts by seconds between scans, so a title quoting it would key
        a fresh incident every run.
        """
        return f"Stale upstream data in {self.blast_radius.failing_table_name}"

    @property
    def evidence(self) -> Mapping[str, str]:
        """The lag, the SLA, and the size of the blast radius. All measured."""
        signal = self.blast_radius.signal
        return {
            "failing_table": self.blast_radius.failing_table_name,
            "lag_hours": f"{signal.lag_hours:.1f}",
            "sla_hours": f"{signal.sla_hours:.1f}",
            "last_updated_ms": str(signal.last_updated_ms),
            "models_at_risk": str(len(self.blast_radius.models)),
            "live_models_at_risk": str(len(self.blast_radius.live_models)),
            "downstream_datasets": str(len(self.blast_radius.downstream_datasets)),
            "severity": str(self.severity),
        }

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """Every model the failing table reaches, already ordered worst first."""
        return self.blast_radius.models


@dataclass(frozen=True)
class LeakingFeature:
    """One feature whose upstream column lineage reaches a label column.

    The model is being trained on a column derived from the answer it is supposed
    to predict. Its offline metrics are inflated by construction, and they will
    not survive contact with production, where the label does not exist yet.

    ``column_path`` is the literal chain of columns the traversal walked, from the
    feature's own source column back to the label. It is what the incident quotes,
    and it is why this finding is auditable rather than a claim.
    """

    feature_urn: str
    feature_name: str
    source_column_urn: str
    """The schemaField the feature is computed from. Where the incident lands."""
    source_column_name: str
    label_column_urn: str
    label_column_name: str
    label_dataset_name: str
    column_path: tuple[str, ...]
    """Column names from the feature's source column back to the label, inclusive."""

    @property
    def path_text(self) -> str:
        """Render the leak path the way the incident and the report quote it.

        Consecutive repeats of one column name collapse to a single step. A real
        warehouse ingested from more than one source has sibling entities for the
        same physical table (dbt and the warehouse itself both describe
        ``customer_features``), so lineage legitimately walks the same column
        twice under two platforms and the chain reads "x <- x <- y". The repeat
        carries no information a reader of the derivation can use.
        """
        collapsed = [
            column
            for index, column in enumerate(self.column_path)
            if index == 0 or column != self.column_path[index - 1]
        ]
        return " <- ".join(collapsed)

    @property
    def origin(self) -> str:
        """Name the feature and its source column without saying the same word twice.

        A feature is very often named after the column it is computed from, and
        "the feature x is computed from x" reads like a bug. When they differ, both
        names matter and both are said.
        """
        if self.feature_name == self.source_column_name:
            return f"The feature {self.feature_name}"
        return f"The feature {self.feature_name}, computed from {self.source_column_name},"


@dataclass(frozen=True)
class LeakageFinding(Finding):
    """A model consumes a feature that derives from its own label.

    One finding per leaking feature, so each leaking column gets its own incident
    and its own dedup key. A model with two leaking features produces two
    findings, not one finding listing two columns.
    """

    model: ModelRef
    leak: LeakingFeature

    @property
    def finding_type(self) -> FindingType:
        """A feature derives from the label the model predicts."""
        return FindingType.TARGET_LEAKAGE

    @property
    def resource_urn(self) -> str:
        """The leaking column itself, not the model and not the table.

        The incident points at the precise column a human has to go fix.
        """
        return self.leak.source_column_urn

    @property
    def incident_type(self) -> str:
        """FIELD is DataHub's column-scoped incident type. There is no COLUMN."""
        return "FIELD"

    @property
    def severity(self) -> Severity:
        """Leakage is never minor, and a live model makes it a production lie.

        A leaking model that is serving is reporting an accuracy it does not have
        on data it will never see at inference time. One that is not yet deployed
        is a release away from the same thing.
        """
        return Severity.CRITICAL if self.model.is_live else Severity.HIGH

    @property
    def title(self) -> str:
        """A pure function of two column names. No measurement, no timestamp."""
        return (
            f"Target leakage: {self.leak.source_column_name} derives from "
            f"label {self.leak.label_column_name}"
        )

    @property
    def evidence(self) -> Mapping[str, str]:
        """The column path proving the derivation. Every value read from lineage."""
        return {
            "feature": self.leak.feature_name,
            "leaking_column": self.leak.source_column_name,
            "label_column": self.leak.label_column_name,
            "label_table": self.leak.label_dataset_name,
            "column_path": self.leak.path_text,
            "model": self.model.name,
            "model_is_live": str(self.model.is_live).lower(),
            "severity": str(self.severity),
        }

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """Exactly the one model that consumes this leaking feature."""
        return (self.model,)


@dataclass(frozen=True)
class SensitiveFeature:
    """One feature whose upstream column lineage reaches a classified column.

    The model was trained on data derived from a column somebody in the
    organization marked as restricted: PII, PHI, a protected attribute, anything
    their taxonomy says must not be learned from. Nothing about the model is
    broken, which is what makes this hard to find by any other means. It is a
    standing exposure: the model has absorbed information from a column whose
    handling somebody agreed to constrain, and the derivation is often several
    joins away from anyone who would recognise it.

    ``column_path`` is the literal chain of columns the traversal walked, from the
    feature's own source column back to the classified one. It is what the
    incident quotes, and it is why this finding is auditable rather than a claim.
    """

    feature_urn: str
    feature_name: str
    source_column_urn: str
    """The schemaField the feature is computed from. Where the incident lands."""
    source_column_name: str
    sensitive_column_urn: str
    sensitive_column_name: str
    sensitive_dataset_name: str
    marker_urn: str
    """The glossary term or tag that classifies the ancestor column.

    Quoted in the finding so the incident names the organization's own
    classification rather than ModelGuard's opinion of it.
    """
    column_path: tuple[str, ...]
    """Column names from the feature's source column back to the classified one."""

    @property
    def marker_name(self) -> str:
        """The classification's readable name, taken from the tail of its URN.

        A URN in the incident title would make the title unreadable and, worse,
        unstable across a re-ingestion that changes a URN's prefix while meaning
        the same thing.
        """
        return self.marker_urn.rsplit(":", 1)[-1]

    @property
    def path_text(self) -> str:
        """Render the derivation the way the incident and the report quote it.

        Consecutive repeats of one column name collapse to a single step, for the
        same reason as :attr:`LeakingFeature.path_text`: a warehouse ingested from
        more than one source has sibling entities for the same physical table, so
        lineage legitimately walks the same column twice under two platforms.
        """
        collapsed = [
            column
            for index, column in enumerate(self.column_path)
            if index == 0 or column != self.column_path[index - 1]
        ]
        return " <- ".join(collapsed)


@dataclass(frozen=True)
class SensitiveSourceFinding(Finding):
    """A model consumes a feature derived from a column classified as restricted.

    One finding per feature, so each exposed column gets its own incident and its
    own dedup key, exactly like leakage.
    """

    model: ModelRef
    exposure: SensitiveFeature

    @property
    def finding_type(self) -> FindingType:
        """A feature derives from a column the organization classified."""
        return FindingType.SENSITIVE_SOURCE

    @property
    def resource_urn(self) -> str:
        """The feature's own source column: the precise column to go and look at."""
        return self.exposure.source_column_urn

    @property
    def incident_type(self) -> str:
        """FIELD is DataHub's column-scoped incident type. There is no COLUMN."""
        return "FIELD"

    @property
    def severity(self) -> Severity:
        """Serious, but never CRITICAL, and the distinction is deliberate.

        CRITICAL in this project means the model's own numbers are wrong and
        cannot be trusted, which is what target leakage does. A feature derived
        from a restricted column is a governance exposure: the model works, and
        what is wrong is what it was allowed to see. Ranking the two the same
        would make CRITICAL stop meaning anything, and a team that cannot sort
        its critical findings triages none of them.
        """
        return Severity.HIGH if self.model.is_live else Severity.MEDIUM

    @property
    def title(self) -> str:
        """A pure function of two column names and the classification's name."""
        return (
            f"Sensitive source: {self.exposure.source_column_name} derives from "
            f"{self.exposure.marker_name} column {self.exposure.sensitive_column_name}"
        )

    @property
    def evidence(self) -> Mapping[str, str]:
        """The column path proving the derivation. Every value read from the graph."""
        return {
            "feature": self.exposure.feature_name,
            "exposed_column": self.exposure.source_column_name,
            "sensitive_column": self.exposure.sensitive_column_name,
            "sensitive_table": self.exposure.sensitive_dataset_name,
            "classification": self.exposure.marker_name,
            "column_path": self.exposure.path_text,
            "model": self.model.name,
            "model_is_live": str(self.model.is_live).lower(),
            "severity": str(self.severity),
        }

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """Exactly the one model that consumes this feature."""
        return (self.model,)


@dataclass(frozen=True)
class DeprecatedInputFinding(Finding):
    """A model trains on an input its own owners have marked deprecated.

    The cheapest finding in the project to compute and one of the more useful to
    receive: DataHub already holds the ``deprecation`` aspect, somebody set it
    deliberately, and nothing today tells the team downstream of that table that
    a model depends on it. Nothing is broken yet, which is the point: this is the
    warning that arrives while there is still time to act on it.
    """

    model: ModelRef
    dataset_urn: str
    dataset_name: str
    note: str = ""
    """The deprecation note its owners left, when they left one."""
    decommission_time_ms: int | None = None
    """When the owners said the table goes away, if they said."""

    @property
    def finding_type(self) -> FindingType:
        """A training input is on its way out."""
        return FindingType.DEPRECATED_INPUT

    @property
    def resource_urn(self) -> str:
        """The deprecated dataset. The incident belongs where the decision was made."""
        return self.dataset_urn

    @property
    def incident_type(self) -> str:
        """OPERATIONAL: nothing is wrong with the data, the asset is being retired."""
        return "OPERATIONAL"

    @property
    def severity(self) -> Severity:
        """Never higher than MEDIUM: this is a deadline, not a defect.

        A live model raises it to MEDIUM because somebody has to plan the
        migration; one that is not serving is LOW, since the deadline lands on a
        model nobody is depending on yet.
        """
        return Severity.MEDIUM if self.model.is_live else Severity.LOW

    @property
    def title(self) -> str:
        """A pure function of the dataset and model names."""
        return f"Deprecated training input {self.dataset_name} for {self.model.name}"

    @property
    def evidence(self) -> Mapping[str, str]:
        """What the owners declared, quoted rather than interpreted."""
        facts = {
            "input_table": self.dataset_name,
            "model": self.model.name,
            "model_is_live": str(self.model.is_live).lower(),
            "severity": str(self.severity),
        }
        if self.note:
            facts["deprecation_note"] = self.note
        if self.decommission_time_ms is not None:
            facts["decommission_time_ms"] = str(self.decommission_time_ms)
        return facts

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """Exactly the one model that trains on this input."""
        return (self.model,)


class ChangeKind(StrEnum):
    """How one column changed between training time and now."""

    ADDED = "added"
    """Present in the current schema, absent when the model was trained."""
    REMOVED = "removed"
    """Present at training time, gone from the current schema. The worst case:
    the model's serving code will read a column the source no longer provides."""
    RETYPED = "retyped"
    """Present in both, but its native type changed. Silent skew: the serving
    pipeline parses a column the model was never trained to expect."""


@dataclass(frozen=True)
class SchemaChange:
    """One column that differs between the training-time and current schema."""

    field_path: str
    kind: ChangeKind
    training_type: str | None
    """The native type at training time. None when the column is newly added."""
    current_type: str | None
    """The native type now. None when the column has been removed."""

    def describe(self) -> str:
        """Render the change the way the incident and the report quote it."""
        if self.kind is ChangeKind.RETYPED:
            return f"{self.field_path}: {self.training_type} -> {self.current_type}"
        if self.kind is ChangeKind.ADDED:
            return f"{self.field_path} (added, {self.current_type})"
        return f"{self.field_path} (removed, was {self.training_type})"


@dataclass(frozen=True)
class SchemaDriftFinding(Finding):
    """A model's input dataset no longer matches the schema it was trained on.

    The schema the model was trained against is captured on the training run at
    training time. Comparing it against the dataset's current schema is exactly
    the training-serving skew check TFX/TFDV perform: freeze a schema at training,
    validate serving data against it. A drifted column feeds the live model values
    it was never trained to parse, and nothing in the serving path signals it.
    """

    model: ModelRef
    training_run_urn: str
    dataset_urn: str
    """The training input dataset whose schema drifted. Where the incident lands."""
    dataset_name: str
    changes: tuple[SchemaChange, ...]
    """Every column that differs, ordered for a stable report."""

    @property
    def finding_type(self) -> FindingType:
        """The training-time input schema and the current one diverged."""
        return FindingType.INPUT_SCHEMA_DRIFT

    @property
    def resource_urn(self) -> str:
        """The drifted input dataset, never the model.

        DataHub refuses an incident on an mlModel, and the schema is a property
        of the dataset anyway.
        """
        return self.dataset_urn

    @property
    def incident_type(self) -> str:
        """DataHub's incident type for a schema that changed."""
        return "DATA_SCHEMA"

    @property
    def severity(self) -> Severity:
        """A live model scoring on a drifted schema is a production skew.

        Lower than leakage: drift corrupts inputs but does not, by itself, mean
        the reported accuracy was a fiction. A model not yet serving is a
        training-time concern.
        """
        return Severity.HIGH if self.model.is_live else Severity.MEDIUM

    @property
    def title(self) -> str:
        """A pure function of the dataset name and the model that trained on it.

        The drifted columns, which can grow between scans, live in the evidence,
        not the dedup key.

        The model belongs in the title because drift is a property of the pair,
        not of the dataset: it is the gap between *this* model's training-time
        snapshot and the dataset's current schema. Two models trained on the
        same input at different times genuinely disagree about whether it
        drifted. Naming only the dataset collapsed both into one incident, so
        the second model's drift was silently deduplicated away and either
        model's recovery resolved the other's live incident (D-070).
        """
        return f"Training-serving schema drift in {self.dataset_name} for {self.model.name}"

    @property
    def evidence(self) -> Mapping[str, str]:
        """The drifted columns and their counts. Every value read from the graph."""
        return {
            "dataset": self.dataset_name,
            "model": self.model.name,
            "model_is_live": str(self.model.is_live).lower(),
            "training_run": self.training_run_urn,
            "added": str(sum(1 for c in self.changes if c.kind is ChangeKind.ADDED)),
            "removed": str(sum(1 for c in self.changes if c.kind is ChangeKind.REMOVED)),
            "retyped": str(sum(1 for c in self.changes if c.kind is ChangeKind.RETYPED)),
            "drifted_fields": ", ".join(c.describe() for c in self.changes),
            "severity": str(self.severity),
        }

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """Exactly the one model this training run produced."""
        return (self.model,)


class TrustBand(StrEnum):
    """A model's reliability band, derived from its trust score."""

    HEALTHY = "healthy"
    WATCH = "watch"
    AT_RISK = "at-risk"


@dataclass(frozen=True)
class TrustScore:
    """A model's aggregate reliability, 0 (worst) to 100 (best), and why.

    Rolls up every risk a scan found about one model into a single number a
    human can act on, in the spirit of a model card (Mitchell et al. 2019). The
    number is deterministic: it is a fixed weighted sum of the findings, and the
    LLM never touches it (modelguard/CLAUDE.md rule 5).
    """

    value: int
    band: TrustBand
    deductions: Mapping[str, float]
    """What each risk subtracted, keyed by name, for the audit trail and report."""
