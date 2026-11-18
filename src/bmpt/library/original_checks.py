"""How each kept original was verified: by Steam's file list (size and SHA-1) or by the CRC rule alone.

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

import logging
import threading
from pathlib import Path
from typing import Literal

from ..host.store_formats import read_runtime, runtime_store
from ..host.upgrade_files import digest
from . import toggle_timing
from .storage import Fingerprint, atomic_json

logger = logging.getLogger(__name__)

FILE = "originals.json"
VERSION = 1

Verdict = Literal["steam", "crc", "failed"]
VERDICTS = ("steam", "crc", "failed")


class OriginalChecks:
    """The verdicts of the kept originals, read and written under a lock of their own (thread-safe)."""

    def __init__(self, root: Path) -> None:
        """Locate the verification record file without creating it.

        Args:
            root: Library folder containing originals.json and the kept originals.
        """
        self.path = root / FILE
        self._lock = threading.Lock()
        self._extra: dict = {}
        self._cache: tuple[tuple[int, int, int] | None, dict[str, dict]] | None = None
        self.managed = runtime_store(self.path) is not None

    def _read(self) -> dict[str, dict]:
        try:
            stat = self.path.stat()
            signature: tuple[int, int, int] | None = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
        except OSError:
            signature = None
        if self._cache is not None and self._cache[0] == signature:
            return self._cache[1]
        records: dict[str, dict] = {}
        self._extra = {}
        if signature is not None:
            try:
                data = read_runtime(self.path)
            except (OSError, ValueError):
                data = None
            entries = data.get("originals") if isinstance(data, dict) and data.get("version") == VERSION else None
            if entries is not None:
                self._extra = {key: value for key, value in data.items() if key not in ("version", "originals")}
            for name, record in (entries or {}).items():
                if (
                    isinstance(name, str)
                    and isinstance(record, dict)
                    and record.get("by") in VERDICTS
                    and isinstance(record.get("sha1"), str)
                    and isinstance(record.get("size"), int)
                ):
                    records[name] = record
        self._cache = (signature, records)
        return records

    def record(self, name: str) -> dict | None:
        """Return a bundle filename's recorded verdict and fingerprint, or None if absent."""
        with self._lock:
            return self._read().get(name)

    @toggle_timing.stage("original_digest")
    def matches_digest(self, original: Path, *, name: str | None = None) -> bool:
        """Check a kept file against its recorded baseline with a fresh full-file read."""
        if not self.managed:
            return True
        with self._lock:
            records = self._read()
            name = name or original.name
            expected = self._extra.get("digests", {}).get(name)
            verdict = records.get(name)
        if not isinstance(expected, str):
            return False
        try:
            if digest(original) != expected:
                return False
            return verdict is None or digest(original, "sha1") == verdict["sha1"]
        except (OSError, ValueError):
            return False

    @toggle_timing.stage("original_digest")
    def remember_digest(self, original: Path, verified_sha1: str) -> None:
        """Record the SHA-256 of bytes that match an independently verified SHA-1."""
        if not self.managed:
            return
        expected = digest(original)
        if digest(original, "sha1") != verified_sha1:
            raise OSError(f"Kept official bytes changed: {original}")
        with self._lock:
            records = self._read()
            digests = {**self._extra.get("digests", {}), original.name: expected}
            self._write(records, {**self._extra, "digests": digests})

    def _write(self, records: dict, extra: dict) -> None:
        atomic_json(self.path, {**extra, "version": VERSION, "originals": dict(sorted(records.items()))})
        self._cache = None

    def of_bytes(self, name: str, fingerprint: Fingerprint) -> Verdict | None:
        """Find the recorded verdict for exact bytes using write-strength verification.

        Args:
            name: Bundle filename indexing the verification record.
            fingerprint: Current SHA-1 and size to match against the record.

        Returns:
            The verdict, or None if the record is absent or names different bytes.
        """
        record = self.record(name)
        if record is None or record["sha1"] != fingerprint.sha1 or record["size"] != fingerprint.size:
            return None
        return record["by"]

    def of_file(self, original: Path) -> Verdict | None:
        """Read a kept original's verdict by size and mtime without hashing its content.

        Args:
            original: Kept bundle whose filename indexes the record shown on library screens.

        Returns:
            The verdict, or None if the file or matching record is unavailable.
        """
        record = self.record(original.name)
        if record is None or not self.matches_digest(original):
            return None
        try:
            stat = original.stat()
        except OSError:
            return None
        if stat.st_size != record["size"] or stat.st_mtime_ns != record.get("mtime"):
            return None
        return record["by"]

    @toggle_timing.stage("original_verdict_save")
    def save(self, original: Path, by: Verdict, fingerprint: Fingerprint, keep: set[str] | None = None) -> None:
        """Record a kept original's verdict, tolerating filesystem failures for later rechecking.

        Args:
            original: Kept bundle whose filename and mtime are recorded.
            by: Verification result: steam, crc, or failed.
            fingerprint: Verified bytes' SHA-1 and size.
            keep: Filenames whose records remain; None skips pruning. The saved record is always kept.
        """
        try:
            mtime = original.stat().st_mtime_ns
            confirmed_digest = None
            if self.managed and by == "steam":
                confirmed_digest = digest(original)
                if digest(original, "sha1") != fingerprint.sha1:
                    return
        except (OSError, ValueError):
            return
        entry = {"by": by, "sha1": fingerprint.sha1, "size": fingerprint.size, "mtime": mtime}
        with self._lock:
            records = dict(self._read())
            extra = self._extra
            if confirmed_digest is not None:
                extra = {**extra, "digests": {**extra.get("digests", {}), original.name: confirmed_digest}}
            if records.get(original.name) == entry and extra == self._extra:
                return
            records[original.name] = entry
            if keep is not None:
                records = {name: value for name, value in records.items() if name in keep or name == original.name}
            try:
                self._write(records, extra)
            except OSError as error:
                logger.warning("Could not save the check of a kept original: %s", error)
                return
            self._cache = None
