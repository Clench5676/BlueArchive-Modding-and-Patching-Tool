"""The shared popups (web/js/popup.js) and the update popup (web/js/update.js) are front-end code: their tests run with
Node's test runner on a stand-in DOM, skipped where Node is not installed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TESTS = Path(__file__).parent / "js" / "popup.test.mjs"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_popup_in_node():
    result = subprocess.run([NODE, "--test", str(TESTS)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_popup_keeps_the_official_sort_source_branches(request, tmp_path):
    if not request.config.pluginmanager.hasplugin("bmpt_dev.testing"):
        pytest.skip("Requires the official development view (-p bmpt_dev.testing)")
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.layouts import subtree

    official = request.getfixturevalue("official_game")
    official.require_layout("UIPopup_Sort")
    cache = AssetCache(root=tmp_path / "memory-only", allowed=False)
    tree = GameAssets(official.resource_dirs, cache).layout("UIPopup_Sort")
    background = subtree(tree, "Center/Popup/Base/BG")
    sprite = next(w for w in background["widgets"] if "sprite" in w)
    assert background["pos"] == [0, 8]
    assert [sprite[k] for k in ("sprite", "w_", "h_", "pivot")] == ["Common_Popup_Bg", 2458, 1355, 4]
    for name, x in [("Btn_Ok", 292), ("Btn_Cancel", -292)]:
        button = subtree(tree, f"Center/Popup/Btn/{name}")
        body = subtree(button, "BG (1)")
        sprite = next(w for w in body["widgets"] if "sprite" in w)
        assert button["pos"] == [x, 0] and body["pos"] == [0, -4]
        assert [sprite[k] for k in ("sprite", "w_", "h_", "pivot")] == ["Common_Btn_BG", 496, 177, 4]
    assert not cache.root.exists()


def test_review_controls_use_official_popup_and_settings_nodes(request, tmp_path):
    if not request.config.pluginmanager.hasplugin("bmpt_dev.testing"):
        pytest.skip("Requires the official development view (-p bmpt_dev.testing)")
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.layouts import subtree

    official = request.getfixturevalue("official_game")
    cache = AssetCache(root=tmp_path / 'memory-only', allowed=False)
    game = GameAssets(official.resource_dirs, cache)
    official.require_layout('UIPopup_Sort')
    tree = game.layout('UIPopup_Sort')
    assert subtree(tree, 'Center/Popup/Base')['pos'] == [0, -20]
    assert subtree(tree, 'Center/Popup/Btn')['pos'] == [0, -499]
    close = subtree(tree, 'Center/Popup/Base/BG/Btn_Close')
    assert close['pos'] == [1144, 616]
    icon = next(w for w in close['widgets'] if 'sprite' in w)
    assert [icon[k] for k in ('sprite', 'w_', 'h_', 'pivot')] == ['Common_Icon_Close', 60, 60, 4]
    title = subtree(tree, 'Center/Popup/Base/BG/Label_Title_Locale')
    assert title['pos'] == [0, 611]
    assert [title['widgets'][0][k] for k in ('w_', 'h_', 'fontSize', 'pivot')] == [146, 72, 72, 4]
    for name in ('Btn_Cancel', 'Btn_Ok'):
        caption = subtree(tree, f'Center/Popup/Btn/{name}/Label_Locale')
        assert caption['pos'] == [0, 2]
        assert [caption['widgets'][0][k] for k in ('w_', 'h_', 'fontSize', 'pivot')] == [400, 180, 72, 4]
    official.require_layout('UIPopup_Option_PC')
    option = game.layout('UIPopup_Option_PC')
    row_path = 'Center/Popup/List/Sound_Setting/Sound_Scroll View/Grid_2/Master/Type_3'
    row_body = subtree(option, f'{row_path}/BG')
    row_sprite = next(w for w in row_body['widgets'] if 'sprite' in w)
    assert [row_sprite[k] for k in ('sprite', 'w_', 'pivot')] == ['Common_Bg_Raius5px', 1552, 4]
    row_title = subtree(option, f'{row_path}/Label_Locale_OptionTitle')
    assert [row_title['widgets'][0][k] for k in ('h_', 'fontSize', 'pivot')] == [76, 50, 3]
    tag = subtree(row_title, 'TagDeco')
    assert tag['pos'] == [-13, 5.2]
    assert [tag['widgets'][0][k] for k in ('sprite', 'w_', 'h_', 'pivot')] == ['Common_Square_Bg', 6, 45, 4]
    button = subtree(option, 'Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg')
    body = next(w for w in button['widgets'] if 'sprite' in w)
    assert [body[k] for k in ('sprite', 'w_', 'h_', 'pivot')] == ['Common_Btn_BG', 402, 136, 4]
    caption = subtree(button, 'Label_Locale')
    assert caption['pos'] == [0, 4]
    assert [caption['widgets'][0][k] for k in ('w_', 'h_', 'fontSize', 'pivot')] == [337, 72, 48, 4]
    assert not cache.root.exists()


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_popup_resizes_official_sort_branches_to_each_class(request, tmp_path):
    if not request.config.pluginmanager.hasplugin("bmpt_dev.testing"):
        pytest.skip("Requires the official development view (-p bmpt_dev.testing)")
    from bmpt.gameassets import AssetCache, GameAssets

    official = request.getfixturevalue("official_game")
    official.require_layout("UIPopup_Sort")
    cache = AssetCache(root=tmp_path / "memory-only", allowed=False)
    source = GameAssets(official.resource_dirs, cache).layout("UIPopup_Sort")
    web = Path(__file__).resolve().parents[1] / "src/bmpt/web/js"
    script = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = JSON.parse(readFileSync(0, 'utf8'));
globalThis.addEventListener = () => {};
globalThis.document = { documentElement: { lang: 'en-US', appendChild() {} }, createElement: () => ({
  remove() {}, getContext: () => ({ font: '', measureText(s) {
    return { width: s.length * .6 * parseFloat(/([\d.]+)px/.exec(this.font)?.[1] || 10) };
  } })
}) };
globalThis.fetch = async url => {
  assert.equal(url, '/asset/ui/layout/UIPopup_Sort.json');
  return { ok: true, json: async () => source };
};
const Popup = await import(POPUP_URL);
const NGUI = await import(NGUI_URL);
const { POPUP_FRAME: P, CONTROL: C, TYPE: T } = await import(TOKENS_URL);
const Grid = await import(GRID_URL);
await Popup.frameReady();
for (const key of ['S', 'M', 'L', 'XL']) for (const requested of [0, 1, 374, 786, 900, 1400, 2000]) for (const n of [1, 2, 3]) {
  const tree = Popup.popupFrame('Title', key, requested, { buttons: Array.from({ length: n }, () => ({ label: 'OK' })) });
  const bg = NGUI.find(tree, 'Popup/Base/BG'), body = bg.widgets.find(w => 'sprite' in w);
  assert.equal(body.sprite, 'Common_Popup_Bg');
  assert.equal(body.w_, P[key].w);
  assert.equal(body.h_, requested ? Math.min(P.maxH, Math.max(P.minH, requested)) : P[key].h);
  const title = NGUI.find(bg, 'Label_Title_Locale'), close = NGUI.find(bg, 'Btn_Close');
  // the title one size under the game's (BL-176), centred where the game's 72 stood under the frame's top
  assert.ok(Math.abs(body.h_ / 2 - title.pos[1] - P.titleTop - Grid.textItem(NGUI, 'Title', T.display, { lines: 1 }).h / 2) < 1e-9);
  assert.equal(body.w_ / 2 - close.pos[0] - C.icon.h / 2, P.close.right);
  assert.equal(body.h_ / 2 - close.pos[1] - C.icon.h / 2, P.close.top);
  const btn = NGUI.find(tree, 'Popup/Btn');
  assert.equal(btn.pos[1] - C.buttonXS.h / 2 - (P.frameY - body.h_ / 2), Popup.footerBottom());
  assert.equal(btn.children.length, n);
  assert.equal(new Set(btn.children.map(child => NGUI.find(child, 'Label_Locale').widgets[0].fontSize)).size, 1);
  for (const child of btn.children) assert.equal(NGUI.find(child, 'BG (1)').widgets[0].h_, C.buttonXS.h);
}
"""
    for marker, filename in (("POPUP_URL", "popup.js"), ("NGUI_URL", "ngui.js"), ("TOKENS_URL", "ui-tokens.js"), ("GRID_URL", "ui-grid.js")):
        script = script.replace(marker, json.dumps((web / filename).as_uri()))
    result = subprocess.run([NODE, "--input-type=module", "-e", script], input=json.dumps(source),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not cache.root.exists()
