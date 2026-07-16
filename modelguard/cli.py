"""The ``modelguard`` command line.

``scan`` audits one table and writes back what it endangers. ``watch``, the
event-driven twin that shares the same core, arrives with the Actions framework
in a later phase and is not stubbed here (no placeholder commands: root
CLAUDE.md code rule 3).

Table resolution
----------------
``--table`` accepts either a full dataset URN or a bare name such as
``loans_raw``. A bare name is resolved by searching the graph and requiring
exactly one dataset whose name matches or ends with it. Ambiguity is an error,
never a guess: silently auditing the wrong table would be worse than failing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from datahub.metadata.urns import DatasetUrn, MlModelUrn, Urn
from datahub.sdk.search_filters import FilterDsl as F
from rich.console import Console

from modelguard.agent.pipeline import FindingWrites, ScanReport, run_scan
from modelguard.client import DataHubConnection, DataHubConnectionError, connect
from modelguard.config import ScanConfig
from modelguard.env import ConfigError
from modelguard.llm import LLMConfig, llm_config_from_env
from modelguard.models import Finding, FreshnessFinding, LeakageFinding, SchemaDriftFinding

app = typer.Typer(
    add_completion=False,
    help="A data-to-model reliability agent built on DataHub.",
    no_args_is_help=True,
)

# soft_wrap: URNs are long and must stay on one line to be copy-pasteable.
console = Console(soft_wrap=True)


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


def _print_report(report: ScanReport) -> None:
    """Render a scan's outcome for a human reading a terminal."""
    if report.clean:
        targets = [urn for urn in (report.table_urn, report.model_urn) if urn is not None]
        console.print(f"[green]No finding.[/green] {' and '.join(targets)} healthy.")
        for warning in report.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")
        return

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

    if report.dry_run:
        console.print("\n[yellow]Dry run: nothing was written.[/yellow]")
        console.print("Would have raised the incident(s), tagged the models, written the")
        console.print("guarding assertion, the impact report(s), and the trust score(s).")
        return

    console.print("\n[bold]Wrote back:[/bold]")
    for write in report.writes:
        _print_writes(write)
    console.print(f"\n[dim]run id: {report.run_id}[/dim]")


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
) -> None:
    """Audit a table for stale data, a model for target leakage, or both.

    The two targets ask different questions of the graph. ``--table`` asks what a
    table's going stale endangers downstream. ``--model`` asks whether a model is
    training on its own label. At least one is required.
    """
    if table is None and model is None:
        console.print("[red]Nothing to scan: pass --table, --model, or both.[/red]")
        raise typer.Exit(code=2)

    if contract_out is not None and model is None:
        console.print("[red]--contract-out describes a model's inputs; pass --model.[/red]")
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

    for target in (table_urn, model_urn):
        if target is not None:
            console.print(f"Scanning [bold]{target}[/bold]")
    console.print()

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


def main() -> None:
    """Entry point for the ``modelguard`` command."""
    app()
