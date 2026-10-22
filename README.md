# Synapse Community Extensions

Operator tooling for diagnosing and recovering Matrix room state and event DAGs
in Synapse.

SCE is intended to complement Synapse, not to fork its persistence or federation
implementations. The first implementation is an offline, read-only-capable CLI
and importable Python library. Online administration and write-capable repair
come later, after room-scoped fencing and publication semantics are explicit.

## Initial direction

The first useful workflow is:

1. inspect a room and produce a machine-readable report;
2. compare local state with a remote server;
3. fetch missing PDUs and auth/state material into a signed/provenance-bearing
   repair bundle; and
4. validate the bundle without modifying Synapse's database.

Only after those pieces are deterministic and auditable should SCE publish a
replayed state graph or alter live state.

See [the initial design](docs/design.md) for the proposed command surface,
repair bundle format, safety constraints, and milestones.

## Exporting to mtxdb

For a Synapse SQLite main database, SCE can write the room interchange format
accepted by `mtxdb import`:

```text
sce export-room '!room:example.org' --database homeserver.db \
  --server example.org --output room.jsonl
sce export-server-keys --database homeserver.db --output server-keys.jsonl
```

Use `--output -` to write JSONL to stdout. Room event records include the
prev/auth edges and Synapse metadata (`outlier`, `soft_failed`, `rejected`, and
`redacted_by`) required by `mtxdb-export/v1`. Server keys are emitted as a
separate JSONL export. These commands open the database read-only.
