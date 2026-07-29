"""The ``modelguard`` command line.

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

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer
from datahub.metadata.urns import DatasetUrn, MlModelUrn, Urn
from datahub.sdk.search_filters import FilterDsl as F
from rich.console import Console

from modelguard.agent.pipeline import FindingWrites, ScanReport, run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.detect.trust_score import trust_inputs_from_findings, trust_score
from modelguard.env import ConfigError
from modelguard.gate import (
    EXIT_ERROR,
    GatePolicy,
    evaluate,
    github_annotations,
    summary,
)
from modelguard.llm import LLMConfig, llm_config_from_env
from modelguard.models import (
    Finding,
    FreshnessFinding,
    LeakageFinding,
    ModelRef,
    SchemaDriftFinding,
    Severity,
)
from modelguard.writeback.incidents import find_active_incident, resolve_incident
from modelguard.writeback.labels import remove_tag
from modelguard.writeback.properties import (
    RISK_FLAGS,
    RUN_ID,
    TRUST_BAND,
    TRUST_SCORE,
    assign_properties,
    read_properties,
    remove_properties,
)
from modelguard.writeback.terms import remove_term

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

WATCH_MAX_BACKOFF_SECONDS = 300.0


@app.callback()
def _main() -> None:
    """Group the subcommands under ``modelguard``.

    Typer promotes a lone command to the root of the app, so without this
    callback the tool would be invoked as ``modelguard --table ...`` and adding
    ``watch`` later would silently change ``scan``'s invocation.
    """


class TableResolutionError(ValueError):
    """The --table argument named zero, or more than one, dataset."""


def resolve_table(conn: DataHubConnection, table: str) -> str:
    """Turn a table name or URN into exactly one dataset URN.

    Args:
        conn: An open connection.
        table: A full dataset URN, or a name like ``loans_raw`` or
            ``ecommerce.public.loans_raw``.

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
        if name == table or name.split(".")[-1] == table:
            matches.append(str(urn))

    unique = sorted(set(matches))
    if not unique:
        raise TableResolutionError(
            f"no dataset named {table!r}. Pass a full dataset URN, or seed the "
            "demo graph first with: modelguard-seed"
        )
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
    for urn in conn.client.search.get_urns(query=model, filter=F.entity_type("mlModel")):
        parsed = Urn.from_string(str(urn))
        if not isinstance(parsed, MlModelUrn):
            continue
        if parsed.name == model or parsed.name.split(".")[-1] == model:
            matches.append(str(urn))

    unique = sorted(set(matches))
    if not unique:
        raise ModelResolutionError(
            f"no model named {model!r}. Pass a full mlModel URN, or seed the "
            "demo graph first with: modelguard-seed"
        )
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
                "API key can only come from MODELGUARD_LLM_API_KEY, which is not set"
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


def _print_clean(report: ScanReport) -> None:
    """Render a scan that found nothing: the healthy targets and any warnings."""
    targets = [urn for urn in (report.table_urn, report.model_urn) if urn is not None]
    console.print(f"[green]No finding.[/green] {' and '.join(targets)} healthy.")
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

    for warning in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")

    if report.trust:
        console.print("\n[bold]Trust scores:[/bold]")
        for trust in report.trust:
            reasons = ", ".join(sorted(trust.score.deductions)) or "no deductions"
            console.print(
                f"  {trust.model_name}: {trust.score.value}/100 ({trust.score.band}) - {reasons}"
            )


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
) -> tuple[DataHubConnection, ScanConfig, LLMConfig | None, str | None, str | None]:
    """Do the setup ``scan`` and ``watch`` share: config, LLM, connect, resolve.

    Both commands need the same five things before they can run a scan, and both
    fail the same way on the same mistakes. Keeping it in one place means a change
    to how a target is resolved or a credential is read cannot drift between them.

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

    if not conn.has_token:
        console.print("[yellow]No DATAHUB_GMS_TOKEN set; writing unauthenticated.[/yellow]")

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
    """In-process state needed to reconcile a finding that has recovered."""

    signature: frozenset[FindingSignature] | None = None
    report: ScanReport | None = None


def _reconcile_recovery(
    conn: DataHubConnection,
    previous: ScanReport,
    config: ScanConfig,
) -> None:
    """Resolve recovered incidents and remove only the risk that recovered.

    The previous dry-run report contains the typed finding and model references
    that are no longer present in a clean scan. Other tags, terms, and risk flags
    are preserved. A trust score is recomputed only when no ModelGuard risk flags
    remain, using the model's current ownership fact and no active findings.
    """
    recovered_models: dict[str, ModelRef] = {}
    recovery_run_id = f"recovery-{int(time.time() * 1000)}"

    for write in previous.writes:
        finding = write.finding
        incident = find_active_incident(
            conn, finding.resource_urn, str(finding.incident_type), finding.title
        )
        if incident is not None:
            resolve_incident(
                conn,
                incident,
                f"Recovered by ModelGuard poll {recovery_run_id}; "
                "the finding is no longer present.",
            )

        if isinstance(finding, LeakageFinding):
            remove_term(conn, finding.leak.feature_urn, config.leakage_risk_term_urn)

        for model in finding.models_at_risk:
            recovered_models[model.urn] = model
            properties = read_properties(conn, model.urn)
            flags = {str(flag) for flag in properties.get(RISK_FLAGS, [])}
            remaining = flags - {str(finding.finding_type)}
            if remaining:
                assign_properties(
                    conn,
                    model.urn,
                    {RISK_FLAGS: sorted(remaining), RUN_ID: [recovery_run_id]},
                )
            else:
                remove_properties(conn, model.urn, {RISK_FLAGS})

    for model_urn, model in recovered_models.items():
        properties = read_properties(conn, model_urn)
        remaining_flags = properties.get(RISK_FLAGS, [])
        if remaining_flags:
            continue

        remove_tag(conn, model_urn, f"urn:li:tag:{config.model_at_risk_tag}")
        score = trust_score(trust_inputs_from_findings((), model), config)
        assign_properties(
            conn,
            model_urn,
            {
                TRUST_SCORE: [float(score.value)],
                TRUST_BAND: [str(score.band)],
                RUN_ID: [recovery_run_id],
            },
        )


def _announce_watch_change(
    previous: frozenset[FindingSignature] | None,
    signature: frozenset[FindingSignature],
    preview: ScanReport,
) -> None:
    """Say what changed since the last poll: a recovery, or new or changed findings."""
    stamp = time.strftime("%H:%M:%S")
    if not signature:
        console.print(f"[green]{stamp} recovered: no findings.[/green]")
        return
    verb = "detected" if not previous else "changed to"
    console.print(f"[bold red]{stamp} {verb} {len(signature)} finding(s):[/bold red]")
    _print_findings_and_trust(preview)


def _watch_once(
    conn: DataHubConnection,
    config: ScanConfig,
    *,
    table_urn: str | None,
    model_urn: str | None,
    llm: LLMConfig | None,
    previous: frozenset[FindingSignature] | None,
    state: WatchState | None = None,
) -> frozenset[FindingSignature]:
    """Poll once: detect, and write back only when the set of findings has changed.

    A dry scan detects without writing; only a changed, non-empty finding set
    triggers the real write-back. Re-writing an unchanged finding every poll would
    be safe (the writes are idempotent) but noisy and pointless, so a steady state
    stays quiet. Returns the current signature, which the caller carries into the
    next poll.
    """
    previous_report = state.report if state is not None else None
    preview = run_scan(
        conn, config, table_urn=table_urn, model_urn=model_urn, llm=llm, dry_run=True
    )
    signature = _finding_signature(preview)

    if signature == previous:
        console.print(
            f"[dim]{time.strftime('%H:%M:%S')} no change ({len(signature)} open finding(s))[/dim]"
        )
        return signature

    _announce_watch_change(previous, signature, preview)
    if signature:
        written = run_scan(conn, config, table_urn=table_urn, model_urn=model_urn, llm=llm)
        _print_writes_section(written)
    elif previous and previous_report is not None:
        _reconcile_recovery(conn, previous_report, config)

    if state is not None:
        state.signature = signature
        state.report = preview
    return signature


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
    at module load: a plain ``modelguard scan`` must run without LangGraph
    installed. The approve callback prints the findings and either prompts or, with
    ``--auto-approve``, writes without asking (the recorded-demo path).
    """
    from modelguard.agent.graph import AgentUnavailableError, run_agent

    def _approve(preview: ScanReport) -> bool:
        _print_findings_and_trust(preview)
        if auto:
            console.print("\n[dim]--auto-approve: writing without prompting.[/dim]")
            return True
        return typer.confirm("\nWrite these findings back to DataHub?", default=False)

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
            help="Freshness SLA in hours. Overrides MODELGUARD_FRESHNESS_SLA_HOURS.",
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
            help="Override MODELGUARD_LLM_PROVIDER: anthropic, openai, or google.",
        ),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override MODELGUARD_LLM_MODEL, the provider's model id."),
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
) -> None:
    """Audit a table for stale data, a model for target leakage, or both.

    The two targets ask different questions of the graph. ``--table`` asks what a
    table's going stale endangers downstream. ``--model`` asks whether a model is
    training on its own label. At least one is required.

    By default the writes land straight away. ``--review`` (or ``--auto-approve``
    for the demo) instead runs the LangGraph agent, which pauses after detection so
    a human can approve the mutations before they are written.
    """
    if contract_out is not None and model is None:
        console.print("[red]--contract-out describes a model's inputs; pass --model.[/red]")
        raise typer.Exit(code=2)

    use_agent = review or auto_approve
    if dry_run and use_agent:
        console.print("[red]--dry-run and --review/--auto-approve are mutually exclusive: ")
        console.print("--review already previews the findings before writing.[/red]")
        raise typer.Exit(code=2)

    conn, config, llm, table_urn, model_urn = _prepare(
        table=table,
        model=model,
        sla_hours=sla_hours,
        no_llm=no_llm,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )

    for target in (table_urn, model_urn):
        if target is not None:
            console.print(f"Scanning [bold]{target}[/bold]")
    console.print()

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
        _print_report(report)

    # The input contract describes the model's expected inputs, not this scan's
    # findings, so it is written even when the scan is clean: a clean model still
    # has a boundary worth contracting (the "before promoting a model" use case).
    if contract_out is not None and model_urn is not None:
        from modelguard.writeback.contract import ContractError, render_input_contract

        try:
            contract_out.write_text(render_input_contract(conn, model_urn, config))
            console.print(f"[dim]wrote {contract_out}[/dim]")
        except ContractError as exc:
            console.print(f"[yellow]{exc}[/yellow]")

    if report.clean:
        return

    if assertion_out is not None and report.assertion_yaml:
        assertion_out.write_text(report.assertion_yaml)
        console.print(f"[dim]wrote {assertion_out}[/dim]")

    if report_out is not None:
        from modelguard.writeback.documents import render_impact_report

        # One file per finding would need one path per finding. The worst finding
        # is the one a human opens first, so that is the one written.
        worst = report.writes[0]
        report_out.write_text(
            render_impact_report(worst.finding, worst.narrative.assessment, report.run_id)
        )
        console.print(f"[dim]wrote {report_out}[/dim]")


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
        typer.Option("--llm-provider", help="Override MODELGUARD_LLM_PROVIDER."),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override MODELGUARD_LLM_MODEL."),
    ] = None,
) -> None:
    """Poll a table and/or model and write back the moment a new problem appears.

    ``watch`` shares ``scan``'s detection and write-back exactly; it only differs
    in what wakes it. It is unattended, so it approves its own writes (there is no
    human to prompt) and, because the writes are idempotent, it acts on the
    transitions, a new finding or a recovery, rather than on every poll.

    This is polling, deliberately: it never depends on Kafka timing, which is what
    makes it reliable for a demo. An event-driven build on DataHub's Actions
    framework (``EntityChangeEvent``) is the upgrade path when poll latency matters.
    """
    # ponytail: polling loop, not the Actions/Kafka EntityChangeEvent consumer.
    # Wire datahub-actions here if sub-poll-interval latency ever matters.
    conn, config, llm, table_urn, model_urn = _prepare(
        table=table,
        model=model,
        sla_hours=sla_hours,
        no_llm=no_llm,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )

    for target in (table_urn, model_urn):
        if target is not None:
            console.print(f"Watching [bold]{target}[/bold]")
    if not once:
        console.print(f"[dim]polling every {interval:.0f}s; Ctrl-C to stop[/dim]")
    console.print()

    state = WatchState()
    backoff = interval
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
                )
                backoff = interval
            except Exception as exc:  # a daemon must survive SDK failures
                console.print(
                    f"[yellow]watch poll failed ({type(exc).__name__}); "
                    f"retrying in {backoff:.0f}s.[/yellow]"
                )
                if once:
                    raise typer.Exit(code=1) from exc
                time.sleep(backoff)
                backoff = min(backoff * 2, WATCH_MAX_BACKOFF_SECONDS)
                continue
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]watch stopped.[/dim]")


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
            "--min-trust", help="Fail when any model's trust score is below this (0-100)."
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
        typer.Option("--no-llm", help="Skip the LLM. A gate needs a verdict, not prose."),
    ] = True,
    llm_provider: Annotated[
        str | None, typer.Option("--llm-provider", help="Override MODELGUARD_LLM_PROVIDER.")
    ] = None,
    llm_model: Annotated[
        str | None, typer.Option("--llm-model", help="Override MODELGUARD_LLM_MODEL.")
    ] = None,
) -> None:
    """Fail the build when a change would ship an unsafe model.

    The preventive half of ModelGuard, for a pull request rather than a postmortem.
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

    # Any setup failure is "could not reach a verdict", so _prepare's own exit
    # codes are remapped onto EXIT_ERROR rather than leaking through as a policy
    # violation. This is the distinction the whole command rests on.
    try:
        conn, config, llm, table_urn, model_urn = _prepare(
            table=table,
            model=model,
            sla_hours=sla_hours,
            no_llm=no_llm,
            llm_provider=llm_provider,
            llm_model=llm_model,
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
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_ERROR) from exc

    for finding in (write_.finding for write_ in report.writes):
        _print_finding(finding)
    for line in github_annotations(verdict):
        # Printed raw: GitHub reads these as workflow commands and turns them into
        # inline pull-request annotations. Other CI systems see ordinary output.
        print(line)

    colour = "red" if verdict.blocked else "green"
    console.print(f"[{colour}]{summary(verdict)}[/{colour}]")
    if write:
        console.print(f"[dim]run id: {report.run_id}[/dim]")

    raise typer.Exit(code=verdict.exit_code)


def main() -> None:
    """Entry point for the ``modelguard`` command."""
    app()
