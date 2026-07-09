"""ModelGuard: a data-to-model reliability agent built on DataHub.

ModelGuard sits on the boundary between the warehouse graph and the ML graph.
It reads column-level and ML lineage from DataHub to detect silent
data-to-model failures (target leakage, upstream blast radius, schema drift,
trust decay) and writes findings back into the graph as incidents, structured
properties, tags, documents, and guarding assertions.

Package layout:
    seed/       builds the ML graph that the sample datapacks lack
    detect/     deterministic detectors (pure functions, no LLM)
    writeback/  idempotent DataHub mutations
    agent/      LangGraph orchestration with a human approval gate

See docs/plan/architecture.md for the full design.
"""

__version__ = "0.1.0"
