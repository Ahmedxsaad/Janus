"""Agent orchestration: LangGraph state machine with a human approval gate.

Node order is fixed and explicit:
    detect -> investigate -> reason_and_score -> [human_approval] -> write_back

The detect and investigate nodes call the deterministic functions in
janus.detect. The reason_and_score node is the only place the LLM runs,
and it only explains, ranks, and drafts text. A LangGraph interrupt() pauses
before any mutation; --auto-approve exists solely for the recorded demo.

The modules (docs/02-architecture.md):
    pipeline     run_scan, the detect-reason-write core every trigger shares
    graph        the LangGraph StateGraph behind ``scan --review``
    narrate      the only place the LLM runs, and it writes prose only
    context_kit  the DataHub Agent Context Kit reads, where it is installable
"""
