"""Folding the flags a user typed into an inferred proposal (D-150).

The write path always honoured a typed flag; for a while the *printed* proposal
did not. `link --infer --label-column churned` showed reasons still reporting
the label as NOT FOUND, rendered a command without `--label-column` in it, and
then asked the user to confirm it. Nothing wrong was written, but the command
shown for confirmation was not the command that would run, which is the one
thing a confirmation prompt cannot get away with.

So these assert on what would be *printed*: the rendered command and the reason
lines, not just the resolved URNs. Offline: no DataHub, no network. Resolving
`--label-table` is the only step that would touch a connection, and only the
test that passes one needs a stub for it.
"""

from __future__ import annotations

from janus.cli import _with_typed_flags
from janus.writeback.link_infer import LinkProposal
from tests.conftest import FEATURE_TABLE_URN, LABEL_COLUMN_URN, MODEL_URN, TABLE_URN

OTHER_TABLE_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,ecommerce.public.other,PROD)"


def _incomplete() -> LinkProposal:
    """A proposal that found a feature table but no label, the common real case."""
    return LinkProposal(
        model_urn=MODEL_URN,
        feature_dataset_urn=FEATURE_TABLE_URN,
        label_column_urn=None,
        exclude=frozenset(),
        reasons=(
            "feature table: the only input recorded on the training run(s)",
            "label column: NOT FOUND. Nothing names a label, so pass --label-column",
        ),
    )


def test_typed_label_replaces_the_not_found_reason() -> None:
    """The line telling the user to pass --label-column must not survive them passing it."""
    merged = _with_typed_flags(
        None,  # type: ignore[arg-type]  # no connection needed without --label-table
        _incomplete(),
        feature_urn=None,
        label_column="churned",
        label_table=None,
        exclude=None,
    )

    assert merged.label_column_urn == f"urn:li:schemaField:({FEATURE_TABLE_URN},churned)"
    assert merged.complete
    assert "label column: churned, from --label-column" in merged.reasons
    assert not any("NOT FOUND" in reason for reason in merged.reasons)
    # The feature table was not overridden, so its inferred reason stays.
    assert any(reason.startswith("feature table: the only input") for reason in merged.reasons)


def test_the_rendered_command_is_the_command_that_runs() -> None:
    """What the confirmation prompt shows has to carry every typed flag."""
    merged = _with_typed_flags(
        None,  # type: ignore[arg-type]
        _incomplete(),
        feature_urn=None,
        label_column="churned",
        label_table=None,
        exclude=["customer_id"],
    )

    command = merged.command()
    assert "--label-column churned" in command
    assert "--exclude customer_id" in command


def test_typed_features_wins_over_the_inferred_table() -> None:
    """A user who names the table knows their catalog better than the inference."""
    merged = _with_typed_flags(
        None,  # type: ignore[arg-type]
        _incomplete(),
        feature_urn=OTHER_TABLE_URN,
        label_column="churned",
        label_table=None,
        exclude=None,
    )

    assert merged.feature_dataset_urn == OTHER_TABLE_URN
    assert "ecommerce.public.other" in merged.command()
    # The label hangs off the table that won, not the one that was inferred.
    assert merged.label_column_urn == f"urn:li:schemaField:({OTHER_TABLE_URN},churned)"
    assert not any(reason.startswith("feature table: the only input") for reason in merged.reasons)


def test_label_table_sends_the_label_to_another_dataset(monkeypatch) -> None:
    """--label-table is where a warehouse usually keeps its labels: not the feature table."""
    monkeypatch.setattr("janus.cli.resolve_table", lambda conn, table: TABLE_URN)

    merged = _with_typed_flags(
        None,  # type: ignore[arg-type]
        _incomplete(),
        feature_urn=None,
        label_column="default_status",
        label_table="ecommerce.public.loans_raw",
        exclude=None,
    )

    assert merged.label_column_urn == LABEL_COLUMN_URN
    assert "--label-table ecommerce.public.loans_raw" in merged.command()


def test_no_typed_flags_returns_the_proposal_untouched() -> None:
    """Plain --infer must render exactly what the inference decided, warts and all."""
    proposal = _incomplete()
    merged = _with_typed_flags(
        None,  # type: ignore[arg-type]
        proposal,
        feature_urn=None,
        label_column=None,
        label_table=None,
        exclude=None,
    )

    assert merged is proposal
    assert not merged.complete
