"""Tunable scan parameters, in one place.

Hop caps, thresholds, and label names are configuration, never literals buried in
a detector (modelguard/CLAUDE.md rule 3). Keeping them here means the benchmark
can sweep them and a reviewer can see the whole contract at once.

These values *do* have defaults, and that is deliberate. A six hour freshness SLA
and a three hop cap are documented product decisions: they behave identically on
every machine, and a reader can check them against this file. They identify no
system, no account, and no vendor. Contrast :mod:`modelguard.llm` and
:mod:`modelguard.client`, whose values identify exactly those things and
therefore have no defaults at all (see :mod:`modelguard.env`).

Overrides arrive through ``MODELGUARD_*`` variables, read via
:mod:`modelguard.env` so that ``.env`` is loaded first. Reading ``os.environ``
directly here was a bug: ``scan`` builds its config before it connects, so a
threshold set in ``.env`` was ignored unless something else had already loaded
the file (D-029).
"""

from __future__ import annotations

from dataclasses import dataclass

from modelguard.env import ConfigError, optional_float, optional_int

__all__ = [
    "ENV_FRESHNESS_SLA_HOURS",
    "ENV_LINEAGE_RESULT_CAP",
    "ENV_MAX_HOPS",
    "ConfigError",
    "ScanConfig",
]

ENV_FRESHNESS_SLA_HOURS = "MODELGUARD_FRESHNESS_SLA_HOURS"
ENV_MAX_HOPS = "MODELGUARD_MAX_HOPS"
ENV_LINEAGE_RESULT_CAP = "MODELGUARD_LINEAGE_RESULT_CAP"


@dataclass(frozen=True)
class ScanConfig:
    """Everything one ``modelguard scan`` needs to know beyond the graph itself."""

    freshness_sla_hours: float = 6.0
    """How long a table may go unchanged before it counts as stale."""

    max_hops: int = 3
    """Lineage hops to traverse downstream of a failing table.

    Three is the shortest path that reaches a model in the seeded graph:
    dataset -> dataset (1) -> mlFeature (2) -> mlModel (3). Above two hops
    DataHub switches to a full-graph search and can return results *beyond* the
    cap, so the detector filters the results by hop count as well.
    """

    lineage_result_cap: int = 500
    """Maximum lineage results to request. Matches the SDK's own default."""

    model_at_risk_tag: str = "model-at-risk"
    """Tag attached to every model downstream of a failing table."""

    freshness_field: str = "updated_at"
    """The column a warehouse-side executor would use to evaluate freshness.

    Recorded in the emitted open-assertions YAML. ModelGuard itself evaluates
    freshness from DataHub's operation aspect, not by querying this column.
    """

    label_term_urn: str = "urn:li:glossaryTerm:modelguard.label"
    """The glossary term that declares a column to be a model's label.

    A column carrying this term is ground truth: any feature whose upstream column
    lineage reaches it is target leakage. This is a *name*, not a credential or an
    endpoint, and it is identical on every machine, so it has a default (D-029).
    Point it at your own term if your organization already has one for labels, and
    the leakage detector will honor that instead.
    """

    leakage_max_hops: int = 6
    """Upstream column hops to traverse when hunting for a label.

    Higher than the downstream cap of 3, and deliberately so. Downstream, the
    traversal is looking for models and stops as soon as it has them. Upstream, a
    feature can be many derivations deep in a warehouse (raw to staging to mart to
    feature table), and a leak that is four joins back is still a leak. Missing one
    is a false negative on the failure ModelGuard exists to catch.
    """

    leakage_risk_term_urn: str = "urn:li:glossaryTerm:modelguard.leakage-risk"
    """The term ModelGuard attaches to a feature it proved leaks."""

    @classmethod
    def from_env(cls) -> ScanConfig:
        """Build a config, applying any ``MODELGUARD_*`` overrides from ``.env``.

        Raises:
            ConfigError: An override is set to something unusable. A typo in a
                threshold must not silently restore the default.
        """
        defaults = cls()
        return cls(
            freshness_sla_hours=optional_float(
                ENV_FRESHNESS_SLA_HOURS, defaults.freshness_sla_hours
            ),
            max_hops=optional_int(ENV_MAX_HOPS, defaults.max_hops),
            lineage_result_cap=optional_int(ENV_LINEAGE_RESULT_CAP, defaults.lineage_result_cap),
        )
