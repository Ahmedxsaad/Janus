"""The typed findings that travel between Janus's layers.

Detectors return these; the narrator reads them to draft prose; the write-back
layer turns them into incidents, tags, properties, assertions, and reports.
Nothing passes dicts across a layer boundary (janus/CLAUDE.md rule 1).

Everything here is a frozen dataclass computed from graph facts. No value in this
module is ever produced by an LLM: the narrative text an LLM writes is carried
separately, in :class:`~janus.agent.narrate.Narrative`, and is never allowed
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
    TABLE_LEVEL_RISK = "table-level-risk"
    """The degraded mode: what can be said about a model with no column link.

    Its own type, and never merged with the four above, because it answers a
    weaker question with weaker evidence and a report that blurred the two would
    be claiming precision it did not have."""

    PROXY_CANDIDATE = "proxy-candidate"
    """A feature that shares an ancestor with a protected attribute (T-11).

    Its own type, and deliberately not folded into SENSITIVE_SOURCE, because the
    two make claims of different strength about different things. Sensitive
    source is a proved derivation: this feature *descends from* a column the
    organization restricted, and the column path is the proof. This is a
    structural coincidence that a human should look at: two columns share an
    ancestor, and whether that makes one a stand-in for the other is a question
    about the data, which no walk of a metadata graph can answer."""


class RemedyKind(StrEnum):
    """The kind of change one remedy asks for.

    Stable identifiers rather than prose, because something other than a human
    reads them: the benchmark keys its appliers on this value so it can perform a
    remedy against a real graph and check that the finding actually clears
    (T-03). A remedy nobody can apply mechanically is still a remedy; it simply
    has no applier registered against its kind.
    """

    CUT_LINEAGE = "cut-lineage"
    """Remove the derivation edge, so the feature stops descending from the column."""
    DROP_FEATURE = "drop-feature"
    """Stop the model consuming the feature at all."""
    CORRECT_MARK = "correct-mark"
    """Withdraw the declaration on the ancestor column, when the declaration is
    what is wrong rather than the derivation."""
    REFRESH_SOURCE = "refresh-source"
    """Make the table refresh inside its SLA again."""
    STOP_CONSUMING = "stop-consuming"
    """Take the models off the failing table."""
    RESTORE_SCHEMA = "restore-schema"
    """Put the drifted columns back the way the model was trained to read them."""
    REVIEW = "review"
    """Answer a question the graph raised but cannot settle (T-11).

    The only remedy kind that asks for a judgement rather than a change, and it
    has no applier in the benchmark for exactly that reason: a proxy candidate
    is cleared by a human deciding it is not one, and a tool that could
    mechanically perform that decision would be making it."""
    RETRAIN = "retrain"
    """Retrain against what the data says now, and record the new snapshot."""
    MIGRATE_INPUT = "migrate-input"
    """Move the model onto whatever replaces the input it trains on."""
    WITHDRAW_DEPRECATION = "withdraw-deprecation"
    """Withdraw the deprecation, when it was set in error."""
    DECLARE_LINK = "declare-link"
    """Declare which columns the model trains on, so the question can be answered
    at column level instead of at table level."""


@dataclass(frozen=True)
class Remedy:
    """One change that, applied on its own, clears the finding.

    ``targets`` names everything the change has to touch, all of it: a remedy
    that cuts one of two derivation paths is not a remedy, it is half of one, and
    a reader who applies half of it gets a finding that stays open with no
    explanation. Where more than one target is listed, all of them change
    together or the finding stands.
    """

    kind: RemedyKind
    summary: str
    """An imperative sentence, complete on its own and readable out of context."""
    targets: tuple[str, ...]
    """The URNs, columns, or edges this change touches. Every one of them."""


@dataclass(frozen=True)
class Counterfactual:
    """What would have to be different for a finding not to exist.

    A finding says what is wrong. This says what to change, and it is the half a
    reader actually acts on. Each remedy is sufficient alone, so the set is a
    genuine choice between alternatives rather than a checklist: a team drops the
    feature, or cuts the derivation, or corrects the declaration, and any one of
    those makes the next scan silent.

    Derived from the same graph facts as the finding it belongs to. Nothing here
    is generated, ranked, or worded by an LLM (janus/CLAUDE.md rule 5): a
    suggested fix that an LLM invented is a suggested fix nobody can verify, and
    the benchmark verifies these by applying them.
    """

    remedies: tuple[Remedy, ...]
    paths: int = 1
    """How many distinct derivations produced this finding.

    One for every finding that is not about a lineage walk. Above one, cutting a
    single path leaves the finding standing, which is the single most likely way
    for somebody to think they have fixed this and be wrong, so it is said out
    loud rather than left to be inferred from a remedy's target list."""

    @property
    def multi_path(self) -> bool:
        """Whether more than one derivation has to be cut."""
        return self.paths > 1

    def lines(self) -> tuple[str, ...]:
        """Render the counterfactual as plain text lines.

        Returns:
            Lines with no markup, safe in a terminal, a markdown code block, and
            a JSON string alike, like :meth:`TrustScore.waterfall`.
        """
        lines = ["Any one of these clears this finding:"]
        lines += [f"  - {remedy.summary}" for remedy in self.remedies]
        if self.multi_path:
            lines += [
                "",
                f"This finding rests on {self.paths} distinct derivation paths. Cutting one "
                "of them leaves it standing: the cut listed above names the first edge of "
                "every path, and all of them have to go.",
            ]
        return tuple(lines)


def _collapse_repeats(column_path: tuple[str, ...]) -> list[str]:
    """Drop consecutive repeats of one column name from a derivation chain.

    A real warehouse ingested from more than one source has sibling entities for
    the same physical table (dbt and the warehouse itself both describe
    ``customer_features``), so lineage legitimately walks the same column twice
    under two platforms and the chain reads "x <- x <- y". The repeat carries no
    information a reader of the derivation can use.
    """
    return [
        column
        for index, column in enumerate(column_path)
        if index == 0 or column != column_path[index - 1]
    ]


def _cuttable_edges(paths: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    """Return the first edge of every derivation path, or nothing.

    Empty when any path is a single column, which is what a walk returns when the
    starting column is itself the marked one: there is no derivation to cut, so
    offering the cut would be offering a remedy that does not work. Empty is how
    a caller is told not to offer it.

    Deduplicated with insertion order preserved, because two paths can genuinely
    share their first edge and diverge later, and listing that edge twice would
    read as two separate things to do.
    """
    if any(len(path) < 2 for path in paths):
        return ()
    return tuple(dict.fromkeys(f"{path[0]} <- {path[1]}" for path in paths))


@dataclass(frozen=True)
class FreshnessSignal:
    """How stale a dataset is, measured against its freshness SLA.

    Read from the dataset's ``operation`` aspect, which is DataHub's own record
    of when the dataset last changed. This is metadata the graph already holds:
    Janus does not query the warehouse.
    """

    dataset_urn: str
    last_updated_ms: int
    """When the dataset last changed, per its operation aspect."""
    observed_at_ms: int
    """When Janus read the aspect. Lag is measured against this instant."""
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
        not the data-to-model failure Janus exists to catch, so it lands at
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

    @property
    @abstractmethod
    def counterfactual(self) -> Counterfactual:
        """What would have to change for this finding not to exist.

        Abstract, and not defaulted to an empty set, on purpose: a detector that
        can prove something is wrong and cannot say what would make it right is
        half a detector, and a silent default would let one ship.
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

    @property
    def counterfactual(self) -> Counterfactual:
        """Either the table refreshes inside its SLA, or the models stop reading it.

        The second is listed because it is sometimes the right answer and nobody
        writes it down: a model consuming a table whose owners have stopped
        refreshing it on purpose is a dependency to remove, not an outage to
        wait out. It is omitted when the radius reached no model, where there is
        nothing to disconnect and the only remedy is the refresh.
        """
        radius = self.blast_radius
        remedies = [
            Remedy(
                kind=RemedyKind.REFRESH_SOURCE,
                summary=(
                    f"Refresh {radius.failing_table_name} so it changes at least once "
                    f"every {radius.signal.sla_hours:.1f} hours, the SLA it is measured "
                    f"against."
                ),
                targets=(radius.failing_table_urn,),
            )
        ]
        if radius.models:
            names = ", ".join(model.name for model in radius.models)
            remedies.append(
                Remedy(
                    kind=RemedyKind.STOP_CONSUMING,
                    summary=(
                        f"Take every model off this table ({names}), so its staleness "
                        f"reaches nothing that serves predictions."
                    ),
                    targets=tuple(model.urn for model in radius.models),
                )
            )
        return Counterfactual(remedies=tuple(remedies))


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
    other_paths: tuple[tuple[str, ...], ...] = ()
    """Every *other* chain from this column to a declared label the walk found.

    The shortest chain is the quoted proof and stays in ``column_path``; these
    are the ones the walk collected and used to discard. They are kept because a
    remedy has to account for them: cutting the quoted path and leaving one of
    these in place fixes nothing, and a reader who is told only about the proof
    has no way to know that."""

    @property
    def path_text(self) -> str:
        """Render the leak path the way the incident and the report quote it."""
        return " <- ".join(_collapse_repeats(self.column_path))

    @property
    def all_paths(self) -> tuple[tuple[str, ...], ...]:
        """Every chain from this column to a declared label, proof first."""
        return (self.column_path, *self.other_paths)

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

    @property
    def counterfactual(self) -> Counterfactual:
        """Cut the derivation, drop the feature, or withdraw the declaration.

        Three genuinely different fixes, in the order a team should consider
        them. The last one is not a way to silence the tool: if that column is
        not the label, the leakage finding was never true, and the graph is what
        is wrong. Saying so is what makes the finding auditable in both
        directions rather than only in the direction that flatters the detector.
        """
        leak = self.leak
        edges = _cuttable_edges(leak.all_paths)

        remedies: list[Remedy] = []
        if edges:
            remedies.append(
                Remedy(
                    kind=RemedyKind.CUT_LINEAGE,
                    summary=(
                        f"Rebuild {leak.source_column_name} from data known before the "
                        f"outcome is, so it stops deriving from the label: cut "
                        f"{', '.join(edges)}."
                    ),
                    targets=edges,
                )
            )
        remedies += [
            Remedy(
                kind=RemedyKind.DROP_FEATURE,
                summary=(
                    f"Drop the feature {leak.feature_name} from {self.model.name} and "
                    f"retrain, so nothing the model reads carries the answer."
                ),
                targets=(leak.feature_urn,),
            ),
            Remedy(
                kind=RemedyKind.CORRECT_MARK,
                summary=(
                    f"If {leak.label_dataset_name}.{leak.label_column_name} is not this "
                    f"model's label, remove the label declaration from it: the finding "
                    f"rests on that declaration and is wrong without it."
                ),
                targets=(leak.label_column_urn,),
            ),
        ]
        return Counterfactual(remedies=tuple(remedies), paths=len(leak.all_paths))


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
    classification rather than Janus's opinion of it.
    """
    column_path: tuple[str, ...]
    """Column names from the feature's source column back to the classified one."""
    other_paths: tuple[tuple[str, ...], ...] = ()
    """Every *other* chain to a classified column the walk found, for the same
    reason :attr:`LeakingFeature.other_paths` keeps them: a remedy that cuts one
    derivation and leaves another standing is not a remedy."""

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
        """Render the derivation the way the incident and the report quote it."""
        return " <- ".join(_collapse_repeats(self.column_path))

    @property
    def all_paths(self) -> tuple[tuple[str, ...], ...]:
        """Every chain from this column to a classified column, proof first."""
        return (self.column_path, *self.other_paths)


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

    @property
    def counterfactual(self) -> Counterfactual:
        """Cut the derivation, drop the feature, or correct the classification.

        The classification remedy is worded as a correction and never as a
        dismissal. A tool that suggested removing a PII tag to make an incident
        go away would be teaching exactly the wrong reflex, so the sentence says
        what it means: if the column was classified in error, the classification
        is the thing to fix, and that is a decision for whoever owns it.
        """
        exposure = self.exposure
        edges = _cuttable_edges(exposure.all_paths)

        remedies: list[Remedy] = []
        if edges:
            remedies.append(
                Remedy(
                    kind=RemedyKind.CUT_LINEAGE,
                    summary=(
                        f"Rebuild {exposure.source_column_name} from a column carrying no "
                        f"such constraint, so it stops deriving from "
                        f"{exposure.marker_name} data: cut {', '.join(edges)}."
                    ),
                    targets=edges,
                )
            )
        remedies += [
            Remedy(
                kind=RemedyKind.DROP_FEATURE,
                summary=(
                    f"Drop the feature {exposure.feature_name} from {self.model.name} and "
                    f"retrain, so the model no longer learns from "
                    f"{exposure.sensitive_column_name}."
                ),
                targets=(exposure.feature_urn,),
            ),
            Remedy(
                kind=RemedyKind.CORRECT_MARK,
                summary=(
                    f"If {exposure.sensitive_dataset_name}."
                    f"{exposure.sensitive_column_name} is not {exposure.marker_name}, have "
                    f"whoever owns that classification correct it: the finding rests on "
                    f"the classification, not on Janus's opinion of the column."
                ),
                targets=(exposure.sensitive_column_urn,),
            ),
        ]
        return Counterfactual(remedies=tuple(remedies), paths=len(exposure.all_paths))


@dataclass(frozen=True)
class ProxyCandidate:
    """A feature sharing an ancestor with a column classified as protected.

    Not a derivation and not an accusation. The structure is a fork rather than
    a chain: some column upstream feeds both this feature's source column and a
    column the organization marked as a protected attribute, and neither
    descends from the other. That is the shape a proxy variable takes when it
    arises by accident, which Barocas and Selbst (2016) identify as the dominant
    mechanism of unintentional discrimination: nobody put race in the model,
    they put postcode in, and postcode carries it.

    What this object asserts is exactly that shape and nothing more. Whether the
    feature actually stands in for the attribute is a question about the *data*,
    answerable only with the data and the predictions, which this tool has by
    design never seen. The finding's job is to put the question in front of a
    human who can answer it, with the shared ancestor named so they can start.
    """

    feature_urn: str
    feature_name: str
    source_column_urn: str
    """The feature's own source column. Where the finding lands."""
    source_column_name: str
    protected_column_urn: str
    protected_column_name: str
    protected_dataset_name: str
    marker_urn: str
    """The term or tag classifying the protected column, quoted so the finding
    names the organization's own declaration rather than Janus's guess."""
    ancestor_urn: str
    """The column both descend from. The thing a human goes and looks at."""
    ancestor_name: str
    ancestor_dataset_name: str
    feature_hops: int
    """Hops from the shared ancestor down to the feature's source column."""
    protected_hops: int
    """Hops from the shared ancestor down to the protected column."""

    @property
    def marker_name(self) -> str:
        """The classification's readable name, from the tail of its URN."""
        return self.marker_urn.rsplit(":", 1)[-1]

    @property
    def shared_text(self) -> str:
        """Render the fork the way the incident quotes it."""
        return f"{self.source_column_name} <- {self.ancestor_name} -> {self.protected_column_name}"


@dataclass(frozen=True)
class ProxyCandidateFinding(Finding):
    """A feature that may stand in for a protected attribute. For human review.

    Every sentence this finding produces is written to be read by somebody who
    will decide, not to decide for them. The tool has no access to the data or
    the predictions and therefore cannot know whether the proxying is real; what
    it can do, and what nothing else does, is find the candidates before the
    model is trained, from lineage alone.
    """

    model: ModelRef
    candidate: ProxyCandidate

    @property
    def finding_type(self) -> FindingType:
        """A structural proxy candidate, never an assertion of discrimination."""
        return FindingType.PROXY_CANDIDATE

    @property
    def resource_urn(self) -> str:
        """The feature's own source column: the precise column to go and look at."""
        return self.candidate.source_column_urn

    @property
    def incident_type(self) -> str:
        """FIELD is DataHub's column-scoped incident type. There is no COLUMN."""
        return "FIELD"

    @property
    def severity(self) -> Severity:
        """MEDIUM, always, whether or not the model is live.

        Every other detector here escalates for a live model, because a live
        model means the harm is happening now. This one does not escalate at
        all, and the reason is the same one that caps it: this is a prompt to
        look, not a defect (the plan's wording, and P6's precedent). A finding
        that cannot be confirmed from the graph must not be able to outrank one
        that can, or a team triaging by severity will spend its attention on
        the maybes and miss the proofs.
        """
        return Severity.MEDIUM

    @property
    def title(self) -> str:
        """A pure function of three column names. Worded as a question to answer."""
        return (
            f"Proxy candidate: {self.candidate.source_column_name} shares "
            f"{self.candidate.ancestor_name} with {self.candidate.marker_name} column "
            f"{self.candidate.protected_column_name}"
        )

    @property
    def evidence(self) -> Mapping[str, str]:
        """The fork, its distances, and the classification. All read from the graph."""
        candidate = self.candidate
        return {
            "feature": candidate.feature_name,
            "feature_column": candidate.source_column_name,
            "protected_column": candidate.protected_column_name,
            "protected_table": candidate.protected_dataset_name,
            "classification": candidate.marker_name,
            "shared_ancestor": candidate.ancestor_name,
            "shared_ancestor_table": candidate.ancestor_dataset_name,
            "hops_to_feature": str(candidate.feature_hops),
            "hops_to_protected": str(candidate.protected_hops),
            "shared_path": candidate.shared_text,
            "model": self.model.name,
            "model_is_live": str(self.model.is_live).lower(),
            "severity": str(self.severity),
            "finding_is": "a candidate for human review, not a determination of proxying",
        }

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """Exactly the one model that consumes this feature."""
        return (self.model,)

    @property
    def counterfactual(self) -> Counterfactual:
        """What would settle the question, rather than what would silence it.

        The first remedy is deliberately not a change to the graph at all. Every
        other finding in this project can be cleared by fixing the thing it
        found; this one is a question, and the honest way to clear a question is
        to answer it. Offering "drop the feature" first would push a team to
        delete a feature they may well be entitled to use, on a tool's
        suggestion, which is the failure mode 09 section 5.1 warns about.
        """
        candidate = self.candidate
        return Counterfactual(
            remedies=(
                Remedy(
                    kind=RemedyKind.REVIEW,
                    summary=(
                        f"Have whoever owns {candidate.feature_name} check whether it carries "
                        f"information about {candidate.protected_column_name}, using the data "
                        f"itself: both are computed from {candidate.ancestor_name}, which is "
                        f"the only thing this finding establishes. If it does not, record "
                        f"that and this stops being a question."
                    ),
                    targets=(candidate.source_column_urn,),
                ),
                Remedy(
                    kind=RemedyKind.CUT_LINEAGE,
                    summary=(
                        f"Rebuild {candidate.source_column_name} from a column that does not "
                        f"also feed {candidate.protected_column_name}, so the two stop sharing "
                        f"{candidate.ancestor_name}."
                    ),
                    targets=(candidate.ancestor_urn,),
                ),
                Remedy(
                    kind=RemedyKind.DROP_FEATURE,
                    summary=(
                        f"Stop the model consuming {candidate.feature_name}. Sufficient, and "
                        f"the bluntest of the three: a feature can share an ancestor with a "
                        f"protected attribute and carry nothing about it."
                    ),
                    targets=(candidate.feature_urn,),
                ),
            )
        )


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

    @property
    def counterfactual(self) -> Counterfactual:
        """Move onto the successor, or withdraw the deprecation.

        The owners' own note is quoted into the migration remedy rather than
        paraphrased, because where a successor exists that note is the only place
        in the graph that names it. When they left no note there is nothing to
        quote, and the remedy says to go and ask them, which is the honest
        instruction: inventing a replacement table name would be inventing a fact.
        """
        successor = (
            f' Its owners\' note reads: "{self.note}"'
            if self.note
            else " Its owners' note names no replacement, so ask them what does."
        )
        return Counterfactual(
            remedies=(
                Remedy(
                    kind=RemedyKind.MIGRATE_INPUT,
                    summary=(
                        f"Retrain {self.model.name} on whatever replaces "
                        f"{self.dataset_name} and record the new input on the run."
                        f"{successor}"
                    ),
                    targets=(self.model.urn, self.dataset_urn),
                ),
                Remedy(
                    kind=RemedyKind.WITHDRAW_DEPRECATION,
                    summary=(
                        f"If {self.dataset_name} was deprecated in error, have its owners "
                        f"withdraw the deprecation in DataHub."
                    ),
                    targets=(self.dataset_urn,),
                ),
            )
        )


class TableRisk(StrEnum):
    """What a table-level check can say about a training input.

    Three facts DataHub already holds about a *table*, each one readable without
    any column-level link: it stopped refreshing, its owners retired it, or it
    holds a column somebody classified as restricted.
    """

    STALE = "stale"
    DEPRECATED = "deprecated"
    CLASSIFIED = "classified"


#: What the degraded mode can and cannot conclude, per risk. Quoted verbatim into
#: the finding's own description, because a table-level finding read as a
#: column-level one is a false alarm the reader was never warned about.
_TABLE_RISK_LIMITS: dict[TableRisk, str] = {
    TableRisk.STALE: (
        "the table this model trains on is past its freshness SLA. Which of the "
        "model's features carry the stale values is not knowable without a "
        "column-level link"
    ),
    TableRisk.DEPRECATED: (
        "the table this model trains on is marked deprecated by its owners. This "
        "one is exact at table level: the deadline is on the table, not on a column"
    ),
    TableRisk.CLASSIFIED: (
        "the table this model trains on holds a column the organization "
        "classified as restricted. Whether the model actually consumes that "
        "column is not knowable without a column-level link"
    ),
}


@dataclass(frozen=True)
class TableLevelRiskFinding(Finding):
    """What is knowable about a model nobody has linked: the degraded mode.

    Janus's whole claim is column-level: the evidence is a path through the
    column graph, and the path names the feature. A model with no declared
    features has no such path, and today a scan of it correctly reports that it
    checked nothing (:mod:`janus.detect.coverage`). Honest, and worth
    nothing on the first day somebody points this at their own catalog.

    So this is the weaker answer, offered deliberately and labelled as weak: the
    table a model trains on is stale, deprecated, or holds a classified column.
    Every instance says which mode produced it and what that mode cannot see, and
    its first remedy is always to declare the link that would replace it with the
    real answer.

    Severity never reaches the column-level detectors' range. A leaking feature is
    CRITICAL; a table that may or may not carry a problem into this model is a
    prompt to look, and :class:`~janus.detect.trust_score.TrustInputs`
    deliberately does not score it at all.
    """

    model: ModelRef
    risk: TableRisk
    dataset_urn: str
    dataset_name: str
    measurement: Mapping[str, str]
    """What was measured about the table, per risk: the lag and its SLA, the
    deprecation note, the classified columns. Graph facts, like every other
    finding's evidence."""
    precision: float
    """The measured precision of this mode, from the benchmark's table-level
    baseline (``janus.config.TABLE_LEVEL_PRECISION``). Carried on the finding
    rather than looked up by each renderer, so every surface quotes one number."""

    @property
    def finding_type(self) -> FindingType:
        """The degraded mode, never one of the four column-level types."""
        return FindingType.TABLE_LEVEL_RISK

    @property
    def resource_urn(self) -> str:
        """The training table. The only asset this mode can name precisely."""
        return self.dataset_urn

    @property
    def incident_type(self) -> str:
        """The type of the fact, not of the mode: a stale table is a FRESHNESS one."""
        return "FRESHNESS" if self.risk is TableRisk.STALE else "OPERATIONAL"

    @property
    def severity(self) -> Severity:
        """Capped at MEDIUM, always below the column-level finding it stands in for.

        A live model earns MEDIUM because somebody has to go and look; one that is
        not serving is LOW. Neither reaches HIGH, and that ceiling is the
        substance of the mode rather than a knob: this finding cannot say the
        model is affected, only that it might be, and a report where a maybe
        outranks a proof is one nobody triages twice.
        """
        return Severity.MEDIUM if self.model.is_live else Severity.LOW

    @property
    def title(self) -> str:
        """Names the mode, so no reader mistakes it for the column-level finding."""
        return (
            f"Table-level {self.risk.value} input {self.dataset_name} for {self.model.name} "
            "(no column link)"
        )

    @property
    def limitation(self) -> str:
        """One sentence on what this mode saw and what it could not see."""
        return _TABLE_RISK_LIMITS[self.risk]

    @property
    def mode_note(self) -> str:
        """The disclosure every surface prints beside this finding.

        States the mode, what it cannot see, and what the mode measures when it
        is pushed one question further, in that order. The number is the
        benchmark's, measured on the table-level baseline scored in
        ``benchmarks/RESULTS.md``, and it is quoted with the question it answers
        attached: it is the precision of table-level reasoning asked *which
        feature* carries a problem, which is exactly the question this finding
        declines to answer. A tool telling somebody how much to trust it should
        quote a measurement, and quote it for the right claim.
        """
        return (
            f"Checked at table level only ({self.model.name} declares no features): "
            f"{self.limitation}. Asked which feature carries it, table-level "
            f"reasoning scores a measured precision of {self.precision:.2f} "
            "(benchmarks/RESULTS.md, table-level baseline), which is why this "
            "finding names the table and not a feature. Run `janus link` to "
            "get the column-level answer instead."
        )

    @property
    def evidence(self) -> Mapping[str, str]:
        """The measurement, plus the mode itself, which is a fact about the answer."""
        return {
            "mode": "table-level",
            "risk": str(self.risk),
            "input_table": self.dataset_name,
            "model": self.model.name,
            "model_is_live": str(self.model.is_live).lower(),
            "mode_precision": f"{self.precision:.2f}",
            "severity": str(self.severity),
            # The disclosure travels with the facts rather than beside them, so
            # every surface that renders evidence (the JSON, the incident body,
            # the narrator's fact block) carries it without each one having to
            # know that this finding type is the weak one.
            "mode_note": self.mode_note,
            **self.measurement,
        }

    @property
    def models_at_risk(self) -> tuple[ModelRef, ...]:
        """The one model that trains on this table."""
        return (self.model,)

    @property
    def counterfactual(self) -> Counterfactual:
        """Declaring the link comes first, and it is a different kind of remedy.

        It does not fix the table; it replaces this finding with the column-level
        one, which either names the feature that carries the problem or clears the
        model. That is what somebody reading a maybe actually wants, so it is
        listed first, and the table's own remedy follows it.
        """
        remedies = [
            Remedy(
                kind=RemedyKind.DECLARE_LINK,
                summary=(
                    f"Declare what {self.model.name} trains on (`janus link "
                    f"--model {self.model.name} --features {self.dataset_name} "
                    "--label-column <column>`, or `--from feast|dbt` if your stack "
                    "already declares it), so this table-level guess is replaced by "
                    "a column-level answer."
                ),
                targets=(self.model.urn,),
            )
        ]
        if self.risk is TableRisk.STALE:
            remedies.append(
                Remedy(
                    kind=RemedyKind.REFRESH_SOURCE,
                    summary=f"Refresh {self.dataset_name} so it is inside its SLA again.",
                    targets=(self.dataset_urn,),
                )
            )
        elif self.risk is TableRisk.DEPRECATED:
            remedies.append(
                Remedy(
                    kind=RemedyKind.MIGRATE_INPUT,
                    summary=(
                        f"Retrain {self.model.name} on whatever replaces "
                        f"{self.dataset_name}, or have its owners withdraw the "
                        "deprecation if it was set in error."
                    ),
                    targets=(self.model.urn, self.dataset_urn),
                )
            )
        else:
            remedies.append(
                Remedy(
                    kind=RemedyKind.STOP_CONSUMING,
                    summary=(
                        f"Take {self.model.name} off {self.dataset_name}, if it must "
                        "not be trained on data derived from a restricted column."
                    ),
                    targets=(self.model.urn,),
                )
            )
        return Counterfactual(remedies=tuple(remedies))


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

    @property
    def counterfactual(self) -> Counterfactual:
        """Put the schema back, or retrain against the one the data now has.

        Both are real fixes and they mean opposite things, which is why both are
        offered rather than one being recommended: restoring says the change was
        a mistake, retraining says it was intended and the model is what is out
        of date. Only the team owning the table knows which, and a tool that
        picked for them would be guessing at an intent that is not in the graph.
        """
        changed = ", ".join(change.describe() for change in self.changes)
        return Counterfactual(
            remedies=(
                Remedy(
                    kind=RemedyKind.RESTORE_SCHEMA,
                    summary=(
                        f"Restore {self.dataset_name} to the schema {self.model.name} was "
                        f"trained on, reversing: {changed}."
                    ),
                    targets=(self.dataset_urn,),
                ),
                Remedy(
                    kind=RemedyKind.RETRAIN,
                    summary=(
                        f"Retrain {self.model.name} against {self.dataset_name} as it is "
                        f"now, so the training-time snapshot on the run matches the "
                        f"schema the serving path reads."
                    ),
                    targets=(self.model.urn, self.training_run_urn),
                ),
            )
        )


class TrustBand(StrEnum):
    """A model's reliability band, derived from its trust score."""

    HEALTHY = "healthy"
    WATCH = "watch"
    AT_RISK = "at-risk"


@dataclass(frozen=True)
class Deduction:
    """One risk's cost to a model's trust score, and what caused it.

    The cause is the actionable half. A reader told a model lost 20 points to
    "leakage" still has to go and find which leak; a reader told it lost them to
    "applicant_income derives from the label" is already looking at the fix. The
    cause is drawn from the finding that triggered the deduction, so it names a
    measured fact and never an LLM's sentence (janus/CLAUDE.md rule 5).
    """

    name: str
    """The stable deduction name, one of the ``DEDUCTION_*`` constants."""
    points: float
    """What it subtracted from 100."""
    cause: str
    """The finding, or the state of the model, that triggered it."""


@dataclass(frozen=True)
class TrustScore:
    """A model's aggregate reliability, 0 (worst) to 100 (best), and why.

    Rolls up every risk a scan found about one model into a single number a
    human can act on, in the spirit of a model card (Mitchell et al. 2019). The
    number is deterministic: it is a fixed weighted sum of the findings, and the
    LLM never touches it (janus/CLAUDE.md rule 5).

    The integer is the least informative part of this object and it is
    deliberately not the part a renderer leads with (F7). The weights behind it
    are a stated preference ordering, not a calibrated model, so 42 is only
    meaningful next to another 42 computed under the same
    :data:`~janus.config.SCORING_VERSION`. The deductions are what a reader
    can act on, which is why :meth:`waterfall` puts them first.
    """

    value: int
    band: TrustBand
    deductions: tuple[Deduction, ...]
    """What each risk subtracted and why, worst first, for the report and the
    audit trail."""

    @property
    def names(self) -> tuple[str, ...]:
        """The deduction names alone, sorted, for a compact one-line summary."""
        return tuple(sorted(deduction.name for deduction in self.deductions))

    def waterfall(self) -> tuple[str, ...]:
        """Render the score contrastively: 100, each deduction, then the total.

        The shape F7 prescribes. A reader sees what went wrong and what it cost
        before they see the number, so the number reads as a consequence rather
        than as a measurement in units nobody defined.

        Returns:
            Aligned plain-text lines, no markup, safe in a terminal, a markdown
            code block, and a JSON string alike.
        """
        rows = [("100", "starting")]
        rows += [
            (f"-{deduction.points:g}", f"{deduction.name}: {deduction.cause}")
            for deduction in self.deductions
        ]
        rows.append((str(self.value), str(self.band)))

        width = max(len(amount) for amount, _ in rows)
        lines = [f"{amount:>{width}}  {label}" for amount, label in rows[:-1]]
        lines.append("-" * width)
        amount, label = rows[-1]
        lines.append(f"{amount:>{width}}  {label}")
        return tuple(lines)
