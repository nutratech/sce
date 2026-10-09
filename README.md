# Synapse Community Extensions

Operator tooling for diagnosing and recovering Matrix room state and event
DAGs in Synapse.

SCE is intended to complement Synapse, not to fork its persistence or
federation implementations. The first implementation is an offline,
read-only-capable CLI and importable Python library. Online administration and
write-capable repair come later, after room-scoped fencing and publication
semantics are explicit.

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
