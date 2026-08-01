"""What a scan could not check, and why.

A detector that returns nothing is saying one of two very different things: "I
checked, and it is clean", or "I had nothing to check with". Collapsing those
two into one green line is the single most misleading thing a reliability tool
can do, and on a real catalog the second case is the common one: most tables
carry no ``operation`` aspect, most models declare no features, and almost no
training run carries a schema snapshot until somebody sets that up. A user who
reads "healthy" over a table nothing has ever instrumented has been told a fact
that was never measured.

So every detector's silence is explained here. This module runs only for the
checks that produced no finding (a finding is itself proof the check ran), asks
the graph the one cheap question that separates "clean" from "not evaluated",
and returns the gaps as data the CLI prints and the gate logs.

Read-only, like everything under detect/. It never changes a verdict: a gap is
not a finding, and nothing here can raise, escalate, or suppress one.
"""

from __future__ import annotations

from dataclasses import dataclass

from datahub.metadata.schema_classes import MLModelPropertiesClass

from modelguard.client import DataHubConnection
from modelguard.config import ScanConfig
from modelguard.detect.blast_radius import freshness_signal
from modelguard.detect.leakage import feature_source_column
from modelguard.detect.schema_drift import schema_drift_candidate_resources
from modelguard.models import (
    Finding,
    FreshnessFinding,
    LeakageFinding,
    SchemaDriftFinding,
)


@dataclass(frozen=True)
class Unevaluated:
    """One check that could not run, and what it would take to make it run."""

    check: str
    """The check's name as a user knows it: ``freshness``, ``target leakage``."""

    target_urn: str
    """The table or model the check was asked about."""

    reason: str
    """The metadata that is missing, named precisely enough to go and look."""

    remedy: str
    """The action that would make this check possible. Never a guess at intent."""

    def describe(self) -> str:
        """One line for a console or a CI log."""
        return f"{self.check} not evaluated: {self.reason}. {self.remedy}"


def _freshness_gap(
    conn: DataHubConnection,
    config: ScanConfig,
    table_urn: str,
) -> Unevaluated | None:
    """Whether the freshness check had a last-changed timestamp to measure."""
    if freshness_signal(conn, table_urn, config) is not None:
        return None
    return Unevaluated(
        check="freshness",
        target_urn=table_urn,
        reason=(
            "this dataset has no operation aspect, so DataHub holds no record of "
            "when it last changed and staleness cannot be measured"
        ),
        remedy=(
            "Emit an operation aspect from whatever writes the table (dbt, Airflow, "
            "Spark, or the DataHub SDK's report_operation)."
        ),
    )


def _leakage_gap(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
    properties: MLModelPropertiesClass | None,
) -> Unevaluated | None:
    """Whether the leakage check had features, lineage, and a label term to work with.

    Three things have to be present before a leakage answer means anything: the
    model has to declare its features, those features have to reach a source
    column through lineage, and some column has to be declared a label. Each
    absence gets its own reason, because each has a different remedy.
    """
    def gap(reason: str, remedy: str) -> Unevaluated:
        return Unevaluated(
            check="target leakage", target_urn=model_urn, reason=reason, remedy=remedy
        )

    if properties is None or not properties.mlFeatures:
        return gap(
            "the model declares no features (mlModelProperties.mlFeatures is empty), "
            "so there is no feature whose lineage could be traced back to a label",
            "Record the model's features as mlFeature entities and list them on the model.",
        )

    if not conn.graph.exists(config.label_term_urn):
        return gap(
            f"no glossary term {config.label_term_urn} exists in this DataHub, so no "
            "column can currently be declared a label",
            "Create that term and apply it to your label column, or point "
            "MODELGUARD_LABEL_TERM_URN at the term your organization already uses.",
        )

    if not any(feature_source_column(conn, urn) is not None for urn in properties.mlFeatures):
        return gap(
            "no feature of this model has column-level lineage to a source column, so "
            "there is no path to walk upstream",
            "Ingest column-level lineage for the feature tables (dbt and Spark sources "
            "emit it; the SDK's add_lineage accepts explicit column mappings).",
        )

    return None


def _drift_gap(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
    properties: MLModelPropertiesClass | None,
) -> Unevaluated | None:
    """Whether the drift check had a training-time schema to diff against."""
    if properties is None or not properties.trainingJobs:
        return Unevaluated(
            check="schema drift",
            target_urn=model_urn,
            reason=(
                "no training run is recorded on the model "
                "(mlModelProperties.trainingJobs is empty), so there is no run that "
                "could carry a training-time schema"
            ),
            remedy=(
                "Link the training run to the model, then capture its input schema "
                "with `modelguard snapshot`."
            ),
        )

    if not schema_drift_candidate_resources(conn, model_urn, config):
        return Unevaluated(
            check="schema drift",
            target_urn=model_urn,
            reason=(
                f"no training run of this model carries a {config.training_schema_property!r} "
                "snapshot, so there is no baseline to diff the current schema against"
            ),
            remedy="Capture one at training time with `modelguard snapshot`.",
        )

    return None


def coverage_gaps(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None,
    model_urn: str | None,
    findings: tuple[Finding, ...],
) -> tuple[Unevaluated, ...]:
    """Return every check this scan asked for but could not actually perform.

    Args:
        conn: An open connection.
        config: The same config the detectors ran with, so a gap names the label
            term and snapshot property actually in force, not the defaults.
        table_urn: The table target, if the scan had one.
        model_urn: The model target, if the scan had one.
        findings: What the detectors returned. A check that produced a finding
            self-evidently ran, and is skipped: this is also what keeps the extra
            reads off the path where something is already wrong.

    Returns:
        The gaps, table check first. Empty when every check the scan asked for
        had the metadata it needed, which is the answer that makes a clean scan
        mean something.
    """
    found = {type(finding) for finding in findings}
    gaps: list[Unevaluated | None] = []

    if table_urn is not None and FreshnessFinding not in found:
        gaps.append(_freshness_gap(conn, config, table_urn))

    if model_urn is not None:
        # One read, shared by both model checks, and only on the path where at
        # least one of them found nothing.
        needs_leakage = LeakageFinding not in found
        needs_drift = SchemaDriftFinding not in found
        properties = (
            conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
            if needs_leakage or needs_drift
            else None
        )
        if needs_leakage:
            gaps.append(_leakage_gap(conn, config, model_urn, properties))
        if needs_drift:
            gaps.append(_drift_gap(conn, config, model_urn, properties))

    return tuple(gap for gap in gaps if gap is not None)
