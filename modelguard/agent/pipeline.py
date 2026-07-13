"""The core loop: detect, explain, write back. Shared by every entry point.

``scan`` (batch) and, later, ``watch`` (event-driven) differ only in what wakes
them up. Both call :func:`run_scan`, so a finding is detected, explained, and
written back identically no matter what triggered it (modelguard/CLAUDE.md
rule 2).

Node order, matching the state machine Phase 3 replaces this with::

    detect -> investigate -> reason -> [approval] -> write_back

Here the first three are plain function calls and the approval gate is
``dry_run``: nothing is written, and the caller sees exactly what would have
been. Phase 3 swaps this for a LangGraph ``StateGraph`` with a real
``interrupt()``; the boundaries are drawn so that swap touches nothing else.

Every write in a run carries the same ``run_id``. It is provenance, not a dedup
key: it changes every run, so keying on it would duplicate every finding on
every scan.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from modelguard.agent.narrate import Narrative, incident_description, narrate
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import blast_radius, finding_for
from modelguard.llm import LLMConfig
from modelguard.models import Finding
from modelguard.writeback.assertions import (
    AssertionWrite,
    record_assertion_result,
    render_assertion_yaml,
    upsert_guarding_assertion,
)
from modelguard.writeback.documents import DocumentWrite, publish_impact_report
from modelguard.writeback.incidents import IncidentWrite, raise_incident
from modelguard.writeback.labels import add_tag, ensure_tag
from modelguard.writeback.properties import RISK_FLAGS, RUN_ID, assign_properties, define_properties

#: Description attached to the tag entity the first time it is created.
AT_RISK_TAG_DESCRIPTION = (
    "A ModelGuard scan found this model downstream of a data asset that is failing "
    "its quality or freshness expectations. The model's inputs are not trustworthy "
    "until the upstream asset recovers."
)


def new_run_id() -> str:
    """Mint an identifier for one scan. Short enough to read in a UI."""
    return f"scan-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class ScanReport:
    """Everything one scan found and wrote. Returned to the CLI and the tests."""

    run_id: str
    table_urn: str
    dry_run: bool
    finding: Finding | None = None
    narrative: Narrative | None = None
    incident: IncidentWrite | None = None
    assertion: AssertionWrite | None = None
    assertion_result: str | None = None
    tagged_models: tuple[str, ...] = ()
    """Models newly tagged. A model already tagged is not listed: nothing was written."""
    documents: tuple[DocumentWrite, ...] = ()
    assertion_yaml: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        """Whether the scan found nothing to report."""
        return self.finding is None


def _write_back(
    conn: DataHubConnection,
    finding: Finding,
    narrative: Narrative,
    config: ScanConfig,
    run_id: str,
    observed_at_ms: int,
) -> tuple[IncidentWrite, AssertionWrite, str, tuple[str, ...], tuple[DocumentWrite, ...]]:
    """Perform every mutation for one finding, idempotently.

    Ordering is deliberate: the property definitions must exist before a value is
    assigned to one, and the assertion entity must exist before a run event can
    reference it.
    """
    radius = finding.blast_radius

    incident = raise_incident(
        conn,
        resource_urn=finding.resource_urn,
        incident_type=finding.incident_type,
        title=finding.title,
        description=incident_description(finding, narrative),
        run_id=run_id,
    )

    assertion = upsert_guarding_assertion(
        conn,
        table_urn=finding.resource_urn,
        sla_hours=config.freshness_sla_hours,
        freshness_field=config.freshness_field,
        created_ms=observed_at_ms,
    )
    assertion_result = record_assertion_result(
        conn,
        assertion_urn=assertion.urn,
        signal=radius.signal,
        run_id=run_id,
    )

    define_properties(conn)
    tag_urn = ensure_tag(conn, config.model_at_risk_tag, AT_RISK_TAG_DESCRIPTION)

    tagged: list[str] = []
    documents: list[DocumentWrite] = []
    for model in radius.models:
        if add_tag(conn, model.urn, tag_urn):
            tagged.append(model.urn)
        # Model-level risk lives on the model, because DataHub refuses an incident
        # there. The trust score itself is a later phase; this run records the flag.
        assign_properties(
            conn,
            model.urn,
            {RISK_FLAGS: [str(finding.finding_type)], RUN_ID: [run_id]},
        )
        documents.append(
            publish_impact_report(
                conn,
                model_urn=model.urn,
                finding=finding,
                narrative=narrative.assessment,
                run_id=run_id,
            )
        )

    return incident, assertion, assertion_result, tuple(tagged), tuple(documents)


def run_scan(
    conn: DataHubConnection,
    table_urn: str,
    config: ScanConfig,
    *,
    run_id: str | None = None,
    llm: LLMConfig | None = None,
    dry_run: bool = False,
    now_ms: int | None = None,
) -> ScanReport:
    """Audit one table and write back what it endangers.

    Args:
        conn: An open connection. Writes need credentials only when the DataHub
            instance has authentication enabled.
        table_urn: The dataset to audit.
        config: Freshness SLA, hop cap, and label names.
        run_id: Provenance stamp. Minted when omitted.
        llm: The configured language model, or None to write deterministic
            template prose. The narrator falls back to the template on any
            failure anyway; passing None makes that the only path.
        dry_run: Detect and explain, write nothing. This is the approval gate
            until the LangGraph interrupt lands.
        now_ms: The instant to measure staleness against. Defaults to now.

    Returns:
        What was found and what was written. A clean table returns a report whose
        ``finding`` is None and which wrote nothing.
    """
    run_id = run_id or new_run_id()
    observed_at = now_ms if now_ms is not None else int(time.time() * 1000)

    radius = blast_radius(conn, table_urn, config, now_ms=observed_at)
    if radius is None:
        return ScanReport(run_id=run_id, table_urn=table_urn, dry_run=dry_run)

    finding = finding_for(radius)
    narrative = narrate(finding, llm)

    warnings: list[str] = []
    if not radius.models:
        warnings.append(
            "the table is stale but no model consumes it within "
            f"{config.max_hops} hops; no model was tagged"
        )

    # The YAML artifact is rendered either way: a dry run must be able to show
    # the assertion it would have written.
    assertion_yaml = render_assertion_yaml(
        table_urn, config.freshness_sla_hours, config.freshness_field
    )

    if dry_run:
        return ScanReport(
            run_id=run_id,
            table_urn=table_urn,
            dry_run=True,
            finding=finding,
            narrative=narrative,
            assertion_yaml=assertion_yaml,
            warnings=tuple(warnings),
        )

    incident, assertion, result, tagged, documents = _write_back(
        conn, finding, narrative, config, run_id, observed_at
    )

    return ScanReport(
        run_id=run_id,
        table_urn=table_urn,
        dry_run=False,
        finding=finding,
        narrative=narrative,
        incident=incident,
        assertion=assertion,
        assertion_result=result,
        tagged_models=tagged,
        documents=documents,
        assertion_yaml=assertion.yaml_text,
        warnings=tuple(warnings),
    )
