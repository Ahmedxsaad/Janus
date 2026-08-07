"""Janus as an MCP server: the detectors, asked conversationally.

``janus scan`` and ``janus gate`` are triggers for a human at a terminal
or a CI runner. This is a third trigger, for an MCP client such as Claude Desktop:
an operator asks "is credit_risk_v3 leaking?" in plain language, and the model
calls a tool here to get a real, measured answer instead of guessing from what it
already knows about the codebase.

Read-only, and not by convention: by the same design law that governs every other
entry point (CONTRIBUTING.md). Detection is deterministic Python; an
LLM never decides whether a finding exists, and here the LLM is not even
Janus's own narrator, it is whatever model the MCP client is running,
completely outside this project's control. Handing a tool like that a write
capability would let a conversation turn into an unreviewed mutation of the
governance graph. So every tool below wraps ``run_scan`` in dry-run and nothing
else: the client can ask what is wrong, never fix it. ``scan --write`` and
``gate --write`` remain the write paths, invoked by a human who typed the
command, which is the same boundary ``janus gate`` draws for CI.

Every tool is annotated ``readOnlyHint: true`` at registration, so an MCP client
that surfaces that hint to its own user shows the tool as safe before it is ever
called.

Run it
------
``janus-mcp`` starts the server on stdio, the transport Claude Desktop and
most MCP clients expect. Point a client's config at the installed console script;
``DATAHUB_GMS_URL`` (and ``DATAHUB_GMS_TOKEN`` if the instance requires one) must
be set in the environment the client launches it with, exactly as for the CLI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from janus.agent.pipeline import run_scan
from janus.cli import (
    ModelResolutionError,
    TableResolutionError,
    resolve_model,
    resolve_table,
)
from janus.client import DataHubConnection, DataHubConnectionError, connect
from janus.config import ScanConfig
from janus.env import ConfigError
from janus.gate import GatePolicy, evaluate, summary
from janus.models import (
    Finding,
    FreshnessFinding,
    LeakageFinding,
    SchemaDriftFinding,
    Severity,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.fastmcp import FastMCP

#: What `janus-mcp` says when the [mcp] extra is not installed. The import is
#: deliberately not at module level: the console script would then die with an
#: ImportError traceback quoting a site-packages path, where every other
#: optional extra in this project (feast, kafka, pet) names itself and the
#: command that installs it.
_MCP_EXTRA = (
    "serving over MCP needs the mcp package, which is an optional extra: "
    'pip install "janus-datahub[mcp]"'
)


def _render_finding(finding: Finding) -> str:
    """One finding as plain text: no rich markup, no color, an LLM reads this."""
    lines = [f"- {finding.title} (severity: {finding.severity.value})"]
    if isinstance(finding, LeakageFinding):
        leak = finding.leak
        lines.append(f"  leak path: {' <- '.join(leak.column_path)}")
        lines.append(f"  model: {finding.model.name}{' LIVE' if finding.model.is_live else ''}")
    elif isinstance(finding, FreshnessFinding):
        radius = finding.blast_radius
        lines.append(
            f"  stale for {radius.signal.lag_hours:.1f}h against a "
            f"{radius.signal.sla_hours:.1f}h SLA"
        )
        lines.append(
            f"  blast radius: {len(radius.downstream_datasets)} dataset(s), "
            f"{len(radius.models)} model(s)"
        )
        for model in radius.models:
            lines.append(f"    - {model.name} ({model.severity.value})")
    elif isinstance(finding, SchemaDriftFinding):
        lines.append(f"  model: {finding.model.name}")
        for change in finding.changes:
            lines.append(f"  - {change.describe()}")
    return "\n".join(lines)


def _connect(*, require_token: bool = False) -> DataHubConnection:
    """Connect or raise a plain message an MCP client can show verbatim.

    A separate wrapper from ``cli.py``'s ``_prepare`` rather than a shared call,
    because that one raises ``typer.Exit`` with codes meant for a shell, which
    means nothing to an MCP client. The error text is worth sharing; the exit
    mechanism is not.
    """
    try:
        return connect(require_token=require_token)
    except DataHubConnectionError as exc:
        raise RuntimeError(str(exc)) from exc


def check_leakage(model: str) -> str:
    """Audit a model for target leakage and training-serving schema drift.

    Read-only: detects and explains, writes nothing to DataHub. Pass a bare name
    such as ``credit_risk_v3`` or a full ``urn:li:mlModel:...``.
    """
    conn = _connect()
    try:
        model_urn = resolve_model(conn, model)
    except ModelResolutionError as exc:
        raise RuntimeError(str(exc)) from exc

    config = ScanConfig.from_env()
    report = run_scan(conn, config, model_urn=model_urn, llm=None, dry_run=True)

    if not report.writes:
        return f"{model_urn}: no leakage or schema drift found."

    findings = "\n".join(_render_finding(write.finding) for write in report.writes)
    trust = "\n".join(
        f"{t.model_name}: {t.score.value}/100 ({t.score.band.value})" for t in report.trust
    )
    return f"{findings}\n\nTrust score:\n{trust}" if trust else findings


def check_freshness(table: str) -> str:
    """Audit a table for staleness and trace its blast radius into live models.

    Read-only. Pass a bare name such as ``loans_raw`` or a full dataset URN.
    """
    conn = _connect()
    try:
        table_urn = resolve_table(conn, table)
    except TableResolutionError as exc:
        raise RuntimeError(str(exc)) from exc

    config = ScanConfig.from_env()
    report = run_scan(conn, config, table_urn=table_urn, llm=None, dry_run=True)

    if not report.writes:
        return f"{table_urn}: fresh, nothing at risk."
    return "\n".join(_render_finding(write.finding) for write in report.writes)


def check_gate(
    model: str | None = None,
    table: str | None = None,
    block_at_or_above: str | None = None,
    min_trust: float | None = None,
) -> str:
    """Ask whether a model or table would pass Janus's CI gate.

    The same policy ``janus gate`` enforces in a pipeline, asked
    conversationally. Read-only regardless of the answer: this tool can say a
    build would be blocked, it cannot block one or write anything.

    Args:
        model: A model name or URN to check.
        table: A table name or URN to check. At least one of model or table
            is required.
        block_at_or_above: Fail on a finding this severe or worse: critical,
            high, medium, low. Leave unset along with min_trust to report
            findings without judging them against a policy.
        min_trust: Fail when any model's trust score is below this (0-100).
    """
    if model is None and table is None:
        raise RuntimeError("pass model, table, or both")

    try:
        policy = GatePolicy(
            block_at_or_above=Severity(block_at_or_above) if block_at_or_above else None,
            min_trust_score=min_trust,
        )
    except ValueError as exc:
        allowed = ", ".join(level.value for level in Severity)
        raise RuntimeError(
            f"{block_at_or_above!r} is not a severity. Use one of: {allowed}."
        ) from exc

    conn = _connect()
    try:
        model_urn = resolve_model(conn, model) if model is not None else None
        table_urn = resolve_table(conn, table) if table is not None else None
    except (ModelResolutionError, TableResolutionError) as exc:
        raise RuntimeError(str(exc)) from exc

    config = ScanConfig.from_env()
    report = run_scan(
        conn, config, table_urn=table_urn, model_urn=model_urn, llm=None, dry_run=True
    )
    verdict = evaluate(report, policy)

    lines = [summary(verdict)]
    for violation in verdict.violations:
        lines.append(f"- {violation.headline}: {violation.detail}")
    return "\n".join(lines)


def create_server() -> FastMCP:
    """Build the server and register every tool. Called once, by ``main``.

    Raises:
        ImportError: The ``[mcp]`` extra is not installed. ``main`` turns this
            into the one-line message a user can act on.
    """
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    # Every tool below carries this. A client that surfaces the hint shows the
    # tool as safe before it is ever invoked, not only after reading its
    # description.
    read_only = ToolAnnotations(readOnlyHint=True)

    mcp = FastMCP(
        name="janus",
        instructions=(
            "Audits ML models and their source tables on DataHub for target "
            "leakage, training-serving schema drift, and upstream staleness, "
            "using deterministic column-level lineage analysis rather than "
            "asking a model to guess. Every tool here is read-only: it explains "
            "what is wrong, it never writes to DataHub."
        ),
    )

    mcp.tool(
        name="check_leakage",
        description=check_leakage.__doc__,
        annotations=read_only,
    )(check_leakage)

    mcp.tool(
        name="check_freshness",
        description=check_freshness.__doc__,
        annotations=read_only,
    )(check_freshness)

    mcp.tool(
        name="check_gate",
        description=check_gate.__doc__,
        annotations=read_only,
    )(check_gate)

    return mcp


def main() -> None:
    """Entry point for the ``janus-mcp`` command. Serves over stdio.

    Connects once before serving, and discards the connection: each tool call
    opens its own, exactly as ``scan`` and ``gate`` do per invocation, since
    nothing here holds state between calls. The point of connecting here is
    failing fast. A client that cannot reach DataHub, or whose thresholds are
    unusable, should fail on startup with one clear message, not on the first
    question a user happens to ask it.
    """
    try:
        server = create_server()
    except ImportError as exc:
        raise SystemExit(_MCP_EXTRA) from exc

    try:
        ScanConfig.from_env()
        connect()
    except (ConfigError, DataHubConnectionError) as exc:
        raise SystemExit(f"{exc}") from exc

    server.run()


if __name__ == "__main__":
    main()
