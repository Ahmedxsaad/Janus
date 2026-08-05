"""Deterministic detectors: pure functions over the DataHub graph.

No LLM calls and no writes happen in this package. Each detector reads
lineage and metadata, applies a deterministic predicate, and returns typed
finding objects. This is what makes detection reproducible, measurable by
the benchmark, and resistant to prompt injection.

Planned modules (one per problem, see docs/plan/02-implementation-plan.md
sections 4 and 5):
    leakage       P1: target leakage via column-cone intersection
    blast_radius  P2: upstream failure to models/deployments at risk
    schema_drift  P3: training-vs-current schema diff
    trust_score   P4: aggregate model trust score
"""
