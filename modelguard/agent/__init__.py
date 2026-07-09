"""Agent orchestration: LangGraph state machine with a human approval gate.

Node order is fixed and explicit:
    detect -> investigate -> reason_and_score -> [human_approval] -> write_back

The detect and investigate nodes call the deterministic functions in
modelguard.detect. The reason_and_score node is the only place the LLM runs,
and it only explains, ranks, and drafts text. A LangGraph interrupt() pauses
before any mutation; --auto-approve exists solely for the recorded demo.

Planned modules (see docs/plan/02-implementation-plan.md section 7):
    graph  the LangGraph StateGraph definition
    tools  DataHub Agent Context Kit toolset plus custom write tools
"""
