"""Deterministic JSON output helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = [_canonical(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def dumps(value: Any) -> str:
    """Serialize a report with sorted keys and stable list ordering."""

    return json.dumps(_canonical(value), indent=2, sort_keys=True) + "\n"
