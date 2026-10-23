# SCE initial design

## Goal

SCE provides operator-facing diagnostics and recovery workflows for Synapse
rooms whose local state, event DAG, or derived indexes are incomplete or
incorrect. It should make a damaged room understandable before it makes any
change to it.

The project should remain usable against an unmodified Synapse installation.
Where possible, it should use Synapse's supported storage and federation
interfaces; database-specific access belongs behind a versioned adapter.

## What we borrow

From Sithnapse:

- the offline-first recovery boundary;
- refusing partial-state rooms for state publication;
- replaying state from the durable event DAG rather than trusting an existing
  state group;
- deterministic dry-run diffs; and
- preserving old state material for audit and rollback.

From Congruent, which is the primary operator-surface template:

- the `yolo` versus `debug` command split;
- command registration and CLI argument conventions;
- restricted/emergency command handling;
- human-readable output suitable for an admin console;
- comparing room state at a selected event against one or more servers;
- explicit commands for fetching individual events and room state;
- DAG/state export and import concepts; and
- separating ordinary authenticated insertion from emergency force operations.

These are capability references, not an instruction to copy Congruent's database
or command implementation into Synapse.

SCE should copy Congruent's operator ergonomics wherever they make sense. In
particular, normal diagnostics and recovery preparation belong in the regular
operator command namespace, while force-set, skip-auth, skip-signature, raw
imports, and similar escape hatches belong behind a clearly restricted
`debug`-style namespace. The initial read-only milestone should preserve the
same argument shape and predictable output conventions where possible, so an
operator familiar with Congruent can use SCE without learning a completely
different console vocabulary.

## First implementation milestone: diagnosis and compare

The first implementation is deliberately read-only and dry-run only. It covers
room inspection, rejected/outlier reporting, DAG-completeness checks, and state
comparison. It does not write the database, create backups, stage outliers, or
apply repairs.

State comparison has two explicit modes:

- local-only comparison, using the Synapse database and local event material;
- remote comparison, requiring explicit server names and using the homeserver's
  signing key for outbound `/state_ids` and `/state` federation requests.

Remote comparison is therefore not presented as an offline operation. It must
make the request destination and authentication mode visible in its report.

Missing-PDU fetching is milestone 2. State replay/rebuild is milestone 3. Both
depend on the diagnosis and comparison results from this first milestone.

## Proposed command surface

Names are provisional, but the safety boundaries are intentional:

```text
sce room inspect ROOM_ID [--json REPORT]
sce room compare-state ROOM_ID --server SERVER [--at-event EVENT_ID]
sce room fetch-pdu ROOM_ID EVENT_ID --server SERVER --bundle BUNDLE
sce room fetch-state ROOM_ID --at-event EVENT_ID --server SERVER --bundle BUNDLE
sce bundle validate BUNDLE
sce bundle export ROOM_ID --output BUNDLE
sce repair plan ROOM_ID --bundle BUNDLE --output PLAN.json
sce repair rebuild-state ROOM_ID --plan PLAN.json --offline --dry-run
sce repair apply ROOM_ID --plan PLAN.json --offline
```

The first six commands are the initial milestone. `repair plan` is the first
command that should understand a proposed recovery, but it must remain
read-only. `repair apply` is deliberately a later command.

Commands that fetch data stage it. They do not silently insert remote PDUs into
Synapse, mark events accepted, or repoint current state.

## Repair bundle

A bundle is the handoff between discovery/federation and repair. It should be
portable, inspectable, and append-only. A suggested layout is:

```text
bundle.json                 # schema, room, room version, creation time
events/<event-id>.json      # canonical PDU JSON
manifests/requests.jsonl    # server, endpoint, timestamp, response metadata
manifests/edges.jsonl       # known prev/auth/state relationships
reports/<name>.json         # comparison and validation results
```

Each event record needs provenance in the manifest: source server, request
endpoint, observed event ID, canonical hash, signature-validation result, and
whether it was fetched as timeline, state, auth, or outlier material. Raw PDU
JSON alone is not a sufficient recovery record.

Bundles must never contain credentials or private signing keys. A future format
version may add an operator signature over the manifest.

## Safety invariants

- Inspection, comparison, fetching, export, and validation are read-only with
  respect to Synapse's database.
- All mutating commands default to a dry run and require an explicit offline
  mode until Synapse can provide room-scoped persistence fencing.
- Partial-state rooms are reportable but cannot be published through the normal
  state-rebuild path.
- A missing predecessor, auth event, or state event causes a repair plan to be
  incomplete; SCE must not invent it.
- Event IDs, room versions, signatures, canonical JSON, room membership, and
  rejection status are checked before a plan can be applied.
- Existing state groups and event rows are retained. Repair creates replacement
  material and records an audit trail before changing any live pointer.
- Current-state tables, rejection state, replication streams, and caches are one
  publication transaction from the operator's point of view. Direct SQL edits
  are not a supported repair mechanism.

## Milestones

### M0: foundation

- package layout and versioned bundle schema;
- Synapse configuration/database discovery;
- structured logging and JSON output;
- room inspection report; and
- fixtures for complete, missing-PDU, rejected-event, and partial-state rooms;
- an expected deterministic report for the first fixture.

### M1: diagnosis and compare

- local/remote state comparison;
- local room inspection and rejected/outlier reports;
- DAG-completeness and missing-input reports;
- explicit local-only versus signed remote operation; and
- deterministic JSON reports with no database writes;
- server-key-aware signature findings;
- recorded soft-fail metadata when the homeserver supplies it; and
- streaming edge reads so the first implementation does not require a RAM-heavy
  global edge index.

### M2: fetch and stage

- remote PDU, state, and auth-chain fetch;
- bundle validation and deterministic manifests;
- retry/rate-limit handling; and
- backup and apply gating before any outlier staging.

### M3: offline replay plan

- topological DAG walk;
- fixed-code state/auth replay;
- old-vs-new state and rejection diff;
- explicit missing-input refusal; and
- reproducible plan files.

### M4: offline publication

- one-room state-group/HAMT publication where the Synapse version supports it;
- current-state and rejection updates through an audited adapter;
- cache/stream repair; and
- rollback metadata and post-apply verification.

### M5: online administration

- authenticated local admin transport;
- room-scoped fencing;
- resumable jobs and progress reporting; and
- carefully restricted emergency operations such as force-set and unreject.

## Comparisons

The relevant comparison is capability-level:

| Capability            | SCE first version | Sithnapse direction         | Congruent analogue         |
| --------------------- | ----------------- | --------------------------- | -------------------------- |
| Room inventory        | read-only report  | `check-room`                | room/debug reports         |
| State comparison      | staged M1         | planned                     | `compare-room-state`       |
| Fetch missing PDU     | staged bundle     | federation machinery exists | `fetch-pdu`                |
| Fetch room state/auth | staged bundle     | federation machinery exists | state/federation fetch     |
| State replay          | M2 plan, M3 apply | planned                     | `rebuild-state`            |
| Force operations      | post-M4 only      | explicitly restricted       | `force-set-state`, imports |

The key distinction is that SCE's initial fetch operations produce evidence for
a later repair; they are not a backdoor write path.

## Rust-first implementation boundary

SCE is Rust-first. Sithnapse deployments using mtxdb must be read through the
existing read-only mtxdb API, not by reimplementing its on-disk format.

The initial workspace should contain:

```text
sce CLI
  ├── mtxdb adapter (first implementation)
  ├── report and comparison engine
  └── rezzy federation/state-resolution integration
```

`mtxdb-cli` is a useful inspection and export oracle, but it is currently a
binary-only package. SCE should depend on the `mtxdb` library directly for
room-scoped reads and use the existing read-only client semantics. A dedicated
Matrix export API can be extracted from `mtxdb-cli` later if direct library
access is insufficient.

Rezzy already exposes reusable library APIs for gap analysis, event hashing,
state resolution, signature verification, and JSONL handling. Its CLI crate also
exposes signed federation request and remote-DAG functions. SCE should reuse
those APIs rather than implement a second federation client or resolver. If a
needed workflow is currently CLI-internal—such as gap-fill orchestration—
extract it into a public rezzy library module before depending on it.

Gap filling should take a fetcher trait or closure. The dry-run implementation
uses a fake fetcher in tests and reports what would be requested; the network
implementation supplies the signed federation fetcher later. SCE must not use
Rezzy's client-server `fetch_room_state` helper for this path, since it accepts
a user access token rather than performing the intended server-server request.

Until the APIs stabilize, pin `mtxdb` and `rezzy` as path or git dependencies at
exact revisions. Update those pins deliberately alongside fixture and report
changes.

The report schema remains the compatibility contract. M1 should produce
deterministic reports from mtxdb and compare them with shared JSONL fixtures
processed through rezzy. A future PostgreSQL adapter can implement the same
local-state trait without changing the report or comparison engine, but it is
not part of the early milestones.

## Evidence and validation model

Every finding carries an evidence category:

1. **Self-verifiable offline** — event hashes, canonical JSON, DAG structure,
   and auth checks against the claimed state.
2. **Requires signing keys** — event signature validation.
3. **Homeserver-local metadata** — soft-fail status, outlier flags, and recorded
   rejection reasons.

The report also lists categories that were not evaluated. Missing signing keys
produce `unverifiable`, never `invalid`. This distinguishes a bad signature from
a key that is unavailable or outside the known cache.

Signing-key material is an explicit input to a run. The key cache records the
source server, fetched-at time, validity interval, and response provenance so a
report can be replayed offline. Historical-event validation must apply the
room-version-specific key validity rules rather than assuming that the current
key is sufficient. Rezzy's encrypted local signing-key handling remains the
source for SCE's own server key.

Soft-fail is tri-state: `soft_failed`, `not_soft_failed`, or `unknown`. Unknown
is the correct result when mtxdb/Sithnapse has not preserved the local flag.
Soft-fail differences between servers are not state divergence by themselves;
they become findings only when they change the resolved state. Recorded
rejection is separate from recomputed rejection. Rezzy can recompute the
deterministic auth result, and disagreement with the recorded rejection status
is a finding in the homeserver-local metadata category.

## Edge streaming boundary

M1 should request a streaming edge iterator from mtxdb, scoped to one room and
bounded by the requested limit/depth. It should not require loading every edge
for every room into memory. Rezzy can build a per-room roaring-bitmap graph when
useful, with a size warning for unusually large rooms. Any future mtxdb
edge-index memory redesign stays behind this iterator and does not change SCE's
diagnostic contract.
