---
name: ml-guard
description: Protect production ML models against target leakage, schema drift, and upstream blast radius
argument-hint: "[model, table, or ML reliability question]"
---

# DataHub ML Guard

Use the Skill tool to invoke the full `datahub-ml-guard` skill:

```
Skill tool:
  skill: "datahub-skills:datahub-ml-guard"
```

**User's request:** $ARGUMENTS

This skill reads the join between column-level warehouse lineage and ML metadata
to answer three questions the catalog cannot answer on its own:

1. **Blast radius:** which models does this stale or changed table put at risk, and which of them are live
2. **Target leakage:** which of a model's features derives from its own label column, with the column chain as proof
3. **Schema drift:** what has changed in a model's input schema since it was trained

Detection is deterministic: the skill never asks a language model whether a
finding exists. Writes back (incident, trust score, guarding assertion, impact
report) are gated on human approval.

If no arguments provided, run `janus inventory` and report what is
checkable in this catalog and what metadata is missing.
