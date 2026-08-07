"""The reusable GitHub Action's contract with the workflow that consumes it.

`action.yml` is shipped as much as the CLI is: a team adopts the gate by writing
five lines of YAML against these inputs and outputs, and nothing in the Python
suite touches the file. What is pinned here is the one promise a workflow can
act on wrongly.

`janus gate` answers in three exit codes, and janus/CLAUDE.md rule 2 is explicit
that exit 2 is never a finding: a gate reporting "I could not connect" as a
violation teaches a team to ignore every red build. The Action used to set
`blocked=true` for exit 2 as well as exit 1, so a downstream step doing
`if: steps.gate.outputs.blocked == 'true'` would post "this model is unsafe"
because DataHub happened to be unreachable. The log text distinguished them; the
output a workflow reads did not (D-156).
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _action() -> dict:
    """The parsed action.

    Read inside the test rather than at import, per tests/CLAUDE.md: mutmut runs
    this suite from a copied tree, and a module-level read of a file missing from
    `[tool.mutmut] also_copy` is a collection error rather than one red test.
    """
    return yaml.safe_load((ROOT / "action.yml").read_text())


def _gate_script() -> str:
    """The shell of the step that runs the gate and reports its exit code."""
    steps = _action()["runs"]["steps"]
    gate = [step for step in steps if step.get("id") == "gate"]
    assert len(gate) == 1, "expected exactly one step with id 'gate'"
    return gate[0]["run"]


def test_the_action_declares_both_outputs() -> None:
    """A workflow cannot read what the action does not declare."""
    outputs = _action()["outputs"]

    assert set(outputs) == {"blocked", "outcome"}


def test_every_exit_code_sets_both_outputs() -> None:
    """Three codes, three named outcomes, and never a bare `blocked=true` fallback."""
    script = _gate_script()

    for outcome in ("clean", "blocked", "error"):
        assert f"outcome={outcome}" in script, outcome
    # Two of the three codes are not a policy violation, so the script must say
    # blocked=false at least as often as it says blocked=true.
    assert script.count("blocked=false") == 2
    assert script.count("blocked=true") == 1


def test_the_could_not_tell_branch_does_not_claim_the_model_was_blocked() -> None:
    """The defect this file exists for: exit 2 must not read as a violation.

    Asserted on the branch's own body rather than on the whole script, so moving
    `blocked=true` into the exit-2 branch fails here even though the counts above
    would still balance.
    """
    script = _gate_script()
    branch = script.split("if [ $code -eq 2 ]; then", 1)[1].split("else", 1)[0]

    assert "blocked=false" in branch
    assert "outcome=error" in branch
    assert "blocked=true" not in branch


def test_the_token_never_reaches_a_command_line() -> None:
    """A secret in argv is visible in the process table; the env is the only route.

    Root CLAUDE.md rule 6d, at the one boundary where this project hands a
    credential to somebody else's runner.
    """
    steps = _action()["runs"]["steps"]
    gate = next(step for step in steps if step.get("id") == "gate")

    assert "DATAHUB_GMS_TOKEN" in gate["env"]
    assert "gms-token" not in gate["run"]
