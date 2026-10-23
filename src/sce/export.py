"""Read-only exporters from Synapse's SQLite schema to mtxdb JSONL."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from itertools import chain
from pathlib import Path
from typing import Any, TextIO

from .db import open_sqlite_read_only

FORMAT = "mtxdb-export/v1"


def _json(value: Any) -> Any:
    if isinstance(value, (bytes, memoryview)):
        value = bytes(value).decode("utf-8")
    return json.loads(value)


def _line(output: TextIO, record: dict[str, Any]) -> None:
    output.write(
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    output.write("\n")


def _output(path: str, database: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdout, False
    destination = Path(path)
    source = Path(database)
    try:
        same_file = destination.exists() and os.path.samefile(destination, source)
    except OSError:
        same_file = False
    if same_file or destination.resolve() == source.resolve():
        raise ValueError("output path must not refer to the source database")
    return destination.open("w", encoding="utf-8", newline="\n"), True


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _edge_ids_for(
    db: sqlite3.Connection,
    table: str,
    source: str,
    target: str,
    event_id: str,
    table_present: bool,
) -> list[str]:
    if not table_present:
        return []
    # Newer Synapse schemas stopped maintaining room_id on event_edges, so
    # scope both edge tables through their source event instead.
    query = f"SELECT {target} FROM {table} WHERE {source}=?"
    return sorted(row[0] for row in db.execute(query, (event_id,)))


def _rejection_code(reason: str | None) -> int:
    if reason is None:
        # Synapse sometimes records a rejection without a reason. mtxdb's
        # explicit Unknown code is more useful than omitting the field.
        return 11
    # Synapse's rejection strings are not the mtxdb numeric enum. Preserve a
    # known equivalent where unambiguous and use mtxdb's explicit Unknown code
    # for all other source-specific wording.
    normalized = reason.lower().replace(" ", "_")
    known = {
        "signature_verification_failed": 0,
        "invalid_pdu_format": 1,
        "depends_on_rejected_auth_event": 2,
        "prev_events_rejected": 3,
        "missing_auth_event": 4,
        "duplicate_auth_event_key": 5,
        "missing_create_event": 6,
        "auth_check_failed": 7,
    }
    return known.get(normalized, 11)


def export_room(database: str, room_id: str, server: str, output_path: str) -> None:
    """Write one complete room export, including Synapse's sidecar metadata."""
    with open_sqlite_read_only(database) as db:
        db.execute("BEGIN")
        needed = {"events", "event_json"}
        absent = [table for table in needed if not _has_table(db, table)]
        if absent:
            raise ValueError(
                f"not a Synapse main database (missing {', '.join(absent)})"
            )

        events = db.execute(
            """SELECT e.event_id, e.outlier, e.rejection_reason,
                      ej.internal_metadata, ej.json
               FROM events AS e
               JOIN event_json AS ej ON ej.event_id=e.event_id
               WHERE e.room_id=?
               ORDER BY e.topological_ordering, e.stream_ordering,
                        e.event_id""",
            (room_id,),
        )
        first_event = events.fetchone()
        if first_event is None:
            raise ValueError(f"room {room_id} has no exportable events")

        has_prev = _has_table(db, "event_edges")
        has_auth = _has_table(db, "event_auth")
        redacted_by: dict[str, str] = {}
        if _has_table(db, "redactions"):
            # If an event has several redactions, the latest one wins. Order by
            # the redaction event's position so the result is deterministic.
            redacted_by = {
                target_id: redaction_id
                for redaction_id, target_id in db.execute(
                    """SELECT redactions.event_id, redactions.redacts
                       FROM redactions
                       JOIN events AS target
                         ON target.event_id=redactions.redacts
                       LEFT JOIN events AS redaction
                         ON redaction.event_id=redactions.event_id
                       WHERE target.room_id=?
                       ORDER BY redaction.topological_ordering,
                                redaction.stream_ordering,
                                redactions.event_id""",
                    (room_id,),
                )
            }
        rejected_reasons: dict[str, str | None] = {}
        if _has_table(db, "rejections"):
            rejected_reasons = {
                event_id: reason
                for event_id, reason in db.execute(
                    """SELECT rejections.event_id, rejections.reason
                       FROM rejections JOIN events
                         ON events.event_id=rejections.event_id
                       WHERE events.room_id=?""",
                    (room_id,),
                )
            }

        output, close_output = _output(output_path, database)
        try:
            _line(
                output,
                {
                    "t": "header",
                    "format": FORMAT,
                    "server": server,
                    "room_id": room_id,
                },
            )
            count = 0
            for (
                event_id,
                outlier,
                rejection_reason,
                internal_json,
                pdu_json,
            ) in chain((first_event,), events):
                pdu = _json(pdu_json)
                internal = _json(internal_json) or {}
                # Query each event's edges as we go so large rooms do not need
                # a complete in-memory copy of the DAG.
                prev_ids = _edge_ids_for(
                    db, "event_edges", "event_id", "prev_event_id", event_id, has_prev
                )
                auth_ids = _edge_ids_for(
                    db, "event_auth", "event_id", "auth_id", event_id, has_auth
                )
                # Some older Synapse schemas have no persisted edge rows for
                # all events. The PDU is the authoritative fallback then.
                if not prev_ids:
                    prev_ids = sorted(_edge_ids(pdu.get("prev_events", [])))
                if not auth_ids:
                    auth_ids = sorted(_edge_ids(pdu.get("auth_events", [])))
                soft_failed = bool(internal.get("soft_failed", False))
                rejection_code = rejected_reasons.get(event_id)
                if rejection_reason is not None:
                    rejection_code = rejection_reason
                rejected = rejection_reason is not None or event_id in rejected_reasons
                record: dict[str, Any] = {
                    "t": "event",
                    "event_id": event_id,
                    "pdu": pdu,
                    "prev_events": prev_ids,
                    "auth_events": auth_ids,
                    "outlier": bool(outlier),
                    "soft_failed": soft_failed,
                    "rejected": rejected,
                }
                if soft_failed:
                    # Synapse preserves the flag but not a reason code.
                    record["soft_fail_code"] = 3
                if rejected:
                    record["rejection_code"] = _rejection_code(rejection_code)
                redactor = internal.get("redacted_by") or redacted_by.get(event_id)
                if redactor:
                    record["redacted_by"] = redactor
                _line(output, record)
                count += 1
            _line(output, {"t": "end", "events": count})
        finally:
            if close_output:
                output.close()


def _edge_ids(values: Any) -> list[str]:
    result: list[str] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str):
                result.append(value)
            elif isinstance(value, list) and value and isinstance(value[0], str):
                result.append(value[0])
            elif isinstance(value, dict) and isinstance(value.get("event_id"), str):
                result.append(value["event_id"])
    return result


def _b64(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    return base64.b64encode(decoded).decode("ascii")


def export_server_keys(
    database: str, output_path: str, origin: str | None = None
) -> None:
    """Export cached Synapse signing-key responses as mtxdb records."""
    with open_sqlite_read_only(database) as db:
        if not _has_table(db, "server_keys_json"):
            raise ValueError("Synapse database has no server_keys_json table")
        query = """SELECT server_name, from_server, ts_added_ms,
                          ts_valid_until_ms, key_json
                   FROM server_keys_json"""
        params: tuple[Any, ...] = ()
        if origin is not None:
            query += " WHERE server_name=?"
            params = (origin,)
        query += " ORDER BY server_name, key_id, ts_added_ms, from_server"
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        selected_rank: dict[tuple[str, str], tuple[int, int]] = {}
        notary_payloads: dict[str, tuple[int, bytes]] = {}
        for (
            server_name,
            from_server,
            added,
            valid_until,
            raw_response,
        ) in db.execute(query, params):
            response_bytes = bytes(raw_response)
            response = _json(raw_response)
            if from_server != server_name:
                notary_rank = int(added or 0)
                previous = notary_payloads.get(server_name)
                if previous is None or notary_rank >= previous[0]:
                    notary_payloads[server_name] = (
                        notary_rank,
                        response_bytes,
                    )
            for field in ("verify_keys", "old_verify_keys"):
                keys = response.get(field, {})
                if not isinstance(keys, dict):
                    continue
                for key_id, metadata in keys.items():
                    if not isinstance(metadata, dict) or not isinstance(
                        metadata.get("key"), str
                    ):
                        continue
                    record: dict[str, Any] = {
                        "t": "server_keys",
                        "origin": server_name,
                        "kind": (
                            "permanent" if from_server == server_name else "provisional"
                        ),
                        "key_id": key_id,
                        "key": _b64(metadata["key"]),
                        "valid_until_ts": int(
                            valid_until or response.get("valid_until_ts", 0)
                        ),
                        "source": from_server,
                        "ts_added_ms": int(added or 0),
                    }
                    if (
                        field == "old_verify_keys"
                        and metadata.get("expired_ts") is not None
                    ):
                        record["expired_ts"] = int(metadata["expired_ts"])
                    selection_key = (server_name, key_id)
                    key_rank = (
                        int(added or 0),
                        int(from_server == server_name),
                    )
                    if key_rank >= selected_rank.get(selection_key, (-1, -1)):
                        selected[selection_key] = record
                        selected_rank[selection_key] = key_rank

        output, close_output = _output(output_path, database)
        try:
            for key in sorted(selected):
                _line(output, selected[key])
            for server_name in sorted(notary_payloads):
                _line(
                    output,
                    {
                        "t": "server_keys",
                        "kind": "notary",
                        "origin": server_name,
                        "payload": base64.b64encode(
                            notary_payloads[server_name][1]
                        ).decode("ascii"),
                    },
                )
        finally:
            if close_output:
                output.close()
