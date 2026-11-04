"""The memorial lobby's viewing options and input: the zoom-out setting, the --debug input trace, the wheel kinds
(lobby-touch.js, run with Node when it is installed) and their UI strings."""

import io
import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from bmpt.config import Settings
from bmpt.webui.api import TRACE_BATCH, TRACE_LINE, build_api, ui_strings
from bmpt.webui.main import _trace_to_file
from bmpt.webui.server import WebApp
from bmpt.webui.services import Services
from bmpt.webui.state import UiState

WEB = Path(__file__).resolve().parents[1] / "src" / "bmpt" / "web"


class FakeGame:
    resource_version = "rv1"


@pytest.fixture
def services(tmp_path: Path):
    s = Services(Settings(tmp_path / "config.toml"), tmp_path, state=UiState(tmp_path / "webui.json"),
                 cache=object(), game=FakeGame(), catalog=object(), library=object())
    yield s
    s.close()


@pytest.fixture
def app(services):
    return WebApp(build_api(services), services.events, assets=services.asset, token="tok",
                  consent=lambda: services.state.consent)


def rpc(app, method, **params):
    data = json.dumps({"method": method, "params": params}).encode()
    environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/rpc", "QUERY_STRING": "", "CONTENT_LENGTH": str(len(data)),
               "wsgi.input": io.BytesIO(data), "HTTP_X_BAMT_TOKEN": "tok"}
    seen = {}
    body = b"".join(app(environ, lambda status, headers: seen.update(status=status)))
    assert seen["status"] == "200 OK"
    return json.loads(body)


def test_zoom_out_setting_is_full_art_by_default_and_persists(app, tmp_path):
    assert rpc(app, "settings.get")["result"]["lobbyZoom"] == {"mode": "art", "custom": 2.0}
    for mode in ("art", "unlocked", "custom", "game"):
        assert rpc(app, "settings.set", key="lobbyZoom", value=mode)["result"]["lobbyZoom"]["mode"] == mode
        assert UiState(tmp_path / "webui.json").get("lobby_zoom") == mode
    for bad in ("wide", "", None, True, ["art"]):
        assert rpc(app, "settings.set", key="lobbyZoom", value=bad)["error"]["code"] == "bad_params"
    assert rpc(app, "settings.get")["result"]["lobbyZoom"]["mode"] == "game"


def test_custom_zoom_out_limit_is_kept_in_range_and_steps(app, tmp_path):
    # game views across, 1.0 to 4.0 in 0.1 steps (lobby.js never zooms out further)
    for value, kept in ((3.33, 3.3), (1, 1.0), (9, 4.0), (0.2, 1.0), (2.75, 2.8)):
        assert rpc(app, "settings.set", key="lobbyZoomCustom", value=value)["result"]["lobbyZoom"]["custom"] == kept
    assert UiState(tmp_path / "webui.json").get("lobby_zoom_custom") == 2.8
    for bad in ("2", None, True, float("nan"), float("inf")):
        assert rpc(app, "settings.set", key="lobbyZoomCustom", value=bad)["error"]["code"] == "bad_params"
    assert rpc(app, "settings.get")["result"]["lobbyZoom"]["custom"] == 2.8


def test_zoom_out_setting_migrates_the_saved_boolean(services, app):
    # before the modes the setting was a boolean (lobby_zoom_out): on was Full Art, off the game's view
    services.state.set("lobby_zoom_out", True)
    assert rpc(app, "settings.get")["result"]["lobbyZoom"] == {"mode": "art", "custom": 2.0}
    services.state.set("lobby_zoom_out", False)
    assert rpc(app, "settings.get")["result"]["lobbyZoom"]["mode"] == "game"
    # a chosen mode wins over the old boolean, and a damaged value falls back to it
    services.state.set("lobby_zoom_out", True)
    rpc(app, "settings.set", key="lobbyZoom", value="unlocked")
    assert rpc(app, "settings.get")["result"]["lobbyZoom"]["mode"] == "unlocked"
    services.state.set("lobby_zoom", "everything")
    services.state.set("lobby_zoom_custom", "far")
    assert rpc(app, "settings.get")["result"]["lobbyZoom"] == {"mode": "art", "custom": 2.0}
    services.state.set("lobby_zoom_out", False)
    assert rpc(app, "settings.get")["result"]["lobbyZoom"]["mode"] == "game"


def test_post_processing_setting_is_on_by_default_and_persists(app, tmp_path):
    # the game's PP option: the lobby is drawn with its post-processing volumes unless it is turned off
    assert rpc(app, "settings.get")["result"]["lobbyPost"] is True
    assert rpc(app, "settings.set", key="lobbyPost", value=False)["result"]["lobbyPost"] is False
    assert UiState(tmp_path / "webui.json").get("lobby_post") is False
    assert rpc(app, "settings.set", key="lobbyPost", value=1)["result"]["lobbyPost"] is True


def test_intro_setting_is_on_by_default_and_persists(app, tmp_path):
    # the memorial lobby's intro plays when the lobby is entered unless it is turned off
    assert rpc(app, "settings.get")["result"]["lobbyIntro"] is True
    assert rpc(app, "settings.set", key="lobbyIntro", value=False)["result"]["lobbyIntro"] is False
    assert UiState(tmp_path / "webui.json").get("lobby_intro") is False
    assert rpc(app, "settings.set", key="lobbyIntro", value=True)["result"]["lobbyIntro"] is True


def test_input_trace_is_logged_only_when_started_with_debug(app, services, caplog):
    caplog.set_level(logging.INFO, logger="bmpt.webui.trace")
    events = [{"k": "down", "pt": "mouse", "b": 0, "bs": 1}, {"k": "wheel", "dy": 4.5, "act": "pan"}]
    assert rpc(app, "app.info")["result"]["debug"] is False
    rpc(app, "debug.trace", events=events)
    assert not [r for r in caplog.records if r.name == "bmpt.webui.trace"]

    services.debug = True
    assert rpc(app, "app.info")["result"]["debug"] is True
    rpc(app, "debug.trace", events=events + [{"k": "move", "note": "x" * 5000}] + [{"k": "move"}] * (TRACE_BATCH + 10))
    lines = [r.getMessage() for r in caplog.records if r.name == "bmpt.webui.trace"]
    assert len(lines) == TRACE_BATCH  # one call logs a bounded batch
    assert json.loads(lines[0]) == events[0] and json.loads(lines[1]) == events[1]
    assert len(lines[2]) == TRACE_LINE  # and bounded lines
    rpc(app, "debug.trace", events="not a list")  # ignored, never an error


def test_input_trace_also_goes_to_a_file(tmp_path):
    trace = logging.getLogger("bmpt.webui.trace")
    before = list(trace.handlers)
    try:
        _trace_to_file(tmp_path / "input-trace.log")
        trace.info('{"k":"down"}')
        for h in trace.handlers:
            h.flush()
        assert '{"k":"down"}' in (tmp_path / "input-trace.log").read_text(encoding="utf-8")
    finally:
        for h in [h for h in trace.handlers if h not in before]:
            trace.removeHandler(h)
            h.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_wheel_kinds():
    # a mouse wheel zooms, a trackpad swipe pans; one scroll keeps the kind of its first event, unless a scroll taken
    # for a wheel sends what no wheel sends. In WebKit (the macOS app) wheelDeltaY is 3 x deltaY for every device: a
    # notch is a tenth of a line in 16.16 fixed point (4.000244 px), a trackpad step whole pixels.
    script = """
const T = await import(process.argv[1]);
const now = 10000, none = { kind: null, at: -Infinity }, pad = { kind: "trackpad", at: now - 50 };
const cases = [
  [{ deltaMode: 1, deltaY: 3, deltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: 100, deltaX: 0, wheelDeltaY: -120, wheelDeltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: -200, deltaX: 0, wheelDeltaY: 240, wheelDeltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: 4.5, deltaX: 0.5, wheelDeltaY: -13, wheelDeltaX: -1 }, none],
  [{ deltaMode: 0, deltaY: 12, deltaX: 0, wheelDeltaY: -36, wheelDeltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: 40, deltaX: 0, wheelDeltaY: -120, wheelDeltaX: 0 }, { kind: "trackpad", at: now - 50 }],
  [{ deltaMode: 0, deltaY: 40, deltaX: 0, wheelDeltaY: -120, wheelDeltaX: 0 }, { kind: "trackpad", at: now - 500 }],
  [{ deltaMode: 0, deltaY: 0, deltaX: 30, wheelDeltaY: 0, wheelDeltaX: -90 }, none],
  [{ deltaMode: 0, deltaY: -4.000244140625, deltaX: 0, wheelDeltaY: 12, wheelDeltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: -3.9996337890625, deltaX: 0, wheelDeltaY: 11, wheelDeltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: 12.000732421875, deltaX: 0, wheelDeltaY: -36, wheelDeltaX: 0 }, none],
  [{ deltaMode: 0, deltaY: 4.000244140625, deltaX: 0, wheelDeltaY: -12, wheelDeltaX: 0 }, pad],
  [{ deltaMode: 0, deltaY: 7, deltaX: 0, wheelDeltaY: -21, wheelDeltaX: 0 }, { kind: "mouse", at: now - 20 }],
  [{ deltaMode: 0, deltaY: 40, deltaX: 0, wheelDeltaY: -120, wheelDeltaX: 0 }, { kind: "mouse", at: now - 20 }],
  [{ deltaMode: 0, deltaY: 0, deltaX: 0, wheelDeltaY: 0, wheelDeltaX: 0 }, none],
];
console.log(JSON.stringify(cases.map(([e, last]) => T.wheelKind(e, now, last))));
"""
    out = subprocess.run(["node", "--input-type=module", "-e", script, (WEB / "js" / "lobby-touch.js").as_uri()],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    assert json.loads(out) == ["mouse", "mouse", "mouse", "trackpad", "trackpad", "trackpad", "mouse", "trackpad",
                               "mouse", "mouse", "mouse", "trackpad", "trackpad", "mouse", "trackpad"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_wheel_zooms_and_pans_in_node():
    # the wheel handler on a recorded lobby: WebKit spins zoom, swipes pan, a swipe taken for a wheel is undone
    tests = Path(__file__).parent / "js" / "lobby_wheel.test.mjs"
    result = subprocess.run(["node", "--test", str(tests)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_zoom_out_limit_in_node():
    # the Full Art limit: every clip's poses are checked and the painted rect gives up what one uncovers
    tests = Path(__file__).parent / "js" / "lobby_frame.test.mjs"
    result = subprocess.run(["node", "--test", str(tests)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_idle_clips_and_sound_keys_in_node():
    # the clips the idle starts, played at random times as the game does, and the sound keys of the clips
    tests = Path(__file__).parent / "js" / "lobby_sync.test.mjs"
    result = subprocess.run(["node", "--test", str(tests)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_lobby_strings_in_both_languages():
    for lang in ("zh-CN", "en-US"):
        strings = ui_strings(lang)
        for key in ("tab.l2d", "zoomOut.label", "zoomOut.game", "zoomOut.art", "zoomOut.unlocked", "zoomOut.custom",
                    "zoomOut.limit", "zoomOut.factor", "post.label", "post.on", "post.off"):
            assert strings.get(f"webui.settings.{key}"), (lang, key)


# Dialogue geometry from the verified development view; no images or source text are retained.
import os

from bmpt.gameassets import AssetCache, GameAssets
from bmpt.gameassets.unity import open_bundle
from ba_modding_toolkit.searching import get_search_dirs
from bmpt.webui.student_hall import dialogue_layout

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")



@pytest.fixture(scope="module")
def game(tmp_path_factory):
    return GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path_factory.mktemp("dialogue-cache"), allowed=False))


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Official development view required")
def test_talk_template_is_the_prefab_box_at_the_origin(game):
    from bmpt.gameassets.layouts import export_layout, subtree
    path = game._bundle_path(game._lobby_prefabs()["lobbycommon"])
    with open_bundle(path) as env:
        original = subtree(export_layout(env, "LobbyCommon", {}), "DialogPanel/ChatDialog/Talk")
    saved = dialogue_layout(game)
    assert saved["pos"] == [0, 0]
    assert [child["name"] for child in saved["children"]] == [child["name"] for child in original["children"]]


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Official development view required")
def test_dialogue_and_pat_boxes_share_prefab_root_coordinates(game):
    # Independently accumulate the official transforms from the root. Unlike local positions, both exported values
    # must include every ancestor's translation, rotation and scale. The collider rect is unrotated about its centre.
    info = game.lobby_info("ch0100_home")
    path = game._bundle_path(game._lobby_prefabs()["lobbych0100"])
    with open_bundle(path) as env:
        objects = {o.path_id: o.read_typetree() for o in env.objects if o.type.name in
                   ("GameObject", "Transform", "RectTransform", "BoxCollider", "MonoBehaviour")}
        kinds = {o.path_id: o.type.name for o in env.objects}
        scripts = {o.path_id: o.read().m_ClassName for o in env.objects if o.type.name == "MonoScript"}
    transforms = {t["m_GameObject"]["m_PathID"]: t for pid, t in objects.items()
                  if kinds[pid] in ("Transform", "RectTransform")}
    root = next(t for t in transforms.values() if not t["m_Father"]["m_PathID"]
                and objects[t["m_GameObject"]["m_PathID"]]["m_Name"].lower() == "lobbych0100")
    descendants = []
    import math
    def walk(t, parent=(1, 0, 0, 1, 0, 0)):
        q, scale, pos = t["m_LocalRotation"], t["m_LocalScale"], t["m_LocalPosition"]
        assert [q["x"], q["y"]] == [0, 0]
        angle = 2 * math.atan2(q["z"], q["w"])
        c, s = math.cos(angle), math.sin(angle)
        a, b, d, e, x, y = parent
        matrix = (a*c*scale["x"] + d*s*scale["x"], b*c*scale["x"] + e*s*scale["x"],
                  -a*s*scale["y"] + d*c*scale["y"], -b*s*scale["y"] + e*c*scale["y"],
                  a*pos["x"] + d*pos["y"] + x, b*pos["x"] + e*pos["y"] + y)
        descendants.append((t["m_GameObject"]["m_PathID"], matrix))
        for child in t.get("m_Children", []):
            branch = objects[child["m_PathID"]]
            if contains_target(branch):
                walk(branch, matrix)
    chat = next(t for pid, t in objects.items() if kinds[pid] == "MonoBehaviour"
                and scripts.get(t.get("m_Script", {}).get("m_PathID")) == "ChatDialog"
                and t.get("DialogBoxes"))
    talk_go = chat["DialogBoxes"][0]["GameObject"]["m_PathID"]
    pat = next(t for pid, t in objects.items() if kinds[pid] == "MonoBehaviour"
               and scripts.get(t.get("m_Script", {}).get("m_PathID")) == "SpineDragIK"
               and objects[t["m_GameObject"]["m_PathID"]]["m_Name"] == info["drags"][0]["name"])
    pat_go = pat["m_GameObject"]["m_PathID"]
    def contains_target(t):
        return t["m_GameObject"]["m_PathID"] in (talk_go, pat_go) or any(
            contains_target(objects[c["m_PathID"]]) for c in t.get("m_Children", []))
    walk(root)
    positions = dict(descendants)
    assert list(positions[talk_go][4:]) == info["dialog"]["Talk"] == [-320, 503]
    a, b, c, d, x, y = positions[pat_go]
    collider = next(t for pid, t in objects.items() if kinds[pid] == "BoxCollider"
                    and t["m_GameObject"]["m_PathID"] == pat_go)
    centre, size = collider["m_Center"], collider["m_Size"]
    x, y = x + a*centre["x"] + c*centre["y"], y + b*centre["x"] + d*centre["y"]
    hw, hh = size["x"]*math.hypot(a, b)/2, size["y"]*math.hypot(c, d)/2
    corners = [round(x-hw, 1), round(y-hh, 1), round(x+hw, 1), round(y+hh, 1)]
    assert corners == info["drags"][0]["rect"] == [-1019, 186, -519, 536]
    assert info["drags"][0]["rot"] == round(math.degrees(math.atan2(b, a)), 2)
