"""Memorial lobby lines and voices from Kivo (kivo.wiki), read on demand and kept in memory only.

The game's own text tables are encrypted, so the lines shown with a memorial lobby come from Kivo's student pages:
each voice entry is named after the game's voice clip (e.g. ``Hoshino_MemorialLobby_1.ogg``), the same name the
memorial lobby skeleton's talk animations carry as Spine events. Nothing read here is written to disk.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from collections import OrderedDict
from typing import Any

from ..core.errors import ServiceError

logger = logging.getLogger(__name__)

API = "https://api.kivo.wiki/api/v1/data/students/{page}"
AUDIO_HOST = "static.kivo.wiki"
USER_AGENT = "Blue-Archive-Modding-and-Patching-Tool"
TIMEOUT = 15.0
AUDIO_CACHE = 48  # clips kept in memory


def clip_id(name: str) -> str:
    """Normalise a voice clip or Spine event name to the key its line is stored under.

    The key is lower case, without folder, audio extension or ``_new`` suffix:
    ``Images/sound/Hoshino_MemorialLobby_3_1.ogg`` -> ``hoshino_memoriallobby_3_1``.
    """
    name = name.lower().replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"\.(ogg|wav|mp3)$", "", name)
    return re.sub(r"_new$", "", name)


def _one_line(text: str | None) -> str:
    """Join wrapped lines; drop the spaces Kivo puts between non-ASCII characters."""
    if not text:
        return ""
    text = re.sub(r"\s*\n\s*", "", text).strip()
    return re.sub(r"(?<=[^\x00-\x7f])[ \u3000]+(?=[^\x00-\x7f])", "", text)


def memorial_lobby_lines(page: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Collect the memorial lobby lines of a Kivo student page.

    Thai text comes from the Global table; Kivo supplies no Thai dialogue fallback.

    Args:
        page: The student page's data from Kivo's API (its ``voice`` and ``voice_cn`` lists).

    Returns:
        ``{clip id: {"zh-CN", "ja", "audio"}}``. zh-CN is the CN server's text when Kivo has it, else Kivo's
        translation; ja is the original line; audio is the URL of the Japanese voice (the one the Global and Japanese
        clients play), or empty.
    """

    def by_clip(entries: list[dict]) -> dict[str, dict]:
        # several entries can share a clip id ("<clip>_new" re-recordings with empty text): keep the one with text
        out: dict[str, dict] = {}
        for v in entries or []:
            desc = str(v.get("description", ""))
            if "memoriallobby" not in desc.lower():
                continue
            cid = clip_id(desc)
            if cid not in out or (v.get("text") and not out[cid].get("text")):
                out[cid] = v
        return out

    jp, cn = by_clip(page.get("voice", [])), by_clip(page.get("voice_cn", []))
    lines = {}
    for cid in sorted(set(jp) | set(cn)):
        j, c = jp.get(cid, {}), cn.get(cid, {})
        audio = j.get("file") or ""
        if audio.startswith("//"):
            audio = "https:" + audio
        lines[cid] = {
            "zh-CN": _one_line(c.get("text") or j.get("text")),
            "ja": _one_line(j.get("text_original")),
            "audio": audio,
        }
    return lines


class Kivo:
    """Kivo student pages and voice clips, fetched once per session and kept in memory."""

    def __init__(self) -> None:
        """Start with nothing fetched."""
        self._lock = threading.Lock()
        self._pages: dict[str, dict[str, dict[str, str]]] = {}
        self._audio: OrderedDict[str, bytes] = OrderedDict()

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.read()

    def lines(self, page_id: str) -> dict[str, dict[str, str]]:
        """Return the memorial lobby lines of a student page, fetched once per session.

        Args:
            page_id: Kivo's numeric student page id.

        Returns:
            The lines as ``memorial_lobby_lines`` gives them.

        Raises:
            ServiceError: ``lines_unavailable`` for an id that is not a number, or a page that could not be fetched
                or parsed.
        """
        if not page_id or not str(page_id).isdigit():
            raise ServiceError("lines_unavailable")
        with self._lock:
            cached = self._pages.get(page_id)
        if cached is not None:
            return cached
        try:
            data = json.loads(self._get(API.format(page=page_id)))
        except Exception as e:  # offline, HTTP errors, bad JSON
            logger.info("Kivo page %s unavailable: %s", page_id, e)
            raise ServiceError("lines_unavailable") from e
        lines = memorial_lobby_lines(data.get("data", data) if isinstance(data, dict) else {})
        with self._lock:
            self._pages[page_id] = lines
        return lines

    def audio(self, page_id: str, clip: str) -> bytes:
        """Return the audio of a memorial lobby voice clip, kept in memory for the last ``AUDIO_CACHE`` clips.

        Only Kivo's own voice host is contacted.

        Args:
            page_id: Kivo's numeric student page id; its lines are read first.
            clip: The clip or Spine event name, in any form ``clip_id`` accepts.

        Returns:
            The clip's audio file as fetched.

        Raises:
            KeyError: The page has no such clip, or its audio is not on Kivo's voice host.
            ServiceError: ``lines_unavailable`` when the page or the clip could not be fetched.
        """
        line = self.lines(page_id).get(clip_id(clip))
        url = (line or {}).get("audio", "")
        if not url.startswith(f"https://{AUDIO_HOST}/"):
            raise KeyError(clip)
        with self._lock:
            data = self._audio.get(url)
            if data is not None:
                self._audio.move_to_end(url)
                return data
        try:
            data = self._get(url)
        except Exception as e:
            raise ServiceError("lines_unavailable") from e
        with self._lock:
            self._audio[url] = data
            while len(self._audio) > AUDIO_CACHE:
                self._audio.popitem(last=False)
        return data
