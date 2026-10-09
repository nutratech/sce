"""Command-line entry point and process exit codes."""

from __future__ import annotations

import argparse
import sys

from . import __version__

CLEAN = 0
FINDINGS = 1
ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sce",
        description="Read-only Synapse room diagnosis and recovery tooling.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return CLEAN


if __name__ == "__main__":
    sys.exit(main())
