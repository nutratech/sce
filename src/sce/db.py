"""Database access primitives for read-only diagnosis."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType


class ReadOnlyViolation(RuntimeError):
    """Raised when a diagnosis connection attempts a mutating operation."""


class ReadOnlySQLite:
    """SQLite connection opened read-only and guarded by SQLite's authorizer."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        uri = f"file:{self.path.absolute()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.set_authorizer(self._authorize)
        self.connection = connection
        return connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @staticmethod
    def _authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        allowed = {
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_TRANSACTION,
        }
        if action in allowed:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY


def open_sqlite_read_only(path: str | Path) -> ReadOnlySQLite:
    """Return a context manager for a guarded read-only SQLite database."""

    return ReadOnlySQLite(Path(path))
