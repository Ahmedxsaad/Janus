# CLAUDE.md - adapters

Readers for declarations that already hold the model-to-column join: a Feast
feature repo, a dbt semantic model. Each one turns a file a team already
maintains into the arguments `janus link` otherwise asks a human to type
(docs/plan/09-depth-axes.md section 1.1, T-05/T-06).

## Local rules

1. An adapter is **read-only and offline**. It parses a declaration on disk and
   nothing else: no vendor service, no registry server, no warehouse query, no
   DataHub write. `feast.repo_operations.parse_repo` imports the repo's own
   Python modules; that is parsing a declaration, and it is the boundary. A
   reader that needs credentials does not belong in this package.
2. One module per source, one public function, one return type
   (`DeclaredLink`). A new adapter is a module plus one entry in `ADAPTERS` and
   one branch in `read_declaration`; nothing else in the codebase learns its
   name.
3. Nothing here decides. An adapter reports what the declaration says; where it
   says nothing (a Feast repo with no label view, a dbt semantic model, which
   has no notion of a label at all) the field is None and the reason line says
   so. Inventing a label or a table name here would put a guess behind a
   confirmation prompt, which is worse than asking.
4. Every fact carries where it came from: `DeclaredFeature.declared_in` per
   line, `DeclaredLink.reasons` per decision. The CLI prints both before the
   proposal, because the point of importing a mapping is that a reviewer can
   check it against a file they already know.
5. Each adapter's dependency is an optional extra, imported inside the function
   that needs it (`read_declaration` dispatches lazily). `--from dbt` must work
   with no feast installed, and a missing package fails with the exact
   `pip install` line, never a traceback.
6. Adapters do not touch DataHub. Resolving a declared table name to a dataset
   URN, reading its schema, and turning the declared feature set into `link`'s
   excluded-column set are the caller's job: `excluded_columns` and
   `missing_columns` here are pure, and `link_infer.declared_proposal` does the
   graph half.
7. A declaration that disagrees with the catalog is fatal, never filtered. If a
   declared column is not in the resolved table, nothing is proposed: linking
   the intersection would leave the undeclared columns unchecked while
   reporting success.

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-08-04 | Claude (for Ghassen Naouar) | Initial version: the adapter framework, the Feast reader and the dbt semantic-model reader, with the read-only/offline rule that defines the package (D-112, T-05/T-06) |
| 2026-08-04 | Claude (for Ghassen Naouar) | Rule 4's every-fact-carries-its-source rule met a source that carries it privately: Feast's SQL contrib sources keep the table in an options object and expose it only through `get_table_query_string()`, so a postgres-backed repo declared its source table as the source's Feast name. Asked second, and taken only when the answer is a bare relation, so a query-backed source still falls back to its name (D-121, T-14) |
| 2026-08-05 | Claude (for Ghassen Naouar) | Package and brand identifiers renamed repo-wide: paths, imports, and prose all match the current name and distribution name (D-136) |
