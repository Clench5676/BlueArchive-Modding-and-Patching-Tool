"""The Preview Cache keys a still by its renderer's code (webui/stills.py, BL-121): the pixel-file lists stay complete."""

import json
import re
import shutil
from pathlib import Path, PurePosixPath

import pytest

from bmpt.errors import ServiceError
from bmpt.webui import stills

WEB = stills.WEB_DIR
# static imports and re-exports (statements, possibly over several lines), side-effect imports, dynamic imports
SPECIFIERS = (re.compile(r"""(?ms)^\s*(?:import|export)\b[^;]*?\bfrom\s*["']([^"']+)["']"""),
              re.compile(r"""(?m)^\s*import\s*["']([^"']+)["']"""),
              re.compile(r"""\bimport\s*\(\s*["']([^"']+)["']\s*\)"""))


def _import_map() -> dict[str, str]:
    html = (WEB / "index.html").read_text()
    found = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
    return json.loads(found.group(1))["imports"]


def _resolve(importer: str, specifier: str, imports: dict[str, str]) -> str:
    if specifier.startswith("."):
        joined = PurePosixPath(importer).parent / specifier
        parts: list[str] = []
        for part in joined.parts:
            if part == "..":
                parts.pop()
            elif part != ".":
                parts.append(part)
        return "/".join(parts)
    if specifier in imports:
        return imports[specifier].removeprefix("./")
    prefix = next(key for key in imports if key.endswith("/") and specifier.startswith(key))
    return imports[prefix].removeprefix("./") + specifier[len(prefix):]


def _imports(name: str) -> set[str]:
    text = (WEB / name).read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)(^|[^:\"'])//[^\n]*", r"\1", text)
    return {match.group(1) for pattern in SPECIFIERS for match in pattern.finditer(text)}


@pytest.mark.parametrize("renderer", sorted(stills.RENDERERS))
def test_every_import_of_a_pixel_file_is_classified(renderer):
    imports = _import_map()
    pixels = set(stills.RENDERERS[renderer])
    for name in pixels:
        assert (WEB / name).is_file(), name
        for specifier in _imports(name):
            target = _resolve(name, specifier, imports)
            assert target in pixels or target in stills.NOT_PIXELS, (
                f"{name} imports {target}: add it to the {renderer!r} pixel files if it can change a still, "
                "else to NOT_PIXELS")


def test_the_vendor_runtimes_in_use_are_the_listed_ones():
    imports = _import_map()
    assert imports["three"].removeprefix("./") in stills.RENDERERS["model"]
    root = re.search(r'const ROOT = "([^"]+)"', (WEB / "js/spine-runtime.js").read_text()).group(1)
    assert root + "spine-player.min.js" in stills.RENDERERS["spine"]
    # the still entry points: the 3D still loads model.js, the Spine stills live in thumbs.js
    assert 'import("./model.js")' in (WEB / "js/model-still.js").read_text()
    assert {"js/model-still.js", "js/model.js"} <= set(stills.RENDERERS["model"])
    assert "js/thumbs.js" in stills.RENDERERS["spine"]


def test_a_change_to_a_pixel_file_changes_only_its_renderers_revision(tmp_path):
    for renderer in stills.RENDERERS.values():
        for name in renderer:
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(WEB / name, tmp_path / name)
    before = {name: stills.revision(name, tmp_path) for name in stills.RENDERERS}
    assert before["model"] != before["spine"]
    changed = tmp_path / "changed"
    shutil.copytree(tmp_path, changed, ignore=shutil.ignore_patterns("changed"))
    with (changed / "js/png-decode.js").open("a") as file:
        file.write("\n// changed\n")
    assert stills.revision("model", changed) != before["model"]
    assert stills.revision("spine", changed) == before["spine"]
    assert stills.revision("model", WEB) == stills.revision("model", WEB)


def test_an_unknown_renderer_is_refused():
    for renderer in (None, "", "other", 3):
        with pytest.raises(ServiceError) as error:
            stills.revision(renderer)
        assert error.value.code == "bad_params"
