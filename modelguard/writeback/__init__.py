"""Write-back layer: idempotent, parameterized DataHub mutations.

Every function here is a fixed, parameterized mutation with validated
arguments. The LLM selects which function to call and supplies arguments;
it never composes raw GraphQL. Every write is keyed by
(resourceUrn, finding_type, run_id) and does read-before-write so reruns
never duplicate.

Planned modules (see docs/plan/02-implementation-plan.md section 6):
    incidents    raiseIncident / updateIncidentStatus via execute_graphql
    properties   structured property definitions and assignment
    labels       tags, glossary terms, owners
    documents    Model Impact Report as a knowledge document
    assertions   guarding assertions as open-assertions YAML plus entity
"""
