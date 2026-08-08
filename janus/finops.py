"""Which pipelines exist only to feed a model nobody uses.

Every other module here answers a question an engineer has. This one answers a
question a budget holder has, from facts already walked:

    These 6 tables exist only to feed churn_model_v2, which has no live
    deployment and nothing has touched in 90 days.

That is money, it is computed entirely from lineage the blast-radius traversal
already crosses, and it reaches a different reader than an incident does.

Why this is a report and not a finding
--------------------------------------
Nothing is broken. A model nobody deployed is not a defect, it is a decision
somebody may not have noticed they made, and raising an incident about it would
put a cost question into the queue where correctness failures live. So there is
no ``FindingType`` here, no incident, no severity and no trust-score deduction:
:func:`report` reads the graph and returns what it read.

What "unused" means, and what it deliberately does not
------------------------------------------------------
Two facts together, and both have to be positive evidence (docs/04-detectors.md
rule 5, which is not a detector rule so much as this project's whole posture):

1. The model has no deployment in service. That read already exists, because it
   is what separates a production incident from a training-time concern.
2. Something recorded a timestamp on the model, and it is older than
   ``unused_model_days``.

A model with **no** timestamp is reported as *undated*, never as unused. DataHub
records what somebody's ingestion put there, and a catalog that never stamped a
model is a catalog that cannot say when it was last touched. Calling that model
abandoned, in a report whose whole purpose is to suggest deleting things, is the
single most expensive mistake this module could make.

And a table is a candidate only when **every** model downstream of it is unused.
One live consumer and it is not a saving, it is a table somebody needs, so the
intersection is over the model set and never over one model at a time.

The model set comes from :mod:`janus.discovery`
----------------------------------------------------
Not from search. GMS hides non-latest versions of a versioned model, and
a hidden version is exactly the live consumer that would make this report
recommend deleting a table something still reads. The one place that would be
catastrophic rather than merely wrong is here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from datahub.metadata.schema_classes import MLModelPropertiesClass
from datahub.metadata.urns import DataPlatformUrn, DatasetUrn

from janus.client import DataHubConnection
from janus.config import ScanConfig
from janus.detect.blast_radius import upstream_datasets
from janus.detect.governance import model_input_datasets
from janus.detect.graph_reads import model_ref
from janus.models import ModelRef

_DAY_MS = 86_400_000


@dataclass(frozen=True)
class ModelUsage:
    """One model, and the evidence for whether anything still uses it."""

    model: ModelRef
    last_touched_ms: int | None
    """The most recent timestamp anything recorded on the model, or None when
    nothing did. None is the reason this type exists rather than a boolean."""

    def idle_days(self, now_ms: int) -> float | None:
        """How long since anything touched it, or None when that is unknown."""
        if self.last_touched_ms is None:
            return None
        return max(now_ms - self.last_touched_ms, 0) / _DAY_MS

    def is_unused(self, config: ScanConfig, now_ms: int) -> bool:
        """Whether this model has no live deployment and has gone quiet.

        Both halves, and the timestamp half must be a measurement. A model with
        no live deployment and no recorded date is undated, not unused.
        """
        idle = self.idle_days(now_ms)
        return (
            not self.model.live_deployments and idle is not None and idle > config.unused_model_days
        )

    @property
    def undated(self) -> bool:
        """Whether nothing in the catalog says when this model was last touched."""
        return self.last_touched_ms is None


@dataclass(frozen=True)
class OrphanedInput:
    """One table whose every downstream model has gone unused."""

    dataset_urn: str
    dataset_name: str
    platform: str
    """The platform the dataset lives on, short (``dbt``, ``postgres``).

    Carried because the name alone is ambiguous and the ambiguity is the normal
    case, not an edge one: a dbt project modelling a postgres warehouse produces
    two datasets with the identical name on two platforms, and a retirement list
    naming the same table twice with no way to tell them apart is a list nobody
    can act on."""

    models: tuple[ModelRef, ...]
    """The unused models it feeds, sorted by name. Never empty: a table with no
    downstream model at all is not this report's business, because nothing here
    walked far enough to know what else reads it."""

    def describe(self) -> str:
        """One line for a console."""
        consumers = ", ".join(model.name for model in self.models)
        return f"{self.dataset_name} ({self.platform})  feeds only: {consumers}"


@dataclass(frozen=True)
class FinOpsReport:
    """What a sweep found, including what it could not judge."""

    window_days: int
    examined: int
    unused: tuple[ModelUsage, ...]
    undated: tuple[ModelUsage, ...]
    """Models with no live deployment and no recorded timestamp. Reported
    separately and never folded into ``unused``: this is the list somebody has to
    look at by hand, and hiding it inside a saving would be a recommendation
    built on an absence."""
    candidates: tuple[OrphanedInput, ...]

    @property
    def has_advice(self) -> bool:
        """Whether anything here is worth a reader's attention."""
        return bool(self.candidates or self.undated)


def _last_touched_ms(properties: MLModelPropertiesClass | None) -> int | None:
    """The newest timestamp the model's own aspect carries, or None.

    ``lastModified`` first, then ``created``, then ``date``, which is the order
    of how much each one actually tells you about recent use. None when the
    aspect carries none of them, which is what DataHub's mlflow source leaves
    behind on a model registered without them.
    """
    if properties is None:
        return None
    stamps = [
        properties.lastModified.time if properties.lastModified else None,
        properties.created.time if properties.created else None,
        properties.date,
    ]
    known = [stamp for stamp in stamps if stamp is not None]
    return max(known) if known else None


def model_usage(conn: DataHubConnection, model_urn: str) -> ModelUsage:
    """Read one model's deployment state and its last recorded timestamp."""
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    return ModelUsage(
        model=model_ref(conn, model_urn, properties=properties),
        last_touched_ms=_last_touched_ms(properties),
    )


def _feeding_datasets(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urn: str,
) -> tuple[str, ...]:
    """Every dataset upstream of a model, its own training inputs included.

    Two hops of provenance rather than one: the inputs the training run recorded,
    plus what those inputs are derived from, which is where the staging and raw
    tables that exist for this model and nothing else actually live. Bounded by
    the same hop cap the downstream traversal uses.
    """
    inputs = model_input_datasets(conn, model_urn)
    found = set(inputs)
    for dataset_urn in inputs:
        found.update(upstream_datasets(conn, dataset_urn, config))
    return tuple(sorted(found))


def report(
    conn: DataHubConnection,
    config: ScanConfig,
    model_urns: Iterable[str],
    *,
    now_ms: int,
) -> FinOpsReport:
    """Return the tables that exist only to feed models nothing uses.

    Args:
        conn: An open connection.
        config: Supplies the staleness window and the hop cap.
        model_urns: Every model in the catalog, from
            :func:`~janus.discovery.search_model_urns`. Passing a filtered
            list is the one way to make this report dangerous: a model left out
            is a live consumer this cannot see, and its table would be listed as
            an orphan.
        now_ms: The instant to measure staleness against. Injected so a test can
            fix it, like every other clock in this package.

    Returns:
        The report. ``candidates`` is empty when every table upstream of an
        unused model also feeds a live one, which is the common and correct
        answer on a warehouse where marts are shared.
    """
    usages = [model_usage(conn, model_urn) for model_urn in model_urns]
    unused = tuple(usage for usage in usages if usage.is_unused(config, now_ms))
    undated = tuple(usage for usage in usages if usage.undated and not usage.model.live_deployments)

    # Which models each dataset feeds, over *every* model examined and not only
    # the unused ones. A dataset is a candidate exactly when its consumer set is
    # non-empty and disjoint from the models still in use, so the used ones have
    # to be walked too.
    consumers: dict[str, list[ModelUsage]] = {}
    for usage in usages:
        for dataset_urn in _feeding_datasets(conn, config, usage.model.urn):
            consumers.setdefault(dataset_urn, []).append(usage)

    unused_urns = {usage.model.urn for usage in unused}
    candidates = tuple(
        OrphanedInput(
            dataset_urn=dataset_urn,
            dataset_name=DatasetUrn.from_string(dataset_urn).name,
            platform=DataPlatformUrn.from_string(
                DatasetUrn.from_string(dataset_urn).platform
            ).platform_name,
            models=tuple(
                sorted(
                    (usage.model for usage in feeders),
                    key=lambda model: (model.name, model.urn),
                )
            ),
        )
        for dataset_urn, feeders in sorted(consumers.items())
        if feeders and all(usage.model.urn in unused_urns for usage in feeders)
    )

    return FinOpsReport(
        window_days=config.unused_model_days,
        examined=len(usages),
        unused=unused,
        undated=undated,
        candidates=candidates,
    )


def describe_undated(usages: Sequence[ModelUsage]) -> str:
    """One line naming the models nothing can be concluded about.

    Its own sentence rather than a footnote, because "we could not tell" is the
    answer most likely to be read as "there is nothing there".
    """
    names = ", ".join(usage.model.name for usage in usages)
    return (
        f"{len(usages)} model(s) have no live deployment and no recorded date, so "
        f"nothing here says whether they are still used: {names}. "
        "Record a lastModified on them at training time and this becomes an answer."
    )
