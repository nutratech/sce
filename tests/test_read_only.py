from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sce.db import open_sqlite_read_only


class ReadOnlyDatabaseTests(unittest.TestCase):
    def test_diagnosis_connection_rejects_mutating_statements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "synapse.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO events VALUES ('$event')")
            connection.commit()
            connection.close()

            with open_sqlite_read_only(database) as read_only:
                self.assertEqual(
                    read_only.execute("SELECT event_id FROM events").fetchone()[0],
                    "$event",
                )
                with self.assertRaises(sqlite3.DatabaseError):
                    read_only.execute("INSERT INTO events VALUES ('$other')")

                with self.assertRaises(sqlite3.DatabaseError):
                    read_only.execute("UPDATE events SET event_id = '$other'")

                with self.assertRaises(sqlite3.DatabaseError):
                    read_only.execute("DELETE FROM events")
