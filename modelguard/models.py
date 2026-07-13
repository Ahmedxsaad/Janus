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

    Only the Phase 1 detector's type exists. Leakage, schema drift, and the trust
    score add their own values when those detectors land; declaring them now
    would be a placeholder for code that does not exist.
    """

    UPSTREAM_FRESHNESS = "upstream-freshness"


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
class ModelAtRisk:
    """One model that consumes, transitively, a dataset that has gone stale."""

    urn: str
    name: str
    hops: int
    """Lineage hops from the failing table to this model."""
    deployments: tuple[str, ...]
    live_deployments: tuple[str, ...]
    """The subset of deployments whose status is IN_SERVICE."""
    features_at_risk: tuple[str, ...]
    """The model's own MLFeatures that sit downstream of the failing table."""
    has_owner: bool

    @property
    def is_live(self) -> bool:
        """Whether this model is currently serving predictions."""
        return bool(self.live_deployments)

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


@dataclass(frozen=True)
class Finding:
    """A detected problem, ready to be written back to the graph.

    ``resource_urn`` is where the incident attaches. It is never an mlModel:
    DataHub's incident model rejects that entity type, so an upstream data
    failure attaches to the dataset that failed, and model-level risk is carried
    by structured properties and a tag on the model instead.
    """

    finding_type: FindingType
    resource_urn: str
    incident_type: str
    """A DataHub incident type, validated by the write-back layer."""
    severity: Severity
    blast_radius: BlastRadius

    @property
    def title(self) -> str:
        """Return the incident title. Deterministic: it is part of the dedup key.

        Deliberately carries no measurement. The lag drifts by seconds between
        scans, so a title quoting it would key a fresh incident every run.
        """
        return f"Stale upstream data in {self.blast_radius.failing_table_name}"

    @property
    def evidence(self) -> Mapping[str, str]:
        """Return the measured facts behind the finding.

        This is the only input the narrator is allowed to speak from, and it is
        what the assertion result records. Every value is computed from the
        graph, never generated.
        """
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
