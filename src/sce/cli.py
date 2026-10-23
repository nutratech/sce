"""Command-line entry point and process exit codes."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .export import export_room, export_server_keys

CLEAN = 0
FINDINGS = 1
ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sce",
        description="Read-only Synapse room diagnosis and recovery tooling.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")

    room = commands.add_parser(
        "export-room",
        help="Export a Synapse room DAG as mtxdb-export/v1 JSONL",
    )
    room.add_argument("room_id")
    room.add_argument("--database", required=True, help="Synapse SQLite database path")
    room.add_argument("--server", required=True, help="Exporting homeserver name")
    room.add_argument(
        "--output", required=True, help="Output JSONL path, or - for stdout"
    )

    keys = commands.add_parser(
        "export-server-keys",
        help="Export Synapse's cached federation keys as JSONL",
    )
    keys.add_argument("--database", required=True, help="Synapse SQLite database path")
    keys.add_argument(
        "--output", required=True, help="Output JSONL path, or - for stdout"
    )
    keys.add_argument("--origin", help="Export only keys belonging to this server")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export-room":
        export_room(args.database, args.room_id, args.server, args.output)
    elif args.command == "export-server-keys":
        export_server_keys(args.database, args.output, args.origin)
    return CLEAN


if __name__ == "__main__":
    sys.exit(main())
