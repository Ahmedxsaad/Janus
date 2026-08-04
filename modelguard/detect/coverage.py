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
from modelguard.detect.column_marks import WalkResult, marked_ancestor
from modelguard.detect.governance import (
    model_input_datasets,
    protected_attribute_index,
    sensitive_index,
)
from modelguard.detect.leakage import feature_source_column, label_index
from modelguard.detect.schema_drift import schema_drift_candidate_resources
from modelguard.models import (
    DeprecatedInputFinding,
    Finding,
    FreshnessFinding,
    LeakageFinding,
    ProxyCandidateFinding,
    SchemaDriftFinding,
    SensitiveSourceFinding,
)
from modelguard.writeback.properties import FEATURE_TABLE, read_properties


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


#: Reason and remedy for a model whose link an ingestion run has since dropped.
#: Separate from "never linked", because the two look identical on the model
#: (mlFeatures is empty either way) and have nothing else in common: one is a
#: model nobody has set up, the other is a model that *was* set up and whose
#: checks stopped silently the night an ingest ran (D-074, F11). Only the second
#: one is fixed by replaying a command the graph already holds the arguments for.
_DELINKED = (
    "this model carries a recorded modelguard link but declares no features, "
    "which is what an ingestion run does to it: DataHub's mlflow source upserts "
    "the whole mlModelProperties aspect and drops the features `link` attached",
    "Replay it: `modelguard link --all`, and schedule that after every ingest "
    "(the modelguard-watch chart ships a CronJob for it).",
)


def was_linked(conn: DataHubConnection, model_urn: str) -> bool:
    """Whether a previous ``modelguard link`` described this model.

    Read off the structured properties ``link`` records, which ingestion does not
    touch, so this stays true across the ingest that wiped the features.
    """
    return bool(read_properties(conn, model_urn).get(FEATURE_TABLE))


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
        if was_linked(conn, model_urn):
            return gap(*_DELINKED)
        return gap(
            "the model declares no features (mlModelProperties.mlFeatures is empty), "
            "so there is no feature whose lineage could be traced back to a label",
            "Declare them with `modelguard link --model ... --features <table> "
            "--label-column <column>`, from the script that trains the model.",
        )

    if not conn.graph.exists(config.label_term_urn):
        return gap(
            f"no glossary term {config.label_term_urn} exists in this DataHub, so no "
            "column can currently be declared a label",
            "Create that term and apply it to your label column, or point "
            "MODELGUARD_LABEL_TERM_URN at the term your organization already uses.",
        )

    source_columns = [
        column
        for urn in properties.mlFeatures
        if (column := feature_source_column(conn, urn)) is not None
    ]
    if not source_columns:
        return gap(
            "no feature of this model has column-level lineage to a source column, so "
            "there is no path to walk upstream",
            "Ingest column-level lineage for the feature tables (dbt and Spark sources "
            "emit it; the SDK's add_lineage accepts explicit column mappings).",
        )

    # leakage_findings() already walked every one of these and found no leak,
    # which is why this function is being asked at all (module docstring): a
    # finding is proof the check ran. What it cannot tell on its own is whether
    # that "no leak" was a real answer or a cap cutting a walk short before it
    # reached a label (F1, docs/plan/07). Re-walking here is bounded by this
    # one model's own feature count, not the catalog, and only happens on the
    # already-uncommon path where a scan found nothing to report. Walked once
    # per column, not twice, so the two caps below share the same read.
    labels = label_index(conn, config)
    walks = [marked_ancestor(conn, column, labels, config) for column in source_columns]
    cap = _cap_reason(walks, config, len(source_columns), noun="a leak")
    if cap is not None:
        return gap(*cap)

    return None


def _cap_reason(
    walks: list[WalkResult], config: ScanConfig, feature_count: int, *, noun: str
) -> tuple[str, str] | None:
    """(reason, remedy) if a re-walk of a clean model's features saw either cap bind.

    The two caps have different remedies (T-09, F1): ``truncated`` means the
    walk may not have *seen* everything past ``MODELGUARD_LINEAGE_RESULT_CAP``
    results, ``hop_capped`` means it *saw* an ancestor and declined it for
    lying beyond ``MODELGUARD_LEAKAGE_MAX_HOPS`` hops. A model that hit both
    caps on different features names both, because either raise on its own
    is a specific, false claim that it was the only gap.
    """
    truncated = sum(1 for w in walks if w.truncated)
    hop_capped = sum(1 for w in walks if w.hop_capped)
    if not truncated and not hop_capped:
        return None

    reasons = []
    remedies = []
    if truncated:
        reasons.append(
            f"{truncated} of {feature_count} feature(s)' upstream lineage returned the "
            f"full {config.lineage_result_cap}-result cap, so the traversal may not have "
            "reached every ancestor"
        )
        remedies.append("raise MODELGUARD_LINEAGE_RESULT_CAP")
    if hop_capped:
        reasons.append(
            f"{hop_capped} of {feature_count} feature(s)' upstream lineage reached an "
            f"ancestor beyond the {config.leakage_max_hops}-hop cap, which the walk saw "
            "and excluded on distance alone"
        )
        remedies.append("raise MODELGUARD_LEAKAGE_MAX_HOPS")
    remedies.append("or narrow the scan to this model.")

    reason = f"{'; and '.join(reasons)}, so {noun} beyond either cap would be missed"
    remedy = ", ".join(remedies)
    return reason, remedy[0].upper() + remedy[1:]


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
                "Link the training run to the model in DataHub, then declare the "
                "model's inputs with `modelguard link`."
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
            remedy="Capture one at training time with `modelguard link`.",
        )

    return None


def _sensitive_gap(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
    properties: MLModelPropertiesClass | None,
) -> Unevaluated | None:
    """Whether the sensitive-source check had a classification to look for.

    Two ways it cannot run, and they have opposite remedies. Either nobody told
    ModelGuard what "sensitive" means in this organization, which is a
    configuration gap, or the model declares no features, which is the same
    missing join `link` fixes for leakage.
    """

    def gap(reason: str, remedy: str) -> Unevaluated:
        return Unevaluated(
            check="sensitive source", target_urn=model_urn, reason=reason, remedy=remedy
        )

    if not sensitive_index(conn, config).configured:
        return gap(
            "no classification is configured, so no column in this catalog counts as "
            "sensitive and there is nothing for the check to look for",
            "Set MODELGUARD_SENSITIVE_TERM_URNS or MODELGUARD_SENSITIVE_TAG_URNS to "
            "the glossary terms or tags your organization already classifies columns "
            "with (comma-separated URNs). Either one alone is enough.",
        )

    if properties is None or not properties.mlFeatures:
        if was_linked(conn, model_urn):
            return gap(*_DELINKED)
        return gap(
            "the model declares no features (mlModelProperties.mlFeatures is empty), "
            "so there is no feature whose lineage could be traced back to a "
            "classified column",
            "Declare them with `modelguard link --model ... --features <table> "
            "--label-column <column>`, from the script that trains the model.",
        )

    source_columns = [
        column
        for urn in properties.mlFeatures
        if (column := feature_source_column(conn, urn)) is not None
    ]
    if not source_columns:
        return gap(
            "no feature of this model has column-level lineage to a source column, so "
            "there is no path to walk upstream",
            "Ingest column-level lineage for the feature tables (dbt and Spark sources "
            "emit it; the SDK's add_lineage accepts explicit column mappings).",
        )

    # Same reasoning as _leakage_gap above: sensitive_source_findings() already
    # walked every one of these, and "no exposure found" cannot be told apart
    # from "the exposed ancestor was past a cap" without re-checking WalkResult
    # (F1, docs/plan/07). Walked once per column, shared by both caps below.
    index = sensitive_index(conn, config)
    walks = [marked_ancestor(conn, column, index, config) for column in source_columns]
    cap = _cap_reason(walks, config, len(source_columns), noun="an exposure")
    if cap is not None:
        return gap(*cap)

    return None


def _proxy_gap(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
    properties: MLModelPropertiesClass | None,
) -> Unevaluated | None:
    """Whether the proxy check had a protected-attribute classification to look for.

    Unset is the common case and it is reported, never passed over: a model
    nobody checked for proxying reads exactly like one that was checked and
    found clean, and of the two only the second is worth anything (T-11, and
    the same posture the sensitive-source gap takes).
    """

    def gap(reason: str, remedy: str) -> Unevaluated:
        return Unevaluated(
            check="proxy candidate", target_urn=model_urn, reason=reason, remedy=remedy
        )

    if not protected_attribute_index(conn, config).configured:
        return gap(
            "no protected attribute is configured, so no column in this catalog counts "
            "as one and there is nothing for the check to compare a feature against",
            "Set MODELGUARD_PROTECTED_ATTRIBUTE_TERM_URNS or "
            "MODELGUARD_PROTECTED_ATTRIBUTE_TAG_URNS to the terms or tags your "
            "organization already marks protected attributes with (comma-separated "
            "URNs). Either one alone is enough.",
        )

    if properties is None or not properties.mlFeatures:
        if was_linked(conn, model_urn):
            return gap(*_DELINKED)
        return gap(
            "the model declares no features (mlModelProperties.mlFeatures is empty), "
            "so there is no feature whose ancestry could be compared against a "
            "protected attribute's",
            "Declare them with `modelguard link --model ... --features <table> "
            "--label-column <column>`, from the script that trains the model.",
        )

    return None


def _deprecated_input_gap(
    conn: DataHubConnection,
    model_urn: str,
    properties: MLModelPropertiesClass | None,
) -> Unevaluated | None:
    """Whether the deprecation check had any training input to look at."""
    if properties is None or not properties.trainingJobs:
        return Unevaluated(
            check="deprecated input",
            target_urn=model_urn,
            reason=(
                "no training run is recorded on the model "
                "(mlModelProperties.trainingJobs is empty), so nothing names the "
                "tables this model was trained on"
            ),
            remedy=(
                "Link the training run to the model in DataHub, then declare the "
                "model's inputs with `modelguard link`."
            ),
        )

    if not model_input_datasets(conn, model_urn):
        return Unevaluated(
            check="deprecated input",
            target_urn=model_urn,
            reason=(
                "the model's training runs record no input datasets "
                "(dataProcessInstanceInput is empty), so there is no table whose "
                "deprecation could be read"
            ),
            remedy="Declare the model's inputs with `modelguard link`.",
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
        # One read, shared by every model check, and only on the path where at
        # least one of them found nothing.
        needs_leakage = LeakageFinding not in found
        needs_drift = SchemaDriftFinding not in found
        needs_sensitive = SensitiveSourceFinding not in found
        needs_deprecation = DeprecatedInputFinding not in found
        needs_proxy = ProxyCandidateFinding not in found
        properties = (
            conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
            if needs_leakage or needs_drift or needs_sensitive or needs_deprecation or needs_proxy
            else None
        )
        if needs_leakage:
            gaps.append(_leakage_gap(conn, config, model_urn, properties))
        if needs_drift:
            gaps.append(_drift_gap(conn, config, model_urn, properties))
        if needs_sensitive:
            gaps.append(_sensitive_gap(conn, config, model_urn, properties))
        if needs_deprecation:
            gaps.append(_deprecated_input_gap(conn, model_urn, properties))
        if needs_proxy:
            gaps.append(_proxy_gap(conn, config, model_urn, properties))

    return tuple(gap for gap in gaps if gap is not None)
