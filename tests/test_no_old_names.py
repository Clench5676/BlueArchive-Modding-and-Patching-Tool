"""The App's code and docs use the screens' names, never the retired internal ones.

Every tracked text file under ``src/bmpt/`` and ``docs/`` is split into word parts (``sprKind`` -> ``spr`` +
``kind``, ``lobby_zoom`` -> ``lobby`` + ``zoom``, ``student-hall`` -> ``student`` + ``hall``) and each part is
checked against the retired names. Locale files are checked by key only: their values are the screens' text.
A hit passes only when an entry of ``ALLOWED`` covers it: a path pattern and a regular expression whose match
spans the hit, with the reason it is not the App's own name.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("src/bmpt", "docs")
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".css", ".html", ".json", ".md"}
# Third-party code kept as published, like the engine package outside src/bmpt.
SKIPPED = ("src/bmpt/web/vendor/",)
LOCALES = "src/bmpt/locales/*.json"
# Release notes of a version that has a release tag are published text and stay as shipped; the notes of
# an unreleased version are checked like any other file.
RELEASE_NOTES = re.compile(r"src/bmpt/assets/release-notes/(?P<version>[^/]+)\.md")

# retired name -> the current one
RETIRED = {
    "variant": "alt",
    "variants": "alts",
    "family": "student",
    "families": "students",
    "photograph": "thumbnail",
    "photographs": "thumbnails",
    "photography": "thumbnailCapture",
    "photographed": "captured",
    "photographing": "capturing",
    "photo": "thumbnail",
    "photos": "thumbnails",
    "roster": "studentList",
    "rosters": "studentLists",
    "graft": "addAnimations",
    "grafts": "addAnimations",
    "grafted": "addedAnimations",
    "grafting": "addAnimations",
    "hall": "studentHall",
    "halls": "studentHalls",
    "spr": "portrait",
    "lobby": "memorialLobby",
    "lobbies": "memorialLobbies",
    "model": "chibi",
    "models": "chibis",
}
# A part that is already the second half of the current name.
CURRENT_PAIRS = {"hall": "student", "halls": "student", "lobby": "memorial", "lobbies": "memorial"}

PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
LOCALE_KEY = re.compile(r'^\s*"([^"]+)"\s*:')

# (path pattern, regular expression covering the hit, reason)
ALLOWED: tuple[tuple[str, str, str], ...] = (
    # names the game defines, read as data
    (
        "*",
        r"\b[a-z]+[0-9]+_spr\b|\{[a-z_]+\}_spr\b|\*_spr\b|_spr\b(?=[\"'`.*/])",
        "game bundle and Spine names (*_spr)",
    ),
    ("src/bmpt/web/*", r"/_spr\$/i?|\(\?:npc\|spr\)\$", "the game's portrait bundle suffix in a regular expression"),
    ("*", r"\bUILobby\w*", "the game's home screen prefab UILobby and the prefabs named after it"),
    ("*", r"\bLobbyCommon(?:Night)?\b", "the game's office prefabs the Student Hall draws"),
    (
        "*",
        r"\bBtn_LobbyCharacter\b|\bLobby_balloon\b|\bUIAni_Lobby\b|\bLobbyCameraConfigPP\b|\bFX_SHADER_Lobby_Order\b",
        "a UILobby node, a sprite, UILobby's animation clip, a camera component and a shader, as the game names them",
    ),
    ("src/bmpt/web/*", r"\bCafe_Icon_Photo\b", "the game's camera icon sprite"),
    (
        "src/bmpt/web/*",
        r"(?:Right|\$\{right\})/Lobby\b|\(Option, Lobby, Line\)",
        "the top bar's home button, the game's node NUITop_Scenes/Top/Right/Lobby",
    ),
    (
        "src/bmpt/web/js/student-hall/student-hall-dialogue.js",
        r"_lobby_\\d",
        "the game's memorial lobby voice clip names (<student>_lobby_<n>)",
    ),
    # library and DOM names
    ("src/bmpt/web/js/chibi/*", r"\bmodel(?:View)?Matrix\b|object's model-view", "three.js and GLSL matrix names"),
    ("src/bmpt/web/*", r"font-variant(?:-[a-z]+)?|\bfont(?:Family|Variant)\b", "CSS and DOM font properties"),
    # the generation-1 readers of the data upgrade keep the old names on the old side
    ("src/bmpt/host/upgrade_inputs.py", r".*", "generation-1 reader of the data upgrade"),
    # words in another sense
    ("*.css", r"font-family", "CSS font family"),
)


# Exact line matches for external spellings that must remain stable.
_FIXED_LINES = (
    (
        "Bundle or texture family, or a group of CSV columns.",
        {
            "src/bmpt/catalog/_names.py": ('field: Column family, "name" or "skin".',),
            "src/bmpt/gameassets/chibi.py": (
                (
                    "The prefabs are in the bundle family ``character-<dev>-_mxload-*``; their meshes, materials, "
                    "textures and clips sit"
                ),
            ),
            "src/bmpt/gameassets/discovery.py": (
                '"""Check whether a bundle belongs to the shared UI texture families."""',
                "uses the EffectsFreezed texture families.",
                "path: Bundle filename to screen for prefab, tooltip, or prologue UI families without loading it.",
                "path: Bundle filename to screen for campaign, skill-icon, or effect texture families.",
            ),
            "src/bmpt/gameassets/office.py": (
                '"""Check whether a bundle belongs to the ordinary lobby\'s background texture family."""',
            ),
            "src/bmpt/gameassets/service.py": ('"""List the names available in the shared UI texture families."""',),
        },
    ),
    (
        "Engine module name exposed by the upstream package.",
        {
            "src/bmpt/engine.py": (
                '"AnimCheckOptions": ("models", "AnimCheckOptions"),',
                '"AnimDiffMap": ("models", "AnimDiffMap"),',
                '"AssetKey": ("models", "AssetKey"),',
                '"AssetType": ("models", "AssetType"),',
                '"ContNameTypeKey": ("models", "ContNameTypeKey"),',
                '"FilePair": ("models", "FilePair"),',
                '"LogFunc": ("models", "LogFunc"),',
                '"MatchStrategy": ("models", "MatchStrategy"),',
                '"Obj": ("models", "Obj"),',
                '"Patch": ("models", "Patch"),',
                '"PatchResult": ("models", "PatchResult"),',
                '"ProgressCallback": ("models", "ProgressCallback"),',
                '"RawAssetBytes": ("models", "RawAssetBytes"),',
                '"REPLACEABLE_ASSET_TYPES": ("models", "REPLACEABLE_ASSET_TYPES"),',
                '"ReplaceAssetType": ("models", "ReplaceAssetType"),',
                '"SaveOptions": ("models", "SaveOptions"),',
                '"SkelConvertOptions": ("models", "SkelConvertOptions"),',
                '"SkelVersionConflict": ("models", "SkelVersionConflict"),',
                "from ba_modding_toolkit.models import (",
            ),
        },
    ),
    (
        "Game bundle or Spine name suffix.",
        {
            "src/bmpt/catalog/__init__.py": (
                "core: Bundle core or internal skeleton name ending in _spr or a memorial lobby suffix.",
            ),
            "src/bmpt/catalog/_names.py": ('FILE_ID = re.compile(r"_|spr$|^(?:np|ch)\\d+$", re.IGNORECASE)',),
            "src/bmpt/library/identity.py": (
                (
                    "CORE = "
                    're.compile(r"(?<![a-z0-9_])([a-z][a-z0-9_]*?_(?:spr|home(?:_gl|_teen)?))(?=$|[^a-z0-9])", re.I)'
                ),
                'suffix = "home" if "spinelobbies/" in name else "spr" if "spinecharacters/" in name else None',
                '"home" if resource_type_of(parsed.core, parsed.category) == "memorialLobby" else "spr"',
            ),
        },
    ),
    (
        "Generated Unity object name or stored camera label used by existing mods.",
        {
            "src/bmpt/library/memorial_lobby_camera.py": (
                'NODE = "LobbyCamera"  # the camera node\'s GameObject, between the root and the scale targets',
                'return library._store(files, "Lobby camera", other_target(files), "other", metadata={TAG: meta})',
            ),
            "src/bmpt/library/memorial_lobby_camera_intro.py": (
                'm_Name="Lobby Camera",',
                (
                    'track.update(m_Name="Lobby Camera", m_InfiniteClip=ptr(clip_id), m_Clips=[], m_Children=[], '
                    "m_Muted=0, m_Locked=0)"
                ),
            ),
        },
    ),
    (
        "Generation-1 keys, source prefixes or paths required by the reversible codec.",
        {
            "src/bmpt/host/store_formats.py": (
                'RESOURCE_TYPES = {"spr": "portrait", "lobby": "memorialLobby", "model": "chibi", "other": "other"}',
                'old, new = ("variant:", "alt:") if forward else ("alt:", "variant:")',
                'old, new = "lobby_zoom", "memorial_lobby_zoom_out_mode"',
                'legacy = prefs.pop("lobby_zoom_out", None)',
                'old, new = "lobby_zoom_custom", "memorial_lobby_zoom_out_custom_limit"',
                '("lobby_post", "memorial_lobby_post_processing"),',
                '("lobby_intro", "memorial_lobby_intro"),',
                'names.move(entry, "variant", "alt")',
                'names.move(prefs, "roster", "student_list")',
                'student_list = _member(prefs, names.key("roster", "student_list"))',
                'names.move(filters, "lobby", "memorialLobby")',
                'names.move(filters, "model", "chibi")',
                'names.move(prefs, "hall_viewed", "student_hall_viewed")',
                'viewed_key = names.key("hall_viewed", "student_hall_viewed")',
                'names.move(record, "lobby_camera", "memorial_lobby_camera")',
                'camera = _member(record, names.key("lobby_camera", "memorial_lobby_camera"))',
                'names.move(camera, "variant", "alt")',
                'names.move(record, "roster_photography", "roster_thumbnails")',
                'recipe = _member(record, names.key("roster_photography", "roster_thumbnails"))',
                'names.move(record, "variant", "alt")',
                'record["mode"] = names.enum({"lobby": "memorialLobby"}, record["mode"])',
                'names.move(data, "next_by_variant", "next_by_alt")',
                'names.move(snapshot, "variant", "alt")',
                '"F": Store("library", "photographs/index.json", "thumbnails/index.json", _thumbnails, 2),',
                '"BF": Store("library", "photographs/images", "thumbnails/images", tree=True, policy="preserve"),',
                'counters = data.get("next_by_variant" if generation == 1 else "next_by_alt", {})',
                'counters = "next_by_variant" if generation == 1 else "next_by_alt"',
                'if path.name == "index.json" and path.parent.name == "photographs":',
                'names.move(entry, "photo", "thumbnail")',
                'names.move(record, "photo_defaults", "thumbnail_defaults")',
                'names.move(data, "photos", "thumbnails")',
                'for record in _objects(data.get(names.key("photos", "thumbnails"))):',
                'names.move(data, "photo", "thumbnail")',
                'snapshot = _member(data, names.key("photo", "thumbnail"))',
                '_require(data, "photos" if generation == 1 else "thumbnails", dict)',
                'items = "photos" if generation == 1 else "thumbnails"',
            ),
        },
    ),
    (
        "Generation-1 thumbnail directory read by upgrade or standalone library handling.",
        {
            "src/bmpt/host/data_upgrade.py": (
                'return "thumbnails/images/" + item.relative.removeprefix("photographs/images/")',
            ),
            "src/bmpt/library/thumbnails.py": (
                (
                    'self.root = Path(library_root) / ("thumbnails" if epoch_root(Path(library_root), "library") '
                    'else "photographs")'
                ),
            ),
            "src/bmpt/webui/services.py": (
                '"photographs",',
                'or (folder / "photographs" / "index.json").exists()',
            ),
        },
    ),
    (
        "Generation-2 file name of a deleted thumbnail's image in the asset deletion journal, read back by recovery.",
        {
            "src/bmpt/host/upgrade_journals.py": (
                'if not _match(_ID, key) or digest(path.parent / "photo.png") != snapshot["entry"]["sha256"]:',
            ),
            "src/bmpt/library/asset_delete.py": (
                'atomic_copy(store._path(key), self.directory / "photo.png")',
                'atomic_copy(self.directory / "photo.png", store._path(thumbnail["key"]))',
            ),
        },
    ),
    (
        "Generation-2 thumbnail recipe field spelling.",
        {
            "src/bmpt/host/store_formats.py": (
                "# Generation 2 stores the student list's thumbnail recipe as roster_thumbnails.",
                'names.move(record, "student_list_thumbnails", "roster_thumbnails")',
                '"roster_thumbnails",',
                'for entry in _objects(_member(record, "roster_thumbnails").get("choices")):',
            ),
        },
    ),
    (
        "Name, field, path or clip pattern defined by the game.",
        {
            "src/bmpt/gameassets/discovery.py": (
                '"""Check for memorial lobby prefab bundles containing UILobbyElement/Lobby<Name>.prefab."""',
            ),
            "src/bmpt/gameassets/memorial_lobby.py": (
                (
                    "The game places a memorial lobby with its UILobbyElement prefab (Lobby<Name>.prefab): every "
                    "skeleton sits at a fixed"
                ),
                'out = ["lobby" + core.lower().replace("_home", "", 1)]',
                'out += ["lobby" + low.replace("_home", "", 1), low, "lobby" + low]',
                "share Airi_home), the name itself (LobbyHoshino_multi), and its lobby-prefixed form.",
            ),
            "src/bmpt/gameassets/memorial_lobby_intro.py": (
                (
                    "hidden and shown again (TimeLineUILobbyController), audio mixer snapshots "
                    "(TimeLineAudioMixerSnapshotsController)."
                ),
                'elif cls == "TimeLineUILobbyController":',
                'out["icons"] = bool(at.get("IsShowLobbyIcons"))',
                'elif a.get("class") == "TimeLineUILobbyController":',
            ),
            "src/bmpt/gameassets/office.py": ('return path.name.startswith("uis-00_images-lobby-")',),
            "src/bmpt/lines/global_lines.py": (
                "The game names the clips ``<name>_lobby_<n>[_<part>]``.",
                'match = re.fullmatch(r"(.+)_lobby_(\\d+)(?:_(\\d+))?", clip)',
            ),
            "src/bmpt/webui/student_hall.py": (
                "Clip id (such as ``shiroko_lobby_1``) mapped to the bundle, serialized file and object that hold",
                'if not re.search(r"_lobby_\\d+(?:_\\d+)?$", cid):',
                'return clip.rsplit("_lobby_", 1)[1]',
                'if re.search(r"_lobby_\\d+(?:_\\d+)?$", name):',
                'clips = set(incoming) | {clip for clip in installed if clip.rsplit("_lobby_", 1)[0] in stems}',
                'match = re.search(r"_lobby_(\\d+)(?:_(\\d+))?$", clip)',
                'groups.setdefault(str(group), [(0, f"{v.key.lower()}_lobby_{group}")])',
                'if not isinstance(clip, str) or not re.fullmatch(r"[a-z0-9_]+_lobby_\\d+(?:_\\d+)?", clip):',
            ),
        },
    ),
    (
        "Portable package enum or a bundle filename family, independent of the App resource type.",
        {
            "src/bmpt/library/ba_mod.py": (
                '"lobby",',
                '"model",',
                'CORE_KINDS = ("portrait", "lobby", "spine_scene", "model")',
                'SPINE_KINDS = ("portrait", "lobby", "spine_scene")',
                (
                    "# The bundle families of each kind, by the family word of the bundle name "
                    "(``assets-_mx-<family>-...`` or"
                ),
                "# ``<family>-..._mxload-...``), as the format's kind table lists them.",
                "_FAMILIES = {",
                '"spinelobbies": "lobby",',
                '"characters": "model",',
                '"character": "model",',
                '"npcs": "model",',
                "def family_kind(name: str) -> str:",
                '"""Classify a bundle filename\'s family word, returning other for an unlisted family."""',
                "family = words[0]",
                'if family == "spinecharacters":',
                'if family == "uis":',
                'if family == "ui" and len(words) > 1 and words[1] == "uilobbyelement":',
                'return "lobby"  # a memorial lobby\'s prefab bundle',
                'return _FAMILIES.get(family, "other")',
                "names: Bundle filenames used to distinguish scenes and generic asset families.",
                'return "model"',
                'return "spine_scene" if any(family_kind(name) == "spine_scene" for name in names) else "lobby"',
                "kinds = {family_kind(name) for name in names}",
            ),
            "src/bmpt/webui/export_service.py": (
                'self.kind = "lobby" if camera else ba_mod.resource_type_of(resource_type, names)',
            ),
        },
    ),
    (
        "Registered GLB MIME type.",
        {
            "src/bmpt/webui/server.py": ('".glb": "model/gltf-binary",',),
        },
    ),
    (
        "Return values defined by the engine's Spine classifier.",
        {
            "src/bmpt/gameassets/spine.py": (
                'return {"spr": "portrait", "lobby": "memorialLobby"}.get(spine_kind(core, category))',
            ),
        },
    ),
    (
        "Shading model or shader variants in the rendering sense.",
        {
            "src/bmpt/gameassets/chibi.py": (
                (
                    "# the viewer's shading model for a shader, by name prefix (variants such as -Inline, "
                    "-Transparent or -NoOutline share"
                ),
                '"""Return a shader name\'s shading model, defaulting to MX/C-General/Single\'s "general"."""',
            ),
        },
    ),
    (
        "Smoothing mode of a geometry rule.",
        {
            "src/bmpt/gameassets/chibi_adapt/skirts.py": (
                "# the rules: Rule A where a part's skin can be rewritten, Rule B (its smooth variant likewise)",
                (
                    "changed = np.abs(chosen - plain).max(axis=1) > 1e-9  # the smooth variant rewrites the blended "
                    "vertices"
                ),
            ),
        },
    ),
)

ALLOWED += tuple(
    (path, rf"^\s*{re.escape(line)}\s*$", reason)
    for reason, paths in _FIXED_LINES
    for path, lines in paths.items()
    for line in lines
)

# The encoded manifest is opaque. Match only the matching fragment.
ALLOWED += (
    (
        "src/bmpt/catalog/data/tested_build.json",
        "yerfL7s/ye7Sr8X4yfSPRLcpyftDWstTyft3oom",
        "Opaque encoded names in the unchanged tested-build manifest.",
    ),
)


def _release_tags() -> set[str]:
    out = subprocess.run(["git", "tag", "--list"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return set(out.split())


def _published(path: str, tags: set[str]) -> bool:
    """Tell whether a path is the release notes of a version that was already released."""
    match = RELEASE_NOTES.fullmatch(path)
    return bool(match) and match["version"] in tags


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCANNED], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    tags = _release_tags()
    return [
        path
        for path in out.split("\0")
        if path
        and Path(path).suffix in TEXT_SUFFIXES
        and not path.startswith(SKIPPED)
        and not _published(path, tags)
        and (ROOT / path).is_file()
    ]


def _hits(text: str) -> list[tuple[int, int, str]]:
    parts = [(m.start(), m.end(), m.group().lower()) for m in PART.finditer(text)]
    found = []
    for index, (start, end, word) in enumerate(parts):
        if word not in RETIRED:
            continue
        before = CURRENT_PAIRS.get(word)
        if before and index and parts[index - 1][2] == before:
            gap = text[parts[index - 1][1] : start]
            if gap in ("", "-", "_", " ", "."):
                continue
        found.append((start, end, word))
    return found


def _allowed(path: str, line: str, start: int, end: int) -> bool:
    for pattern, expression, _reason in ALLOWED:
        if not fnmatch.fnmatch(path, pattern):
            continue
        for match in re.finditer(expression, line):
            if match.start() <= start and end <= match.end():
                return True
    return False


def scan() -> list[str]:
    """List every retired name in the scanned files as ``path:line: word -> current``."""
    report = []
    for path in _tracked():
        for start, end, word in _hits(path):
            report.append(f"{path}:0: {word} -> {RETIRED[word]} (file name)")
        locale = fnmatch.fnmatch(path, LOCALES)
        text = (ROOT / path).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            scanned = line
            if locale:
                key = LOCALE_KEY.match(line)
                scanned = line[: key.end(1)] if key else ""
            for start, end, word in _hits(scanned):
                if not _allowed(path, line, start, end):
                    report.append(f"{path}:{number}: {word} -> {RETIRED[word]}")
    return report


def test_parts_split_identifiers_and_keep_current_names():
    words = [word for _start, _end, word in _hits("sprKind lobby_zoom modelCores studentHall student-hall hallView")]
    assert words == ["spr", "lobby", "model", "hall"]
    assert _hits("memorialLobby memorial-lobby memorial_lobbies Memorial Lobby") == []
    assert _hits("spinelobbies MemoryLobbyPaths") == [(19, 24, "lobby")]


def test_allowlist_covers_only_the_matched_span():
    assert _allowed("src/bmpt/x.py", '"ch0001_spr"', 8, 11)
    assert not _allowed("src/bmpt/x.py", "sprKind", 0, 3)
    assert _allowed("src/bmpt/web/css/app.css", "  font-family: x;", 7, 13)


def test_only_released_notes_are_skipped():
    tags = {"0.7.0"}
    assert _published("src/bmpt/assets/release-notes/0.7.0.md", tags)
    assert not _published("src/bmpt/assets/release-notes/0.8.0.md", tags)
    assert not _published("src/bmpt/assets/release-notes/0.7.0-rc.1.md", tags)
    assert not _published("docs/0.7.0.md", tags)


def test_no_retired_names_in_src_and_docs():
    hits = scan()
    assert not hits, f"{len(hits)} retired names:\n" + "\n".join(hits)
