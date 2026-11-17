"""SpineSkeletonDataConverter (wang606, PolyForm Noncommercial 1.0.0) as an optional helper of the graft.

Where the built-in graft (``bmpt.graft``) refuses an animation (it needs a bone, slot, event, constraint or skin the
mod's rig lacks), the rigs go through the converter to JSON, the animation is added there by name with what it needs
(:func:`graft_json`), and the result goes back to binary.

The project's own code (GPLv3): it starts the converter as a separate program and edits JSON; it holds none of the
converter's code. The converter is never shipped with the App: the user fetches it after confirming its licence
(:func:`download`), on Windows the release's exe at a pinned version and SHA-256. The converter's author publishes no
macOS build; a user who compiled one puts it into the tools folder (:func:`located`).

Safety: mod skeletons are untrusted and the converter is a C++ parser. A skeleton goes to it only after the App's own
reader (``bmpt.spine_binary``) walked it whole (:func:`to_json`), only as a temporary copy, started with an argument
list, never through a shell, with a time limit. The converter rounds numbers to two decimals: the caller compares the
mod's own poses before and after (:func:`drift`) and refuses a result past ``DRIFT``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class Release:
    """A converter release the App fetches: its tag, file name, download URL, SHA-256 and size in bytes."""

    version: str
    name: str
    url: str
    sha256: str
    size: int


#: the converter release the App fetches on Windows (GitHub release v3.8, 2026-07-19)
RELEASE = Release(
    "v3.8",
    "SpineSkeletonDataConverter.exe",
    "https://github.com/wang606/SpineSkeletonDataConverter/releases/download/v3.8/SpineSkeletonDataConverter.exe",
    "b2ca82e46f1f4ca463abf0ccfab32e3c01eb0dd89fc7289b6478f728ca8ed68a",
    1587712,
)
LICENCE = "PolyForm Noncommercial 1.0.0"
LICENCE_URL = "https://polyformproject.org/licenses/noncommercial/1.0.0/"
HOME = "https://github.com/wang606/SpineSkeletonDataConverter"
#: a macOS build the user compiled, in the tools folder
MAC_NAME = "SpineSkeletonDataConverter"
TOOLS = "tools"
TIMEOUT = 60.0  # seconds one conversion may take
MAX_OUTPUT = 64 << 20  # bytes a conversion may write
DRIFT = 0.05  # world units the mod's own poses may move through the rewrite (it rounds to two decimals)
USER_AGENT = "bmpt-spine-converter"


class ConverterError(Exception):
    """A step of the converter route failed: ``code`` names it (the caller turns it into a ``ServiceError``)."""

    def __init__(self, code: str, **params: Any):
        """Create the error.

        Args:
            code: The ``ServiceError`` code the caller raises.
            **params: The error's parameters, kept as text.
        """
        super().__init__(code)
        self.code, self.params = code, {key: str(value) for key, value in params.items()}


def platform_kind(platform: str | None = None) -> str:
    """Return ``"windows"``, ``"macos"`` or ``"other"`` for a ``sys.platform`` value; None uses this one."""
    platform = platform or sys.platform
    return "windows" if platform.startswith("win") else "macos" if platform == "darwin" else "other"


def tool_dir(data_dir: Path) -> Path:
    """Return the tools folder in the App's data folder, where the converter is kept."""
    return Path(data_dir) / TOOLS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def located(data_dir: Path, platform: str | None = None) -> Path | None:
    """Return the converter to run, or None.

    On Windows that is the pinned release in the tools folder, only while its size and SHA-256 are the pinned ones; on
    macOS an executable build the user compiled and put there as ``MAC_NAME``; nothing elsewhere.

    Args:
        data_dir: The App's data folder.
        platform: A ``sys.platform`` value; None uses this one.
    """
    kind = platform_kind(platform)
    folder = tool_dir(data_dir)
    if kind == "windows":
        path = folder / f"{Path(RELEASE.name).stem}-{RELEASE.version}.exe"
        return (
            path if path.is_file() and path.stat().st_size == RELEASE.size and _sha256(path) == RELEASE.sha256 else None
        )
    if kind == "macos":
        path = folder / MAC_NAME
        return path if path.is_file() and os.access(path, os.X_OK) else None
    return None


def status(data_dir: Path, platform: str | None = None) -> dict[str, Any]:
    """Return what the switch to the converter route shows.

    That is the platform, whether a converter is in place, whether the App can fetch one (Windows only), and what the
    consent names: the release's version and size, the licence and the converter's home page.

    Args:
        data_dir: The App's data folder.
        platform: A ``sys.platform`` value; None uses this one.
    """
    kind = platform_kind(platform)
    return {
        "platform": kind,
        "installed": located(data_dir, platform) is not None,
        "downloadable": kind == "windows",
        "version": RELEASE.version,
        "size": RELEASE.size,
        "licence": LICENCE,
        "licenceUrl": LICENCE_URL,
        "home": HOME,
    }


def download(
    data_dir: Path,
    *,
    consented: bool,
    platform: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 30.0,
) -> Path:
    """Fetch the pinned release into the tools folder, after the user confirmed its licence. Windows only.

    The size and SHA-256 must be the pinned ones, else nothing stays.

    Args:
        data_dir: The App's data folder.
        consented: Whether the user confirmed the converter's licence.
        platform: A ``sys.platform`` value; None uses this one.
        opener: Opens the download request, as ``urllib.request.urlopen`` does.
        progress: Called with bytes done and the release's size after each chunk.
        timeout: Seconds for the request.

    Returns:
        The converter's path.

    Raises:
        ConverterError: ``converter_consent`` without consent, ``converter_unavailable`` off Windows,
            ``converter_mismatch`` for a file of another size or hash, ``converter_download_failed`` on a network or
            disk error.
    """
    if not consented:
        raise ConverterError("converter_consent")
    if platform_kind(platform) != "windows":
        raise ConverterError("converter_unavailable", platform=platform_kind(platform))
    folder = tool_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{Path(RELEASE.name).stem}-{RELEASE.version}.exe"
    partial = target.with_name(target.name + ".part")
    digest, done = hashlib.sha256(), 0
    try:
        request = urllib.request.Request(RELEASE.url, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=timeout) as response, partial.open("wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                done += len(chunk)
                if done > RELEASE.size:
                    raise ConverterError("converter_mismatch", file=RELEASE.name)
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, RELEASE.size)
    except ConverterError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as error:  # a network or disk failure: nothing stays
        partial.unlink(missing_ok=True)
        raise ConverterError("converter_download_failed", error=type(error).__name__) from error
    if done != RELEASE.size or digest.hexdigest() != RELEASE.sha256:
        partial.unlink(missing_ok=True)
        raise ConverterError("converter_mismatch", file=RELEASE.name)
    os.replace(partial, target)
    return target


def _run(exe: Path, source: Path, target: Path) -> subprocess.CompletedProcess:
    """One conversion: the converter on a temporary copy, an argument list, no shell, a time limit."""
    return subprocess.run(
        [str(exe), str(source), str(target)],
        cwd=str(source.parent),
        capture_output=True,
        timeout=TIMEOUT,
        shell=False,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def convert(exe: Path, data: bytes, source_suffix: str, target_suffix: str) -> bytes:
    """Run the converter on ``data`` in a temporary folder of its own and return the output.

    Args:
        exe: The converter.
        data: The skeleton to convert.
        source_suffix: ``.skel`` or ``.json``, the format of ``data``.
        target_suffix: ``.json`` or ``.skel``, the format wanted.

    Raises:
        ConverterError: ``converter_failed`` on a time-out, a start failure, a non-zero exit, no output or output
            above ``MAX_OUTPUT``.
    """
    with tempfile.TemporaryDirectory(prefix="bmpt-converter-") as folder:
        source = Path(folder) / f"in{source_suffix}"
        target = Path(folder) / f"out{target_suffix}"
        source.write_bytes(data)
        try:
            done = _run(exe, source, target)
        except subprocess.TimeoutExpired:
            raise ConverterError("converter_failed", detail="timeout") from None
        except OSError as error:
            raise ConverterError("converter_failed", detail=type(error).__name__) from error
        if done.returncode != 0 or not target.is_file():
            raise ConverterError("converter_failed", detail=f"exit {done.returncode}")
        if target.stat().st_size > MAX_OUTPUT:
            raise ConverterError("converter_failed", detail="output too large")
        return target.read_bytes()


def to_json(exe: Path, skeleton: bytes) -> dict:
    """Convert a binary skeleton to the converter's JSON, after the App's own reader walked it whole.

    A file the reader cannot walk never reaches the converter.

    Args:
        exe: The converter.
        skeleton: The binary skeleton.

    Returns:
        The JSON document.

    Raises:
        ConverterError: ``converter_unreadable`` for a skeleton the reader refuses; ``converter_failed`` as
            ``convert`` raises it, or for output that is not a skeleton document.
    """
    from .spine_binary import Skeleton  # the runtime-derived reader, after the user's Spine licence answer

    try:
        Skeleton(skeleton)
    except (ValueError, IndexError, EOFError) as error:
        raise ConverterError("converter_unreadable", detail=str(error)) from None
    try:
        out = json.loads(convert(exe, skeleton, ".skel", ".json").decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConverterError("converter_failed", detail="json") from error
    if not isinstance(out, dict) or not isinstance(out.get("skeleton"), dict):
        raise ConverterError("converter_failed", detail="json")
    return out


def to_binary(exe: Path, document: dict) -> bytes:
    """Convert a JSON skeleton document back to binary and check that the App's reader walks the result.

    Raises:
        ConverterError: ``converter_failed`` as ``convert`` raises it, or for a result the reader refuses.
    """
    data = convert(exe, json.dumps(document, ensure_ascii=False).encode("utf-8"), ".json", ".skel")
    from .spine_binary import Skeleton

    try:
        Skeleton(data)
    except (ValueError, IndexError, EOFError) as error:
        raise ConverterError("converter_failed", detail="binary") from error
    return data


# --- the graft in JSON ---------------------------------------------------------------------------------------------

CONSTRAINTS = ("ik", "transform", "path", "physics")


@dataclass
class JsonGraft:
    """What :func:`graft_json` carried.

    The animations added, the reason for each animation left out, the bones and slots added, and the atlas regions the
    carried attachments draw.
    """

    added: list[str]
    refused: dict[str, str]
    bones: list[str]
    slots: list[str]
    regions: list[str] = None  # the atlas regions the carried attachments draw

    def __post_init__(self) -> None:
        """Give ``regions`` a list of its own."""
        if self.regions is None:
            self.regions = []


def _names(items: Iterable[Mapping[str, Any]] | None) -> list[str]:
    return [item.get("name") for item in items or [] if isinstance(item, Mapping)]


def _skin(document: Mapping[str, Any], name: str) -> dict | None:
    return next((s for s in document.get("skins") or [] if isinstance(s, dict) and s.get("name") == name), None)


def _needs(animation: Mapping[str, Any]) -> dict[str, set[str]]:
    """The names an animation's timelines refer to.

    Bones, slots, constraints by kind, events, skins (deforms) and per (skin, slot) the attachments its attachment
    keys show or its deforms change.
    """
    need: dict[str, set] = {
        "bones": set(animation.get("bones") or {}),
        "slots": set(animation.get("slots") or {}),
        "events": {key.get("name") for key in animation.get("events") or [] if isinstance(key, dict)},
        "skins": set(),
        "shown": set(),
        "deformed": set(),
    }
    for kind in CONSTRAINTS:
        need[kind] = set(animation.get(kind) or {})
    for slot, timelines in (animation.get("slots") or {}).items():
        for key in (timelines or {}).get("attachment") or []:
            if isinstance(key, dict) and key.get("name"):
                need["shown"].add((slot, key["name"]))
    for skin, slots in (animation.get("attachments") or {}).items():
        need["skins"].add(skin)
        for slot, attachments in (slots or {}).items():
            need["slots"].add(slot)
            for attachment in attachments or {}:
                need["deformed"].add((skin, slot, attachment))
    need["events"].discard(None)
    return need


def graft_json(mod: dict, official: dict) -> JsonGraft:
    """Add every animation of ``official`` that ``mod`` lacks to ``mod``, in place, with what it refers to.

    Matched by name, what the mod lacks is copied: bones with the ancestors the mod lacks (parents first, in the
    official order), slots (after the last slot before them in the official order that the mod has), events, the
    constraints the animation keys with their bones and targets, and the attachments its keys show (into the default
    skin) or deform (into the mod's skin of the same name). Refused, with the reason, and nothing of it added: an
    animation that needs a skin the mod lacks, deforms an attachment the mod has in another shape (a mesh the mod
    redrew), shows an attachment neither rig has, or shows a linked mesh the mod lacks.

    Args:
        mod: The mod's skeleton as the converter's JSON; changed in place.
        official: The official skeleton as the converter's JSON.

    Returns:
        What was carried and what was refused.
    """
    result = JsonGraft([], {}, [], [])
    m_bones = {b.get("name"): b for b in mod.setdefault("bones", [])}
    o_bones = {b.get("name"): b for b in official.get("bones") or []}
    m_slots = _names(mod.setdefault("slots", []))
    o_slots = official.get("slots") or []
    mod.setdefault("animations", {})
    for name, animation in (official.get("animations") or {}).items():
        if name in mod["animations"] or not isinstance(animation, dict):
            continue
        need = _needs(animation)
        reason = _missing_reason(mod, official, need)
        if reason:
            result.refused[name] = reason
            continue
        # bones: the missing ones with the ancestors the mod lacks, parents first
        wanted_bones = set(need["bones"]) | {s.get("bone") for s in o_slots if s.get("name") in need["slots"]}
        for kind in CONSTRAINTS:
            for c in official.get(kind) or []:
                if c.get("name") in need[kind]:
                    wanted_bones |= set(c.get("bones") or []) | {c.get("target"), c.get("bone")} - {None}
        chain = []
        for bone in sorted(b for b in wanted_bones if b and b not in m_bones):
            while bone and bone not in m_bones and bone not in chain:
                chain.append(bone)
                bone = o_bones.get(bone, {}).get("parent")
        for bone in [b.get("name") for b in official.get("bones") or [] if b.get("name") in chain]:
            mod["bones"].append(json.loads(json.dumps(o_bones[bone])))
            m_bones[bone] = mod["bones"][-1]
            result.bones.append(bone)
        # slots where the official order has them
        for i, slot in enumerate(o_slots):
            if slot.get("name") in need["slots"] and slot.get("name") not in m_slots:
                before = next((s.get("name") for s in reversed(o_slots[:i]) if s.get("name") in m_slots), None)
                at = m_slots.index(before) + 1 if before is not None else 0
                mod["slots"].insert(at, json.loads(json.dumps(slot)))
                m_slots.insert(at, slot.get("name"))
                result.slots.append(slot.get("name"))
        for event in need["events"]:
            mod.setdefault("events", {}).setdefault(
                event, json.loads(json.dumps((official.get("events") or {})[event]))
            )
        for kind in CONSTRAINTS:
            have = set(_names(mod.get(kind)))
            for c in official.get(kind) or []:
                if c.get("name") in need[kind] and c.get("name") not in have:
                    mod.setdefault(kind, []).append(json.loads(json.dumps(c)))
        # attachments shown or deformed: from the official skin that has them into the mod's of the same name
        for skin_name, slot, attachment in sorted({("default", s, a) for s, a in need["shown"]} | need["deformed"]):
            o_skin = _skin(official, skin_name) or {}
            source = ((o_skin.get("attachments") or {}).get(slot) or {}).get(attachment)
            m_skin = _skin(mod, skin_name)
            if source is None or m_skin is None:
                continue
            kept = m_skin.setdefault("attachments", {}).setdefault(slot, {})
            if attachment not in kept:
                kept[attachment] = json.loads(json.dumps(source))
                if source.get("type", "region") in ("region", "mesh", "linkedmesh"):
                    region = source.get("path") or attachment
                    if region not in result.regions:
                        result.regions.append(region)
        mod["animations"][name] = json.loads(json.dumps(animation))
        result.added.append(name)
    return result


def _missing_reason(mod: Mapping[str, Any], official: Mapping[str, Any], need: Mapping[str, set]) -> str | None:
    for skin in need["skins"]:
        if _skin(mod, skin) is None:
            return f"skin {skin}"
    for skin, slot, attachment in need["deformed"]:
        mine = ((_skin(mod, skin) or {}).get("attachments") or {}).get(slot, {}).get(attachment)
        theirs = ((_skin(official, skin) or {}).get("attachments") or {}).get(slot, {}).get(attachment)
        if (
            mine is not None
            and theirs is not None
            and (
                mine.get("type") != theirs.get("type")
                or len(mine.get("vertices") or []) != len(theirs.get("vertices") or [])
            )
        ):
            return f"deform of {slot}/{attachment}, redrawn by the mod"
    for slot, attachment in need["shown"]:
        theirs = ((_skin(official, "default") or {}).get("attachments") or {}).get(slot, {}).get(attachment)
        mine = ((_skin(mod, "default") or {}).get("attachments") or {}).get(slot, {}).get(attachment)
        if theirs is None and mine is None:
            return f"attachment {slot}/{attachment}"
        if mine is None and theirs.get("type") == "linkedmesh":  # its parent mesh would have to come along
            return f"linked mesh {slot}/{attachment}"
    return None


def drift(before: Mapping[str, Iterable[float]], after: Mapping[str, Iterable[float]]) -> float:
    """Return how far the mod's own poses moved through the rewrite, in world units.

    That is the largest distance between the world vertices of each (pose, slot) the page posed before and after; a
    part only one side has, or with another vertex count, counts as infinitely far.

    Args:
        before: World vertices (``x, y, x, y, ...``) by pose and slot, from the original rig.
        after: The same from the rewritten rig.
    """
    worst = 0.0
    for key in set(before) | set(after):
        a, b = list(before.get(key) or []), list(after.get(key) or [])
        if len(a) != len(b) or key not in before or key not in after:
            return float("inf")
        for i in range(0, len(a) - 1, 2):
            worst = max(worst, ((a[i] - b[i]) ** 2 + (a[i + 1] - b[i + 1]) ** 2) ** 0.5)
    return worst
