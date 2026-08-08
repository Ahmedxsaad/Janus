"""Deterministic detectors: pure functions over the DataHub graph.

No LLM calls and no writes happen in this package. Each detector reads
lineage and metadata, applies a deterministic predicate, and returns typed
finding objects. This is what makes detection reproducible, measurable by
the benchmark, and resistant to prompt injection.

One module per question (docs/04-detectors.md):
    blast_radius    an upstream failure, to the models and deployments at risk
    leakage         a feature derived from the label it predicts
    schema_drift    the training-time schema against the current one
    governance      a classified source column, a deprecated input, a proxy
    degraded        the table-level answer, for a model nothing has linked
    coverage        which checks could run, and what each missing one needs
    guard_coverage  the same sweep folded into one catalog figure
    trust_score     the findings rolled up into a number and a band
    column_marks    the shared upstream column walk the mark detectors reuse
    graph_reads     the shared model, feature and deployment reads
"""
