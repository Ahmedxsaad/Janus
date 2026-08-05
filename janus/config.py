"""Tunable scan parameters, in one place.

Hop caps, thresholds, and label names are configuration, never literals buried in
a detector (janus/CLAUDE.md rule 3). Keeping them here means the benchmark
can sweep them and a reviewer can see the whole contract at once.

These values *do* have defaults, and that is deliberate. A six hour freshness SLA
and a three hop cap are documented product decisions: they behave identically on
every machine, and a reader can check them against this file. They identify no
system, no account, and no vendor. Contrast :mod:`janus.llm` and
:mod:`janus.client`, whose values identify exactly those things and
therefore have no defaults at all (see :mod:`janus.env`).

Overrides arrive through ``JANUS_*`` variables, read via
:mod:`janus.env` so that ``.env`` is loaded first. Reading ``os.environ``
directly here was a bug: ``scan`` builds its config before it connects, so a
threshold set in ``.env`` was ignored unless something else had already loaded
the file (D-029).
"""

from __future__ import annotations

from dataclasses import dataclass

from janus.env import (
    ConfigError,
    optional_float,
    optional_int,
    optional_list,
    optional_value,
)

__all__ = [
    "ENV_COMPANION_ENTITY_CAP",
    "ENV_FRESHNESS_SLA_HOURS",
    "ENV_LABEL_COLUMN_NAMES",
    "ENV_LABEL_TERM_URN",
    "ENV_LEAKAGE_MAX_HOPS",
    "ENV_LINEAGE_RESULT_CAP",
    "ENV_MAX_HOPS",
    "ENV_PROTECTED_ATTRIBUTE_TAG_URNS",
    "ENV_PROTECTED_ATTRIBUTE_TERM_URNS",
    "ENV_PROXY_MAX_HOPS",
    "ENV_SENSITIVE_TAG_URNS",
    "ENV_SENSITIVE_TERM_URNS",
    "ENV_UNUSED_MODEL_DAYS",
    "SCORE_PROVENANCE",
    "SCORING_VERSION",
    "TABLE_LEVEL_PRECISION",
    "ConfigError",
    "ScanConfig",
]

#: The version of the scoring function, bumped whenever a trust weight, a band
#: boundary, or the set of findings that contribute to the score changes.
#:
#: A trust score is only comparable to another score computed the same way, and
#: this project has already broken that comparability once: D-079 added the two
#: governance detectors, which changed what every previously-scored model would
#: now score, and nothing recorded it. A trend that drops because a release added
#: a detector looks exactly like a trend that drops because somebody shipped a
#: bug. Stamped into every history entry so the first kind reads as a labelled
#: discontinuity instead (F7 step 1).
#:
#: Not a ``ScanConfig`` field: it is a fact about the code, not a knob. Nobody
#: overrides it from the environment, because a scan that lied about which
#: function produced its number would corrupt the history it is appended to.
#:
#: History: v1 was the original five deductions; v2 added sensitive source and
#: deprecated input (D-079).
SCORING_VERSION = 2

#: The sentence printed wherever a trust score is shown to a human (F7 step 4).
#:
#: A composite score with unjustified weights looks far more rigorous than it is,
#: and this one's weights are the plan's illustrative values. Saying so where the
#: number appears is the difference between a measurement and a ranking, and a
#: reader who knows which one they are holding will not calibrate a policy
#: against it.
SCORE_PROVENANCE = (
    "The weights behind this score are a stated preference ordering, not a "
    "calibrated model. Compare scores to each other, not to a threshold."
)

#: Measured precision of the table-level degraded mode, quoted wherever one of
#: its findings is shown (:class:`~janus.models.TableLevelRiskFinding`).
#:
#: Not a knob and not an estimate: it is the precision ``benchmarks/baselines.py``
#: measures for the table-level approach on the seeded graph, published in
#: benchmarks/RESULTS.md. ``benchmarks/run_bench.py`` compares this constant
#: against what it just measured on every run and says so in RESULTS.md, so a
#: stale number here is reported rather than quietly repeated.
#:
#: A fact about the code, like SCORING_VERSION above, so it is not overridable
#: from the environment: a tool that let an operator dial up the confidence it
#: claims for itself would be worse than one that claimed none.
TABLE_LEVEL_PRECISION = 0.25

ENV_FRESHNESS_SLA_HOURS = "JANUS_FRESHNESS_SLA_HOURS"
ENV_MAX_HOPS = "JANUS_MAX_HOPS"
ENV_LINEAGE_RESULT_CAP = "JANUS_LINEAGE_RESULT_CAP"
ENV_LEAKAGE_MAX_HOPS = "JANUS_LEAKAGE_MAX_HOPS"
ENV_PROXY_MAX_HOPS = "JANUS_PROXY_MAX_HOPS"
ENV_LABEL_TERM_URN = "JANUS_LABEL_TERM_URN"
ENV_LABEL_COLUMN_NAMES = "JANUS_LABEL_COLUMN_NAMES"
ENV_SENSITIVE_TERM_URNS = "JANUS_SENSITIVE_TERM_URNS"
ENV_SENSITIVE_TAG_URNS = "JANUS_SENSITIVE_TAG_URNS"
ENV_PROTECTED_ATTRIBUTE_TERM_URNS = "JANUS_PROTECTED_ATTRIBUTE_TERM_URNS"
ENV_PROTECTED_ATTRIBUTE_TAG_URNS = "JANUS_PROTECTED_ATTRIBUTE_TAG_URNS"
ENV_COMPANION_ENTITY_CAP = "JANUS_COMPANION_ENTITY_CAP"
ENV_UNUSED_MODEL_DAYS = "JANUS_UNUSED_MODEL_DAYS"


@dataclass(frozen=True)
class ScanConfig:
    """Everything one ``janus scan`` needs to know beyond the graph itself."""

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

    Recorded in the emitted open-assertions YAML. Janus itself evaluates
    freshness from DataHub's operation aspect, not by querying this column.
    """

    label_term_urn: str = "urn:li:glossaryTerm:janus.label"
    """The glossary term that declares a column to be a model's label.

    A column carrying this term is ground truth: any feature whose upstream column
    lineage reaches it is target leakage. This is a *name*, not a credential or an
    endpoint, so it has a default (D-029). The default term, though, exists only
    in a graph ``janus-seed`` built. Point ``JANUS_LABEL_TERM_URN`` at
    your own term if your organization already has one for labels, and the
    leakage detector will honor that instead.
    """

    leakage_max_hops: int = 6
    """Upstream column hops to traverse when hunting for a label.

    Higher than the downstream cap of 3, and deliberately so. Downstream, the
    traversal is looking for models and stops as soon as it has them. Upstream, a
    feature can be many derivations deep in a warehouse (raw to staging to mart to
    feature table), and a leak that is four joins back is still a leak. Missing one
    is a false negative on the failure Janus exists to catch.
    """

    leakage_risk_term_urn: str = "urn:li:glossaryTerm:janus.leakage-risk"
    """The term Janus attaches to a feature it proved leaks."""

    label_column_names: tuple[str, ...] = (
        "label",
        "target",
        "y",
        "outcome",
        "churned",
        "is_churn",
        "default_status",
        "converted",
        "fraud",
    )
    """Column names ``link --infer`` falls back on when nothing is declared.

    A guess, and reported as one: the proposal says which route found the label,
    and a name match is never written without a human confirming it. Names, not
    identity, so they carry a documented default; override the list when your
    organization has a convention of its own.
    """

    sensitive_term_urns: tuple[str, ...] = ()
    """Glossary terms that mark a column as one a model must not learn from.

    Empty by default, and that is not a threshold with a sensible middle value:
    every organization classifies its data under its own taxonomy, and a guessed
    default here would either match nothing (useless) or match a term that means
    something else in somebody's catalog (a false incident about compliance,
    which is the worst kind to be wrong about). Unset means the detector does not
    run, and a scan says so rather than reporting the model clean.
    """

    sensitive_tag_urns: tuple[str, ...] = ()
    """Tags that mark a column as one a model must not learn from.

    The same setting as :attr:`sensitive_term_urns` through DataHub's other
    classification surface. Either alone is a complete configuration: many
    catalogs classify with tags only, many with terms only. Both empty means the
    detector does not run.
    """

    protected_attribute_term_urns: tuple[str, ...] = ()
    """Glossary terms marking a column as a protected attribute (T-11).

    Separate from :attr:`sensitive_term_urns` on purpose, and not a synonym for
    it. "Restricted" is a handling rule: do not learn from this. "Protected
    attribute" is a legal category: race, sex, age, disability. A catalog often
    classifies the two with different taxonomies, and merging them would mean
    reporting every PII column as a proxy candidate, which is noise, or missing
    a protected attribute nobody marked restricted, which is the finding.

    Empty by default and no fallback, for the reason
    :attr:`sensitive_term_urns` has none, but more so: a guessed URN here would
    invent a claim about discrimination out of a catalog that never made one.
    Unset means the check reports itself not evaluated, never clean.
    """

    protected_attribute_tag_urns: tuple[str, ...] = ()
    """Tags marking a column as a protected attribute. See the terms above."""

    proxy_max_hops: int = 3
    """How far to look for the ancestor a feature and a protected attribute share.

    Lower than :attr:`leakage_max_hops`, and the asymmetry is the point. A leak
    four joins back is still a leak: the label's information is present in the
    feature either way. A *shared ancestor* four joins back is most of a
    warehouse, because everything descends from the same handful of raw tables
    eventually, and a proxy candidate that names half the catalog is worse than
    silence. Three hops keeps the claim to columns a human would recognise as
    related. An algorithm parameter rather than an identity, so it has a
    documented default (root rule 6b).
    """

    training_schema_property: str = "janus.training_schema"
    """The custom property on a training run holding the input schema fingerprint.

    A JSON object of ``field_path -> native_type`` captured when the model was
    trained, which the schema-drift detector diffs against the input dataset's
    current schema. A name, not a credential, so it has a default (D-029).
    """

    trust_weight_upstream_failure: float = 40.0
    """Points a model loses for sitting downstream of a failing upstream table.

    The trust-score weights are the plan's illustrative values (section 5.3). They
    are algorithm parameters, not identity, so they carry documented defaults and
    a benchmark can sweep them. An unowned, leaking, drifting model behind a live
    endpoint on a stale source loses every one of them and scores zero.
    """

    trust_weight_leakage: float = 20.0
    """Points lost for a target-leakage finding on the model."""

    trust_weight_schema_drift: float = 15.0
    """Points lost for an input-schema-drift finding on the model."""

    trust_weight_freshness_lag: float = 15.0
    """Points lost, scaled by lag/SLA capped at 1, for stale upstream data."""

    trust_weight_missing_owner: float = 10.0
    """Points lost when no one owns the model, so no one is on the hook to fix it."""

    trust_weight_sensitive_source: float = 15.0
    """Points lost for a feature derived from a column classified as restricted.

    Between drift (15) and leakage (20) on purpose. It is not a statement that
    the model's numbers are wrong, which is what leakage is, but it is a standing
    exposure that does not resolve itself and that somebody outside the ML team
    will eventually ask about.
    """

    trust_weight_deprecated_input: float = 5.0
    """Points lost for training on an input its own owners marked deprecated.

    The smallest weight here, deliberately. It is a signal about the future
    rather than a defect in the model today: nothing is wrong yet, and the table
    may keep working for months. It costs points because a model whose inputs are
    being retired is a model with an expiry date nobody has diarised.
    """

    trust_band_healthy_min: float = 70.0
    """At or above this score a model is healthy."""

    trust_band_watch_min: float = 40.0
    """At or above this (but below healthy) a model is on watch; below it, at risk."""

    unused_model_days: int = 90
    """How long a model may go untouched, with no live deployment, before the
    FinOps report calls the pipelines behind it candidates for retirement (T-18).

    Ninety days is a quarter, which is the period a budget holder already thinks
    in and the shortest window that survives a model retrained seasonally. An
    algorithm parameter and not identity, so it carries a documented default and
    is overridable (root rule 6b): a team shipping weekly will want it shorter,
    and a team with an annual regulatory model will want it much longer.

    It never decides anything on its own. A model with no recorded timestamp is
    reported as undated rather than as unused, because detect/CLAUDE.md rule 5
    holds here too: a model nobody instrumented is not a model nobody uses.
    """

    companion_entity_cap: int = 200
    """Most owned entities `janus companion` inspects in one poll.

    Not politeness: the companion asks three questions per entity, so an
    uncapped sweep of a large catalogue takes minutes and the dog ends up
    describing the past. A cap is a threshold, not identity, so it has a
    documented default and the poll says when it hit it rather than pretending
    it saw everything.
    """

    @classmethod
    def from_env(cls) -> ScanConfig:
        """Build a config, applying any ``JANUS_*`` overrides from ``.env``.

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
            leakage_max_hops=optional_int(ENV_LEAKAGE_MAX_HOPS, defaults.leakage_max_hops),
            # A term URN is a name, not an endpoint or a credential, so like the
            # thresholds above it keeps a default. It is overridable because the
            # default term only exists in a graph `janus-seed` built: on a
            # real catalog the organization already has its own term for a label,
            # and without this the detector could only ever look for a term that
            # is not there (D-074).
            label_term_urn=optional_value(ENV_LABEL_TERM_URN) or defaults.label_term_urn,
            # No default, unlike the label term above: see the field docstrings.
            # A guessed classification URN is either dead config or a false
            # compliance incident, so unset stays unset and the detector reports
            # itself as not evaluated.
            sensitive_term_urns=optional_list(ENV_SENSITIVE_TERM_URNS),
            sensitive_tag_urns=optional_list(ENV_SENSITIVE_TAG_URNS),
            protected_attribute_term_urns=optional_list(ENV_PROTECTED_ATTRIBUTE_TERM_URNS),
            protected_attribute_tag_urns=optional_list(ENV_PROTECTED_ATTRIBUTE_TAG_URNS),
            proxy_max_hops=optional_int(ENV_PROXY_MAX_HOPS, defaults.proxy_max_hops),
            label_column_names=optional_list(ENV_LABEL_COLUMN_NAMES) or defaults.label_column_names,
            companion_entity_cap=optional_int(
                ENV_COMPANION_ENTITY_CAP, defaults.companion_entity_cap
            ),
            unused_model_days=optional_int(ENV_UNUSED_MODEL_DAYS, defaults.unused_model_days),
        )
