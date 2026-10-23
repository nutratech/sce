from __future__ import annotations

import base64
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from sce.cli import ERROR, main
from sce.export import FORMAT, export_room, export_server_keys

ROOM_ID = "!room:example.org"
SERVER = "example.org"


def _encoded_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii").rstrip("=")


def _decoded_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise TypeError("expected a base64 string")
    return base64.b64decode(value)


def _key_response(
    verify_keys: dict[str, bytes],
    old_verify_keys: dict[str, tuple[bytes, int]] | None = None,
    valid_until_ts: int = 0,
) -> str:
    response: dict[str, object] = {
        "verify_keys": {
            key_id: {"key": _encoded_key(key)} for key_id, key in verify_keys.items()
        },
        "valid_until_ts": valid_until_ts,
    }
    if old_verify_keys:
        response["old_verify_keys"] = {
            key_id: {"key": _encoded_key(key), "expired_ts": expired_ts}
            for key_id, (key, expired_ts) in old_verify_keys.items()
        }
    return json.dumps(response)


class ExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "homeserver.db"
        self.output = Path(self.directory.name) / "export.jsonl"
        self._create_database()

    def _create_database(self) -> None:
        db = sqlite3.connect(self.database)
        db.executescript("""
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                outlier INTEGER NOT NULL DEFAULT 0,
                rejection_reason TEXT,
                topological_ordering INTEGER NOT NULL,
                stream_ordering INTEGER NOT NULL
            );
            CREATE TABLE event_json (
                event_id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                internal_metadata TEXT NOT NULL,
                json TEXT NOT NULL
            );
            CREATE TABLE event_edges (event_id TEXT, prev_event_id TEXT);
            CREATE TABLE event_auth (event_id TEXT, auth_id TEXT);
            CREATE TABLE redactions (event_id TEXT, redacts TEXT);
            CREATE TABLE rejections (event_id TEXT PRIMARY KEY, reason TEXT);
            CREATE TABLE server_keys_json (
                server_name TEXT NOT NULL,
                from_server TEXT NOT NULL,
                key_id TEXT NOT NULL,
                ts_added_ms INTEGER NOT NULL,
                ts_valid_until_ms INTEGER NOT NULL,
                key_json BLOB NOT NULL
            );
            """)
        self.db = db
        for key in (
            "$create",
            "$msg",
            "$fallback",
            "$rejected-event",
            "$rejected-table",
            "$rejected-unknown",
            "$soft",
            "$outlier",
            "$redact-old",
            "$redact-new",
            "$target",
            "$internal-target",
        ):
            self._insert_event(
                key,
                topo=self._next_topological(),
                pdu={
                    "type": "m.test",
                    "event_id": key,
                    "room_id": ROOM_ID,
                    "prev_events": [],
                    "auth_events": [],
                },
            )
        self.db.commit()
        self.db.close()

    def _next_topological(self) -> int:
        row = self.db.execute(
            "SELECT COALESCE(MAX(topological_ordering), -1) + 1 FROM events"
        ).fetchone()
        return int(row[0])

    def _insert_event(
        self,
        event_id: str,
        *,
        topo: int,
        pdu: dict[str, object],
        internal: dict[str, object] | None = None,
        rejection_reason: str | None = None,
        outlier: bool = False,
    ) -> None:
        self.db.execute(
            """INSERT INTO events
               (event_id, room_id, outlier, rejection_reason,
                topological_ordering, stream_ordering)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                ROOM_ID,
                int(outlier),
                rejection_reason,
                topo,
                topo,
            ),
        )
        self.db.execute(
            "INSERT INTO event_json VALUES (?, ?, ?, ?)",
            (
                event_id,
                ROOM_ID,
                json.dumps(internal or {}),
                json.dumps(pdu),
            ),
        )

    def _insert_key(
        self,
        *,
        server_name: str,
        from_server: str,
        key_id: str,
        added: int,
        valid_until: int,
        response: str,
    ) -> None:
        self.db.execute(
            "INSERT INTO server_keys_json VALUES (?, ?, ?, ?, ?, ?)",
            (
                server_name,
                from_server,
                key_id,
                added,
                valid_until,
                response.encode("utf-8"),
            ),
        )

    def _read_lines(self, path: Path | None = None) -> list[dict[str, object]]:
        target = path or self.output
        return [
            json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
        ]


class RoomExportTests(ExportTestCase):
    def setUp(self) -> None:
        super().setUp()
        db = sqlite3.connect(self.database)
        self.db = db
        # Edge rows disagree with the PDU: the persisted graph must win.
        db.execute("INSERT INTO event_edges VALUES ('$msg', '$create')")
        db.execute("INSERT INTO event_auth VALUES ('$msg', '$create')")
        db.execute(
            """UPDATE event_json SET json=?
               WHERE event_id='$msg'""",
            (
                json.dumps(
                    {
                        "type": "m.test",
                        "prev_events": [["$pdu-prev", {}]],
                        "auth_events": [["$pdu-auth", {}]],
                    }
                ),
            ),
        )
        # No edge rows at all: fall back to the PDU's prev/auth events.
        db.execute(
            """UPDATE event_json SET json=?
               WHERE event_id='$fallback'""",
            (
                json.dumps(
                    {
                        "type": "m.test",
                        "prev_events": [["$msg", {"sha256": "abc"}]],
                        "auth_events": [["$create", {"sha256": "def"}]],
                    }
                ),
            ),
        )
        db.execute(
            "UPDATE events SET rejection_reason='auth_check_failed' "
            "WHERE event_id='$rejected-event'"
        )
        db.execute(
            "INSERT INTO rejections VALUES ('$rejected-table', "
            "'signature_verification_failed')"
        )
        db.execute("INSERT INTO rejections VALUES ('$rejected-unknown', NULL)")
        db.execute(
            """UPDATE event_json SET internal_metadata=?
               WHERE event_id='$soft'""",
            (json.dumps({"soft_failed": True}),),
        )
        db.execute("UPDATE events SET outlier=1 WHERE event_id='$outlier'")
        db.execute(
            """UPDATE event_json SET internal_metadata=?
               WHERE event_id='$msg'""",
            (json.dumps({"redacted_by": "$inline-redactor"}),),
        )
        # Insert the older redaction last so ordering cannot be accidental.
        db.execute("INSERT INTO redactions VALUES ('$redact-new', '$target')")
        db.execute("INSERT INTO redactions VALUES ('$redact-old', '$target')")
        db.execute("INSERT INTO redactions VALUES ('$redact-old', '$msg')")
        db.commit()
        db.close()

    def test_export_room_writes_edges_metadata_and_deterministic_redactions(
        self,
    ) -> None:
        export_room(str(self.database), ROOM_ID, SERVER, str(self.output))
        lines = self._read_lines()

        self.assertEqual(
            lines[0],
            {
                "t": "header",
                "format": FORMAT,
                "server": SERVER,
                "room_id": ROOM_ID,
            },
        )
        self.assertEqual(lines[-1], {"t": "end", "events": len(lines) - 2})
        records = {line["event_id"]: line for line in lines[1:-1]}

        self.assertEqual(records["$msg"]["prev_events"], ["$create"])
        self.assertEqual(records["$msg"]["auth_events"], ["$create"])
        self.assertEqual(records["$fallback"]["prev_events"], ["$msg"])
        self.assertEqual(records["$fallback"]["auth_events"], ["$create"])

        self.assertTrue(records["$rejected-event"]["rejected"])
        self.assertEqual(records["$rejected-event"]["rejection_code"], 7)
        self.assertTrue(records["$rejected-table"]["rejected"])
        self.assertEqual(records["$rejected-table"]["rejection_code"], 0)
        self.assertTrue(records["$rejected-unknown"]["rejected"])
        self.assertEqual(records["$rejected-unknown"]["rejection_code"], 11)

        self.assertTrue(records["$soft"]["soft_failed"])
        self.assertEqual(records["$soft"]["soft_fail_code"], 3)
        self.assertTrue(records["$outlier"]["outlier"])

        # The latest redaction wins regardless of row insertion order, and an
        # inline redacted_by marker takes precedence over the redactions table.
        self.assertEqual(records["$target"]["redacted_by"], "$redact-new")
        self.assertEqual(records["$msg"]["redacted_by"], "$inline-redactor")
        self.assertNotIn("redacted_by", records["$create"])

    def test_export_room_to_stdout(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            export_room(str(self.database), ROOM_ID, SERVER, "-")

        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(lines[0]["t"], "header")
        self.assertEqual(lines[-1], {"t": "end", "events": len(lines) - 2})

    def test_output_cannot_alias_the_source_database(self) -> None:
        original = self.database.read_bytes()
        with self.assertRaises(ValueError):
            export_room(str(self.database), ROOM_ID, SERVER, str(self.database))
        self.assertEqual(self.database.read_bytes(), original)

        symlink = Path(self.directory.name) / "database-link"
        symlink.symlink_to(self.database)
        with self.assertRaises(ValueError):
            export_room(str(self.database), ROOM_ID, SERVER, str(symlink))
        self.assertEqual(self.database.read_bytes(), original)


class ServerKeyExportTests(ExportTestCase):
    def setUp(self) -> None:
        super().setUp()
        db = sqlite3.connect(self.database)
        self.db = db
        self._insert_key(
            server_name="example.org",
            from_server="example.org",
            key_id="ed25519:main",
            added=100,
            valid_until=1000,
            response=_key_response(
                {"ed25519:main": b"old-main-key"},
                {"ed25519:old": (b"old-key", 111)},
                valid_until_ts=1000,
            ),
        )
        # A later permanent response for the same key id must replace the first.
        self._insert_key(
            server_name="example.org",
            from_server="example.org",
            key_id="ed25519:main",
            added=200,
            valid_until=2000,
            response=_key_response(
                {"ed25519:main": b"new-main-key"}, valid_until_ts=2000
            ),
        )
        self._insert_key(
            server_name="example.org",
            from_server="notary.example.org",
            key_id="ed25519:notary",
            added=300,
            valid_until=3000,
            response=_key_response(
                {"ed25519:notary": b"notary-key"}, valid_until_ts=3000
            ),
        )
        # The notary payload record should come from the newest response.
        self._insert_key(
            server_name="example.org",
            from_server="notary.example.org",
            key_id="ed25519:notary2",
            added=400,
            valid_until=4000,
            response=_key_response(
                {"ed25519:notary2": b"newer-notary-key"}, valid_until_ts=4000
            ),
        )
        self._insert_key(
            server_name="other.example",
            from_server="other.example",
            key_id="ed25519:other",
            added=500,
            valid_until=5000,
            response=_key_response(
                {"ed25519:other": b"other-key"}, valid_until_ts=5000
            ),
        )
        db.commit()
        db.close()

    def _records(self, path: Path | None = None) -> list[dict[str, object]]:
        return self._read_lines(path)

    def test_server_keys_include_permanent_provisional_old_and_notary(self) -> None:
        export_server_keys(str(self.database), str(self.output))
        records = self._records()
        keys = {
            (record["origin"], record["key_id"]): record
            for record in records
            if record["kind"] != "notary"
        }
        notary = [record for record in records if record["kind"] == "notary"]

        main = keys[("example.org", "ed25519:main")]
        self.assertEqual(_decoded_key(main["key"]), b"new-main-key")
        self.assertEqual(main["ts_added_ms"], 200)
        self.assertEqual(main["valid_until_ts"], 2000)
        self.assertEqual(main["kind"], "permanent")

        old = keys[("example.org", "ed25519:old")]
        self.assertEqual(_decoded_key(old["key"]), b"old-key")
        self.assertEqual(old["expired_ts"], 111)

        provisional = keys[("example.org", "ed25519:notary")]
        self.assertEqual(provisional["kind"], "provisional")
        self.assertEqual(provisional["source"], "notary.example.org")
        self.assertEqual(_decoded_key(provisional["key"]), b"notary-key")

        self.assertIn(("other.example", "ed25519:other"), keys)

        self.assertEqual([record["origin"] for record in notary], ["example.org"])
        payload = json.loads(_decoded_key(notary[0]["payload"]))
        self.assertIn("ed25519:notary2", payload["verify_keys"])

    def test_server_keys_origin_filter_and_stdout(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            export_server_keys(str(self.database), "-", origin="other.example")

        records = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(records)
        self.assertEqual({record["origin"] for record in records}, {"other.example"})
        self.assertEqual({record["key_id"] for record in records}, {"ed25519:other"})


class CliErrorTests(ExportTestCase):
    def test_missing_database_returns_error_exit_code(self) -> None:
        missing = Path(self.directory.name) / "missing.db"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(
                [
                    "export-room",
                    ROOM_ID,
                    "--database",
                    str(missing),
                    "--server",
                    SERVER,
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(code, ERROR)
        self.assertIn("sce: error:", stderr.getvalue())

    def test_output_aliasing_database_returns_error_exit_code(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(
                [
                    "export-server-keys",
                    "--database",
                    str(self.database),
                    "--output",
                    str(self.database),
                ]
            )
        self.assertEqual(code, ERROR)
        self.assertIn("must not refer to the source database", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
