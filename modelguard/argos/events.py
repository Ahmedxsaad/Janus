"""Turning what a scan found into what the dog does.

The rule this module exists to keep is docs/plan/08 section 3: no animation
without a real event behind it. Every event built here is a pure function of a
detector's own output, so the sprite depicts something that was measured and
never something that was inferred to look busy.

The speech-bubble sentence comes from a finding's ``title``, which is a pure
function of stable graph facts (models.py), not from a log line and not from the
narrator: the log channel is forbidden to carry prose (modelguard/logs.py), and
LLM output must never reach a key or a state (modelguard/CLAUDE.md rule 5). It
is fine in a bubble, but the title is the honest, stable thing to show.
"""

from __future__ import annotations

from modelguard.agent.pipeline import ScanReport
from modelguard.argos.protocol import Event, Hop
from modelguard.detect.graph_reads import entity_type
from modelguard.env import optional_value
from modelguard.models import Finding, FreshnessFinding, TrustBand, severity_rank

#: Base URL of the DataHub *UI*, which is a different port from GMS (9002 next
#: to 8080 on a Quickstart). Identity, so it gets no default and no guess: with
#: it unset, events simply carry no link and the window's "Open in DataHub" does
#: nothing rather than opening a URL somebody's browser cannot reach.
ENV_UI_URL = "MODELGUARD_DATAHUB_UI_URL"

#: How many hops of a blast radius the walk animates. Beyond this the animation
#: is a blur and the screen has no room for the labels.
MAX_WALK_HOPS = 6


def entity_link(urn: str) -> str | None:
    """Return the DataHub UI URL for an entity, when one can be built.

    None on either of the two ways this fails, and neither is worth an
    exception: the UI URL is not configured, or the URN is not one the SDK can
    parse into an entity type. A link is a convenience on a speech bubble, so it
    must never be the reason a scan raises.
    """
    base = optional_value(ENV_UI_URL)
    if base is None:
        return None
    try:
        kind = entity_type(urn)
    except Exception:  # noqa: BLE001 - the SDK raises several types for a bad URN
        return None
    return f"{base.rstrip('/')}/{kind}/{urn}"


def walk_path(finding: Finding) -> tuple[Hop, ...]:
    """Return the blast radius as hops the walk can animate.

    Only a freshness finding carries a traversal: it is the one detector whose
    output *is* a path (`detect/blast_radius.py`). The other detectors answer a
    question about one model's inputs, so their findings have a resource and no
    route, and the walk simply does not offer itself.

    The column labels are the point of the animation: they are what makes this
    the column-level traversal the benchmarks measure rather than a table-level
    guess, so the model hop carries the feature that put it at risk.
    """
    if not isinstance(finding, FreshnessFinding):
        return ()
    radius = finding.blast_radius
    hops = [Hop(urn=radius.failing_table_urn)]
    for dataset_urn in radius.downstream_datasets[:MAX_WALK_HOPS]:
        hops.append(Hop(urn=dataset_urn))
    for model in radius.models[:1]:
        column = model.features_at_risk[0] if model.features_at_risk else None
        hops.append(Hop(urn=model.urn, column=column))
    return tuple(hops) if len(hops) > 1 else ()


def _worst_trust(report: ScanReport) -> tuple[int | None, str | None]:
    """Return the lowest trust score this scan measured, and its band.

    The lowest rather than the first: with several models in one report, the
    meter must show the one a person needs to act on, and the band comes from
    the detector rather than from a threshold applied again here.
    """
    if not report.trust:
        return None, None
    worst = min(report.trust, key=lambda write: write.score.value)
    return worst.score.value, str(worst.score.band)


def from_report(report: ScanReport, *, recovered: bool = False) -> Event:
    """Return the one event that describes a completed scan.

    Ranked by what a person needs to know first: a finding outranks a recovery,
    which outranks a dropped trust band, which outranks a check that could not
    run, which outranks silence. One event rather than one per finding, because
    the dog has one body and the worst finding is the one to bark about.

    Args:
        report: What the scan found.
        recovered: True when this scan is the poll on which a finding that was
            open a moment ago stopped reproducing. It is a transition, not a
            property of the report, so the caller is the only thing that knows.
    """
    trust_value, trust_band = _worst_trust(report)
    findings = report.findings
    if findings:
        worst = min(findings, key=lambda finding: severity_rank(finding.severity))
        return Event(
            state="barking",
            title=worst.title,
            entity=worst.resource_urn,
            severity=str(worst.severity),
            link=entity_link(worst.resource_urn),
            trust=trust_value,
            band=trust_band,
            path=walk_path(worst),
        )

    if recovered:
        return Event(
            state="recovered",
            title="recovered: nothing is failing",
            trust=trust_value,
            band=trust_band,
        )

    dropped = [write for write in report.trust if write.score.band is not TrustBand.HEALTHY]
    if dropped:
        worst_model = min(dropped, key=lambda write: write.score.value)
        return Event(
            state="sick",
            title=(
                f"{worst_model.model_name} trust {worst_model.score.value}"
                f" ({worst_model.score.band})"
            ),
            entity=worst_model.model_urn,
            trust=worst_model.score.value,
            band=str(worst_model.score.band),
            link=entity_link(worst_model.model_urn),
        )

    if report.not_evaluated:
        # The state detect/coverage.py exists for: a check that could not run is
        # not a check that passed, and rendering the two the same way is how an
        # ambient display starts lying.
        gap = report.not_evaluated[0]
        return Event(
            state="unchecked",
            title=f"{len(report.not_evaluated)} check(s) could not run: {gap.check}",
            trust=trust_value,
            band=trust_band,
        )

    return Event(state="patrolling", title="no findings", trust=trust_value, band=trust_band)


def unreachable(reason: str) -> Event:
    """Return the event for a poll that could not reach DataHub.

    The row that earns the pet its trust: a cheerful dog that is silently
    disconnected is a lie, and blind must look different from healthy.
    """
    return Event(state="ghost", title=reason)


def asleep(polls: int) -> Event:
    """Return the event for a run of polls in which nothing changed."""
    return Event(state="asleep", title=f"{polls} polls, nothing changed")
