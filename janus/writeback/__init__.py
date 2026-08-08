"""Write-back layer: idempotent, parameterized DataHub mutations.

Every function here is a fixed, parameterized mutation with validated
arguments. The LLM selects which function to call and supplies arguments;
it never composes raw GraphQL. Every write does read-before-write on a dedup
key so reruns never duplicate. ``run_id`` is stamped into the body as provenance
and is deliberately not part of any key: it changes every run, so keying on it
would raise a fresh copy of the same finding every scan (docs/02-architecture.md).

The modules, by what they write:
    incidents           raiseIncident / updateIncidentStatus via execute_graphql
    properties          structured property definitions and assignment
    labels, terms       tags and glossary terms
    documents           the Model Impact Report as a knowledge document
    model_documents     the model card and the evidence pack
    feature_documents   one Data Card per feature
    assertions          guarding assertions as open-assertions YAML plus entity
    contract            the input schema as an ODCS data contract
    link, link_infer    the model-to-column join, and the proposal for it
    trust_history       the trust score's trend, one point per scan
    coverage_history    the catalog coverage figure's trend
    process_instance    the scan itself, as an entity in the graph it guards
"""
