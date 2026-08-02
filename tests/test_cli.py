from __future__ import annotations

import time
from dataclasses import replace

import pytest
from datahub.metadata.schema_classes import (
    AuditStampClass,
    DeploymentStatusClass,
    GlobalTagsClass,
    IncidentInfoClass,
    IncidentStateClass,
    IncidentStatusClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OperationClass,
    OperationTypeClass,
    StructuredPropertiesClass,
)

from modelguard.cli import (
    WATCH_FAILURE_ESCALATION_THRESHOLD,
    TableResolutionError,
    WatchState,
    _watch_failure_message,
    _watch_once,
    app,
    resolve_table,
    safe_error,
)
from modelguard.client import ENV_GMS_TOKEN, DataHubConnection
from modelguard.config import ScanConfig
from tests.conftest import (
    DEPLOYMENT_URN,
    LEAK_FEATURE_URN,
    MODEL_URN,
    TABLE_URN,
    FakeClient,
    FakeGraph,
    lineage_result,
    make_connection,
)

OTHER_TABLE = "urn:li:dataset:(urn:li:dataPlatform:bigquery,analytics.public.loans_raw,PROD)"


def _conn(search_urns: list[str]) -> DataHubConnection:
    return make_connection(FakeGraph(), FakeClient(search_urns=search_urns))


def test_a_full_urn_is_used_as_given():
    assert resolve_table(_conn([]), TABLE_URN) == TABLE_URN


def test_a_malformed_urn_is_rejected_rather_than_scanned():
    with pytest.raises(Exception, match="urn"):
        resolve_table(_conn([]), "urn:li:dataset:not-a-real-urn")


def test_a_bare_table_name_resolves_through_search():
    assert resolve_table(_conn([TABLE_URN]), "loans_raw") == TABLE_URN


def test_a_fully_qualified_name_resolves_too():
    assert resolve_table(_conn([TABLE_URN]), "ecommerce.public.loans_raw") == TABLE_URN


def test_a_search_hit_whose_name_does_not_match_is_ignored():
    """Search is fuzzy. Only an exact name or last-segment match counts."""
    unrelated = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.loans_archive,PROD)"
    with pytest.raises(TableResolutionError, match="no dataset named"):
        resolve_table(_conn([unrelated]), "loans_raw")


def test_an_unknown_table_on_a_local_quickstart_points_at_the_seeder():
    local = replace(_conn([]), gms_url="http://localhost:8080")
    with pytest.raises(TableResolutionError, match="modelguard-seed"):
        resolve_table(local, "nonexistent")


def test_an_unknown_table_on_a_remote_instance_never_suggests_seeding():
    """Seeding somebody's real catalog would write demo datasets into it."""
    with pytest.raises(TableResolutionError, match="no dataset named") as raised:
        resolve_table(_conn([]), "nonexistent")
    assert "modelguard-seed" not in str(raised.value)


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Two platforms hold a loans_raw. Scanning the wrong one silently is worse than failing."""
    with pytest.raises(TableResolutionError, match="matches 2 datasets"):
        resolve_table(_conn([TABLE_URN, OTHER_TABLE]), "loans_raw")


# --------------------------------------------------------------------------
# watch: poll, act on transitions only
# --------------------------------------------------------------------------

FEATURE_TABLE = (
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.customer_features,PROD)"
)
HOUR = 3_600_000


def _watch_fixture(lag_hours: float) -> tuple[FakeGraph, FakeClient]:
    """A live model downstream of a table that is ``lag_hours`` old, per its operation aspect.

    ``watch`` measures staleness against real wall-clock time (it has no fixed
    ``now``), so the operation timestamp is anchored to ``time.time()`` rather than
    the fixed ``NOW_MS`` the ``now_ms``-driven tests use.
    """
    now_ms = int(time.time() * 1000)
    graph = FakeGraph(
        {
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(
                name="Credit Risk v3", deployments=[DEPLOYMENT_URN], mlFeatures=[LEAK_FEATURE_URN]
            ),
            (DEPLOYMENT_URN, MLModelDeploymentPropertiesClass): (
                MLModelDeploymentPropertiesClass(status=DeploymentStatusClass.IN_SERVICE)
            ),
        },
        timeseries={
            (TABLE_URN, OperationClass): OperationClass(
                timestampMillis=now_ms,
                operationType=OperationTypeClass.UPDATE,
                lastUpdatedTimestamp=now_ms - int(lag_hours * HOUR),
                actor="urn:li:corpuser:datahub",
            )
        },
    )
    graph.graphql_response = {"raiseIncident": "urn:li:incident:abc"}
    client = FakeClient(
        lineage_results=[
            lineage_result(FEATURE_TABLE, 1),
            lineage_result(LEAK_FEATURE_URN, 2),
            lineage_result(MODEL_URN, 3),
        ]
    )
    return graph, client


def _poll(
    graph: FakeGraph, client: FakeClient, previous: frozenset | None
) -> frozenset[tuple[str, str, str, str]]:
    return _watch_once(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=None,
        llm=None,
        previous=previous,
    )


def test_the_signature_is_stable_across_polls_of_an_unchanged_stale_state():
    """Two polls of the same stale table compare equal, so watch does not re-fire."""
    graph, client = _watch_fixture(30.0)
    first = _poll(graph, client, previous=None)
    # A second poll of the same unchanged state must yield the identical signature.
    graph.emitted.clear()
    graph.graphql_calls.clear()
    second = _poll(graph, client, previous=first)
    assert first == second
    assert first != frozenset()


def test_a_newly_stale_table_is_written_back_on_the_first_poll():
    graph, client = _watch_fixture(30.0)
    signature = _poll(graph, client, previous=None)

    assert signature != frozenset()
    # The transition from clean to stale wrote the incident back.
    assert len(graph.graphql_calls) == 1
    _, variables = graph.graphql_calls[0]
    assert variables["input"]["resourceUrn"] == TABLE_URN


def test_an_unchanged_finding_set_writes_nothing_on_the_next_poll():
    """Idempotent by design, but re-writing every poll would be noise: stay quiet."""
    graph, client = _watch_fixture(30.0)
    signature = _poll(graph, client, previous=None)
    graph.emitted.clear()
    graph.graphql_calls.clear()

    unchanged = _poll(graph, client, previous=signature)

    assert unchanged == signature
    assert graph.emitted == [], "an unchanged finding set must not write again"
    assert graph.graphql_calls == []


def test_a_healthy_target_writes_nothing_and_has_an_empty_signature():
    graph, client = _watch_fixture(1.0)  # within the 6h default SLA
    signature = _poll(graph, client, previous=None)

    assert signature == frozenset()
    assert graph.emitted == []
    assert graph.graphql_calls == []


def test_finding_signature_carries_the_incident_dedup_key():
    """Signature starts with the incident key and includes measured severity."""
    graph, client = _watch_fixture(30.0)
    signature = _poll(graph, client, previous=None)
    (entry,) = signature
    finding_type, resource_urn, title, severity = entry
    assert finding_type == "upstream-freshness"
    assert resource_urn == TABLE_URN
    assert title == "Stale upstream data in ecommerce.public.loans_raw"
    assert severity == "critical"


def test_recovery_resolves_incident_and_clears_model_risk_state():
    """A recovered finding must not leave stale governance metadata behind."""
    graph, client = _watch_fixture(30.0)
    state = WatchState()
    first = _watch_once(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=None,
        llm=None,
        previous=None,
        state=state,
    )
    assert first

    incident_urn = "urn:li:incident:abc"
    stamp = AuditStampClass(time=0, actor="urn:li:corpuser:datahub")
    graph._related[TABLE_URN] = [incident_urn]
    graph._aspects[(incident_urn, IncidentInfoClass)] = IncidentInfoClass(
        type="FRESHNESS",
        entities=[TABLE_URN],
        title="Stale upstream data in ecommerce.public.loans_raw",
        description="body",
        status=IncidentStatusClass(state=IncidentStateClass.ACTIVE, lastUpdated=stamp),
        created=stamp,
    )
    graph.graphql_response = {"updateIncidentStatus": True}
    graph.graphql_calls.clear()
    graph.emitted.clear()
    now_ms = int(time.time() * 1000)
    graph._timeseries[(TABLE_URN, OperationClass)] = OperationClass(
        timestampMillis=now_ms,
        operationType=OperationTypeClass.UPDATE,
        lastUpdatedTimestamp=now_ms - HOUR,
        actor="urn:li:corpuser:datahub",
    )

    recovered = _watch_once(
        make_connection(graph, client),
        ScanConfig(),
        table_urn=TABLE_URN,
        model_urn=None,
        llm=None,
        previous=state.signature,
        state=state,
    )

    assert recovered == frozenset()
    assert any("updateIncidentStatus" in query for query, _ in graph.graphql_calls)
    tags = graph.get_aspect(MODEL_URN, GlobalTagsClass)
    assert tags is None or all(tag.tag != "urn:li:tag:model-at-risk" for tag in tags.tags)
    properties = graph.get_aspect(MODEL_URN, StructuredPropertiesClass)
    assert properties is not None
    assert all(
        a.propertyUrn != "urn:li:structuredProperty:modelguard.risk_flags"
        for a in properties.properties
    )


def test_the_cli_never_renders_locals_into_a_traceback():
    """Locals in a traceback would print the DataHub token.

    The frames that open a connection hold the raw token, and the SDK's own
    DatahubClientConfig prints it in its repr, so a pretty traceback carrying
    locals would put a credential on the terminal and into any CI log. Typer's
    default is already False; this asserts ModelGuard's own choice, so an upstream
    change of default cannot quietly turn it back on.
    """
    assert app.pretty_exceptions_show_locals is False


def test_an_exception_printed_to_the_console_carries_no_token(monkeypatch, capsys):
    """``gate`` prints an SDK failure straight into a CI log the whole team reads.

    ModelGuard's own errors name a variable and never its value, but an exception
    surfacing from someone else's SDK may quote a request or a header we handed
    the token to (root CLAUDE.md rule 6d).
    """
    secret = "dh-token-super-secret-value"
    monkeypatch.setenv(ENV_GMS_TOKEN, secret)

    rendered = safe_error(RuntimeError(f"401 Unauthorized: Authorization=Bearer {secret}"))

    assert secret not in rendered
    # Still readable: the operator has to be able to act on it.
    assert "401 Unauthorized" in rendered


def test_a_watch_failure_message_names_the_real_error_not_just_its_class(monkeypatch):
    """F12: the class name alone threw away the one detail an operator needed."""
    monkeypatch.delenv(ENV_GMS_TOKEN, raising=False)
    exc = RuntimeError("GMS returned 503: search index unavailable")

    message = _watch_failure_message(exc, consecutive_failures=1)

    assert "503" in message
    assert "search index unavailable" in message


def test_a_watch_failure_message_carries_no_token(monkeypatch):
    secret = "dh-token-super-secret-value"
    monkeypatch.setenv(ENV_GMS_TOKEN, secret)
    exc = RuntimeError(f"401 Unauthorized: Authorization=Bearer {secret}")

    message = _watch_failure_message(exc, consecutive_failures=1)

    assert secret not in message


def test_a_single_watch_failure_stays_routine():
    message = _watch_failure_message(RuntimeError("boom"), consecutive_failures=1)

    assert "not working" not in message
    assert "yellow" in message


def test_repeated_watch_failures_escalate_to_a_daemon_not_working_message():
    """A run of failures is not the expected shape GMS-still-starting-up is."""
    message = _watch_failure_message(
        RuntimeError("boom"), consecutive_failures=WATCH_FAILURE_ESCALATION_THRESHOLD
    )

    assert "not working" in message
    assert "red" in message


def test_a_first_poll_of_a_healthy_target_does_not_claim_a_recovery(capsys):
    """Nothing recovered: the target was already healthy when watch started.

    ``previous`` is None on a process's first poll, which is not the same as an
    empty set, and announcing a recovery there invents an incident that never
    happened.
    """
    graph, client = _watch_fixture(1.0)

    signature = _poll(graph, client, previous=None)

    assert signature == frozenset()
    printed = capsys.readouterr().out
    assert "clean: no findings" in printed
    assert "recovered" not in printed


def test_a_target_that_actually_recovered_is_announced_as_recovered(capsys):
    """The other half: a remembered finding that is gone is a real recovery."""
    graph, client = _watch_fixture(1.0)
    was_failing = frozenset({(TABLE_URN, "upstream-freshness", "Stale upstream data", "critical")})

    signature = _poll(graph, client, previous=was_failing)

    assert signature == frozenset()
    assert "recovered: no findings" in capsys.readouterr().out


def test_a_first_poll_that_finds_something_says_detected(capsys):
    """A first poll is only quiet about recovery, never about a finding."""
    graph, client = _watch_fixture(30.0)

    signature = _poll(graph, client, previous=None)

    assert signature
    assert "detected" in capsys.readouterr().out


def _invoke(*args: str):  # noqa: ANN202 - a typer Result, asserted on inline
    """Run the CLI in-process. Only used for guards that fire before connecting."""
    from typer.testing import CliRunner

    return CliRunner().invoke(app, list(args))


def test_json_output_refuses_the_review_agent_rather_than_hanging():
    """--review prompts a human; there is no JSON document that can wait for that."""
    result = _invoke("scan", "--model", "credit_risk_v3", "--format", "json", "--review")

    assert result.exit_code == 2
    assert "incompatible" in result.output


def test_json_output_is_refused_on_a_whole_catalog_sweep():
    """One report per model, concatenated, is not a JSON document."""
    result = _invoke("scan", "--all-models", "--format", "json")

    assert result.exit_code == 2
    assert "--format" in result.output


def test_an_unknown_output_format_is_a_usage_error_not_a_silent_default():
    result = _invoke("scan", "--model", "credit_risk_v3", "--format", "yaml")

    assert result.exit_code == 2


def test_the_dry_run_review_guard_explains_itself_instead_of_crashing():
    """The guard explains itself rather than dying in the markup parser.

    Rich parses each print alone, so a tag opened in one call and closed in the
    next raised MarkupError and the guard died mid-sentence.
    """
    result = _invoke("scan", "--model", "credit_risk_v3", "--dry-run", "--review")

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_infer_is_refused_on_a_whole_catalog_replay():
    """--all replays recorded facts; --infer proposes guesses for a human to check.

    Combining them would write an unreviewed proposal to every model at once.
    """
    result = _invoke("link", "--all", "--infer")

    assert result.exit_code == 2
    assert "incompatible" in result.output
