# A raise_incident tool for DataHub's MCP server

The second OSS contribution: a mutation tool for
[`acryldata/mcp-server-datahub`](https://github.com/acryldata/mcp-server-datahub),
plus an RFC for the metadata-model gap it works around.

Today that server ships read tools and, behind a flag, metadata-edit tools, but
no way to raise an incident, create an assertion, or write lineage: an agent can
find a data-to-model failure and has nowhere to record it. Incidents are
OSS-native (`raiseIncident` already exists and works), so the only missing
piece is the tool wrapper this folder adds.

## Files

| Path | What it is |
| --- | --- |
| `RFC-ml-incidents.md` | The proposal: the missing tool, and the separate GMS gap (incidents cannot attach to an `mlModel`, GMS answers 500) that the tool has to work around in the meantime |
| `raise_incident_tool.py` | The tool itself: thin, standalone, does not import `janus`. Mirrors `janus/writeback/incidents.py`'s GraphQL and allowed-set derivation, because both talk to the same GMS |

## Trying it

```bash
python mcp_ext/raise_incident_tool.py   # offline self-check, no DataHub needed
```

Gated behind `TOOLS_IS_MUTATION_ENABLED` (the same flag the upstream server uses
for every mutation tool), documented in the
[configuration reference](https://docs.ahmedxsaad.me/#config). Nothing in the
`janus` package reads that flag; it exists only for this contribution.

Full context, including how this relates to what `janus-mcp` (Janus's own,
read-only server) already does: [docs/14-oss-contributions.md](../docs/14-oss-contributions.md).
