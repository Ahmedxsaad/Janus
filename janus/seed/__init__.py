"""ML-graph seeder: builds the ML supply chain the sample datapacks lack.

The DataHub datapacks are warehouse/BI-centric and contain no ML entities.
This package seeds a small but complete ML supply chain (model group, model,
features, training run, deployment) on top of real datapack tables so that
genuine column-level lineage exists from source columns into the model.

Production code never imports this package (docs/02-architecture.md).

    graph_spec     the single source of truth for every seeded URN and value
    seed_ml_graph  models, features, training runs, deployments, lineage
    scenarios      deterministic planted failures for the demo and benchmark
"""
