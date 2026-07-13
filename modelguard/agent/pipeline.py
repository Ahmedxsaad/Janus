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
from modelguard.detect.leakage import leakage_findings
from modelguard.llm import LLMConfig
from modelguard.models import (
    Finding,
    FreshnessFinding,
    LeakageFinding,
    Severity,
    severity_rank,
)
from modelguard.writeback.assertions import (
    AssertionWrite,
    record_assertion_result,
    render_assertion_yaml,
    upsert_guarding_assertion,
)
from modelguard.writeback.documents import DocumentWrite, publish_impact_report
from modelguard.writeback.incidents import IncidentWrite, raise_incident
from modelguard.writeback.labels import add_tag, ensure_tag
from modelguard.writeback.properties import (
    RISK_FLAGS,
    RUN_ID,
    assign_properties,
    define_properties,
    read_properties,
)
from modelguard.writeback.terms import add_term, ensure_term

#: Description attached to the tag entity the first time it is created.
AT_RISK_TAG_DESCRIPTION = (
    "A ModelGuard scan found this model downstream of a data asset that is failing "
    "its quality or freshness expectations. The model's inputs are not trustworthy "
    "until the upstream asset recovers."
)

#: The term ModelGuard attaches to a feature whose lineage reaches a label column.
LEAKAGE_RISK_TERM_NAME = "leakage-risk"
LEAKAGE_RISK_TERM_DEFINITION = (
    "ModelGuard traced this feature's column-level lineage back to a column declared "
    "as a model's label. A model consuming it is learning from the answer, so its "
    "offline metrics are inflated and will not hold in production, where the label is "
    "not known at scoring time."
)


def new_run_id() -> str:
    """Mint an identifier for one scan. Short enough to read in a UI."""
    return f"scan-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class FindingWrites:
    """One finding, its prose, and every mutation it produced."""

    finding: Finding
    narrative: Narrative
    incident: IncidentWrite | None = None
    assertion: AssertionWrite | None = None
    """Only a freshness finding leaves a guarding assertion behind."""
    assertion_result: str | None = None
    tagged_models: tuple[str, ...] = ()
    """Models newly tagged. A model already tagged is not listed: nothing was written."""
    termed_features: tuple[str, ...] = ()
    """Features newly marked leakage-risk. Empty when the term was already there."""
    documents: tuple[DocumentWrite, ...] = ()


@dataclass(frozen=True)
class ScanReport:
    """Everything one scan found and wrote. Returned to the CLI and the tests.

    A scan can now run more than one detector, and a detector can return more than
    one finding, so the report holds a list. A clean scan holds an empty one.
    """

    run_id: str
    dry_run: bool
    table_urn: str | None = None
    model_urn: str | None = None
    writes: tuple[FindingWrites, ...] = ()
    assertion_yaml: str = ""
    """The guarding assertion a freshness finding would leave. Rendered even on a
    dry run, so the caller can see the artifact that was not written."""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        """Whether the scan found nothing to report."""
        return not self.writes

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Every finding this scan produced, in the order they were written."""
        return tuple(write.finding for write in self.writes)

    @property
    def severity(self) -> Severity | None:
        """The severity of the worst finding, or None when the scan was clean."""
        if not self.writes:
            return None
        return min((write.finding.severity for write in self.writes), key=severity_rank)


def _write_back(
    conn: DataHubConnection,
    finding: Finding,
    narrative: Narrative,
    config: ScanConfig,
    run_id: str,
    observed_at_ms: int,
) -> FindingWrites:
    """Perform every mutation for one finding, idempotently.

    Ordering is deliberate: the property definitions must exist before a value is
    assigned to one, and the assertion entity must exist before a run event can
    reference it.

    What every finding writes: an incident on the offending data asset, and, on
    each model it endangers, a tag, a risk flag, and an impact report. What only
    some findings write is dispatched below: a stale table earns a guarding
    freshness assertion, and a leaking feature earns a leakage-risk term. Writing
    a freshness assertion for a leakage finding would be a lie about what was
    checked.
    """
    incident = raise_incident(
        conn,
        resource_urn=finding.resource_urn,
        incident_type=finding.incident_type,
        title=finding.title,
        description=incident_description(finding, narrative),
        run_id=run_id,
    )

    assertion: AssertionWrite | None = None
    assertion_result: str | None = None
    if isinstance(finding, FreshnessFinding):
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
            signal=finding.blast_radius.signal,
            run_id=run_id,
        )

    termed: tuple[str, ...] = ()
    if isinstance(finding, LeakageFinding):
        ensure_term(
            conn,
            config.leakage_risk_term_urn,
            LEAKAGE_RISK_TERM_NAME,
            LEAKAGE_RISK_TERM_DEFINITION,
        )
        if add_term(conn, finding.leak.feature_urn, config.leakage_risk_term_urn):
            termed = (finding.leak.feature_urn,)

    define_properties(conn)
    tag_urn = ensure_tag(conn, config.model_at_risk_tag, AT_RISK_TAG_DESCRIPTION)

    tagged: list[str] = []
    documents: list[DocumentWrite] = []
    for model in finding.models_at_risk:
        if add_tag(conn, model.urn, tag_urn):
            tagged.append(model.urn)
        # Model-level risk lives on the model, because DataHub refuses an incident
        # there. The trust score itself is a later phase; this run records the flag.
        #
        # Read-then-union, not a blind overwrite: assign_properties replaces the
        # named property's whole value, and a model can appear in more than one
        # finding within a single scan (downstream of a stale table AND itself
        # leaking, or leaking through two separate features). Writing this
        # finding's type alone would erase whatever an earlier finding in the
        # same scan just wrote for the same model.
        existing_flags = {
            str(flag) for flag in read_properties(conn, model.urn).get(RISK_FLAGS, [])
        }
        assign_properties(
            conn,
            model.urn,
            {
                RISK_FLAGS: sorted(existing_flags | {str(finding.finding_type)}),
                RUN_ID: [run_id],
            },
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

    return FindingWrites(
        finding=finding,
        narrative=narrative,
        incident=incident,
        assertion=assertion,
        assertion_result=assertion_result,
        tagged_models=tuple(tagged),
        termed_features=termed,
        documents=tuple(documents),
    )


def _detect(
    conn: DataHubConnection,
    config: ScanConfig,
    table_urn: str | None,
    model_urn: str | None,
    observed_at: int,
) -> tuple[list[Finding], list[str]]:
    """Run every detector the caller asked for. Deterministic, and no writes.

    Returns:
        The findings, worst first, and any warnings worth surfacing.
    """
    findings: list[Finding] = []
    warnings: list[str] = []

    if table_urn is not None:
        radius = blast_radius(conn, table_urn, config, now_ms=observed_at)
        if radius is not None:
            findings.append(finding_for(radius))
            if not radius.models:
                warnings.append(
                    "the table is stale but no model consumes it within "
                    f"{config.max_hops} hops; no model was tagged"
                )

    if model_urn is not None:
        findings.extend(leakage_findings(conn, model_urn, config))

    findings.sort(key=lambda finding: severity_rank(finding.severity))
    return findings, warnings


def run_scan(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None = None,
    model_urn: str | None = None,
    run_id: str | None = None,
    llm: LLMConfig | None = None,
    dry_run: bool = False,
    now_ms: int | None = None,
) -> ScanReport:
    """Audit a table, a model, or both, and write back what is wrong.

    The two targets answer different questions and run different detectors. A
    table is asked "what does your going stale endanger", and the answer comes
    from the downstream blast radius. A model is asked "are you learning from your
    own label", and the answer comes from upstream column lineage. A scan may ask
    both; each finding is written back independently.

    Args:
        conn: An open connection. Writes need credentials only when the DataHub
            instance has authentication enabled.
        config: SLA, hop caps, tag and term names.
        table_urn: The dataset to audit for freshness, if any.
        model_urn: The model to audit for target leakage, if any.
        run_id: Provenance stamp. Minted when omitted.
        llm: The configured language model, or None to write deterministic
            template prose. The narrator falls back to the template on any
            failure anyway; passing None makes that the only path.
        dry_run: Detect and explain, write nothing. This is the approval gate
            until the LangGraph interrupt lands.
        now_ms: The instant to measure staleness against. Defaults to now.

    Returns:
        What was found and what was written. A healthy target returns a report
        whose ``writes`` is empty and which wrote nothing at all.
    """
    run_id = run_id or new_run_id()
    observed_at = now_ms if now_ms is not None else int(time.time() * 1000)

    findings, warnings = _detect(conn, config, table_urn, model_urn, observed_at)

    # Rendered either way: a dry run must be able to show the assertion it would
    # have written. Only meaningful for a table target.
    assertion_yaml = (
        render_assertion_yaml(table_urn, config.freshness_sla_hours, config.freshness_field)
        if table_urn is not None
        else ""
    )

    if dry_run:
        return ScanReport(
            run_id=run_id,
            table_urn=table_urn,
            model_urn=model_urn,
            dry_run=True,
            writes=tuple(
                FindingWrites(finding=finding, narrative=narrate(finding, llm))
                for finding in findings
            ),
            assertion_yaml=assertion_yaml,
            warnings=tuple(warnings),
        )

    writes = tuple(
        _write_back(conn, finding, narrate(finding, llm), config, run_id, observed_at)
        for finding in findings
    )

    # Empty, not the unwritten preview, when no write in this scan has an
    # assertion: a scan that found only a leakage finding never called
    # upsert_guarding_assertion, and reporting the table's rendered-but-unwritten
    # YAML here would claim a check was written when it was not.
    written_yaml = next(
        (write.assertion.yaml_text for write in writes if write.assertion is not None),
        "",
    )

    return ScanReport(
        run_id=run_id,
        table_urn=table_urn,
        model_urn=model_urn,
        dry_run=False,
        writes=writes,
        assertion_yaml=written_yaml,
        warnings=tuple(warnings),
    )
