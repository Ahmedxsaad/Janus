"""P5/P6: is this model allowed to have learned from this data, and for how long?

The other detectors ask whether a model's data is *correct*: fresh, undrifted,
not contaminated by its own label. This one asks a question the organization has
already answered somewhere else in the graph, and that nothing joins back to the
model:

* **Sensitive source (P5).** Somebody classified a column as PII, PHI, a
  protected attribute, or whatever their taxonomy calls restricted. Three joins
  downstream, a feature derives from it, and a model trains on that feature.
  Nothing is broken. The model works. What is wrong is what it was allowed to
  see, and the derivation is far enough from the classified column that nobody
  looking at either end would notice.
* **Deprecated input (P6).** A table's own owners marked it deprecated, with a
  note and sometimes a decommission date. The people who set that flag have no
  way to know a model depends on the table, and the team that owns the model has
  no way to know the flag was set.

Both are failures of the same kind, and DataHub is the only place they are
findable: the classification and the deprecation live on the data side, the model
lives on the ML side, and the column-level lineage between them is what turns two
unrelated facts into one finding. Neither needs a training run, a query against
the warehouse, or a single row of data.

Why these are configured, and not on by default
-----------------------------------------------
The sensitive check does nothing until ``JANUS_SENSITIVE_TERM_URNS`` or
``JANUS_SENSITIVE_TAG_URNS`` names the organization's own classification.
There is no default worth shipping: every catalog uses its own taxonomy, so a
guess either matches nothing or matches a term that means something else
somewhere, and a false incident about a compliance exposure is the worst kind to
be wrong about. Unset means the check reports itself as not evaluated, never as
passed (see :mod:`janus.detect.coverage`).

The deprecation check needs no configuration, because ``deprecation`` is
DataHub's own aspect with one meaning everywhere.

Literature
----------
Sculley et al., "Hidden Technical Debt in Machine Learning Systems" (NeurIPS
2015), names undeclared consumers as a principal source of ML debt: a table
acquires consumers its owners never agreed to serve, and neither side can see
the dependency. A classified column feeding a model, and a deprecated table
feeding one, are both exactly that, made visible by reading the lineage the
catalog already holds.
"""

from __future__ import annotations

from datahub.metadata.schema_classes import (
    DataProcessInstanceInputClass,
    DeprecationClass,
    MLModelPropertiesClass,
)
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, SchemaFieldUrn

from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.detect.column_marks import ColumnMarkIndex, marked_ancestor, related_columns
from janus.detect.graph_reads import model_ref
from janus.detect.leakage import feature_source_column
from janus.models import (
    DeprecatedInputFinding,
    ModelRef,
    ProxyCandidate,
    ProxyCandidateFinding,
    SensitiveFeature,
    SensitiveSourceFinding,
)


def sensitive_index(conn: DataHubConnection, config: ScanConfig) -> ColumnMarkIndex:
    """Return the index of columns the organization classified as restricted.

    Terms and tags both, because catalogs classify through either surface and
    plenty use only one. An index built from an empty configuration reports
    itself as unconfigured rather than matching nothing quietly, so a caller can
    skip the traversal instead of walking a graph for a mark that cannot exist.
    """
    return ColumnMarkIndex(
        conn,
        terms=frozenset(config.sensitive_term_urns),
        tags=frozenset(config.sensitive_tag_urns),
    )


def protected_attribute_index(conn: DataHubConnection, config: ScanConfig) -> ColumnMarkIndex:
    """Return the index of columns the organization classified as protected.

    A separate index from :func:`sensitive_index`, over separate configuration,
    because "restricted" and "protected attribute" are different claims. See
    :attr:`~janus.config.ScanConfig.protected_attribute_term_urns`.
    """
    return ColumnMarkIndex(
        conn,
        terms=frozenset(config.protected_attribute_term_urns),
        tags=frozenset(config.protected_attribute_tag_urns),
    )


def classification_index(conn: DataHubConnection, config: ScanConfig) -> ColumnMarkIndex:
    """Return the union of every column classification this organization configured.

    Sensitive and protected-attribute are independently configured groups, and an
    org is free to set only one: nothing here requires both. A feature that
    directly descends from a column marked under *either* group is proved, not
    suggested, so :func:`sensitive_source_findings` walks this union rather than
    :func:`sensitive_index` alone. Without it, an org that classified a column
    only as a protected attribute got no finding at all for a feature built
    straight from it: :func:`proxy_candidate_findings` deliberately excludes that
    same direct descent, on the assumption that this function is the one that
    reports it (a fork is not a chain, and reporting the chain twice, once
    weakly and once proved, would dilute the proof).
    """
    return ColumnMarkIndex(
        conn,
        terms=frozenset(config.sensitive_term_urns)
        | frozenset(config.protected_attribute_term_urns),
        tags=frozenset(config.sensitive_tag_urns) | frozenset(config.protected_attribute_tag_urns),
    )


def proxy_candidate_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[ProxyCandidateFinding, ...]:
    """Return every feature that shares an ancestor with a protected attribute.

    The shape being looked for is a fork, not a chain::

        shared ancestor
           |        |
           v        v
        feature   protected attribute

    Both descend from one column, and neither descends from the other. That is
    how a proxy variable arises without anyone choosing it: postcode and race
    are not derived from each other, they are both derived from the person
    (Barocas and Selbst 2016, docs/plan/resources.md).

    Direct descent through either classification (see :func:`classification_index`)
    is deliberately excluded and left to :func:`sensitive_source_findings`, which
    proves it rather than suggesting it. Reporting both would raise two incidents
    about one column, the weaker of which would dilute the stronger.

    Deterministic and read-only. Nothing here decides that a feature *is* a
    proxy: see :class:`~janus.models.ProxyCandidateFinding`.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Supplies the protected-attribute classification and the proxy
            hop cap. Unconfigured means no walk at all, and coverage.py reports
            the check as never having run.

    Returns:
        One finding per (feature, protected attribute) pair, ordered by the
        feature's source column so a report reads identically on every run.
    """
    index = protected_attribute_index(conn, config)
    if not index.configured:
        return ()

    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.mlFeatures:
        return ()

    model = model_ref(conn, model_urn, properties=properties)
    hops = config.proxy_max_hops

    # sensitive_source_findings proves direct descent through the union of both
    # classification groups (classification_index), not this detector's
    # protected-attribute-only one: excluding on `index` (protected_attribute_index)
    # here would only skip a column this same index already caught, not one
    # sensitive_source_findings will catch, so a feature that derives directly
    # from a protected attribute nobody also tagged sensitive would clear this
    # detector's fork search on the exclusion below without being covered by
    # sensitive_source_findings either: zero findings for the strongest case,
    # direct use. Checking classification_index instead is what actually matches
    # the docstring's claim that direct descent is "left to" that detector.
    direct_descent_index = classification_index(conn, config)

    # Downstream walks are shared across features: two features of one model
    # very often derive from the same handful of raw columns, and re-walking a
    # shared ancestor once per feature would turn a fork into an N+1
    # (detect/CLAUDE.md rule 3).
    descendants: dict[str, dict[str, int]] = {}

    findings: list[ProxyCandidateFinding] = []
    for feature_urn in properties.mlFeatures:
        source_column = feature_source_column(conn, feature_urn)
        if source_column is None:
            continue

        # Direct descent is sensitive_source_findings' finding, not this one,
        # but only when that detector is actually configured to catch it.
        if direct_descent_index.configured and (
            marked_ancestor(conn, source_column, direct_descent_index, config).hit is not None
        ):
            continue

        ancestors = related_columns(
            conn, source_column, config, direction="upstream", max_hops=hops
        )
        for ancestor_urn, up_hops in sorted(ancestors.items()):
            if ancestor_urn not in descendants:
                descendants[ancestor_urn] = related_columns(
                    conn, ancestor_urn, config, direction="downstream", max_hops=hops
                )

            for protected_urn, down_hops in sorted(descendants[ancestor_urn].items()):
                if protected_urn in (source_column, ancestor_urn):
                    continue
                marker = index.marker(protected_urn)
                if marker is None:
                    continue
                findings.append(
                    _proxy_finding(
                        model,
                        feature_urn,
                        source_column,
                        protected_urn,
                        marker,
                        ancestor_urn,
                        up_hops,
                        down_hops,
                    )
                )

    return tuple(
        sorted(
            _first_per_pair(findings),
            key=lambda finding: (
                finding.candidate.source_column_name,
                finding.candidate.protected_column_name,
            ),
        )
    )


def _first_per_pair(
    findings: list[ProxyCandidateFinding],
) -> list[ProxyCandidateFinding]:
    """Keep the nearest shared ancestor for each (feature, protected) pair.

    Two columns that share one ancestor usually share that ancestor's ancestors
    too, so an unfiltered walk reports the same pair once per generation. The
    nearest is the one worth quoting: it is the column a human would recognise
    as the thing they have in common, and the further ones are consequences of
    it rather than separate findings.
    """
    nearest: dict[tuple[str, str], ProxyCandidateFinding] = {}
    for finding in findings:
        key = (finding.candidate.source_column_urn, finding.candidate.protected_column_urn)
        current = nearest.get(key)
        if current is None or _distance(finding) < _distance(current):
            nearest[key] = finding
    return list(nearest.values())


def _distance(finding: ProxyCandidateFinding) -> tuple[int, str]:
    """Total hops through the shared ancestor, ties broken on its URN."""
    candidate = finding.candidate
    return (candidate.feature_hops + candidate.protected_hops, candidate.ancestor_urn)


def _proxy_finding(
    model: ModelRef,
    feature_urn: str,
    source_column_urn: str,
    protected_column_urn: str,
    marker_urn: str,
    ancestor_urn: str,
    feature_hops: int,
    protected_hops: int,
) -> ProxyCandidateFinding:
    """Assemble one candidate from the fork the traversal actually walked."""
    source_field = SchemaFieldUrn.from_string(source_column_urn)
    protected_field = SchemaFieldUrn.from_string(protected_column_urn)
    ancestor_field = SchemaFieldUrn.from_string(ancestor_urn)

    return ProxyCandidateFinding(
        model=model,
        candidate=ProxyCandidate(
            feature_urn=feature_urn,
            feature_name=MlFeatureUrn.from_string(feature_urn).name,
            source_column_urn=source_column_urn,
            source_column_name=source_field.field_path,
            protected_column_urn=protected_column_urn,
            protected_column_name=protected_field.field_path,
            protected_dataset_name=DatasetUrn.from_string(protected_field.parent).name,
            marker_urn=marker_urn,
            ancestor_urn=ancestor_urn,
            ancestor_name=ancestor_field.field_path,
            ancestor_dataset_name=DatasetUrn.from_string(ancestor_field.parent).name,
            feature_hops=feature_hops,
            protected_hops=protected_hops,
        ),
    )


def sensitive_source_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[SensitiveSourceFinding, ...]:
    """Return every feature of a model that derives from a classified column.

    "Classified" is the union of both classification groups (see
    :func:`classification_index`): a column marked sensitive or marked a
    protected attribute is proved exposure either way, and this is the only
    detector that reports proved descent rather than a candidate for review.

    Deterministic and read-only. The LLM is never asked whether a feature is
    exposed; it is told that one is and writes the prose around it.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Supplies the classification URNs and the hop cap.

    Returns:
        One finding per exposed feature, ordered by the exposed column's name so
        a report reads identically on every run. Empty when no feature's lineage
        reaches a classified column, and also empty when nothing is configured to
        look for, which coverage reports separately as a check that never ran.
    """
    index = classification_index(conn, config)
    if not index.configured:
        return ()

    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.mlFeatures:
        return ()

    model = model_ref(conn, model_urn, properties=properties)

    findings: list[SensitiveSourceFinding] = []
    for feature_urn in properties.mlFeatures:
        source_column = feature_source_column(conn, feature_urn)
        if source_column is None:
            continue

        walk = marked_ancestor(conn, source_column, index, config)
        if walk.hit is None:
            continue

        sensitive_urn, marker_urn, column_path = walk.hit
        findings.append(
            _sensitive_finding(
                model,
                feature_urn,
                source_column,
                sensitive_urn,
                marker_urn,
                column_path,
                walk.others,
            )
        )

    return tuple(sorted(findings, key=lambda finding: finding.exposure.source_column_name))


def _sensitive_finding(
    model: ModelRef,
    feature_urn: str,
    source_column_urn: str,
    sensitive_column_urn: str,
    marker_urn: str,
    column_path: tuple[str, ...],
    other_paths: tuple[tuple[str, ...], ...] = (),
) -> SensitiveSourceFinding:
    """Assemble one finding from the columns the traversal actually proved."""
    source_field = SchemaFieldUrn.from_string(source_column_urn)
    sensitive_field = SchemaFieldUrn.from_string(sensitive_column_urn)

    return SensitiveSourceFinding(
        model=model,
        exposure=SensitiveFeature(
            feature_urn=feature_urn,
            feature_name=MlFeatureUrn.from_string(feature_urn).name,
            source_column_urn=source_column_urn,
            source_column_name=source_field.field_path,
            sensitive_column_urn=sensitive_column_urn,
            sensitive_column_name=sensitive_field.field_path,
            sensitive_dataset_name=DatasetUrn.from_string(sensitive_field.parent).name,
            marker_urn=marker_urn,
            column_path=column_path,
            other_paths=other_paths,
        ),
    )


def model_input_datasets(conn: DataHubConnection, model_urn: str) -> tuple[str, ...]:
    """Return the datasets a model's training runs read, deduplicated and ordered.

    The join between a model and its data on the run side rather than the feature
    side: ``dataProcessInstanceInput`` on each training run. A model trained more
    than once, or on more than one table, contributes each input once.
    """
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    if properties is None or not properties.trainingJobs:
        return ()

    seen: dict[str, None] = {}
    for run_urn in properties.trainingJobs:
        inputs = conn.graph.get_aspect(run_urn, DataProcessInstanceInputClass)
        for dataset_urn in (inputs.inputs or []) if inputs else []:
            seen.setdefault(dataset_urn, None)
    return tuple(sorted(seen))


def deprecated_input_findings(
    conn: DataHubConnection,
    model_urn: str,
    config: ScanConfig,
) -> tuple[DeprecatedInputFinding, ...]:
    """Return every training input its owners have marked deprecated.

    Args:
        conn: An open connection.
        model_urn: The model to audit.
        config: Unused today; taken so this detector has the same signature as
            its siblings and `_detect` calls all of them the same way.

    Returns:
        One finding per deprecated input, ordered by dataset name. Empty when no
        input carries the aspect, or carries it with ``deprecated=False``, which
        is DataHub's way of recording that a deprecation was lifted and is
        positive evidence of health rather than absence of evidence.
    """
    del config

    input_urns = model_input_datasets(conn, model_urn)
    if not input_urns:
        return ()

    model = model_ref(conn, model_urn)

    findings: list[DeprecatedInputFinding] = []
    for dataset_urn in input_urns:
        deprecation = conn.graph.get_aspect(dataset_urn, DeprecationClass)
        if deprecation is None or not deprecation.deprecated:
            continue
        findings.append(
            DeprecatedInputFinding(
                model=model,
                dataset_urn=dataset_urn,
                dataset_name=DatasetUrn.from_string(dataset_urn).name,
                note=deprecation.note or "",
                decommission_time_ms=deprecation.decommissionTime,
            )
        )

    return tuple(sorted(findings, key=lambda finding: finding.dataset_name))
