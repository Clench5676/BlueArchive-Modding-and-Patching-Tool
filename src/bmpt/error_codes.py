"""Permanent display references and severity for the App's error names."""

from __future__ import annotations

import json
from pathlib import Path

_REGISTRY = json.loads((Path(__file__).resolve().parent / "web" / "error-codes.json").read_text(encoding="utf-8"))


def code_of(name: str) -> str | None:
    return _REGISTRY.get(name, {}).get("code") if isinstance(name, str) else None


def level_of(name: str) -> str | None:
    return _REGISTRY.get(name, {}).get("level") if isinstance(name, str) else None
