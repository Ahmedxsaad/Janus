# Resources

## Documentation

Start here to get DataHub running, plug it into your agents, and understand what's available. The Quickstart spins up DataHub locally in minutes. The Skills, Agent Context Kit, and MCP Server docs cover the **three primary ways to wire DataHub into your agent stack**.

- [DataHub Docs](https://docs.datahub.com)
- [Quickstart Guide](https://docs.datahub.com/docs/quickstart)
- [DataHub Skills](https://docs.datahub.com/docs/dev-guides/agent-context/skills)
- [Agent Context Kit](https://docs.datahub.com/docs/dev-guides/agent-context/agent-context)
- [DataHub MCP Server](https://github.com/acryldata/mcp-server-datahub)
- [Analytics Agent](https://docs.datahub.com/docs/features/feature-guides/analytics-agent)

## Repositories

The DataHub open source codebase and the DataHub Skills repo are where the platform lives. **Contributions back to either are welcomed and count toward the bonus open-source contribution criterion.**

- [DataHub Core](https://github.com/datahub-project/datahub)
- [DataHub Skills](https://github.com/datahub-project/datahub-skills)

## Sample Datasets

Spin up a rich DataHub environment without wiring it to your own infrastructure. These sample datasets give you cross-platform metadata, lineage, and real-world data quality scenarios to build against.

### Cross-platform metadata graphs

- **showcase-ecommerce datapack** - 1,049 entities across Snowflake, Looker, PowerBI, Tableau, dbt, Spark, PostgreSQL, S3 with cross-platform lineage, governance, glossary, and domains.
  - Load with: `datahub datapack load showcase-ecommerce`
- **bootstrap** - Lightweight starter with datasets, dashboards, users, tags.
  - Load with: `datahub datapack load bootstrap`

### Real datasets with built-in scenarios

- [nyc-taxi](https://github.com/datahub-project/static-assets/tree/main/datasets/nyc-taxi) - NYC Yellow Taxi Trip Records (~500k trips). 3-stage pipeline with **planted freshness issues**.
- [healthcare](https://github.com/datahub-project/static-assets/tree/main/datasets/healthcare) - Synthetic patient records (~55k records) with **planted data quality issues**.
- [fiction-retail](https://github.com/datahub-project/static-assets/tree/main/datasets/fiction-retail) - Synthetic global retail dataset (50k customers, 150k orders) across 10 tables. Clean schema, blank canvas.

> These datasets are **safe for Apache 2.0 submissions**. If you bring your own data, make sure its license permits publication in your open-source repo.

## Community

This is where you'll get help, share progress, and connect with other builders during the hackathon. **Office hours will be hosted mid-hackathon for live Q&A.**

- [Join DataHub Slack](https://join.slack.com/t/datahubspace/shared_invite/zt-3rxzw3uww-7F2k5mDpjKXIGLskiQPwLQ) - head to **#agent-hackathon** for live help.
- [DataHub Town Halls](https://datahub.com/community/datahub-town-halls/)

## Need Help?

- **DataHub questions:** drop into the **#agent-hackathon** channel in DataHub Slack - DataHub team members and other builders are there to help.
- **Devpost or submission issues:** email support@devpost.com.
