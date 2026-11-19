"""Answers of content checks, kept on disk by the bytes they were read from.

Reading what a bundle points at, which objects it holds, or what a game update did to a mod opens the bundle, and
opening unpacks all of it. The answers depend only on the files' bytes (and, for a judgement against game files,
on the game's resource version), so they are kept by SHA-1 across runs. Losing the file costs time only: it is
derived data in the library's cache folder, bounded, and dropped whole when its schema number or its content does
not read.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .storage import atomic_bytes

logger = logging.getLogger(__name__)

#: raised when a reader's stored answer changes shape or meaning: every kept answer is dropped
SCHEMA = 1
KINDS = ("pointers", "layouts", "references", "impacts")
#: kept answers per kind, the least recently used dropped first
LIMIT = 8192
#: seconds between saves while a job keeps adding answers
SAVE_EVERY = 5.0


class CheckCache:
    """A bounded store of check answers by key, loaded at the first question and saved when asked or due."""

    def __init__(self, path: Path) -> None:
        """Name the file; nothing is read until the first question.

        Args:
            path: The cache file in the library's cache folder.
        """
        self.path = path
        self._lock = threading.Lock()
        self._kinds: dict[str, OrderedDict[str, Any]] | None = None
        self._dirty = False
        self._saved = time.monotonic()
        self.hits = self.misses = 0

    def _load(self) -> dict[str, OrderedDict[str, Any]]:
        if self._kinds is None:
            kinds: dict[str, OrderedDict[str, Any]] = {kind: OrderedDict() for kind in KINDS}
            try:
                data = json.loads(self.path.read_bytes())
                if data.get("schema") == SCHEMA:
                    for kind in KINDS:
                        kinds[kind].update(data.get(kind) or {})
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError, TypeError) as error:
                logger.info("The check cache did not read and is made again: %s", type(error).__name__)
                kinds = {kind: OrderedDict() for kind in KINDS}
            self._kinds = kinds
        return self._kinds

    def get(self, kind: str, key: str) -> Any | None:
        """Give a kept answer, None when there is none."""
        with self._lock:
            values = self._load()[kind]
            if key in values:
                values.move_to_end(key)
                self.hits += 1
                return values[key]
            self.misses += 1
            return None

    def put(self, kind: str, key: str, value: Any) -> None:
        """Keep an answer; it is saved at the next ``flush`` or when a save is due."""
        with self._lock:
            values = self._load()[kind]
            values[key] = value
            values.move_to_end(key)
            while len(values) > LIMIT:
                values.popitem(last=False)
            self._dirty = True
            due = time.monotonic() - self._saved >= SAVE_EVERY
        if due:
            self.flush()

    def flush(self) -> None:
        """Save what was added since the last save. A failed save is tried again at the next one."""
        with self._lock:
            if not self._dirty or self._kinds is None:
                return
            data = {"schema": SCHEMA, **{kind: dict(values) for kind, values in self._kinds.items()}}
            self._dirty = False
            self._saved = time.monotonic()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_bytes(self.path, json.dumps(data, separators=(",", ":")).encode())
        except (OSError, TypeError, ValueError) as error:
            logger.info("The check cache was not saved: %s", type(error).__name__)
            with self._lock:
                self._dirty = True

    def clear(self) -> None:
        """Forget every answer and delete the file."""
        with self._lock:
            self._kinds = {kind: OrderedDict() for kind in KINDS}
            self._dirty = False
        self.path.unlink(missing_ok=True)
