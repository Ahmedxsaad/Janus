"""How long Janus's findings stay open, read back out of the graph.

Janus raises incidents and resolves them when the problem goes away
. It has never measured the gap between the two, which is the one
number that says whether the tool changed anything: a leak caught in eleven
minutes and a leak caught in eleven days are the same detection and completely
different outcomes. Mean time to resolution per finding type closes the SRE loop
the SRE framing opened, and it costs nothing new to
collect, because every fact it needs is already written.

Where the numbers come from
---------------------------
Nowhere new. ``incidentInfo.created.time`` is when Janus raised the
incident and ``incidentInfo.status.lastUpdated.time`` is when it moved to
RESOLVED, both stamped by GMS. Nothing here writes, and nothing here is
estimated: an incident whose two stamps disagree with the order of events would
produce a negative duration, so it is reported as unusable rather than clamped
to zero (a floor of zero would silently flatter the mean).

Why incidents are reached from their resources
----------------------------------------------
There is no incident search. ``scrollAcrossEntities(types: [INCIDENT])`` fails
with a GraphQL non-null violation on a live GMS 1.5.0.6 `[verified]`, through
every route the SDK offers, which is the same failure ``writeback/incidents.py``
already records for searching incidents by their ``entities`` field. The
relationship index works, so the traversal runs the way dedup already does:
inbound over ``IncidentOn`` from each resource a scan of a model could have
raised an incident on.

That candidate set is assembled from the same three public reads reconciliation
uses, so it holds an incident whose finding has since been fixed. That is the
whole point: an incident nobody can reach any more is one whose resolution time
would never be counted, and it is the one that proves the tool works.

Whose incidents get counted
---------------------------
Only Janus's. Every incident this project raises carries the run footer
``raise_incident`` stamps into its description, so an incident somebody else's
tool or a human opened on the same column is excluded by a fact rather than by a
guess at its title. The run id comes back with it, which makes each duration
traceable to the scan that started it.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from datahub.metadata.schema_classes import (
    IncidentInfoClass,
    IncidentStateClass,
    MLModelPropertiesClass,
)

from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.detect.blast_radius import upstream_datasets
from janus.detect.governance import model_input_datasets
from janus.detect.leakage import feature_source_column
from janus.detect.schema_drift import schema_drift_candidate_resources
from janus.models import FindingType
from janus.writeback.incidents import attached_incident_urns

#: The title prefix each finding type writes, so an incident already in the graph
#: can be classified back to the detector that raised it.
#:
#: Derived rather than authoritative: ``Finding.title`` in models.py is the source
#: of truth, and a test asserts every concrete finding's title starts with the
#: prefix registered here. Keyed by :class:`~janus.models.FindingType` for
#: the reason the AI RMF crosswalk is: a detector added without a prefix
#: fails a test, rather than quietly contributing nothing to an MTTR figure.
#:
#: Prefix and not the whole title, because a title names the columns and tables
#: involved and those differ per finding; the prefix is the part that names the
#: detector.
TITLE_PREFIXES: dict[FindingType, str] = {
    FindingType.UPSTREAM_FRESHNESS: "Stale upstream data in ",
    FindingType.TARGET_LEAKAGE: "Target leakage: ",
    FindingType.INPUT_SCHEMA_DRIFT: "Training-serving schema drift in ",
    FindingType.SENSITIVE_SOURCE: "Sensitive source: ",
    FindingType.PROXY_CANDIDATE: "Proxy candidate: ",
    FindingType.DEPRECATED_INPUT: "Deprecated training input ",
    FindingType.TABLE_LEVEL_RISK: "Table-level ",
}

#: Pulls the run id out of the footer ``incidents._stamp_run_id`` appends. Its
#: presence is also what marks an incident as Janus's own: a human writing a
#: description by hand does not end it with this sentence, and no other tool
#: writes Janus's run ids.
_RUN_FOOTER = re.compile(r"Raised by Janus run (\S+?)\.\s*$")

#: Milliseconds in an hour, for reporting a duration in units somebody reasons in.
_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class IncidentLifecycle:
    """One Janus incident, and what the graph records about its lifetime."""

    incident_urn: str
    resource_urn: str
    finding_type: FindingType
    run_id: str
    """The scan that raised it, read off the description footer."""
    opened_ms: int
    resolved_ms: int | None
    """None while the incident is still open, which is a different fact from a
    resolution that took no time."""

    @property
    def resolved(self) -> bool:
        """Whether this incident has been closed."""
        return self.resolved_ms is not None

    @property
    def duration_ms(self) -> int | None:
        """How long it stayed open, or None when that cannot be established.

        None for an open incident, and also for a resolution stamped before the
        creation. The second case is a graph somebody edited by hand or a clock
        that moved, and a negative duration clamped to zero would pull a mean
        down with a number nothing measured.
        """
        if self.resolved_ms is None:
            return None
        elapsed = self.resolved_ms - self.opened_ms
        return elapsed if elapsed >= 0 else None


@dataclass(frozen=True)
class TypeLifecycle:
    """Every Janus incident of one finding type, rolled up."""

    finding_type: FindingType
    raised: int
    open_now: int
    resolved: int
    """Incidents closed *and* carrying a usable duration. Never larger than the
    number closed: a resolution whose stamps disagree is excluded from both the
    mean and this count, so the denominator matches what was averaged."""

    mean_ms: float | None
    median_ms: float | None
    """None when nothing of this type has been resolved yet. Zero would be a
    claim that findings of this type close instantly."""

    @property
    def mean_hours(self) -> float | None:
        """The mean, in the unit an on-call rotation is discussed in."""
        return None if self.mean_ms is None else self.mean_ms / _HOUR_MS

    @property
    def median_hours(self) -> float | None:
        """The median, in the same unit as the mean.

        Reported beside it because a single incident left open across a weekend
        moves a mean by days and describes none of the others.
        """
        return None if self.median_ms is None else self.median_ms / _HOUR_MS

    def describe(self) -> str:
        """One line for a console or a report."""
        if self.median_hours is None:
            return (
                f"{self.finding_type.value}: {self.raised} raised, "
                f"{self.open_now} still open, none resolved yet"
            )
        return (
            f"{self.finding_type.value}: {self.raised} raised, {self.open_now} still open, "
            f"MTTR {self.mean_hours:.1f}h (median {self.median_hours:.1f}h over "
            f"{self.resolved})"
        )


def _classify(title: str | None) -> FindingType | None:
    """Return the detector whose title prefix this incident's title carries."""
    if title is None:
        return None
    for finding_type, prefix in TITLE_PREFIXES.items():
        if title.startswith(prefix):
            return finding_type
    return None


def _lifecycle(
    incident_urn: str, resource_urn: str, info: IncidentInfoClass
) -> IncidentLifecycle | None:
    """Read one incident, or None when it is not a classifiable Janus one."""
    footer = _RUN_FOOTER.search(info.description or "")
    if footer is None:
        return None

    finding_type = _classify(info.title)
    if finding_type is None:
        return None

    status = info.status
    resolved_ms = (
        status.lastUpdated.time
        if status.state == IncidentStateClass.RESOLVED and status.lastUpdated is not None
        else None
    )
    return IncidentLifecycle(
        incident_urn=incident_urn,
        resource_urn=resource_urn,
        finding_type=finding_type,
        run_id=footer.group(1),
        opened_ms=info.created.time,
        resolved_ms=resolved_ms,
    )


def model_resources(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
) -> tuple[str, ...]:
    """Every entity a scan of this model could have raised an incident on.

    Four sets. Three are the ones reconciliation already computes: the datasets
    its training runs read, the inputs carrying a training-time snapshot, and the
    source column behind each declared feature. Present whether or not anything
    is currently wrong with them, because an incident worth timing is one that
    has already been closed.

    The fourth is the datasets *upstream* of those inputs, and leaving it out was
    a wrong number rather than a missing one: a freshness incident lands on the
    table that stopped refreshing, which is never the model's own input but
    something behind it. Without this walk the freshness row read "0 raised" on a
    graph holding twenty of them, which is exactly the shape of silence the rest
    of this project exists to refuse. It is the same walk ``blast_radius`` makes
    forwards, run backwards from the model, and it reuses that module's own
    hop-capped read rather than a second copy of it.

    Args:
        conn: An open connection.
        config: Supplies the training-snapshot property name and the hop cap.
        model_urn: The model whose resources to enumerate.

    Returns:
        The resource URNs, sorted and deduplicated.
    """
    inputs = set(model_input_datasets(conn, model_urn))
    resources = set(inputs)
    resources.update(schema_drift_candidate_resources(conn, model_urn, config))

    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    for feature_urn in (properties.mlFeatures or []) if properties else []:
        column = feature_source_column(conn, feature_urn)
        if column is not None:
            resources.add(column)

    for dataset_urn in inputs:
        resources.update(upstream_datasets(conn, dataset_urn, config))

    return tuple(sorted(resources))


def read_lifecycles(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urns: Iterable[str],
) -> tuple[IncidentLifecycle, ...]:
    """Return every Janus incident reachable from these models' resources.

    Deduplicated by incident URN: one dataset feeds several models, and an
    incident on it would otherwise be counted once per model and pull the mean
    toward whatever the busiest table did.

    Args:
        conn: An open connection.
        config: The same config a scan would run with.
        model_urns: The models whose resources to sweep.

    Returns:
        The lifecycles, sorted oldest first, so a reader sees the history in the
        order it happened.
    """
    found: dict[str, IncidentLifecycle] = {}
    for model_urn in model_urns:
        for resource_urn in model_resources(conn, config, model_urn):
            for incident_urn in attached_incident_urns(conn, resource_urn):
                if incident_urn in found:
                    continue
                info = conn.graph.get_aspect(incident_urn, IncidentInfoClass)
                if info is None:
                    continue
                lifecycle = _lifecycle(incident_urn, resource_urn, info)
                if lifecycle is not None:
                    found[incident_urn] = lifecycle

    return tuple(sorted(found.values(), key=lambda item: (item.opened_ms, item.incident_urn)))


def mttr_by_type(lifecycles: Sequence[IncidentLifecycle]) -> tuple[TypeLifecycle, ...]:
    """Roll lifecycles up per finding type. Pure: no graph, no clock.

    Every registered finding type gets a row, including the ones nothing has ever
    raised. A detector missing from the table reads as a detector that was never
    built, and a zero row reads as one that has never fired, which is the fact.

    Args:
        lifecycles: What :func:`read_lifecycles` returned, or a fixture.

    Returns:
        One row per :data:`TITLE_PREFIXES` key, in ``FindingType`` declaration
        order so two runs render the same way.
    """
    durations: dict[FindingType, list[int]] = {ft: [] for ft in TITLE_PREFIXES}
    raised: dict[FindingType, int] = dict.fromkeys(TITLE_PREFIXES, 0)
    open_now: dict[FindingType, int] = dict.fromkeys(TITLE_PREFIXES, 0)

    for lifecycle in lifecycles:
        raised[lifecycle.finding_type] += 1
        if not lifecycle.resolved:
            open_now[lifecycle.finding_type] += 1
        duration = lifecycle.duration_ms
        if duration is not None:
            durations[lifecycle.finding_type].append(duration)

    rows = []
    for finding_type in FindingType:
        if finding_type not in TITLE_PREFIXES:
            continue
        measured = durations[finding_type]
        rows.append(
            TypeLifecycle(
                finding_type=finding_type,
                raised=raised[finding_type],
                open_now=open_now[finding_type],
                resolved=len(measured),
                mean_ms=statistics.fmean(measured) if measured else None,
                median_ms=statistics.median(measured) if measured else None,
            )
        )
    return tuple(rows)
