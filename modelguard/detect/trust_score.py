"""P4: roll every risk a scan found about a model into one 0-100 trust score.

The incidents, tags, and reports the other detectors write each describe one
problem. A person deciding whether to trust a model in production wants the whole
picture in a single number, the way a model card summarizes a model's caveats
(Mitchell et al. 2019). That is what the trust score is: a deterministic weighted
sum of the findings a scan produced about a model, plus whether anyone owns it.

Deterministic, like every detector. The score is a fixed function of the
findings; the LLM never touches it (modelguard/CLAUDE.md rule 5). The weights are
configuration, so the benchmark can sweep them and a reviewer can read the whole
contract in :mod:`modelguard.config`.

What feeds the score
--------------------
The inputs are derived from the findings the scan already produced, not from a
fresh graph traversal. A model is scored only against what this scan actually
checked: a scan that audited freshness and leakage but not schema drift cannot
deduct for drift it never looked for. This keeps the score honest about its own
evidence. The one exception is ownership, read straight off the model, because a
:class:`~modelguard.models.ModelRef` already carries it.

Literature
----------
Sculley et al., "Hidden Technical Debt in Machine Learning Systems" (NeurIPS
2015), argues that a model's real reliability is dominated by the data and
plumbing around it, not the model code. The trust score makes that surrounding
debt (stale inputs, leakage, drift, no owner) a single visible number.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from modelguard.config import ScanConfig
from modelguard.models import (
    DeprecatedInputFinding,
    Finding,
    FreshnessFinding,
    LeakageFinding,
    ModelRef,
    SchemaDriftFinding,
    SensitiveSourceFinding,
    Severity,
    TrustBand,
    TrustScore,
    severity_rank,
)

#: Severities a live-serving model's finding can carry that must never be
#: reported alongside a HEALTHY band. Points alone (trust_weight_leakage=20,
#: trust_weight_missing_owner=10) can land a critical, actively-lying leakage
#: finding at exactly 70, the healthy floor: gate correctly blocks it as
#: critical while the trust score would otherwise call it healthy, a
#: contradiction a judge can trivially reproduce by running scan and gate
#: back to back. MEDIUM (a non-live model's finding) is deliberately excluded:
#: nothing is currently lying to production traffic.
_SEVERITIES_THAT_CAP_HEALTHY = frozenset({Severity.CRITICAL, Severity.HIGH})

#: The names under which each deduction is recorded on the score, so a reader can
#: see exactly what cost the model its points. Stable strings: they appear in the
#: report and, being deterministic, are safe to assert on in tests.
DEDUCTION_UPSTREAM_FAILURE = "upstream_failure"
DEDUCTION_LEAKAGE = "leakage"
DEDUCTION_SCHEMA_DRIFT = "schema_drift"
DEDUCTION_FRESHNESS_LAG = "freshness_lag"
DEDUCTION_MISSING_OWNER = "missing_owner"
DEDUCTION_SENSITIVE_SOURCE = "sensitive_source"
DEDUCTION_DEPRECATED_INPUT = "deprecated_input"


@dataclass(frozen=True)
class TrustInputs:
    """The deterministic signals the trust score is computed from.

    Every field is derived from findings a scan produced, or read straight off the
    model. Nothing here is an LLM output or an estimate.
    """

    has_upstream_failure: bool
    """A failing upstream table (a freshness finding) endangers this model."""
    has_leakage: bool
    has_schema_drift: bool
    freshness_lag_ratio: float
    """Worst upstream lag over its SLA, clamped to [0, 1]. Zero when not checked."""
    missing_owner: bool
    worst_severity: Severity | None = None
    """The most severe finding rolled into this score, or None when there is
    none. Defaults to None so a directly-constructed TrustInputs (as the
    detector's own tests do for the freshness-only cases) does not have to
    name a severity it is not testing."""
    has_sensitive_source: bool = False
    """A feature derives from a column the organization classified as restricted.

    Defaulted, and placed after the fields that are not, both because a dataclass
    requires it and because it says the useful thing: a caller that predates this
    detector still means exactly what it meant.
    """
    has_deprecated_input: bool = False
    """A training input its own owners have marked deprecated."""


def trust_inputs_from_findings(
    findings: Iterable[Finding],
    model: ModelRef,
) -> TrustInputs:
    """Reduce the findings about one model into the trust score's inputs.

    Args:
        findings: The findings from this scan that name this model. A finding that
            endangers several models contributes to each of their scores.
        model: The model being scored, for its ownership.

    Returns:
        The aggregated inputs. The freshness lag ratio is the worst (largest) over
        every freshness finding, so two stale sources cannot cancel out.
    """
    has_upstream_failure = False
    has_leakage = False
    has_schema_drift = False
    has_sensitive_source = False
    has_deprecated_input = False
    lag_ratio = 0.0
    worst_severity: Severity | None = None

    for finding in findings:
        if worst_severity is None or severity_rank(finding.severity) < severity_rank(
            worst_severity
        ):
            worst_severity = finding.severity
        if isinstance(finding, FreshnessFinding):
            has_upstream_failure = True
            signal = finding.blast_radius.signal
            if signal.sla_hours > 0:
                lag_ratio = max(lag_ratio, min(signal.lag_hours / signal.sla_hours, 1.0))
        elif isinstance(finding, LeakageFinding):
            has_leakage = True
        elif isinstance(finding, SchemaDriftFinding):
            has_schema_drift = True
        elif isinstance(finding, SensitiveSourceFinding):
            has_sensitive_source = True
        elif isinstance(finding, DeprecatedInputFinding):
            has_deprecated_input = True

    return TrustInputs(
        has_upstream_failure=has_upstream_failure,
        has_leakage=has_leakage,
        has_schema_drift=has_schema_drift,
        has_sensitive_source=has_sensitive_source,
        has_deprecated_input=has_deprecated_input,
        freshness_lag_ratio=lag_ratio,
        missing_owner=not model.has_owner,
        worst_severity=worst_severity,
    )


def _band(value: int, config: ScanConfig) -> TrustBand:
    """Map a score to its reliability band, using the configured thresholds."""
    if value >= config.trust_band_healthy_min:
        return TrustBand.HEALTHY
    if value >= config.trust_band_watch_min:
        return TrustBand.WATCH
    return TrustBand.AT_RISK


def trust_score(inputs: TrustInputs, config: ScanConfig) -> TrustScore:
    """Compute a model's trust score from its aggregated risk inputs.

    Starts at 100 and subtracts each applicable weight. The freshness deduction is
    scaled by how far past its SLA the worst input is; the rest are all-or-nothing.
    The result is clamped to [0, 100] so an unusually harsh weight set cannot drive
    it negative.

    Args:
        inputs: The deterministic signals, from :func:`trust_inputs_from_findings`.
        config: Supplies the weights and the band thresholds.

    Returns:
        The score, its band, and the deductions that produced it. Only non-zero
        deductions are recorded, so the map reads as the reasons the model lost
        trust.
    """
    deductions: dict[str, float] = {}
    if inputs.has_upstream_failure:
        deductions[DEDUCTION_UPSTREAM_FAILURE] = config.trust_weight_upstream_failure
    if inputs.has_leakage:
        deductions[DEDUCTION_LEAKAGE] = config.trust_weight_leakage
    if inputs.has_schema_drift:
        deductions[DEDUCTION_SCHEMA_DRIFT] = config.trust_weight_schema_drift
    if inputs.has_sensitive_source:
        deductions[DEDUCTION_SENSITIVE_SOURCE] = config.trust_weight_sensitive_source
    if inputs.has_deprecated_input:
        deductions[DEDUCTION_DEPRECATED_INPUT] = config.trust_weight_deprecated_input
    if inputs.freshness_lag_ratio > 0:
        deductions[DEDUCTION_FRESHNESS_LAG] = (
            config.trust_weight_freshness_lag * inputs.freshness_lag_ratio
        )
    if inputs.missing_owner:
        deductions[DEDUCTION_MISSING_OWNER] = config.trust_weight_missing_owner

    value = round(max(0.0, min(100.0, 100.0 - sum(deductions.values()))))
    band = _band(value, config)

    # Points alone can leave a critical or high-severity finding inside the
    # healthy band (see _SEVERITIES_THAT_CAP_HEALTHY). gate's severity policy
    # and this band must never disagree about whether a model is fine to ship.
    if band is TrustBand.HEALTHY and inputs.worst_severity in _SEVERITIES_THAT_CAP_HEALTHY:
        band = TrustBand.WATCH

    return TrustScore(value=value, band=band, deductions=deductions)
