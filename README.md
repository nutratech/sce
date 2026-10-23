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

## Development

Install the Python development tools and the documentation formatters:

```text
python -m pip install -e '.[dev]'
npm install
pre-commit install
```

`make format` and `make lint` use the Python packages from the `dev` extra and
the Node tools declared in `package.json`. Run `make test` to execute the test
suite.

## Publishing keys while Synapse is offline

Generate the signed Matrix key response from a Rezzy-encrypted Ed25519 key file:

```text
scripts/generate_matrix_key_response.sh \
  --origin example.org \
  --signing-key /etc/rezzy/example.org.key \
  --output /var/lib/sce/matrix-key/server.json
```

The script prompts for the passphrase, or reads
`REZZY_KEY_PASSPHRASE_EXAMPLE_ORG` (falling back to `REZZY_KEY_PASSPHRASE`) for
scheduled runs. Regenerate the file before its `valid_until_ts` expires. An
optional `--old-keys old-keys.json` adds the configured `old_verify_keys` map.
An Nginx example is in
[`examples/nginx/matrix-key-server.conf`](examples/nginx/matrix-key-server.conf).
Set `--origin` to the Matrix server name being advertised. If
`/.well-known/matrix/server` delegates that name, Nginx's `server_name` and TLS
certificate must match the delegated destination.
