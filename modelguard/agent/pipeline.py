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

What a run reports about itself
-------------------------------
Every scan logs one line keyed by ``run_id``, carrying the phase timings and the
counts: how long detection took, how many findings it produced, how many writes
landed. Those are the numbers an MTTD budget is made of, and they are emitted as
``key=value`` pairs so a log pipeline can parse them without a custom format and
a human can still read them. The library only emits; choosing a level and a
destination belongs to whoever runs the process, which for the unattended path
is ``modelguard watch``.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from datahub.metadata.schema_classes import (
    IncidentInfoClass,
    IncidentStateClass,
    MLModelPropertiesClass,
)
from datahub.metadata.urns import DatasetUrn, SchemaFieldUrn

from modelguard.agent.narrate import Narrative, incident_description, narrate
from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import (
    blast_radius,
    downstream_models,
    finding_for,
    freshness_signal,
)
from modelguard.detect.coverage import Unevaluated, coverage_gaps
from modelguard.detect.governance import (
    deprecated_input_findings,
    model_input_datasets,
    sensitive_index,
    sensitive_source_findings,
)
from modelguard.detect.graph_reads import model_ref
from modelguard.detect.leakage import feature_source_column, leakage_findings
from modelguard.detect.schema_drift import schema_drift_candidate_resources, schema_drift_findings
from modelguard.detect.trust_score import trust_inputs_from_findings, trust_score
from modelguard.llm import LLMConfig
from modelguard.logs import LOG_FIELDS, logfmt
from modelguard.models import (
    Finding,
    FindingType,
    FreshnessFinding,
    LeakageFinding,
    ModelRef,
    Severity,
    TrustScore,
    severity_rank,
)
from modelguard.writeback.assertions import (
    AssertionWrite,
    record_assertion_result,
    render_assertion_yaml,
    upsert_guarding_assertion,
)
from modelguard.writeback.documents import DocumentWrite, publish_impact_report
from modelguard.writeback.incidents import (
    IncidentWrite,
    attached_incident_urns,
    find_active_incident,
    raise_incident,
    resolve_incident,
)
from modelguard.writeback.labels import add_tag, ensure_tag, remove_tag
from modelguard.writeback.properties import (
    OPEN_LEAK_COLUMNS,
    RISK_FLAGS,
    RUN_ID,
    TRUST_BAND,
    TRUST_SCORE,
    assign_properties,
    define_properties,
    read_properties,
    remove_properties,
)
from modelguard.writeback.terms import add_term, ensure_term, remove_term

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


#: One logger for the whole loop, so every scan's timings arrive on one channel
#: no matter which trigger woke it. No handler is attached here: a library that
#: configures logging steals the decision from the application that imported it.
logger = logging.getLogger(__name__)


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
class TrustWrite:
    """One model's trust score, and whether it was persisted this run."""

    model_urn: str
    model_name: str
    score: TrustScore


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
    trust: tuple[TrustWrite, ...] = ()
    """One entry per model the scan produced a finding about, worst score first."""
    assertion_yaml: str = ""
    """The guarding assertion a freshness finding would leave. Rendered even on a
    dry run, so the caller can see the artifact that was not written."""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    not_evaluated: tuple[Unevaluated, ...] = field(default_factory=tuple)
    """Checks this scan asked for but could not perform, with the metadata each
    one is missing. A clean scan is only meaningful alongside this: without it,
    "no finding" reads the same whether a check passed or never ran."""

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


def _trust_scores(findings: list[Finding], config: ScanConfig) -> tuple[TrustWrite, ...]:
    """Roll the scan's findings into one trust score per model. Pure, no writes.

    A model is scored against every finding that names it, so a model both
    downstream of a stale table and independently leaking loses trust for both.
    Only models the scan actually found something about are scored: a clean model
    is not written a score of 100, which keeps a clean scan writing nothing.

    Returns:
        One entry per model, worst (lowest) score first, then by URN for stability.
    """
    by_model: dict[str, list[Finding]] = {}
    refs: dict[str, ModelRef] = {}
    for finding in findings:
        for model in finding.models_at_risk:
            by_model.setdefault(model.urn, []).append(finding)
            refs.setdefault(model.urn, model)

    trust_writes = [
        TrustWrite(
            model_urn=model_urn,
            model_name=refs[model_urn].name,
            score=trust_score(trust_inputs_from_findings(model_findings, refs[model_urn]), config),
        )
        for model_urn, model_findings in by_model.items()
    ]
    return tuple(sorted(trust_writes, key=lambda w: (w.score.value, w.model_urn)))


def _persist_trust(conn: DataHubConnection, trust_writes: tuple[TrustWrite, ...]) -> None:
    """Write each model's trust score and band as structured properties.

    Read-then-merge inside ``assign_properties`` preserves the risk flags and run
    id an earlier per-finding write set on the same model, and rewriting an
    unchanged score is a no-op, so a rerun converges.
    """
    for trust_write in trust_writes:
        assign_properties(
            conn,
            trust_write.model_urn,
            {
                TRUST_SCORE: [float(trust_write.score.value)],
                TRUST_BAND: [str(trust_write.score.band)],
            },
        )


def written_assertion_yaml(writes: tuple[FindingWrites, ...]) -> str:
    """The YAML of the first guarding assertion actually written this scan, or ``""``.

    Empty, not the unwritten preview, when no write in this scan carries an
    assertion: a scan that found only a leakage finding never called
    ``upsert_guarding_assertion``, and reporting the table's rendered-but-unwritten
    YAML would claim a check was written when it was not. Shared by ``run_scan`` and
    the LangGraph agent so both report the written artifact identically.
    """
    return next(
        (write.assertion.yaml_text for write in writes if write.assertion is not None),
        "",
    )


def _detect(
    conn: DataHubConnection,
    config: ScanConfig,
    table_urn: str | None,
    model_urn: str | None,
    observed_at: int,
) -> tuple[list[Finding], list[str], tuple[Unevaluated, ...]]:
    """Run every detector the caller asked for. Deterministic, and no writes.

    Returns:
        The findings worst first, any warnings worth surfacing, and the checks
        that could not run at all for want of the metadata they read.
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
        findings.extend(schema_drift_findings(conn, model_urn, config))
        findings.extend(sensitive_source_findings(conn, model_urn, config))
        findings.extend(deprecated_input_findings(conn, model_urn, config))

    findings.sort(key=lambda finding: severity_rank(finding.severity))
    gaps = coverage_gaps(
        conn,
        config,
        table_urn=table_urn,
        model_urn=model_urn,
        findings=tuple(findings),
    )
    return findings, warnings, gaps


def _recovery_message(run_id: str) -> str:
    """The message stamped on an incident this reconciliation resolves."""
    return f"Recovered by ModelGuard run {run_id}; the finding is no longer present."


def _record_recovered_assertion(
    conn: DataHubConnection,
    config: ScanConfig,
    table_urn: str,
    run_id: str,
    observed_at: int,
) -> None:
    """Write the passing run event for a table that is no longer stale.

    The guarding assertion's latest run event is the FAILURE recorded when the
    table went stale. Resolving the incident without recording the passing run
    leaves the assertion red in the DataHub UI forever, so a recovered table
    reads as still breaching the very check ModelGuard wrote to guard it.

    Called only after a FRESHNESS incident was actually resolved, which means
    the assertion already exists: a table that was never stale must not have one
    conjured for it by a clean scan. ``upsert_guarding_assertion`` derives the
    URN as a guid over the declaration, so this resolves to that same assertion
    rather than creating a second one.

    Does nothing when the table reports no ``operation`` aspect: with no signal
    there is no measurement to record, and inventing one would assert a lag that
    was never observed.
    """
    signal = freshness_signal(conn, table_urn, config, now_ms=observed_at)
    if signal is None:
        return
    assertion = upsert_guarding_assertion(
        conn,
        table_urn=table_urn,
        sla_hours=config.freshness_sla_hours,
        freshness_field=config.freshness_field,
        created_ms=observed_at,
    )
    record_assertion_result(conn, assertion_urn=assertion.urn, signal=signal, run_id=run_id)


def _recorded_leak_columns(conn: DataHubConnection, model_urn: str) -> tuple[str, ...]:
    """Return the columns the last scan left an open leakage incident on."""
    recorded = read_properties(conn, model_urn).get(OPEN_LEAK_COLUMNS, [])
    return tuple(str(value) for value in recorded)


def _record_leak_columns(
    conn: DataHubConnection,
    model_urn: str,
    findings: list[Finding],
) -> None:
    """Record exactly the columns this model is leaking through, as of this scan.

    Written after reconciliation, so it always describes the state the graph was
    just left in: a column that was resolved above is gone from it, and a column
    raised this run is in it. An empty set removes the property rather than
    writing an empty list, so a model that has never leaked carries nothing.
    """
    leaking = sorted(
        {
            finding.resource_urn
            for finding in findings
            if isinstance(finding, LeakageFinding) and finding.model.urn == model_urn
        }
    )
    if leaking:
        assign_properties(conn, model_urn, {OPEN_LEAK_COLUMNS: list(leaking)})
    else:
        remove_properties(conn, model_urn, {OPEN_LEAK_COLUMNS})


def _active_incidents_titled(
    conn: DataHubConnection,
    resource_urn: str,
    title_prefix: str,
) -> tuple[str, ...]:
    """Return the active FIELD incidents on a column whose title starts as given.

    Prefix matching, not the exact-title lookup ``raise_incident``'s own dedup
    uses, because a column-scoped title names what the derivation *reached* (the
    label, or the classified column), and that is exactly the fact that no longer
    exists once somebody has fixed the derivation. Matching on the part of the
    title that is a function of the column alone is what lets a fixed problem be
    recognised as fixed.

    The prefix is what keeps the two column detectors from resolving each
    other's incidents: they raise on the same column with the same incident
    type, and only the title distinguishes them.
    """
    matched: list[str] = []
    for incident_urn in attached_incident_urns(conn, resource_urn):
        info = conn.graph.get_aspect(incident_urn, IncidentInfoClass)
        if info is None or info.status.state != IncidentStateClass.ACTIVE:
            continue
        if info.type != "FIELD" or info.title is None or not info.title.startswith(title_prefix):
            continue
        matched.append(incident_urn)
    return tuple(matched)


def _reconcile_stale_findings(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None,
    model_urn: str | None,
    findings: list[Finding],
    run_id: str,
    observed_at: int,
) -> None:
    """Resolve any stale ModelGuard incident and clear the risk it named.

    A stale incident is one this scan's target could raise, but did not raise
    this run. Graph-driven, not memory-driven: this asks the graph what is
    already
    active on the resources ``table_urn``/``model_urn`` could name, the same
    way ``raise_incident``'s own dedup does, rather than diffing against a
    previous run's in-memory typed report. A process that never saw the
    finding raised in the first place, a fresh ``watch`` after a restart, a
    plain ``scan``, ``watch --once``, can therefore still resolve it. Before
    this, only a single uninterrupted ``watch`` process that both raised and
    later re-observed a finding could ever resolve it; every other path left
    a fixed problem's incident, tag, and trust score open forever (D-067).

    Called only from ``run_scan``'s write path: resolving an incident is a
    write, so a dry run (including ``gate`` without ``--write``) must never
    reach this.
    """
    # Keyed by finding type, not by (resource, incident_type) alone. Leakage and
    # a sensitive source both raise a FIELD incident on the same column, so a
    # single flat key set would let a sensitive finding on a column mark that
    # column "still failing" for leakage, and a leak somebody had already fixed
    # would keep its incident open forever. Each detector below consults only its
    # own type's keys.
    current_keys: dict[FindingType, set[tuple[str, str]]] = {}
    for finding in findings:
        current_keys.setdefault(finding.finding_type, set()).add(
            (finding.resource_urn, finding.incident_type)
        )

    def still_failing(finding_type: FindingType, resource_urn: str, incident_type: str) -> bool:
        """Whether this scan raised this exact finding, so it must not be resolved."""
        return (resource_urn, incident_type) in current_keys.get(finding_type, set())

    recovered_flags: dict[str, set[str]] = {}
    recovered_models: dict[str, ModelRef] = {}

    def record(model: ModelRef, finding_type: FindingType) -> None:
        recovered_models[model.urn] = model
        recovered_flags.setdefault(model.urn, set()).add(str(finding_type))

    if table_urn is not None and not still_failing(
        FindingType.UPSTREAM_FRESHNESS, table_urn, "FRESHNESS"
    ):
        title = f"Stale upstream data in {DatasetUrn.from_string(table_urn).name}"
        incident = find_active_incident(conn, table_urn, "FRESHNESS", title)
        if incident is not None:
            resolve_incident(conn, incident, _recovery_message(run_id))
            _record_recovered_assertion(conn, config, table_urn, run_id, observed_at)
            for at_risk in downstream_models(conn, table_urn, config):
                record(at_risk, FindingType.UPSTREAM_FRESHNESS)

    if model_urn is not None:
        model = model_ref(conn, model_urn)
        properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
        # Asked once. With no classification configured the detector never runs,
        # so it can raise nothing, so there is nothing of its to resolve: walking
        # the columns for it anyway would be a graph read per column per scan
        # buying an answer that is known in advance.
        sensitive_configured = sensitive_index(conn, config).configured

        # Leakage: every candidate source column, matched by title prefix rather
        # than find_active_incident's exact match, because the incident's title
        # also names the label reached and that is not reconstructable once the
        # leak is gone.
        #
        # The candidates are the model's current features *and* the columns the
        # last scan recorded an open incident on. The second half is what closes
        # D-069's gap: a leak is most often fixed by deleting the offending
        # column outright, and a deleted column is in no feature list, so walking
        # only the current features left the incident open forever on a graph
        # somebody had already fixed. Hit for real on a live dbt project (D-074),
        # which is the condition D-069 said it would wait for. The record is a
        # structured property on the model, not the side-channel state store
        # D-067 rejected: it lives in the graph, it is written by the same scan
        # that raised the incident, and losing it costs a stale incident, never a
        # wrong verdict.
        candidates: dict[str, str | None] = dict.fromkeys(_recorded_leak_columns(conn, model_urn))
        for feature_urn in (properties.mlFeatures or []) if properties else []:
            source_column = feature_source_column(conn, feature_urn)
            if source_column is not None:
                candidates[source_column] = feature_urn

        for source_column, known_feature_urn in sorted(
            candidates.items(), key=lambda pair: pair[0]
        ):
            field_path = SchemaFieldUrn.from_string(source_column).field_path

            if not still_failing(FindingType.TARGET_LEAKAGE, source_column, "FIELD"):
                prefix = f"Target leakage: {field_path} derives from label "
                for incident_urn in _active_incidents_titled(conn, source_column, prefix):
                    resolve_incident(conn, incident_urn, _recovery_message(run_id))
                    if known_feature_urn is not None:
                        # Only a feature that still exists can have its risk term
                        # taken back off; a deleted one has nothing left to clear.
                        remove_term(conn, known_feature_urn, config.leakage_risk_term_urn)
                    record(model, FindingType.TARGET_LEAKAGE)

            # A sensitive source is the same walk over the same columns with a
            # different mark, so it recovers the same way and for the same
            # reasons: the title names the classified column reached, which is
            # not reconstructable once the derivation is gone, so it is matched
            # by prefix rather than by find_active_incident's exact title.
            if sensitive_configured and not still_failing(
                FindingType.SENSITIVE_SOURCE, source_column, "FIELD"
            ):
                prefix = f"Sensitive source: {field_path} derives from "
                for incident_urn in _active_incidents_titled(conn, source_column, prefix):
                    resolve_incident(conn, incident_urn, _recovery_message(run_id))
                    record(model, FindingType.SENSITIVE_SOURCE)

        _record_leak_columns(conn, model_urn, findings)

        # Schema drift: every training input with a captured snapshot, title
        # fully known (no measurement in it), so this reuses the exact dedup
        # lookup raise_incident itself uses.
        for dataset_urn in schema_drift_candidate_resources(conn, model_urn, config):
            if still_failing(FindingType.INPUT_SCHEMA_DRIFT, dataset_urn, "DATA_SCHEMA"):
                continue
            title = (
                "Training-serving schema drift in "
                f"{DatasetUrn.from_string(dataset_urn).name} for {model.name}"
            )
            incident = find_active_incident(conn, dataset_urn, "DATA_SCHEMA", title)
            if incident is not None:
                resolve_incident(conn, incident, _recovery_message(run_id))
                record(model, FindingType.INPUT_SCHEMA_DRIFT)

        # A deprecation that was lifted. DataHub records that as the aspect with
        # deprecated=False rather than by removing it, so the input is still a
        # candidate and the title is fully known: an exact dedup lookup, like
        # drift above.
        for dataset_urn in model_input_datasets(conn, model_urn):
            if still_failing(FindingType.DEPRECATED_INPUT, dataset_urn, "OPERATIONAL"):
                continue
            title = (
                f"Deprecated training input {DatasetUrn.from_string(dataset_urn).name} "
                f"for {model.name}"
            )
            incident = find_active_incident(conn, dataset_urn, "OPERATIONAL", title)
            if incident is not None:
                resolve_incident(conn, incident, _recovery_message(run_id))
                record(model, FindingType.DEPRECATED_INPUT)

    # A risk flag is a finding type, but a model can carry one type from several
    # resources at once: two stale upstream tables, two leaking features. One of
    # them recovering does not clear the type, so a flag may only be dropped when
    # this run raised no finding of that type for that model. Without this, a
    # scan that resolves one resource and re-raises another in the same breath
    # strips the flags and the at-risk tag off a model it just found failing, and
    # overwrites the trust score _persist_trust wrote seconds earlier with a
    # from-scratch "no findings" score (D-070).
    current_flags: dict[str, set[str]] = {}
    for finding in findings:
        for at_risk_model in finding.models_at_risk:
            current_flags.setdefault(at_risk_model.urn, set()).add(str(finding.finding_type))

    # One read-modify-write per affected model, after every resolve above, so
    # a model whose leakage and drift both recovered this run is cleared once
    # rather than raced across two partial writes.
    for urn, model in recovered_models.items():
        cleared = recovered_flags[urn] - current_flags.get(urn, set())
        if not cleared:
            continue
        existing_flags = {str(flag) for flag in read_properties(conn, urn).get(RISK_FLAGS, [])}
        remaining = existing_flags - cleared
        if remaining:
            # Flags only. The trust score is deliberately left alone: scoring the
            # remaining flags would need the findings behind them, which a scan
            # of a different target never produced, and a flag string carries no
            # severity to reconstruct one from. The score stays as the last scan
            # that did see those findings left it, which errs low (it still
            # deducts for what was just resolved) and self-corrects the next time
            # this model is scanned directly. Erring low is the safe direction:
            # it never advertises a failing model as healthy.
            assign_properties(conn, urn, {RISK_FLAGS: sorted(remaining), RUN_ID: [run_id]})
            continue
        remove_properties(conn, urn, {RISK_FLAGS})
        remove_tag(conn, urn, f"urn:li:tag:{config.model_at_risk_tag}")
        score = trust_score(trust_inputs_from_findings((), model), config)
        assign_properties(
            conn,
            urn,
            {
                TRUST_SCORE: [float(score.value)],
                TRUST_BAND: [str(score.band)],
                RUN_ID: [run_id],
            },
        )


def _log_scan(
    run_id: str,
    *,
    table_urn: str | None,
    model_urn: str | None,
    dry_run: bool,
    findings: int,
    warnings: int,
    writes: int,
    detect_seconds: float,
    total_seconds: float,
) -> None:
    """Emit one machine-readable line summarizing a completed scan.

    The facts are assembled once and rendered twice: as ``key=value`` in the
    message, which stays readable in a terminal tailing ``modelguard watch``, and
    as structured fields on the record, which :class:`~modelguard.logs.JsonFormatter`
    emits as object keys when the operator asked for JSON. One mapping feeds
    both, so the human line and the indexed fields cannot drift apart.

    ``detect_ms`` is the number an MTTD budget is actually built from: the rest
    of the delay between a table breaking and an incident existing belongs to
    the poll interval and to DataHub's own index convergence, neither of which
    this process controls, and folding them into one figure would hide which
    one moved. URNs are logged, values never are: everything here is an
    identifier or a count that a detector measured, and no aspect content, no
    prose, and no credential reaches this line.
    """
    fields = {
        "run_id": run_id,
        "table": table_urn or "-",
        "model": model_urn or "-",
        "dry_run": str(dry_run).lower(),
        "findings": findings,
        "writes": writes,
        "warnings": warnings,
        "detect_ms": round(detect_seconds * 1000),
        "total_ms": round(total_seconds * 1000),
    }
    logger.info("scan complete %s", logfmt(fields), extra={LOG_FIELDS: fields})


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

    # perf_counter, not the clock: this measures an elapsed duration, and the
    # wall clock can step sideways under NTP mid-scan.
    started = time.perf_counter()
    findings, warnings, gaps = _detect(conn, config, table_urn, model_urn, observed_at)
    detect_seconds = time.perf_counter() - started

    # Rendered either way: a dry run must be able to show the assertion it would
    # have written. Only meaningful for a table target.
    assertion_yaml = (
        render_assertion_yaml(table_urn, config.freshness_sla_hours, config.freshness_field)
        if table_urn is not None
        else ""
    )

    trust = _trust_scores(findings, config)

    if dry_run:
        _log_scan(
            run_id,
            table_urn=table_urn,
            model_urn=model_urn,
            dry_run=True,
            findings=len(findings),
            warnings=len(warnings),
            writes=0,
            detect_seconds=detect_seconds,
            total_seconds=time.perf_counter() - started,
        )
        return ScanReport(
            run_id=run_id,
            table_urn=table_urn,
            model_urn=model_urn,
            dry_run=True,
            writes=tuple(
                FindingWrites(finding=finding, narrative=narrate(finding, llm))
                for finding in findings
            ),
            trust=trust,
            assertion_yaml=assertion_yaml,
            warnings=tuple(warnings),
            not_evaluated=gaps,
        )

    writes = tuple(
        _write_back(conn, finding, narrate(finding, llm), config, run_id, observed_at)
        for finding in findings
    )
    # After every per-finding write, so each model's risk flags are already on the
    # graph when the trust score reads and merges alongside them.
    _persist_trust(conn, trust)

    # After this run's own writes, so reconciliation reads risk flags that
    # already include anything just raised above, and never fights the write
    # it is standing next to.
    _reconcile_stale_findings(
        conn,
        config,
        table_urn=table_urn,
        model_urn=model_urn,
        findings=findings,
        run_id=run_id,
        observed_at=observed_at,
    )

    _log_scan(
        run_id,
        table_urn=table_urn,
        model_urn=model_urn,
        dry_run=False,
        findings=len(findings),
        warnings=len(warnings),
        writes=len(writes),
        detect_seconds=detect_seconds,
        total_seconds=time.perf_counter() - started,
    )

    return ScanReport(
        run_id=run_id,
        table_urn=table_urn,
        model_urn=model_urn,
        dry_run=False,
        writes=writes,
        trust=trust,
        assertion_yaml=written_assertion_yaml(writes),
        warnings=tuple(warnings),
        not_evaluated=gaps,
    )
