"""How each kept original was verified (BL-194): by Steam's file list (size and SHA-1) or by the CRC rule alone.

Without Steam's list (no scan consent, an install Steam does not keep a list of) an official file is judged by the
CRC32 of its whole content against the CRC in its name, with no trailing bytes. A mod can match a CRC, never Steam's
SHA-1, so a copy kept by the CRC rule alone is named as such, and one that a Steam list later rejects stays rejected
after the list is gone again: it is never used as an official copy, never written back as official.

``originals.json`` in the library folder, beside ``originals/``:

    {"version": 1, "originals": {"<bundle name>": {"by": "steam" | "crc" | "failed", "sha1": "...", "size": 1,
                                                   "mtime": 1}}}

A record speaks for the bytes it names (``sha1``). Write strength compares the SHA-1; the cheap judgement of the
screens trusts a record whose size and modification time still match the file.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Literal

from .storage import Fingerprint, atomic_json

logger = logging.getLogger(__name__)

FILE = "originals.json"
VERSION = 1

Verdict = Literal["steam", "crc", "failed"]
VERDICTS = ("steam", "crc", "failed")


class OriginalChecks:
    """The verdicts of the kept originals, read and written under a lock of their own (thread-safe)."""

    def __init__(self, root: Path) -> None:
        self.path = root / FILE
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int] | None, dict[str, dict]] | None = None

    def _read(self) -> dict[str, dict]:
        try:
            stat = self.path.stat()
            signature: tuple[int, int] | None = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            signature = None
        if self._cache is not None and self._cache[0] == signature:
            return self._cache[1]
        records: dict[str, dict] = {}
        if signature is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            entries = data.get("originals") if isinstance(data, dict) and data.get("version") == VERSION else None
            for name, record in (entries or {}).items():
                if (isinstance(name, str) and isinstance(record, dict) and record.get("by") in VERDICTS
                        and isinstance(record.get("sha1"), str) and isinstance(record.get("size"), int)):
                    records[name] = record
        self._cache = (signature, records)
        return records

    def record(self, name: str) -> dict | None:
        with self._lock:
            return self._read().get(name)

    def of_bytes(self, name: str, fingerprint: Fingerprint) -> Verdict | None:
        """The verdict recorded for these very bytes (write strength: by SHA-1)."""
        record = self.record(name)
        if record is None or record["sha1"] != fingerprint.sha1 or record["size"] != fingerprint.size:
            return None
        return record["by"]

    def of_file(self, original: Path) -> Verdict | None:
        """The verdict recorded for a kept original as it is on disk now, judged by its size and modification time
        (the screens; never the content). None when there is none or the file changed since."""
        record = self.record(original.name)
        if record is None:
            return None
        try:
            stat = original.stat()
        except OSError:
            return None
        if stat.st_size != record["size"] or stat.st_mtime_ns != record.get("mtime"):
            return None
        return record["by"]

    def save(self, original: Path, by: Verdict, fingerprint: Fingerprint, keep: set[str] | None = None) -> None:
        """Record a verdict of a kept original's bytes. ``keep``: the names still kept; the records of the others
        go. Never raises: an unsaved verdict is judged again next time."""
        try:
            mtime = original.stat().st_mtime_ns
        except OSError:
            return
        entry = {"by": by, "sha1": fingerprint.sha1, "size": fingerprint.size, "mtime": mtime}
        with self._lock:
            records = dict(self._read())
            if records.get(original.name) == entry:
                return
            records[original.name] = entry
            if keep is not None:
                records = {name: value for name, value in records.items() if name in keep or name == original.name}
            try:
                atomic_json(self.path, {"version": VERSION, "originals": dict(sorted(records.items()))})
            except OSError as error:
                logger.warning("Could not save the check of a kept original: %s", error)
                return
            self._cache = None
