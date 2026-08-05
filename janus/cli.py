"""The ``janus`` command line.

``scan`` audits one table and writes back what it endangers. ``watch`` is the
polling twin that shares the same core; it can later move to the Actions framework
without changing detection or write-back.

Table resolution
----------------
``--table`` accepts either a full dataset URN or a bare name such as
``loans_raw``. A bare name is resolved by searching the graph and requiring
exactly one dataset whose name matches or ends with it. Ambiguity is an error,
never a guess: silently auditing the wrong table would be worse than failing.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated

import typer
from datahub.metadata.schema_classes import MLModelPropertiesClass
from datahub.metadata.urns import DatasetUrn, MlFeatureUrn, MlModelUrn, SchemaFieldUrn, Urn
from datahub.sdk.search_filters import FilterDsl as F
from rich.console import Console

from janus import companion as companion_module
from janus.adapters import ADAPTERS, AdapterError, DeclaredLink, read_declaration
from janus.agent.pipeline import FindingWrites, ScanReport, new_run_id, run_scan
from janus.argos import events as argos_events
from janus.argos.producer import ArgosProducer
from janus.client import (
    DataHubConnection,
    DataHubConnectionError,
    connect,
    gms_token,
    is_local_gms,
)
from janus.config import SCORE_PROVENANCE, SCORING_VERSION, ScanConfig
from janus.detect.guard_coverage import CatalogCoverage, ModelCoverage, aggregate
from janus.discovery import search_model_urns
from janus.env import ConfigError, scrub
from janus.finops import describe_undated
from janus.finops import report as finops_report
from janus.gate import (
    EXIT_ERROR,
    GatePolicy,
    evaluate,
    github_annotations,
    summary,
)
from janus.lifecycle import mttr_by_type, read_lifecycles
from janus.llm import LLMConfig, llm_config_from_env
from janus.logs import LOG_FIELDS, configure_logging, logfmt
from janus.mcl import (
    ENV_KAFKA_BOOTSTRAP,
    ENV_KAFKA_GROUP_ID,
    ENV_SCHEMA_REGISTRY_URL,
    ChangeLog,
    MclConfig,
    MclEvent,
    mcl_config_from_env,
)
from janus.models import (
    Finding,
    FreshnessFinding,
    LeakageFinding,
    SchemaDriftFinding,
    Severity,
    TableLevelRiskFinding,
)
from janus.reconcile import consider
from janus.render import (
    OutputFormat,
    crosswalk_markdown,
    job_summary_markdown,
    report_json,
    write_job_summary,
)
from janus.telemetry import start_exporter
from janus.writeback.coverage_history import CoverageEntry, append_entry
from janus.writeback.feature_documents import (
    gather_feature,
    publish_feature_card,
    render_feature_card,
)
from janus.writeback.link import (
    LinkError,
    link_model,
    models_with_recorded_link,
    recorded_link,
)
from janus.writeback.link_infer import LinkProposal, declared_proposal, infer_link
from janus.writeback.model_documents import (
    gather,
    publish_evidence_pack,
    publish_model_card,
    render_evidence_pack,
    render_model_card,
)
from janus.writeback.process_instance import agent_flow_urn

app = typer.Typer(
    add_completion=False,
    help="A data-to-model reliability agent built on DataHub.",
    no_args_is_help=True,
    # Typer renders locals into its pretty traceback when this is on, and the
    # frames that build a connection hold the DataHub token, while the SDK's own
    # DatahubClientConfig prints the token in its repr (verified against
    # acryl-datahub 1.6.0.13). An unhandled error would then put a credential on
    # the terminal and into any CI log that captured it. Typer's default is
    # already False, which is exactly why this is written down: a security
    # property that holds because of somebody else's default is one upgrade away
    # from not holding.
    pretty_exceptions_show_locals=False,
)

# soft_wrap: URNs are long and must stay on one line to be copy-pasteable.
console = Console(soft_wrap=True)

# Where progress lines and warnings go when stdout is carrying machine-readable
# output. `--format json` promises a parseable document on stdout, and a single
# "Scanning loans_raw" or "No token set" printed alongside it breaks every
# consumer. Those lines are still worth having, so they move rather than vanish.
err_console = Console(stderr=True, soft_wrap=True)

# The SDK logs a paragraph about `max_hops` above 2 on every lineage query, and
# with no handler configured it lands on stderr through logging's last-resort
# handler: twice per scan, in the middle of a report a human is reading, and in
# every CI log `gate` writes. It is not about a mistake here. Above two hops
# DataHub switches to a full-graph search and can return results beyond the cap,
# which the detector already knows and already filters for (see
# ScanConfig.max_hops), so the one thing the message warns about is handled.
logging.getLogger("datahub.sdk.lineage_client").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

WATCH_MAX_BACKOFF_SECONDS = 300.0
#: Consecutive poll failures before the console line escalates from a routine
#: yellow retry notice to a red "the daemon is not working" one. A single
#: failure is the expected shape of GMS still starting up (the docstring on
#: janus-watch.service says so); a run of them in a row is not routine
#: and a wall of identical yellow lines is exactly the kind of thing an
#: operator stops reading (F12, docs/plan/07).
WATCH_FAILURE_ESCALATION_THRESHOLD = 3
#: Consecutive unchanged polls before Argos curls up and sleeps. Low enough that
#: a quiet catalogue looks quiet within a couple of minutes at the default
#: interval, high enough that one steady poll does not put the dog out.
WATCH_ASLEEP_AFTER_POLLS = 4


def safe_error(exc: BaseException) -> str:
    """Render an exception for the console with the DataHub token removed.

    For exceptions we did not raise ourselves. Janus's own errors name a
    variable and never its value, but an exception surfacing from the DataHub
    SDK or from ``requests`` may quote a request, a header, or a URL we handed
    the token to, and ``gate`` in particular prints straight into a CI log that
    the whole team can read (root CLAUDE.md rule 6d).
    """
    return scrub(str(exc), gms_token())


@app.callback()
def _main() -> None:
    """Group the subcommands under ``janus``.

    Typer promotes a lone command to the root of the app, so without this
    callback the tool would be invoked as ``janus --table ...`` and adding
    ``watch`` later would silently change ``scan``'s invocation.
    """


def _no_match_hint(conn: DataHubConnection) -> str:
    """The advice to append when a name matched nothing in the graph.

    ``janus-seed`` writes the demo ML supply chain. That is the right next
    step against a local Quickstart, where an empty graph usually means the demo
    has not been built yet, and exactly the wrong one against somebody's real
    catalog, where it would put demo datasets into production metadata. So the
    hint that names it is gated on the instance being local, and a remote
    instance gets the advice that actually applies there: how names are matched.
    """
    if is_local_gms(conn.gms_url):
        return "Pass a full URN, or seed the demo graph first with: janus-seed"
    return (
        "Pass a full URN. A bare name matches only the entity's full name or its "
        "last dotted segment, so a partial word never resolves."
    )


def _model_urns(conn: DataHubConnection, *, limit: int | None = None) -> tuple[str, ...]:
    """Return every mlModel in the graph, sorted, optionally capped.

    Sorted so a bulk run reads the same way twice and a caller can tell which
    models a cap left out. Older versions of a versioned model are included:
    GMS hides them from search, and a model this sweep cannot see is one that
    silently stops being checked (janus/discovery.py).
    """
    urns = list(search_model_urns(conn))
    return tuple(urns[:limit] if limit is not None else urns)


class TableResolutionError(ValueError):
    """The --table argument named zero, or more than one, dataset."""


def resolve_table(conn: DataHubConnection, table: str) -> str:
    """Turn a table name or URN into exactly one dataset URN.

    Args:
        conn: An open connection.
        table: A full dataset URN, or any dotted suffix of a dataset's name:
            ``loans_raw``, ``public.loans_raw`` or ``ecommerce.public.loans_raw``.

    Returns:
        The dataset URN.

    Raises:
        TableResolutionError: Nothing matched, or several datasets did.
    """
    if table.startswith("urn:li:dataset:"):
        # Parsing validates the shape before we build a scan around it.
        return str(DatasetUrn.from_string(table))

    matches: list[str] = []
    for urn in conn.client.search.get_urns(query=table, filter=F.entity_type("dataset")):
        parsed = Urn.from_string(str(urn))
        if not isinstance(parsed, DatasetUrn):
            continue
        name = parsed.name
        # Any dotted suffix, not only the last segment. A warehouse names a
        # relation `schema.table` and DataHub names the dataset for it
        # `database.schema.table`, so a declaration importing the first (a dbt
        # semantic model's node_relation, a Feast source's table) matched neither
        # form and resolved against nothing (T-14, on the ingested real project).
        # Ambiguity is still an error naming every candidate, so a suffix that
        # means two tables asks rather than picks.
        if name == table or name.endswith(f".{table}"):
            matches.append(str(urn))

    unique = sorted(set(matches))
    if not unique:
        raise TableResolutionError(f"no dataset named {table!r}. {_no_match_hint(conn)}")
    if len(unique) > 1:
        listed = "\n  ".join(unique)
        raise TableResolutionError(
            f"{table!r} matches {len(unique)} datasets; pass a full URN:\n  {listed}"
        )
    return unique[0]


class ModelResolutionError(ValueError):
    """The --model argument named zero, or more than one, model."""


def resolve_model(conn: DataHubConnection, model: str) -> str:
    """Turn a model name or URN into exactly one mlModel URN.

    Args:
        conn: An open connection.
        model: A full mlModel URN, or a name like ``credit_risk_v3``.

    Returns:
        The mlModel URN.

    Raises:
        ModelResolutionError: Nothing matched, or several models did.
    """
    if model.startswith("urn:li:mlModel:"):
        return str(MlModelUrn.from_string(model))

    matches: list[str] = []
    for urn in search_model_urns(conn, query=model):
        parsed = Urn.from_string(str(urn))
        if not isinstance(parsed, MlModelUrn):
            continue
        if parsed.name == model or parsed.name.split(".")[-1] == model:
            matches.append(str(urn))

    unique = sorted(set(matches))
    if not unique:
        raise ModelResolutionError(f"no model named {model!r}. {_no_match_hint(conn)}")
    if len(unique) > 1:
        listed = "\n  ".join(unique)
        raise ModelResolutionError(
            f"{model!r} matches {len(unique)} models; pass a full URN:\n  {listed}"
        )
    return unique[0]


def _resolve_llm(*, no_llm: bool, provider: str | None, model: str | None) -> LLMConfig | None:
    """Decide which language model, if any, this scan narrates with.

    ``--no-llm`` wins outright. Otherwise the environment supplies the provider,
    the model, and the key, and the two flags may override the first two. The key
    is never a flag: a credential on a command line lands in the shell history
    and in the process table.

    Raises:
        ConfigError: The LLM is partially configured, or the provider is unknown.
    """
    if no_llm:
        return None

    configured = llm_config_from_env()
    if configured is None:
        if provider or model:
            raise ConfigError(
                "--llm-provider and --llm-model override the environment, but the "
                "API key can only come from JANUS_LLM_API_KEY, which is not set"
            )
        return None

    return LLMConfig(
        provider=provider or configured.provider,
        model=model or configured.model,
        api_key=configured.api_key,
    )


def _print_finding(finding: Finding) -> None:
    """Render one finding's measured facts. Never the narrative: that comes after."""
    # Parentheses, not brackets, around a severity: rich would read "[critical]"
    # as a style tag and silently swallow it.
    console.print(f"[bold red]{finding.title}[/bold red]  (severity: {finding.severity})")

    if isinstance(finding, FreshnessFinding):
        radius = finding.blast_radius
        console.print(
            f"  stale for {radius.signal.lag_hours:.1f}h against a "
            f"{radius.signal.sla_hours:.1f}h SLA"
        )
        console.print(
            f"  blast radius: {len(radius.downstream_datasets)} dataset(s), "
            f"{len(radius.downstream_features)} feature(s), {len(radius.models)} model(s)"
        )
        for model in radius.models:
            serving = "[red]LIVE[/red]" if model.is_live else "not serving"
            console.print(f"    - {model.name} ({model.severity}) {serving}, {model.hops} hops")

    elif isinstance(finding, LeakageFinding):
        leak = finding.leak
        console.print(f"  feature      {leak.feature_name}")
        console.print(f"  leak path    {leak.path_text}")
        console.print(f"  label        {leak.label_dataset_name}.{leak.label_column_name}")
        serving = "[red]LIVE[/red]" if finding.model.is_live else "not serving"
        console.print(f"  model        {finding.model.name} {serving}")

    elif isinstance(finding, SchemaDriftFinding):
        console.print(f"  input        {finding.dataset_name}")
        for change in finding.changes:
            console.print(f"  drift        {change.describe()}")
        serving = "[red]LIVE[/red]" if finding.model.is_live else "not serving"
        console.print(f"  model        {finding.model.name} {serving}")

    elif isinstance(finding, TableLevelRiskFinding):
        console.print(f"  input        {finding.dataset_name} ({finding.risk})")
        for key, value in sorted(finding.measurement.items()):
            console.print(f"  {key.replace('_', ' '):<12} {value}", markup=False, highlight=False)
        serving = "[red]LIVE[/red]" if finding.model.is_live else "not serving"
        console.print(f"  model        {finding.model.name} {serving}")
        # In yellow, and before the remedies, because the sentence is the finding:
        # read without it this looks exactly like the column-level answer, which
        # is the one way the degraded mode can mislead somebody (T-07).
        console.print(f"  [yellow]{finding.mode_note}[/yellow]")

    # Printed for every finding type, including the two the branches above do not
    # detail, because it comes off the Finding contract rather than off a subclass.
    # markup=False: these sentences carry catalog names, and a column called
    # something like "[dim]" would otherwise be swallowed as a rich style tag.
    for line in finding.counterfactual.lines():
        console.print(f"  {line}".rstrip(), markup=False, highlight=False)


def _print_writes(write: FindingWrites) -> None:
    """Render what one finding actually mutated in the graph."""
    if write.incident is not None:
        verb = "raised" if write.incident.created else "reused (already open)"
        console.print(f"  incident         {verb}: {write.incident.urn}")
    if write.assertion is not None:
        verb = "created" if write.assertion.created else "updated"
        console.print(f"  assertion        {verb}: {write.assertion.urn}")
        console.print(f"  assertion result {write.assertion_result}")
    for feature_urn in write.termed_features:
        console.print(f"  leakage term     {feature_urn}")
    console.print(
        f"  tagged models    {len(write.tagged_models)} newly tagged "
        f"of {len(write.finding.models_at_risk)} at risk"
    )
    for document in write.documents:
        console.print(f"  impact report    {document.urn}")


def _print_not_evaluated(report: ScanReport) -> None:
    """Render the checks that could not run, and what each one needs.

    Printed on every scan, clean or not. A check that never ran is the thing a
    reader is most likely to mistake for a check that passed, and the mistake
    costs the most on somebody's real catalog, where a table with no operation
    aspect and a model with no declared features are the normal case rather than
    the exception.
    """
    for gap in report.not_evaluated:
        console.print(f"[yellow]not evaluated:[/yellow] {gap.check} on {gap.target_urn}")
        console.print(f"  reason  {gap.reason}")
        console.print(f"  to fix  {gap.remedy}")


def _print_clean(report: ScanReport) -> None:
    """Render a scan that found nothing: what passed, what never ran, any warnings."""
    targets = [urn for urn in (report.table_urn, report.model_urn) if urn is not None]
    # A table target buys one check (freshness); a model target buys two (target
    # leakage, schema drift). Every gap is one of those checks that never ran, so
    # the difference is how many actually measured something.
    asked = (1 if report.table_urn else 0) + (2 if report.model_urn else 0)
    ran = asked - len(report.not_evaluated)

    if ran == asked:
        console.print(f"[green]No finding.[/green] {' and '.join(targets)} healthy.")
    elif ran > 0:
        console.print(
            f"[green]No finding[/green] from the {ran} of {asked} checks that ran on "
            f"{' and '.join(targets)}."
        )
    else:
        # Nothing was measured, so nothing may be called healthy. Saying so is the
        # whole point: silence here means "no metadata to check with".
        console.print("[yellow]Nothing was evaluated.[/yellow] No check had the metadata it needs.")

    _print_not_evaluated(report)
    for warning in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def _print_findings_and_trust(report: ScanReport) -> None:
    """Render every finding, its assessment, the warnings, and the trust scores.

    The measured half of a report, shared by the plain ``scan`` output and the
    ``--review`` preview a human approves before anything is written.
    """
    for write in report.writes:
        _print_finding(write.finding)
        console.print(
            f"\n[dim]assessment ({write.narrative.source}):[/dim] {write.narrative.assessment}\n"
        )

    _print_not_evaluated(report)
    for warning in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")

    if report.trust:
        console.print("\n[bold]Trust scores:[/bold]")
        for trust in report.trust:
            # The waterfall, not the integer (F7). A reader who sees "45/100"
            # first has to go looking for what to do about it; a reader who sees
            # the deductions first is already reading the work list, and the
            # total arrives as their consequence.
            console.print(f"  {trust.model_name}: [bold]{trust.score.band}[/bold]")
            for line in trust.score.waterfall():
                console.print(f"    [dim]{line}[/dim]")
            # The direction, when there is one to report. A score on its own is
            # not actionable: 82 matters against the 95 it was last week, and
            # that is the number a reader is looking for here.
            if trust.previous_score is not None:
                change = trust.score.value - trust.previous_score
                arrow = (
                    "unchanged from" if change == 0 else ("up from" if change > 0 else "DOWN from")
                )
                colour = "yellow" if change < 0 else "dim"
                console.print(
                    f"    [{colour}]{arrow} {trust.previous_score}/100 last scan[/{colour}]"
                )
        console.print(f"  [dim]scoring v{SCORING_VERSION}. {SCORE_PROVENANCE}[/dim]")


def _print_writes_section(report: ScanReport) -> None:
    """Render what a scan actually mutated in the graph, and its run id."""
    console.print("\n[bold]Wrote back:[/bold]")
    for write in report.writes:
        _print_writes(write)
    console.print(f"\n[dim]run id: {report.run_id}[/dim]")


def _print_report(report: ScanReport) -> None:
    """Render a scan's outcome for a human reading a terminal."""
    if report.clean:
        _print_clean(report)
        return

    _print_findings_and_trust(report)

    if report.dry_run:
        console.print("\n[yellow]Dry run: nothing was written.[/yellow]")
        console.print("Would have raised the incident(s), tagged the models, written the")
        console.print("guarding assertion, the impact report(s), and the trust score(s).")
        return

    _print_writes_section(report)


def _prepare(
    *,
    table: str | None,
    model: str | None,
    sla_hours: float | None,
    no_llm: bool,
    llm_provider: str | None,
    llm_model: str | None,
    writes: bool,
    chatter: Console | None = None,
) -> tuple[DataHubConnection, ScanConfig, LLMConfig | None, str | None, str | None]:
    """Do the setup ``scan`` and ``watch`` share: config, LLM, connect, resolve.

    Both commands need the same five things before they can run a scan, and both
    fail the same way on the same mistakes. Keeping it in one place means a change
    to how a target is resolved or a credential is read cannot drift between them.

    Args:
        table: The ``--table`` argument: a dataset URN or a bare name, or None.
        model: The ``--model`` argument: an mlModel URN or a bare name, or None.
        sla_hours: A freshness SLA overriding the configured one, or None.
        no_llm: Skip the narrator entirely and use template prose.
        llm_provider: Override the configured provider, or None.
        llm_model: Override the configured model id, or None.
        writes: Whether this invocation can mutate the graph. Only the warning
            about running unauthenticated depends on it: a ``--dry-run`` scan and
            a plain ``gate`` write nothing, so telling their user that writes are
            unauthenticated describes a write that is never attempted.
        chatter: Where the unauthenticated-writes warning goes. Defaults to
            stdout, and a caller emitting JSON on stdout passes the stderr
            console instead, so the warning is still seen without landing in the
            middle of the document a program is parsing.

    Raises:
        typer.Exit: A target is missing, the config or LLM is unusable, DataHub is
            unreachable, or a name did not resolve. The message is already printed.
    """
    if table is None and model is None:
        console.print("[red]Nothing to scan: pass --table, --model, or both.[/red]")
        raise typer.Exit(code=2)

    try:
        config = ScanConfig.from_env()
        if sla_hours is not None:
            # replace(), not a fresh ScanConfig: a hand-listed constructor silently
            # drops any field added later, which is how a threshold stops working.
            config = replace(config, freshness_sla_hours=sla_hours)
        llm = _resolve_llm(no_llm=no_llm, provider=llm_provider, model=llm_model)
    except ConfigError as exc:
        # A half-configured LLM, or an unusable threshold. Both are mistakes the
        # operator wants to hear about now, not after a bland report lands.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    try:
        # A token is not required: the OSS Quickstart ships with metadata service
        # authentication disabled and accepts unauthenticated writes. Demanding one
        # would break the judge's out-of-the-box path.
        conn = connect()
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        table_urn = resolve_table(conn, table) if table is not None else None
        model_urn = resolve_model(conn, model) if model is not None else None
    except (TableResolutionError, ModelResolutionError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if writes and not conn.has_token:
        (chatter or console).print(
            "[yellow]No DATAHUB_GMS_TOKEN set; writing unauthenticated.[/yellow]"
        )

    return conn, config, llm, table_urn, model_urn


FindingSignature = tuple[str, str, str, str]


def _finding_signature(report: ScanReport) -> frozenset[FindingSignature]:
    """Reduce a scan to the set of distinct problems it found.

    The first three fields are the incident dedup key
    ``(finding_type, resource_urn, title)``. The fourth is measured severity, so a
    deployment becoming live triggers a new write even though the incident key is
    unchanged. No timestamp or narrative enters the signature.
    """
    return frozenset(
        (
            str(finding.finding_type),
            finding.resource_urn,
            finding.title,
            str(finding.severity),
        )
        for finding in report.findings
    )


@dataclass
class WatchState:
    """In-process state used to skip a write when nothing has changed.

    Resolving a finding that recovered no longer depends on this: run_scan's
    own reconciliation is graph-driven, not a diff against a remembered
    report, so it works even on a process's very first poll.
    """

    signature: frozenset[FindingSignature] | None = None

    unchanged_polls: int = field(default=0)
    """Consecutive polls that found the same thing, which is what puts the dog
    to sleep. Counted here rather than in the pet, because a producer without a
    window still wants the state and the count is a property of the watch."""

    deferred_write: bool = field(default=False)
    """A change was seen while the window's mute was on, so its write is owed.
    Without it a mute would swallow the finding outright: the poll after the
    mute expires compares equal to the remembered signature and would stay
    quiet forever."""


def _announce_watch_change(
    previous: frozenset[FindingSignature] | None,
    signature: frozenset[FindingSignature],
    preview: ScanReport,
) -> None:
    """Say what changed since the last poll: a recovery, or new or changed findings."""
    stamp = time.strftime("%H:%M:%S")
    if not signature:
        # previous is None only on a process's first poll, where there is no
        # "since" to compare against: a target that was already healthy when
        # watch started never recovered from anything, and saying it did would
        # be inventing an incident that never happened. The scan still runs, and
        # still reconciles whatever an earlier process left open on the graph.
        opening = "clean" if previous is None else "recovered"
        console.print(f"[green]{stamp} {opening}: no findings.[/green]")
        return
    verb = "detected" if not previous else "changed to"
    console.print(f"[bold red]{stamp} {verb} {len(signature)} finding(s):[/bold red]")
    _print_findings_and_trust(preview)


def _watch_failure_message(exc: Exception, *, consecutive_failures: int) -> str:
    """Render one console line for a failed poll, with no token in it.

    safe_error(exc), not str(exc) or the exception's class name alone: an SDK
    failure can quote the request we handed the token to, and the class name
    alone (the previous behaviour) threw away the one detail an operator
    needed to tell an expired token from a network partition from a GMS out
    of disk (F12, docs/plan/07).

    Escalates to a plain statement that the daemon is not working once
    failures repeat past WATCH_FAILURE_ESCALATION_THRESHOLD: a single failure
    is the expected shape of GMS still starting up, a run of them is not, and
    an operator stops reading a wall of identical yellow lines.
    """
    message = safe_error(exc) or type(exc).__name__
    if consecutive_failures >= WATCH_FAILURE_ESCALATION_THRESHOLD:
        return (
            f"[bold red]watch has failed {consecutive_failures} polls in a "
            f"row and is not working: {message}[/bold red]"
        )
    return f"[yellow]watch poll failed: {message}[/yellow]"


def _watch_once(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None,
    model_urn: str | None,
    llm: LLMConfig | None,
    previous: frozenset[FindingSignature] | None,
    state: WatchState | None = None,
    argos: ArgosProducer | None = None,
) -> frozenset[FindingSignature]:
    """Poll once: detect, and write back only when the set of findings has changed.

    A dry scan detects without writing; only a changed finding set triggers the
    real write-back, which raises whatever this poll newly found and resolves
    whatever this scan's target no longer reproduces (run_scan's own
    reconciliation, not a mechanism unique to watch: it fires the same way
    whether the previous finding was raised by this exact process, an earlier
    one before a restart, or a plain scan). Re-writing an unchanged finding
    every poll would be safe (the writes are idempotent) but noisy and
    pointless, so a steady state stays quiet after the first poll.

    The first poll is never quiet, even against a healthy target: ``previous``
    is None rather than an empty set, so there is no remembered state to match,
    and the write that follows is what reconciles findings an earlier process
    raised and never got to resolve. Returns the current signature, which the
    caller carries into the next poll.

    ``argos``, when present, is the desktop companion. It is told what this poll
    found and nothing more: the events are built from the report by
    argos/events.py, so the pet depicts a measured finding and never a guess
    (docs/plan/08 section 3).
    """
    preview = run_scan(
        conn, config, table_urn=table_urn, model_urn=model_urn, llm=llm, dry_run=True
    )
    signature = _finding_signature(preview)

    muted = argos is not None and argos.muted
    # A mute defers a write, it does not cancel one. Without this, a change that
    # arrived during the hour would be remembered as the new steady state and
    # never written at all: the poll after the mute expires would compare equal
    # and stay quiet, and the incident nobody muted would simply never be
    # raised.
    deferred = state is not None and state.deferred_write

    if signature == previous and not (deferred and not muted):
        console.print(
            f"[dim]{time.strftime('%H:%M:%S')} no change ({len(signature)} open finding(s))[/dim]"
        )
        if state is not None:
            state.unchanged_polls += 1
        if argos is not None:
            unchanged = state.unchanged_polls if state is not None else 0
            argos.send(
                argos_events.asleep(unchanged)
                if unchanged >= WATCH_ASLEEP_AFTER_POLLS
                else argos_events.from_report(preview)
            )
        return signature

    if signature != previous:
        _announce_watch_change(previous, signature, preview)
    if muted:
        # Keep looking, stop writing. The finding is still on screen and in the
        # terminal, so a mute hides the noise and never the problem.
        console.print("[dim]argos: muted, not writing this change[/dim]")
        if argos is not None:
            argos.send(argos_events.from_report(preview))
        if state is not None:
            state.signature = signature
            state.unchanged_polls = 0
            state.deferred_write = True
        return signature

    written = run_scan(conn, config, table_urn=table_urn, model_urn=model_urn, llm=llm)
    if signature:
        _print_writes_section(written)
    if argos is not None:
        # A recovery is a transition, not a property of the report: this is the
        # only place that knows the target was failing a moment ago and is not
        # now, which is the one moment the dog gets to wag.
        argos.send(argos_events.from_report(written, recovered=bool(previous) and not signature))

    if state is not None:
        state.signature = signature
        state.unchanged_polls = 0
        state.deferred_write = False
    return signature


def _touches(event: MclEvent, table_urn: str | None, model_urn: str | None) -> bool:
    """Whether a change-log event concerns one of the watched targets.

    A column's own aspects arrive under a ``schemaField`` URN that embeds its
    parent dataset, so containment rather than equality: a term applied to a
    column of the watched table is exactly the change a leakage scan should wake
    up for, and an equality test would sleep through it.
    """
    return any(
        target is not None and target in event.entity_urn for target in (table_urn, model_urn)
    )


def _watch_events(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None,
    model_urn: str | None,
    llm: LLMConfig | None,
    mcl: MclConfig,
    argos: ArgosProducer | None,
) -> None:
    """Run the watch loop off DataHub's change log instead of a timer (T-20).

    Two handlers over one subscription, which is what the plan means by "the same
    consumer with a second handler":

    * **Catalog-wide**, every event: an ``mlModelProperties`` upsert that dropped
      a model's features gets the recorded link replayed. That failure is not
      about the watched target, so it is deliberately not filtered by one.
    * **Target-scoped**: an event touching the watched table or model runs the
      same ``_watch_once`` a poll would have run, so detection, write-back and
      the transition logic are byte-identical to polling. Only the wake-up
      differs, which is the whole claim.

    One scan runs before the loop opens. Without it the first event's transition
    would be computed against an empty state and would announce every existing
    finding as new.
    """
    state = WatchState()
    _watch_once(
        conn,
        config,
        table_urn=table_urn,
        model_urn=model_urn,
        llm=llm,
        previous=state.signature,
        state=state,
        argos=argos,
    )

    with ChangeLog(mcl) as log:
        console.print("[dim]listening to DataHub's change log; Ctrl-C to stop[/dim]\n")
        for event in log.events():
            try:
                relink = consider(conn, config, event)
                if relink is not None:
                    colour = "green" if relink.relinked else "yellow"
                    console.print(
                        f"[{colour}]{relink.model_urn}[/{colour}]\n  {relink.reason}"
                        + (f" ({relink.features} features)" if relink.relinked else "")
                    )
                if _touches(event, table_urn, model_urn):
                    _watch_once(
                        conn,
                        config,
                        table_urn=table_urn,
                        model_urn=model_urn,
                        llm=llm,
                        previous=state.signature,
                        state=state,
                        argos=argos,
                    )
            except Exception as exc:  # noqa: BLE001 - a daemon must survive one bad event
                # Logged and carried on, never re-raised: a single model whose
                # feature table has since been deleted must not stop the loop
                # from protecting every other model behind it. The offset is
                # committed either way, because the handlers are idempotent and
                # re-delivering an event that already failed would wedge the
                # consumer on it forever.
                fields = {"error_type": type(exc).__name__, "urn": event.entity_urn}
                logger.warning(
                    "change log handler failed %s",
                    logfmt(fields),
                    extra={LOG_FIELDS: fields},
                    exc_info=True,
                )
                console.print(f"[yellow]event skipped:[/yellow] {safe_error(exc)}")


def _wait(seconds: float, argos: ArgosProducer | None) -> None:
    """Sleep between polls, but wake early when the window asks for a scan.

    ``time.sleep`` would make "Scan now" mean "in up to thirty seconds", which
    reads as a broken button. Waiting on the producer's event costs nothing when
    nobody clicks and returns immediately when somebody does.
    """
    if argos is None:
        time.sleep(seconds)
        return
    if argos.wake.wait(timeout=seconds):
        argos.wake.clear()


def _run_review(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None,
    model_urn: str | None,
    llm: LLMConfig | None,
    auto: bool,
) -> ScanReport:
    """Run the LangGraph agent, prompting for approval before any write lands.

    The agent is the optional ``agent`` extra, so it is imported here rather than
    at module load: a plain ``janus scan`` must run without LangGraph
    installed. The approve callback prints the findings and either prompts or, with
    ``--auto-approve``, writes without asking (the recorded-demo path).
    """
    from janus.agent.graph import AgentUnavailableError, run_agent

    def _approve(preview: ScanReport) -> bool:
        _print_findings_and_trust(preview)
        if auto:
            console.print("\n[dim]--auto-approve: writing without prompting.[/dim]")
            return True
        # A clean preview still reaches this prompt, because a clean scan is what
        # resolves a recovered target's incident and clears the risk it left on
        # the graph. Asking about "these findings" when there are none would be a
        # lie about what is being approved.
        question = (
            "\nResolve anything on the graph this scan found to be fixed?"
            if preview.clean
            else "\nWrite these findings back to DataHub?"
        )
        return typer.confirm(question, default=False)

    try:
        report = run_agent(
            conn, config, table_urn=table_urn, model_urn=model_urn, llm=llm, approve=_approve
        )
    except AgentUnavailableError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if report.clean:
        _print_clean(report)
    elif report.dry_run:
        console.print("\n[yellow]Declined: nothing was written.[/yellow]")
    else:
        _print_writes_section(report)
    return report


def _scan_all_models(
    *,
    sla_hours: float | None,
    dry_run: bool,
    no_llm: bool,
    llm_provider: str | None,
    llm_model: str | None,
    forbidden: dict[str, bool],
) -> None:
    """Run one scan per model in the graph, sharing a single connection.

    Most of the options this refuses alongside ``--all-models`` mean "one
    target": a report path names one file, a contract describes one model, and
    the review agent stops for a human, which across a whole catalog is a prompt
    storm nobody reads to the end. Refusing them beats guessing which model wins.

    ``--format`` is refused for a different reason: a sweep produces one report
    per model, and concatenated JSON documents are not a JSON document. Emitting
    an array instead would be a second, differently-shaped output contract for
    the same flag, which is worse than saying no until somebody needs it.
    """
    named = sorted(flag for flag, given in forbidden.items() if given)
    if named:
        console.print(f"[red]--all-models cannot be combined with {', '.join(named)}.[/red]")
        raise typer.Exit(code=2)

    try:
        config = ScanConfig.from_env()
        if sla_hours is not None:
            config = replace(config, freshness_sla_hours=sla_hours)
        llm = _resolve_llm(no_llm=no_llm, provider=llm_provider, model=llm_model)
        conn = connect()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    model_urns = _model_urns(conn)
    if not model_urns:
        console.print(f"[yellow]No models in {conn.gms_url}.[/yellow] {_no_match_hint(conn)}")
        return

    if not dry_run and not conn.has_token:
        console.print("[yellow]No DATAHUB_GMS_TOKEN set; writing unauthenticated.[/yellow]")

    for model_urn in model_urns:
        console.print(f"\nScanning [bold]{model_urn}[/bold]")
        report = run_scan(conn, config, model_urn=model_urn, llm=llm, dry_run=dry_run)
        _print_report(report)


@app.command()
def scan(
    table: Annotated[
        str | None,
        typer.Option("--table", help="Dataset to audit: a full URN, or a name such as loans_raw."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Model to audit for target leakage: a full URN, or a name such as credit_risk_v3.",
        ),
    ] = None,
    sla_hours: Annotated[
        float | None,
        typer.Option(
            "--sla-hours",
            help="Freshness SLA in hours. Overrides JANUS_FRESHNESS_SLA_HOURS.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Detect and explain, but write nothing.")
    ] = False,
    no_llm: Annotated[
        bool,
        typer.Option("--no-llm", help="Skip the LLM and use the deterministic template prose."),
    ] = False,
    llm_provider: Annotated[
        str | None,
        typer.Option(
            "--llm-provider",
            help="Override JANUS_LLM_PROVIDER: anthropic, openai, or google.",
        ),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override JANUS_LLM_MODEL, the provider's model id."),
    ] = None,
    report_out: Annotated[
        Path | None,
        typer.Option("--report-out", help="Also write the impact report markdown to this path."),
    ] = None,
    assertion_out: Annotated[
        Path | None,
        typer.Option("--assertion-out", help="Also write the guarding-assertion YAML here."),
    ] = None,
    contract_out: Annotated[
        Path | None,
        typer.Option(
            "--contract-out",
            help="Write the model's ODCS input-data-contract YAML here. Requires --model.",
        ),
    ] = None,
    review: Annotated[
        bool,
        typer.Option(
            "--review",
            help="Run the human-approval agent: show the findings and prompt before writing.",
        ),
    ] = False,
    auto_approve: Annotated[
        bool,
        typer.Option(
            "--auto-approve",
            help="Run the agent but write without prompting. For the recorded demo.",
        ),
    ] = False,
    all_models: Annotated[
        bool,
        typer.Option("--all-models", help="Audit every model in the graph, one after another."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="text for a human, json for a program. json puts the whole report "
            "on stdout and progress lines on stderr.",
        ),
    ] = OutputFormat.TEXT,
) -> None:
    """Audit a table for stale data, a model for target leakage, or both.

    The two targets ask different questions of the graph. ``--table`` asks what a
    table's going stale endangers downstream. ``--model`` asks whether a model is
    training on its own label. At least one is required, or ``--all-models`` to
    sweep every model the graph holds.

    By default the writes land straight away. ``--review`` (or ``--auto-approve``
    for the demo) instead runs the LangGraph agent, which pauses after detection so
    a human can approve the mutations before they are written.
    """
    if all_models:
        _scan_all_models(
            sla_hours=sla_hours,
            dry_run=dry_run,
            no_llm=no_llm,
            llm_provider=llm_provider,
            llm_model=llm_model,
            forbidden={
                "--table": table is not None,
                "--model": model is not None,
                "--review/--auto-approve": review or auto_approve,
                "--report-out/--assertion-out/--contract-out": any(
                    path is not None for path in (report_out, assertion_out, contract_out)
                ),
                "--format": output_format is not OutputFormat.TEXT,
            },
        )
        return

    if contract_out is not None and model is None:
        console.print("[red]--contract-out describes a model's inputs; pass --model.[/red]")
        raise typer.Exit(code=2)

    use_agent = review or auto_approve
    if dry_run and use_agent:
        # One call, not two. Rich parses each print independently, so an opening
        # tag in one and its closing tag in the next raises MarkupError and the
        # guard crashes instead of explaining itself (found by the --format json
        # guard's test, which is the first test to invoke one of these paths).
        console.print(
            "[red]--dry-run and --review/--auto-approve are mutually exclusive: "
            "--review already previews the findings before writing.[/red]"
        )
        raise typer.Exit(code=2)

    as_json = output_format is OutputFormat.JSON
    if as_json and use_agent:
        # --review is a conversation: it prints the findings and waits for a human
        # to type an answer. There is no way to render that as one JSON document,
        # and pretending otherwise would hang a script that never answers.
        console.print(
            "[red]--format json and --review/--auto-approve are incompatible: "
            "the review agent prompts a human before it writes.[/red]"
        )
        raise typer.Exit(code=2)

    # Everything that is not the report itself moves to stderr under --format
    # json, so stdout carries exactly one parseable document.
    chatter = err_console if as_json else console

    conn, config, llm, table_urn, model_urn = _prepare(
        table=table,
        model=model,
        sla_hours=sla_hours,
        no_llm=no_llm,
        llm_provider=llm_provider,
        llm_model=llm_model,
        writes=not dry_run,
        chatter=chatter,
    )

    for target in (table_urn, model_urn):
        if target is not None:
            chatter.print(f"Scanning [bold]{target}[/bold]")
    chatter.print()

    if use_agent:
        report = _run_review(
            conn, config, table_urn=table_urn, model_urn=model_urn, llm=llm, auto=auto_approve
        )
    else:
        report = run_scan(
            conn,
            config,
            table_urn=table_urn,
            model_urn=model_urn,
            llm=llm,
            dry_run=dry_run,
        )
        if as_json:
            print(report_json(report))
        else:
            _print_report(report)

    # Unconditional, like the gate's GitHub annotations and for the same reason:
    # it lands only where a CI runner has asked for it (GITHUB_STEP_SUMMARY set),
    # and a flag somebody has to remember is a flag nobody sets.
    write_job_summary(job_summary_markdown(report))

    # The input contract describes the model's expected inputs, not this scan's
    # findings, so it is written even when the scan is clean: a clean model still
    # has a boundary worth contracting (the "before promoting a model" use case).
    if contract_out is not None and model_urn is not None:
        from janus.writeback.contract import ContractError, render_input_contract

        try:
            contract_out.write_text(render_input_contract(conn, model_urn, config))
            chatter.print(f"[dim]wrote {contract_out}[/dim]")
        except ContractError as exc:
            chatter.print(f"[yellow]{exc}[/yellow]")

    if report.clean:
        return

    if assertion_out is not None and report.assertion_yaml:
        assertion_out.write_text(report.assertion_yaml)
        chatter.print(f"[dim]wrote {assertion_out}[/dim]")

    if report_out is not None:
        from janus.writeback.documents import render_impact_report

        # One file per finding would need one path per finding. The worst finding
        # is the one a human opens first, so that is the one written.
        worst = report.writes[0]
        report_out.write_text(
            render_impact_report(worst.finding, worst.narrative.assessment, report.run_id)
        )
        chatter.print(f"[dim]wrote {report_out}[/dim]")


@app.command()
def watch(
    table: Annotated[
        str | None,
        typer.Option("--table", help="Dataset to watch: a full URN, or a name such as loans_raw."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Model to watch: a full URN, or a name like credit_risk_v3."),
    ] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Seconds between polls.", min=1.0),
    ] = 30.0,
    once: Annotated[
        bool,
        typer.Option("--once", help="Poll a single time and exit. For scripts and the demo."),
    ] = False,
    events: Annotated[
        bool,
        typer.Option(
            "--events",
            help="React to DataHub's change log instead of polling, and re-apply any "
            "link an ingestion run drops, anywhere in the catalog.",
        ),
    ] = False,
    pet: Annotated[
        bool,
        typer.Option(
            "--pet",
            help="Show Argos, the desktop watchdog, and let it drive this watch. "
            "Falls back to a terminal line when no window binary is installed.",
        ),
    ] = False,
    sla_hours: Annotated[
        float | None,
        typer.Option("--sla-hours", help="Freshness SLA in hours. Overrides the env default."),
    ] = None,
    no_llm: Annotated[
        bool,
        typer.Option("--no-llm", help="Skip the LLM and use the deterministic template prose."),
    ] = False,
    llm_provider: Annotated[
        str | None,
        typer.Option("--llm-provider", help="Override JANUS_LLM_PROVIDER."),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override JANUS_LLM_MODEL."),
    ] = None,
) -> None:
    """Poll a table and/or model and write back the moment a new problem appears.

    ``watch`` shares ``scan``'s detection and write-back exactly; it only differs
    in what wakes it. It is unattended, so it approves its own writes (there is no
    human to prompt) and, because the writes are idempotent, it acts on the
    transitions, a new finding or a recovery, rather than on every poll.

    Polling is the default, deliberately: it never depends on Kafka timing, which
    is what makes it reliable for a demo and behind a proxy.

    ``--events`` is the upgrade 03-production-hardening.md section C.1 has always
    named (T-20). It consumes DataHub's own ``MetadataChangeLog``, so a scan
    happens when the graph changes rather than when a timer fires, and it does one
    thing polling structurally cannot: it re-applies, **catalog-wide**, any
    `janus link` an ingestion run drops. That failure is not about the
    watched target, so no poll of one target could ever see it (D-074, F11).

    It replays only what a human already confirmed. A model nobody linked is left
    alone: guessing a training table would write a join indistinguishable from a
    confirmed one and wrong.
    """
    # The one entry point that runs unattended for days, so it is the one that
    # turns the pipeline's per-scan timing lines on. The other commands print a
    # report to a human who is standing there and would only be talked over.
    # basicConfig is a no-op if the embedding application already configured
    # logging, which is what makes it safe to call from a command rather than
    # at import. JANUS_LOG_FORMAT=json switches the same lines to one JSON
    # object each, for the deployment that ships them to a log pipeline.
    try:
        log_format = configure_logging(level=logging.INFO, stream=sys.stderr)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    console.print(f"[dim]logging: {log_format}[/dim]")

    # Installed here and nowhere else, for the reason the log handler is: this is
    # the one entry point that runs unattended, and a library that started a
    # background exporter would be exporting from an application that never asked
    # for it. Unset means off, and a misconfigured endpoint fails now rather than
    # after a week of exporting nowhere (T-17).
    try:
        metrics = start_exporter()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    if metrics is not None:
        console.print("[dim]exporting scan metrics over OTLP[/dim]")

    conn, config, llm, table_urn, model_urn = _prepare(
        table=table,
        model=model,
        sla_hours=sla_hours,
        no_llm=no_llm,
        llm_provider=llm_provider,
        llm_model=llm_model,
        writes=True,
    )

    for target in (table_urn, model_urn):
        if target is not None:
            console.print(f"Watching [bold]{target}[/bold]")
    if not once and not events:
        console.print(f"[dim]polling every {interval:.0f}s; Ctrl-C to stop[/dim]")
    console.print()

    argos = ArgosProducer.start(console) if pet else None
    if events:
        if once:
            console.print(
                "[red]--events and --once are not compatible.[/red] A subscription has "
                "no single poll to take: --once is the scripted path, --events is the "
                "daemon one."
            )
            raise typer.Exit(code=2)
        try:
            mcl = mcl_config_from_env()
        except ConfigError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        if mcl is None:
            console.print(
                f"[red]--events needs the change log configured.[/red] Set "
                f"{ENV_KAFKA_BOOTSTRAP}, {ENV_SCHEMA_REGISTRY_URL} and "
                f"{ENV_KAFKA_GROUP_ID}, and install the client: "
                'pip install "janus-datahub[kafka]"'
            )
            raise typer.Exit(code=2)
        try:
            _watch_events(
                conn,
                config,
                table_urn=table_urn,
                model_urn=model_urn,
                llm=llm,
                mcl=mcl,
                argos=argos,
            )
        except ConfigError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        except KeyboardInterrupt:
            console.print("\n[dim]watch stopped.[/dim]")
        finally:
            if argos is not None:
                argos.close()
            if metrics is not None:
                metrics.force_flush()
                metrics.shutdown()
        return

    state = WatchState()
    backoff = interval
    consecutive_failures = 0
    try:
        while True:
            try:
                _watch_once(
                    conn,
                    config,
                    table_urn=table_urn,
                    model_urn=model_urn,
                    llm=llm,
                    previous=state.signature,
                    state=state,
                    argos=argos,
                )
                backoff = interval
                consecutive_failures = 0
            except Exception as exc:  # a daemon must survive SDK failures
                consecutive_failures += 1
                fields = {
                    "error_type": type(exc).__name__,
                    "backoff_s": round(backoff),
                    "consecutive_failures": consecutive_failures,
                }
                # exc_info carries the full traceback into the log, where an
                # operator can find it; safe_error(exc), not str(exc), on the
                # console, since an SDK failure can quote the request we
                # handed the token to and this line lands in a terminal the
                # whole team may be watching (root CLAUDE.md rule 6d).
                logger.warning(
                    "watch poll failed %s",
                    logfmt(fields),
                    extra={LOG_FIELDS: fields},
                    exc_info=True,
                )
                console.print(
                    _watch_failure_message(exc, consecutive_failures=consecutive_failures)
                )
                console.print(f"[dim]retrying in {backoff:.0f}s[/dim]")
                if argos is not None:
                    # The row that earns the pet its trust: a poll that could not
                    # reach DataHub must not leave a cheerful dog on screen.
                    argos.send(argos_events.unreachable(safe_error(exc)))
                if once:
                    raise typer.Exit(code=1) from exc
                _wait(backoff, argos)
                backoff = min(backoff * 2, WATCH_MAX_BACKOFF_SECONDS)
                continue
            if once:
                break
            _wait(interval, argos)
    except KeyboardInterrupt:
        console.print("\n[dim]watch stopped.[/dim]")
    finally:
        if argos is not None:
            argos.close()
        if metrics is not None:
            # Flush before shutdown: the reader batches on an interval, so a
            # watch stopped by Ctrl-C would otherwise drop up to one interval of
            # measurements, including the failures that most likely prompted
            # somebody to stop it.
            metrics.force_flush()
            metrics.shutdown()


@app.command()
def companion(
    interval: Annotated[
        float,
        typer.Option("--interval", help="Seconds between sweeps.", min=5.0),
    ] = 120.0,
    once: Annotated[
        bool,
        typer.Option("--once", help="Sweep a single time and exit."),
    ] = False,
    owner: Annotated[
        str | None,
        typer.Option(
            "--owner",
            help="Owner URN whose assets to watch. Defaults to JANUS_COMPANION_OWNER.",
        ),
    ] = None,
    no_window: Annotated[
        bool,
        typer.Option("--no-window", help="Skip the desktop window and report in the terminal."),
    ] = False,
) -> None:
    """Watch the assets you own, and show them as Argos on your desktop.

    The general DataHub companion, and the reason Argos is not a Janus pet:
    it runs no detector of its own and asks the catalogue three questions about
    every entity a given owner owns. Is an incident open on it, did its last
    assertion run fail, did its owners deprecate it.

    Read-only. Nothing here writes an aspect, so a read-only token is enough,
    and it is safe to point at a production catalogue.

    Janus's own findings reach the same window through
    ``janus watch --pet``. A window belongs to one process, so running both
    gives two dogs rather than one dog with two sources.
    """
    try:
        configure_logging(level=logging.INFO, stream=sys.stderr)
        watched_owner = owner or companion_module.owner_urn()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    conn = connect(require_token=False)
    config = ScanConfig.from_env()
    console.print(f"Watching assets owned by [bold]{watched_owner}[/bold]")
    if not once:
        console.print(f"[dim]sweeping every {interval:.0f}s; Ctrl-C to stop[/dim]")

    argos = ArgosProducer.start(console, window=not no_window)
    try:
        while True:
            try:
                sweep = companion_module.poll(conn, watched_owner, config)
                argos.send(companion_module.event_for(sweep))
                for issue in sweep.issues[:5]:
                    console.print(f"[yellow]{issue.source}[/yellow] {issue.title}")
                if not sweep.issues:
                    console.print(f"[green]{sweep.owned} owned assets, nothing wrong[/green]")
            except Exception as exc:  # a daemon must survive SDK failures
                logger.warning("companion sweep failed", exc_info=True)
                console.print(f"[yellow]companion sweep failed: {safe_error(exc)}[/yellow]")
                argos.send(argos_events.unreachable(safe_error(exc)))
                if once:
                    raise typer.Exit(code=1) from exc
            if once:
                break
            _wait(interval, argos)
    except KeyboardInterrupt:
        console.print("\n[dim]companion stopped.[/dim]")
    finally:
        argos.close()


@app.command()
def gate(
    table: Annotated[
        str | None,
        typer.Option(
            "--table", help="Dataset to gate on: a full URN, or a name such as loans_raw."
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model", help="Model to gate on: a full URN, or a name like credit_risk_v3."
        ),
    ] = None,
    block_at_or_above: Annotated[
        str | None,
        typer.Option(
            "--block-at-or-above",
            help="Fail on any finding this severe or worse: critical, high, medium, low.",
        ),
    ] = None,
    min_trust: Annotated[
        float | None,
        typer.Option(
            "--min-trust",
            help=(
                "Fail when any model's trust score is below this (0-100). A blunt "
                "secondary control: prefer --block-at-or-above, whose units are defined."
            ),
        ),
    ] = None,
    write: Annotated[
        bool,
        typer.Option(
            "--write",
            help="Also write findings back. Off by default: a gate runs on every push.",
        ),
    ] = False,
    sla_hours: Annotated[
        float | None,
        typer.Option("--sla-hours", help="Freshness SLA in hours. Overrides the env default."),
    ] = None,
    no_llm: Annotated[
        bool,
        typer.Option(
            "--no-llm/--llm",
            help="Skip the LLM (default: a gate needs a verdict, not prose). Pass "
            "--llm to narrate violations with prose; only then do --llm-provider "
            "and --llm-model have anything to override.",
        ),
    ] = True,
    llm_provider: Annotated[
        str | None, typer.Option("--llm-provider", help="Override JANUS_LLM_PROVIDER.")
    ] = None,
    llm_model: Annotated[
        str | None, typer.Option("--llm-model", help="Override JANUS_LLM_MODEL.")
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="text for a CI log, json for a program. json also suppresses the "
            "GitHub annotations, which are not JSON.",
        ),
    ] = OutputFormat.TEXT,
) -> None:
    """Fail the build when a change would ship an unsafe model.

    The preventive half of Janus, for a pull request rather than a postmortem.
    It runs the same detectors ``scan`` runs, judges them against a policy, and
    answers in an exit code: 0 shippable, 1 blocked, 2 could not tell.

    That third code matters more than it looks. A gate that reported "I could not
    reach DataHub" as a policy violation would teach the team to read every red
    build as flakiness, and the first real finding would be waved through with the
    rest. Setup failures exit 2, always, and never 1.

    Writes nothing unless asked. A gate runs on every push to every branch, most
    of which never merge, so raising an incident per run would fill the graph with
    findings about code that does not exist. The write-back belongs on the branch
    that merged, which is what ``scan`` is for.

    With no policy flag it reports and passes, deliberately: a gate that fails the
    moment it is installed, before anyone has said what they care about, gets
    removed the same afternoon.
    """
    try:
        policy = GatePolicy(
            block_at_or_above=Severity(block_at_or_above) if block_at_or_above else None,
            min_trust_score=min_trust,
        )
    except ValueError as exc:
        allowed = ", ".join(level.value for level in Severity)
        console.print(f"[red]{block_at_or_above!r} is not a severity. Use one of: {allowed}.[/red]")
        raise typer.Exit(code=EXIT_ERROR) from exc

    # Said here, where the choice is being made, rather than in documentation
    # nobody reads at the moment they need it. The judgement itself is the
    # policy's own, so gate.py can be asked about it offline (F7 step 3).
    if policy.advisory:
        console.print(f"[yellow]warning:[/yellow] {policy.advisory}")

    # Any setup failure is "could not reach a verdict", so _prepare's own exit
    # codes are remapped onto EXIT_ERROR rather than leaking through as a policy
    # violation. This is the distinction the whole command rests on.
    as_json = output_format is OutputFormat.JSON
    chatter = err_console if as_json else console

    try:
        conn, config, llm, table_urn, model_urn = _prepare(
            table=table,
            model=model,
            sla_hours=sla_hours,
            no_llm=no_llm,
            llm_provider=llm_provider,
            llm_model=llm_model,
            writes=write,
            chatter=chatter,
        )
    except typer.Exit as exc:
        raise typer.Exit(code=EXIT_ERROR) from exc

    # Same remapping as _prepare's setup failures, and for the same reason: a GMS
    # connection dropped mid-traversal is "could not reach a verdict", not a policy
    # violation. Letting this propagate would exit 1, indistinguishable from a real
    # finding, which is exactly the collapse this command exists to prevent.
    try:
        report = run_scan(
            conn,
            config,
            table_urn=table_urn,
            model_urn=model_urn,
            llm=llm,
            dry_run=not write,
        )
        verdict = evaluate(report, policy)
    except Exception as exc:
        console.print(f"[red]{safe_error(exc)}[/red]")
        raise typer.Exit(code=EXIT_ERROR) from exc

    # The verdict on the run's own summary page, where the reviewer already is,
    # instead of behind a click into the log. Lands only when GitHub Actions set
    # GITHUB_STEP_SUMMARY, and is a no-op everywhere else.
    write_job_summary(job_summary_markdown(report, verdict))

    if as_json:
        print(report_json(report, verdict))
        raise typer.Exit(code=verdict.exit_code)

    for finding in (write_.finding for write_ in report.writes):
        _print_finding(finding)

    # A gate that passes is a claim about the build, so it has to say which checks
    # it could not make. Without this, a model whose features carry no lineage at
    # all goes green for exactly the same reason a genuinely clean one does.
    _print_not_evaluated(report)

    for line in github_annotations(verdict):
        # Printed raw: GitHub reads these as workflow commands and turns them into
        # inline pull-request annotations. Other CI systems see ordinary output.
        print(line)

    colour = "red" if verdict.blocked else "green"
    console.print(f"[{colour}]{summary(verdict)}[/{colour}]")
    if write:
        console.print(f"[dim]run id: {report.run_id}[/dim]")

    raise typer.Exit(code=verdict.exit_code)


@app.command()
def inventory(
    limit: Annotated[
        int,
        typer.Option("--limit", help="Stop after this many models."),
    ] = 50,
) -> None:
    """List every model in the graph and say what can and cannot be checked.

    The first command to run against a DataHub you did not seed. It writes
    nothing, scans every model it finds, and answers the question a new user
    actually has: what does this tool know about my project, and what would I
    have to declare before it knows more.

    A model shows as unlinked when nothing connects it to the columns it was
    trained on, which is the out-of-the-box state of every model DataHub's own
    ingestion produced. `janus link` is what changes that.
    """
    try:
        config = ScanConfig.from_env()
        conn = connect()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    model_urns = _model_urns(conn, limit=limit)
    if not model_urns:
        console.print(f"[yellow]No models in {conn.gms_url}.[/yellow] {_no_match_hint(conn)}")
        raise typer.Exit(code=0)

    console.print(f"[bold]{len(model_urns)} model(s) in {conn.gms_url}[/bold]\n")
    unchecked = 0
    for model_urn in sorted(model_urns):
        report = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)
        name = MlModelUrn.from_string(model_urn).name

        # A model whose only findings are table-level ones is still an unlinked
        # model: the degraded mode gave it something to act on, and the thing to
        # act on first is the link that would replace those findings with real
        # ones. Counting it as checked would drop exactly the advice it needs.
        degraded = [
            write for write in report.writes if isinstance(write.finding, TableLevelRiskFinding)
        ]
        if report.writes:
            worst = report.severity
            titles = ", ".join(write.finding.title for write in report.writes)
            console.print(f"[red]{name}[/red]  {len(report.writes)} finding(s), worst {worst}")
            console.print(f"  {titles}")
            if len(degraded) == len(report.writes):
                unchecked += 1
                console.print(
                    "  [yellow]table level only: nothing links this model to its "
                    "columns, so no finding here names a feature[/yellow]"
                )
        elif report.not_evaluated:
            unchecked += 1
            checks = ", ".join(gap.check for gap in report.not_evaluated)
            console.print(f"[yellow]{name}[/yellow]  not checked: {checks}")
        else:
            console.print(f"[green]{name}[/green]  clean, every check ran")

    _print_lifecycle(conn, config, model_urns)

    if unchecked:
        console.print(
            f"\n[dim]{unchecked} model(s) have nothing linking them to their training "
            "data. Start with: janus link --model <name> --infer, which reads "
            "the arguments off the graph and shows them for confirmation. Where the "
            "graph is silent, declare it yourself: janus link --model <name> "
            "--features <table> --label-column <column>[/dim]"
        )


@app.command()
def finops(
    limit: Annotated[
        int,
        typer.Option("--limit", help="Stop after this many models."),
    ] = 200,
    days: Annotated[
        int | None,
        typer.Option("--days", help="Idle window before a model counts as unused."),
    ] = None,
) -> None:
    """List the tables that exist only to feed models nothing uses (T-18).

    The one command here whose reader is a budget holder rather than an engineer.
    Nothing is broken, so nothing is written and no incident is raised: a model
    nobody deployed is a decision somebody may not have noticed they made.

    A table is listed only when *every* model downstream of it is unused, and
    "unused" means both no deployment in service and a recorded date older than
    the window. A model whose catalog entry carries no date at all is reported
    separately as undated, never as unused: in a report that suggests deleting
    things, an absence is not evidence.
    """
    try:
        config = ScanConfig.from_env()
        if days is not None:
            config = replace(config, unused_model_days=days)
        conn = connect()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    model_urns = _model_urns(conn, limit=limit)
    if not model_urns:
        console.print(f"[yellow]No models in {conn.gms_url}.[/yellow] {_no_match_hint(conn)}")
        raise typer.Exit(code=0)

    result = finops_report(conn, config, model_urns, now_ms=int(time.time() * 1000))
    console.print(
        f"[bold]{result.examined} model(s) examined[/bold], idle window {result.window_days} days\n"
    )

    if result.unused:
        console.print(f"[yellow]{len(result.unused)} model(s) with no live deployment[/yellow]")
        for usage in result.unused:
            idle = usage.idle_days(int(time.time() * 1000))
            console.print(f"  {usage.model.name}  idle {idle:.0f} days")
        console.print()

    if result.candidates:
        console.print(
            f"[bold]{len(result.candidates)} table(s) feed nothing else[/bold]  "
            "(every model downstream of these is unused)"
        )
        for candidate in result.candidates:
            console.print(f"  {candidate.describe()}")
    else:
        console.print(
            "[green]No table feeds only unused models.[/green] Every input reaches "
            "something still in service, which is the usual answer on a warehouse "
            "where marts are shared."
        )

    if result.undated:
        console.print(f"\n[dim]{describe_undated(result.undated)}[/dim]")


def _print_lifecycle(
    conn: DataHubConnection, config: ScanConfig, model_urns: tuple[str, ...]
) -> None:
    """Print how long Janus's own findings have stayed open (T-16).

    Printed after the per-model list rather than before it: the list says what is
    wrong now, and this says whether anything ever gets fixed. Silent when
    Janus has never raised an incident on this graph, because a table of
    zeroes on a first run is noise, not a measurement.
    """
    rows = [row for row in mttr_by_type(read_lifecycles(conn, config, model_urns)) if row.raised]
    if not rows:
        return

    console.print("\n[bold]Incident lifecycle[/bold]  (Janus's own writes)")
    for row in rows:
        console.print(f"  {row.describe()}")


@app.command()
def coverage(
    limit: Annotated[
        int,
        typer.Option("--limit", help="Stop after this many models."),
    ] = 50,
    write: Annotated[
        bool,
        typer.Option(
            "--write/--dry-run",
            help="Record this sweep in the coverage trend on Janus's own dataFlow.",
        ),
    ] = False,
) -> None:
    """Report what fraction of this catalog's models each check can run against (T-15).

    `inventory` answers the question one model at a time. This is the same sweep
    folded into the figure a platform lead reports upward: how observable is this
    ML estate, which direction is it moving, and what is the single next
    declaration that would raise it most.

    It measures declaring, not health. A catalog at 100% coverage may still be
    full of leaking models; a catalog at 8% is one where Janus mostly cannot
    tell you either way, which is the more urgent problem.

    `--write` appends one capped entry to janus.coverage_history, keyed on
    the sweep's run id so a rerun replaces its own row rather than adding a
    second. Nothing else is written: the per-model scans are dry runs.
    """
    try:
        config = ScanConfig.from_env()
        conn = connect()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    model_urns = _model_urns(conn, limit=limit)
    if not model_urns:
        console.print(f"[yellow]No models in {conn.gms_url}.[/yellow] {_no_match_hint(conn)}")
        raise typer.Exit(code=0)

    run_id = new_run_id()
    sweep = []
    for model_urn in sorted(model_urns):
        report = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)
        sweep.append(ModelCoverage(model_urn=model_urn, gaps=report.not_evaluated))

    catalog = aggregate(sweep)
    _print_coverage(catalog)

    if write:
        history = append_entry(conn, catalog, run_id)
        console.print(f"\n[green]Recorded[/green] on {agent_flow_urn()}")
        _print_coverage_trend(history)


def _print_coverage(catalog: CatalogCoverage) -> None:
    """Print a catalog figure: the headline, the per-check rows, then the advice."""
    console.print(
        f"[bold]Guard coverage: {catalog.rate:.0%}[/bold] "
        f"({catalog.covered_checks}/{catalog.total_checks} checks evaluable "
        f"across {catalog.models} model(s))\n"
    )
    for check in catalog.checks:
        # Colour tracks the rate and nothing else. A check nothing can run is not
        # a failing check, it is an unasked one, so the worst colour here is
        # yellow: red is reserved for a finding somebody has to act on.
        colour = "green" if check.rate >= 1.0 else "yellow" if check.rate > 0.0 else "dim"
        console.print(f"  [{colour}]{check.describe()}[/{colour}]")

    if catalog.next_join is not None:
        console.print(f"\n[bold]Next join[/bold]  {catalog.next_join.describe()}")


def _print_coverage_trend(history: tuple[CoverageEntry, ...]) -> None:
    """Print the recorded trend, most recent last, when there is more than a point."""
    if len(history) < 2:
        return
    console.print("\n[bold]Trend[/bold]")
    for entry in history:
        console.print(f"  {entry.recorded_at}  {entry.rate:>4.0%}  ({entry.models} models)")


def _print_proposal(proposal: LinkProposal) -> None:
    """Show an inferred link and how each part of it was decided.

    The reasons are printed before the command, not after: a proposal a person
    accepts without reading the reasoning is a guess with a confirmation prompt
    stapled to it, which is worse than asking them to type the arguments.
    """
    console.print("[bold]Inferred from the graph:[/bold]")
    for reason in proposal.reasons:
        colour = "yellow" if "guess" in reason or "NOT FOUND" in reason else "dim"
        console.print(f"  [{colour}]{reason}[/{colour}]")
    if proposal.candidates:
        # Numbered and named, not URNs alone: this list exists to be read and
        # answered in one line, and a bare URN column is not read.
        console.print("\n[bold]Nearest tables, for you to choose:[/bold]")
        for index, urn in enumerate(proposal.candidates, start=1):
            console.print(f"  {index}. {DatasetUrn.from_string(urn).name}")
    console.print("\n[bold]Proposed:[/bold]")
    console.print(proposal.command())


def _print_declaration(declaration: DeclaredLink) -> None:
    """Show what an adapter read, line by line, before anything is proposed.

    Every feature carries the declaration it came from, because that is the whole
    difference between importing a mapping and guessing one: a reviewer checks
    these lines against a file they already maintain, in one pass, without
    opening DataHub.
    """
    console.print(f"[bold]Read from the {declaration.adapter} declaration:[/bold]")
    for reason in declaration.reasons:
        colour = "yellow" if "not read" in reason or "pass --label-column" in reason else "dim"
        console.print(f"  [{colour}]{reason}[/{colour}]")
    console.print(f"\n[bold]Features {declaration.name!r} declares:[/bold]")
    for feature in declaration.features:
        # The arrow is the interesting half: a feature whose name differs from
        # its column is exactly what a hand-typed link gets wrong.
        console.print(
            f"  {feature.name} <- {feature.source_column}  [dim]({feature.declared_in})[/dim]"
        )
    console.print("")


def _declared_link(
    conn: DataHubConnection,
    model_urn: str,
    *,
    adapter: str,
    repo: Path,
    select: str | None,
    features: str | None,
    label_table: str | None,
) -> LinkProposal:
    """Read a declaration and turn it into a proposal for the human to confirm.

    Args:
        conn: An open connection.
        model_urn: The model being linked.
        adapter: The ``--from`` value.
        repo: The declaration root.
        select: Which declaration inside it, when there is more than one.
        features: A ``--features`` the user typed, which always wins over the
            table the declaration names: the declaration knows the warehouse, the
            user knows their catalog, and only one of those two can be checked
            here.
        label_table: A ``--label-table`` the user typed, same precedence.

    Returns:
        The proposal.

    Raises:
        AdapterError: The declaration could not be read.
        TableResolutionError: The table it names is not in the catalog.
        LinkError: The declaration and the catalog disagree about the columns.
    """
    declaration = read_declaration(adapter, repo, select)
    _print_declaration(declaration)

    feature_urn = resolve_table(conn, features or declaration.source_table)

    label_dataset_urn: str | None = None
    if declaration.label_column is not None:
        wanted = label_table or declaration.label_table
        try:
            label_dataset_urn = resolve_table(conn, wanted) if wanted else feature_urn
        except TableResolutionError as exc:
            # Not fatal: the features are the hard part and they resolved. The
            # label is one argument a human can supply, and saying so beats
            # discarding a proposal that is otherwise complete.
            console.print(
                f"[yellow]the declared label table did not resolve ({exc}), so the "
                "label was not taken from the declaration; pass --label-column "
                "(and --label-table).[/yellow]"
            )

    return declared_proposal(
        conn,
        model_urn,
        declaration,
        feature_dataset_urn=feature_urn,
        label_dataset_urn=label_dataset_urn,
    )


def _link_all(*, dry_run: bool, named: tuple[object, ...]) -> None:
    """Re-apply every link the graph already records, for the post-ingestion step.

    An ingestion run drops the features `link` wrote (D-074), so on any real
    schedule the links have to be replayed. Replaying them one model at a time is
    a loop somebody has to maintain and keep in step with the models that exist,
    which is how a model quietly stops being checked; this is the same loop, run
    off what the graph itself records.
    """
    if any(value for value in named):
        console.print("[red]--all replays what was recorded; pass no other options with it.[/red]")
        raise typer.Exit(code=2)

    try:
        config = ScanConfig.from_env()
        conn = connect()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except DataHubConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    recorded = models_with_recorded_link(conn)
    if not recorded:
        console.print(
            f"[yellow]No model in {conn.gms_url} carries a recorded link.[/yellow] "
            "Link one first: janus link --model <name> --features <table> "
            "--label-column <column>"
        )
        return

    if not dry_run and not conn.has_token:
        console.print("[yellow]No DATAHUB_GMS_TOKEN set; writing unauthenticated.[/yellow]")

    for model_urn, previous in recorded:
        name = MlModelUrn.from_string(model_urn).name
        try:
            result = link_model(
                conn,
                config,
                model_urn=model_urn,
                feature_dataset_urn=previous.feature_dataset_urn,
                label_column_urn=previous.label_column_urn,
                exclude=previous.exclude,
                dry_run=dry_run,
            )
        except LinkError as exc:
            # One model whose feature table was dropped must not stop the rest:
            # the whole point of the sweep is that everything else stays checked.
            console.print(f"[red]{name}[/red]  {exc}")
            continue
        console.print(
            f"[green]{name}[/green]  {len(result.feature_urns)} feature(s), "
            f"{len(result.training_runs)} run(s)"
        )

    if dry_run:
        console.print("\n[dim]Dry run: nothing was written.[/dim]")


@app.command()
def link(
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="The trained model: a full mlModel URN, or a name. Omit only with --all.",
        ),
    ] = None,
    features: Annotated[
        str | None,
        typer.Option(
            "--features",
            help="The table the model trains on: a full dataset URN, or a name. "
            "Omit to reuse what a previous link recorded.",
        ),
    ] = None,
    label_column: Annotated[
        str | None,
        typer.Option(
            "--label-column",
            help="The column the model predicts, by name. Omit to reuse what a "
            "previous link recorded.",
        ),
    ] = None,
    label_table: Annotated[
        str | None,
        typer.Option(
            "--label-table",
            help="Table holding the label column. Defaults to the feature table.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            help="A feature-table column that is not a feature (a join key). Repeatable.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be declared, write nothing.")
    ] = False,
    all_models: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Replay the recorded link for every model that has one. For the "
            "step after an ingestion run.",
        ),
    ] = False,
    infer: Annotated[
        bool,
        typer.Option(
            "--infer",
            help="Work the arguments out from the graph and show them for confirmation.",
        ),
    ] = False,
    from_: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="Read the link from a declaration instead of typing it: "
            f"{', '.join(ADAPTERS)}. Needs --repo.",
        ),
    ] = None,
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            help="Where the declaration lives: a Feast feature repo, or a dbt "
            "project (or its manifest.json). Read offline, never connected to.",
        ),
    ] = None,
    select: Annotated[
        str | None,
        typer.Option(
            "--select",
            help="Which declaration to read, when the repo holds more than one "
            "(a Feast feature service, a dbt semantic model).",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Accept an inferred proposal without prompting."),
    ] = False,
) -> None:
    """Tell DataHub which columns a model trains on, and which one is its label.

    Run this once per training run, from the script that trains the model.

    The detectors read a model through its features: leakage walks each feature's
    source column upstream to see whether it descends from the label, and drift
    diffs the schema captured at training time against the schema now. DataHub's
    own ingestion does not produce those links. Its mlflow source records the
    model and its run, its dbt and warehouse sources record excellent
    column-level lineage between tables, and nothing joins the two, so a scan of
    a freshly ingested model can only report that it had nothing to check.

    This is the join, made from facts the training script already has: the table
    it read, the columns it used, and the column it predicted.

    Where those facts are already declared somewhere, do not type them twice:
    ``--from feast --repo ./feature_repo`` (or ``--from dbt --repo ./dbt_project``)
    reads the declaration your team already maintains and proposes the link from
    it, printing which declaration every line came from and writing nothing until
    you say yes.

    Run it again after every ingestion of the model. DataHub's mlflow source
    upserts the whole ``mlModelProperties`` aspect, which drops the features this
    command attached (verified live, D-074), and a later scan will say so rather
    than report a model it cannot see as healthy. The arguments are recorded as
    structured properties on the model, which ingestion does not touch, so the
    replay is just ``janus link --model <name>``.
    """
    if all_models:
        if infer or from_ is not None:
            # --all replays what link already recorded, which is a fact. --infer
            # and --from each propose something for a human to check. Combining
            # them would mean writing an unreviewed proposal to every model in
            # the catalog at once, and one declaration cannot describe them all.
            console.print(
                "[red]--all is incompatible with --infer and --from: --all replays "
                "recorded links, the other two propose new ones for you to "
                "confirm, one model at a time.[/red]"
            )
            raise typer.Exit(code=2)
        _link_all(
            dry_run=dry_run,
            named=(model, features, label_table, label_column, exclude, repo, select),
        )
        return

    if model is None:
        console.print("[red]link needs --model, or --all to replay every recorded link.[/red]")
        raise typer.Exit(code=2)

    # Argument-shape checks need no DataHub connection, so they run before
    # _prepare() rather than after: a live GMS being unreachable must never
    # mask a usage error behind its own, unrelated exit code (D-114).
    if from_ is None and (repo is not None or select is not None):
        console.print(
            "[red]--repo and --select say where to read a declaration; --from says "
            f"which reader. Add --from ({'|'.join(ADAPTERS)}).[/red]"
        )
        raise typer.Exit(code=2)

    if infer and from_ is not None:
        # Both propose, from different evidence. Running them together would mean
        # deciding which one wins per field, which is a decision the user is
        # better placed to make by running the one they trust.
        console.print(
            "[red]--infer and --from are incompatible: --infer reads the graph, "
            "--from reads a declaration. Run whichever you trust for this model.[/red]"
        )
        raise typer.Exit(code=2)

    if from_ is not None and repo is None:
        console.print(f"[red]--from {from_} needs --repo <path to the declaration>.[/red]")
        raise typer.Exit(code=2)

    conn, config, _, feature_urn, model_urn = _prepare(
        table=features,
        model=model,
        sla_hours=None,
        no_llm=True,
        llm_provider=None,
        llm_model=None,
        writes=not dry_run,
    )
    if model_urn is None:
        # _prepare only returns None for a target the caller did not pass, and
        # --model is required here, so this is unreachable. A hard stop rather
        # than an assert, which -O would strip out.
        console.print("[red]link needs --model.[/red]")
        raise typer.Exit(code=2)

    # An ingestion run overwrites the model's aspect and drops the features, so a
    # link is replayed regularly. Replaying it must not mean retyping it.
    previous = recorded_link(conn, model_urn)

    proposal: LinkProposal | None = None
    if from_ is not None:
        if repo is None:
            # Already refused above when --from has no --repo; this is
            # unreachable, and only here so repo narrows to Path below.
            raise typer.Exit(code=2)
        try:
            proposal = _declared_link(
                conn,
                model_urn,
                adapter=from_,
                repo=repo,
                select=select,
                features=features,
                label_table=label_table,
            )
        except (AdapterError, TableResolutionError, LinkError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
    elif infer:
        try:
            proposal = infer_link(conn, config, model_urn)
        except LinkError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc

    if proposal is not None:
        _print_proposal(proposal)
        # An explicitly typed flag always wins: a proposal proposes, it never
        # overrules somebody who already knows the answer.
        features = features or proposal.feature_dataset_urn
        feature_urn = feature_urn or proposal.feature_dataset_urn
        if not exclude and proposal.exclude:
            exclude = sorted(proposal.exclude)

        if feature_urn is None and previous is None:
            console.print(
                "[red]Nothing in the graph says which table this model trains on, "
                "so the proposal is incomplete. Rerun with --features <table>"
                f"{', picking one of the above' if proposal.candidates else ''}.[/red]"
            )
            raise typer.Exit(code=2)

        if label_column is None and proposal.label_column_urn is None:
            source = (
                f"The {from_} declaration names no label column"
                if from_ is not None
                else "Nothing in the graph names this model's label"
            )
            console.print(
                f"[red]{source}, so the proposal is incomplete. Rerun with "
                "--label-column <column>.[/red]"
            )
            raise typer.Exit(code=2)

        if not (yes or dry_run) and not typer.confirm("\nDeclare this?", default=True):
            console.print("[yellow]Nothing was written.[/yellow]")
            raise typer.Exit(code=0)

    if label_column is not None:
        try:
            label_dataset_urn = (
                resolve_table(conn, label_table) if label_table is not None else feature_urn
            )
        except TableResolutionError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        if label_dataset_urn is None:
            console.print("[red]--label-column needs --label-table, or --features.[/red]")
            raise typer.Exit(code=2)
        label_column_urn = str(SchemaFieldUrn(label_dataset_urn, label_column))
    elif proposal is not None and proposal.label_column_urn is not None:
        label_column_urn = proposal.label_column_urn
    elif previous is not None:
        label_column_urn = previous.label_column_urn
    else:
        console.print(
            "[red]link needs --label-column the first time. Later runs on this "
            "model can omit it.[/red]"
        )
        raise typer.Exit(code=2)

    if feature_urn is None:
        if previous is None:
            console.print(
                "[red]link needs --features the first time. Later runs on this "
                "model can omit it.[/red]"
            )
            raise typer.Exit(code=2)
        feature_urn = previous.feature_dataset_urn
        console.print(f"[dim]reusing the recorded link: {feature_urn}[/dim]")

    excluded = frozenset(exclude) if exclude else (previous.exclude if previous else frozenset())

    try:
        result = link_model(
            conn,
            config,
            model_urn=model_urn,
            feature_dataset_urn=feature_urn,
            label_column_urn=label_column_urn,
            exclude=excluded,
            dry_run=dry_run,
        )
    except LinkError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    console.print(f"features    {len(result.feature_urns)} declared on {result.model_urn}")
    console.print(f"label       {label_column_urn}")
    if len(result.label_column_urns) > 1:
        # The ancestors matter to a reader: they are what a feature has to
        # descend from to be caught, and they are columns the user did not name.
        console.print(
            f"            and {len(result.label_column_urns) - 1} upstream column(s) "
            "it derives from, so a feature built from the same source is caught"
        )
    if result.training_runs:
        console.print(
            f"training    {len(result.training_runs)} run(s), "
            f"{result.snapshot_columns} column(s) of schema captured"
        )
    else:
        console.print(
            "[yellow]training    no run recorded on the model, so schema drift "
            "stays unevaluated[/yellow]"
        )

    if dry_run:
        console.print("\n[dim]Dry run: nothing was written.[/dim]")
    else:
        console.print(f"\n[dim]Now run: janus scan --model {model}[/dim]")


@app.command()
def crosswalk() -> None:
    """Print the map from each detector to the NIST AI RMF subcategory it evidences.

    Connects to nothing and reads no graph: the crosswalk is a fact about the
    detectors, not about anybody's catalog, so it works on a fresh install before
    a token exists. Markdown on stdout, so it can be piped straight into whatever
    document a governance function actually files.

    It is a mapping and not a conformity claim, and the artifact says so in its
    own first paragraph. A generated document that implied conformity would be
    worse than no document.
    """
    # print rather than console.print: this output is meant to be redirected to a
    # file, and rich would soft-wrap the table cells to the terminal width.
    print(crosswalk_markdown())


@app.command(name="model-card")
def model_card(
    model: Annotated[
        str,
        typer.Option("--model", help="Model name or URN to document."),
    ],
    write: Annotated[
        bool,
        typer.Option("--write/--dry-run", help="Publish to DataHub, or print and write nothing."),
    ] = False,
) -> None:
    """Generate a model card for one model, from what the catalog records (T-12).

    Intended use where somebody declared it, where the training data came from,
    the trust score with its waterfall, the findings open against it, and the
    checks that could not run. Mitchell et al. 2019 proposed the artifact;
    everything in it here is read from DataHub rather than maintained by hand,
    so it is current by construction and empty where the catalog is.

    Prints by default and writes nothing. `--write` publishes it as a document
    linked to the model, keyed on the model alone so a rerun replaces the card
    rather than adding a second one.
    """
    _publish_model_document(model, write=write, kind="card")


@app.command(name="evidence-pack")
def evidence_pack(
    model: Annotated[
        str,
        typer.Option("--model", help="Model name or URN to assemble evidence for."),
    ],
    write: Annotated[
        bool,
        typer.Option("--write/--dry-run", help="Publish to DataHub, or print and write nothing."),
    ] = False,
) -> None:
    """Assemble the EU AI Act Article 10 evidence pack for one model (T-13).

    Training-data provenance, the input schema at training time, governance
    findings, record-keeping, and ownership, mapped to Article 10 and Article 12
    by number so a reader can check the mapping rather than trust it.

    **It is not a conformity assessment and not a certification**, it says so in
    its own first paragraph, and what it could not establish is its first
    section rather than a closing caveat. A generated document that implied
    conformity would be worse than no document at all.
    """
    _publish_model_document(model, write=write, kind="evidence-pack")


@app.command(name="feature-card")
def feature_card(
    model: Annotated[
        str,
        typer.Option("--model", help="Model whose features to document."),
    ],
    feature: Annotated[
        str | None,
        typer.Option("--feature", help="Only features whose name contains this."),
    ] = None,
    write: Annotated[
        bool,
        typer.Option("--write/--dry-run", help="Publish to DataHub, or print and write nothing."),
    ] = False,
) -> None:
    """Generate a Data Card for each of a model's features (T-19).

    Where the feature is computed from, hop by hop; every table that derivation
    crosses and how current each one is; whether it reaches a column classified
    as restricted or as a protected attribute; whether its type has moved since
    training; and, when a finding names it, the changes that would clear it.

    Taken per model rather than per feature because that is what somebody has in
    hand: `--feature` filters within it by substring. Read-only either way,
    like the model card: generating documentation must not raise incidents.
    """
    conn, config, _, _, model_urn = _prepare(
        table=None,
        model=model,
        sla_hours=None,
        no_llm=True,
        llm_provider=None,
        llm_model=None,
        writes=write,
    )
    if model_urn is None:
        console.print("[red]This command needs --model.[/red]")
        raise typer.Exit(code=2)

    report = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)
    properties = conn.graph.get_aspect(model_urn, MLModelPropertiesClass)
    feature_urns = [
        urn
        for urn in ((properties.mlFeatures or []) if properties else [])
        if feature is None or feature.lower() in MlFeatureUrn.from_string(urn).name.lower()
    ]
    if not feature_urns:
        console.print(
            f"[yellow]No matching feature on {model}.[/yellow] A model with no declared "
            "features has nothing to document: `janus link` writes that join."
        )
        raise typer.Exit(code=0)

    model_name = MlModelUrn.from_string(model_urn).name
    for feature_urn in feature_urns:
        facts = gather_feature(
            conn, feature_urn, model_urn, model_name, config, findings=report.findings
        )
        print(render_feature_card(facts))
        if write:
            urn = publish_feature_card(conn, facts, run_id=report.run_id)
            console.print(f"\n[green]Published[/green] {urn}\n")


def _publish_model_document(model: str, *, write: bool, kind: str) -> None:
    """Shared body: scan read-only, gather the facts, render, optionally publish."""
    conn, config, _, _, model_urn = _prepare(
        table=None,
        model=model,
        sla_hours=None,
        no_llm=True,
        llm_provider=None,
        llm_model=None,
        writes=write,
    )
    if model_urn is None:
        console.print("[red]This command needs --model.[/red]")
        raise typer.Exit(code=2)

    # Read-only always: the artifacts describe what a scan would find, and a
    # document generation that also raised incidents would be a surprising
    # side effect of asking for documentation.
    report = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)
    facts = gather(
        conn,
        model_urn,
        config,
        findings=report.findings,
        gaps=report.not_evaluated,
        score=report.trust[0].score if report.trust else None,
    )

    render = render_model_card if kind == "card" else render_evidence_pack
    print(render(facts))

    if write:
        publish = publish_model_card if kind == "card" else publish_evidence_pack
        urn = publish(conn, facts, run_id=report.run_id)
        console.print(f"\n[green]Published[/green] {urn}")


def main() -> None:
    """Entry point for the ``janus`` command."""
    app()
