"""The design tokens (web/js/ui-tokens.js) against the game's own prefabs, from the official development view.

Skipped without BAMT_TEST_GAME_DIR (set by the development test plugin) or Node.js. Only numbers and colour codes of prefab
nodes are read: no image data. The call sites are pinned against the tokens by tests/js/ui_tokens.test.mjs.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bmpt.gameassets.layouts import export_layout, subtree
from bmpt.gameassets.service import PREFAB_HINTS
from bmpt.gameassets.unity import open_bundle

NODE = shutil.which("node")
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
TOKENS = Path(__file__).resolve().parents[1] / "src/bmpt/web/js/ui-tokens.js"

pytestmark = pytest.mark.skipif(NODE is None or not GAME_DIR.is_dir(), reason="Node.js and an official development view are required")


@pytest.fixture(scope="module")
def tokens():
    script = f"import * as T from {json.dumps(TOKENS.as_uri())}; console.log(JSON.stringify(T));"
    run = subprocess.run([NODE, "--input-type=module", "-e", script], check=True, capture_output=True, text=True, timeout=30)
    return json.loads(run.stdout)


def bundle_of(root):
    return next(GAME_DIR.rglob(PREFAB_HINTS[root]))


@pytest.fixture(scope="module")
def layouts():
    out = {}
    for root in ("UIPopup_Sort", "UIPopup_Option_PC", "UICharacterDetail"):
        with open_bundle(bundle_of(root)) as env:
            out[root] = export_layout(env, root, {})
    return out


def sprite(node, name=None):
    return next(w for w in node["widgets"] if "sprite" in w and (name is None or w["sprite"] == name))


def label(node):
    return next(w for w in node["widgets"] if "text" in w)


def test_popup_frame_and_footer_geometry(tokens, layouts):
    sort = layouts["UIPopup_Sort"]
    base, bg = subtree(sort, "Center/Popup/Base"), subtree(sort, "Center/Popup/Base/BG")
    popup, footer = tokens["POPUP"], tokens["FOOTER"]
    assert [base["pos"][1], bg["pos"][1]] == [popup["baseY"], popup["bgY"]]
    assert base["pos"][1] + bg["pos"][1] == popup["frameY"]
    frame = sprite(bg, "Common_Popup_Bg")
    assert [frame["w_"], frame["h_"]] == [popup["frameW"], popup["frameH"]]
    # the footer's centre above the frame's bottom edge: Btn's y less the bottom edge (frame centre less half its height)
    btn = subtree(sort, "Center/Popup/Btn")
    assert btn["pos"][1] == footer["btnY"]
    assert btn["pos"][1] - (popup["frameY"] - frame["h_"] / 2) == footer["centre"] == 190.5
    ok, cancel = subtree(sort, "Center/Popup/Btn/Btn_Ok"), subtree(sort, "Center/Popup/Btn/Btn_Cancel")
    assert [ok["pos"][0], cancel["pos"][0]] == [footer["pairX"], -footer["pairX"]]
    body = sprite(subtree(sort, "Center/Popup/Btn/Btn_Ok/BG (1)"), "Common_Btn_BG")
    assert [body["w_"], body["h_"]] == [footer["body"]["w"], footer["body"]["h"]] == [496, 177]
    cancel_body = sprite(subtree(sort, "Center/Popup/Btn/Btn_Cancel/BG (1)"), "Common_Btn_BG")
    assert [cancel_body["w_"], cancel_body["h_"]] == [496, 177]
    # the bodies of the pair stand 88 apart
    assert ok["pos"][0] - cancel["pos"][0] - body["w_"] == footer["pairGap"] == 88
    caption = label(subtree(sort, "Center/Popup/Btn/Btn_Ok/Label_Locale"))
    assert [caption["fontSize"], caption["w_"]] == [footer["caption"]["fs"], footer["caption"]["w"]] == [72, 400]


def test_small_button_is_the_option_popups_button_bg(tokens, layouts):
    option = layouts["UIPopup_Option_PC"]
    button = subtree(option, "Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg")
    body = sprite(button, "Common_Btn_BG")
    small = tokens["SMALL_BUTTON"]
    assert [body["w_"], body["h_"]] == [small["w"], small["h"]] == [402, 136]
    assert label(subtree(option, "Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg/Label_Locale"))["fontSize"] == small["fs"] == 48
    # buttons side by side stand 20 apart
    assert small["pitch"] - small["w"] == small["gap"] == 20


def test_chip_is_the_detail_screens_type_chip(tokens, layouts):
    detail = layouts["UICharacterDetail"]
    path = "Inventory_ListControl/LeftInfo/CharInfo/Char_Target/Type/MainType/BG"
    chip = tokens["CHIP"]
    body = sprite(subtree(detail, path), "Common_Bg_Raius10px")
    assert [body["w_"], body["h_"]] == [chip["w"], chip["h"]] == [272, 72]
    assert body["incline"] == 10
    assert label(subtree(detail, f"{path}/Label"))["fontSize"] == chip["fs"] == 40
    # the pitch (290) and the second row (84) are the nameplate's own layout, not a node of this prefab
    assert chip["pitch"] - chip["w"] == chip["gap"]


def test_stats_box_is_the_detail_screens_status_box(tokens, layouts):
    detail = layouts["UICharacterDetail"]
    path = "Inventory_ListControl/RightInfo/Target/Active/1_NormalInfo/Status"
    box = tokens["STATS_BOX"]
    bg, bg_line = subtree(detail, f"{path}/Bg"), subtree(detail, f"{path}/Bg/Deco")
    second, second_line = subtree(detail, f"{path}/Bg/Bg_1"), subtree(detail, f"{path}/Bg/Bg_1/Deco")
    fill, line = sprite(bg, "Common_Bg_Raius10px"), sprite(bg_line, "Common_Line_Raius10px")
    fill_1, line_1 = sprite(second, "Common_Bg_Raius10px"), sprite(second_line, "Common_Line_Raius10px")
    divider = sprite(subtree(detail, f"{path}/Bar"), "Common_Line_Dot")
    # the fill, the line and the divider colours
    assert [fill["color"], fill_1["color"]] == [f"{box['fill']}/1.00"] * 2 == ["#ecf5f9/1.00"] * 2
    assert [line["color"], line_1["color"]] == [f"{box['line']}/1.00"] * 2 == ["#c0c9d4/1.00"] * 2
    assert divider["color"] == f"{box['divider']}/1.00" == "#c0c9d4/1.00"
    assert divider["h_"] == 4
    # the line is the fill plus 5 across and 5 down, centred to within a unit (Deco at -2.3, +3.0)
    assert [line_1["w_"] - fill_1["w_"], line_1["h_"] - fill_1["h_"]] == [box["lineOutset"]] * 2 == [5, 5]
    assert line["h_"] - fill["h_"] == box["lineOutset"]
    assert abs(second_line["pos"][0] + box["lineOutset"] / 2) <= 0.5 and abs(second_line["pos"][1] - box["lineOutset"] / 2) <= 0.5
    assert second_line["pos"] == bg_line["pos"]
    # the first fill's line is 10 wider: its right 5 more lie under the second fill, which is drawn above it
    assert line["w_"] - fill["w_"] == 2 * box["lineOutset"]
    assert second["pos"][0] + fill_1["w_"] >= bg_line["pos"][0] + line["w_"] and fill_1["depth"] > line["depth"]


@pytest.mark.parametrize("root", ["UIPopup_Sort", "UIPopup_Option_PC", "UIPopup_FilterSort"])
def test_backdrop_rests_at_the_tween_alpha(tokens, root):
    # BlackBgClickArea carries a TweenAlpha from 0 to 0.80: that is where the backdrop rests, not the sprite's own 0.94
    tweens = []
    with open_bundle(bundle_of(root)) as env:
        scripts = {o.path_id: o.read().m_ClassName for o in env.objects if o.type.name == "MonoScript"}
        names = {o.path_id: o.read().m_Name for o in env.objects if o.type.name == "GameObject"}
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                data = obj.read_typetree()
            except (ValueError, TypeError):
                continue
            if names.get(data.get("m_GameObject", {}).get("m_PathID")) == "BlackBgClickArea" \
                    and scripts.get(data.get("m_Script", {}).get("m_PathID")) == "TweenAlpha":
                tweens.append((data["from"], data["to"]))
    assert len(tweens) == 1
    colour, alpha = tokens["BACKDROP"].split("/")
    assert colour == "#000000"
    assert tweens[0][0] == 0.0
    assert float(alpha) == pytest.approx(tweens[0][1], abs=1e-6) == pytest.approx(0.80, abs=1e-6)


def test_status_colours_exist_in_the_games_prefabs(tokens):
    wanted = {name: value for name, value in tokens["COLOUR"].items()}
    found = set()

    def walk(node):
        for widget in node.get("widgets", []):
            for key in ("color", "effectColor"):
                if isinstance(widget.get(key), str) and widget[key].startswith("#"):
                    found.add(widget[key][:7].lower())
        for child in node.get("children", []):
            walk(child)

    for root in PREFAB_HINTS:
        try:
            path = bundle_of(root)
        except StopIteration:
            continue
        with open_bundle(path) as env:
            walk(export_layout(env, root, {}))
    missing = {name: value for name, value in wanted.items() if value.lower() not in found}
    assert not missing, f"not a colour of any hinted prefab: {missing}"


def test_applied_selection_gold_is_the_mulligan_selectline_sprite(tokens):
    with open_bundle(bundle_of("UIPopup_Mulligan")) as env:
        tree = export_layout(env, "UIPopup_Mulligan", {})
    line = subtree(tree, "Center/Popup/01/UIMulliganSlot/SelectLine")
    sprites = [w for w in line["widgets"] if "sprite" in w]
    assert len(sprites) == 1
    assert sprites[0]["color"] == tokens["APPLIED_SELECTION_GOLD"] + "/1.00"


def test_lobby_entry_is_uiani_lobby(tokens, tmp_path_factory):
    # UILobby's root plays its legacy Animation clip as the lobby opens: the top bar and bottom menu keys, as offsets
    # from where the prefab puts the nodes (chrome-motion.js plays them on the App's bars).
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.model import BundleGraph
    from ba_modding_toolkit.searching import get_search_dirs
    game = GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path_factory.mktemp("lobby-entry"), allowed=False))
    with game._cab_discovery().batch(), BundleGraph({}, resolver=game._resolve_cabs) as graph:
        env = graph.add(game._prefab_bundle("UILobby"))
        root = next(o for o in env.objects if o.type.name == "GameObject" and graph.tree(o)["m_Name"] == "UILobby")
        animation = graph.component(root, "Animation")
        clip = graph.tree(graph.deref(animation, graph.tree(animation)["m_Animation"]))
        layout = export_layout(env, "UILobby", {})
    assert clip["m_Name"] == "UIAni_Lobby" and clip["m_Legacy"]
    curves = {c["path"]: c["curve"]["m_Curve"] for c in clip["m_PositionCurves"]}
    for edge, path in (("top", "Front/NUITop/Top/Right"), ("bottom", "Front/Bottom/Menu")):
        keys, rest = curves[path], subtree(layout, path)["pos"][1]
        assert keys[-1]["value"]["y"] == rest
        assert all(k["weightedMode"] == 0 for k in keys)
        got = [[k["time"], k["value"]["y"] - rest, k["outSlope"]["y"]] for k in keys]
        assert all(k["inSlope"]["y"] == k["outSlope"]["y"] for k in keys)
        flat = lambda rows: [v for row in rows for v in row]
        assert flat(got) == pytest.approx(flat(tokens["LOBBY_ENTRY"][edge]), abs=1e-3)
