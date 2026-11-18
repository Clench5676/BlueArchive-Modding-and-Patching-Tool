import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { POPUP_MODULES } from "./helpers/source-text.mjs";
// The thumbnail capture gallery is the Switch pickers' list popup (popup.js listPopup): the chosen thumbnail and its actions in
// the details at the left, the thumbnails' cards in the list at the right. popup.js draws prefabs at import, so the
// gallery's code is taken from the source and run against a recording listPopup.
const source = readFileSync(new URL("../../src/bmpt/web/js/thumbnail-capture/controller.js", import.meta.url), "utf8");
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const code =
  slice("function galleryReveal(", "// The captions of the two text buttons") +
  slice("async function openGallery(", "async function deleteThumbnail()") +
  slice("async function restoreGame()", "async function applyGame()");
const S = 1920 / 2960,
  PITCH = 500,
  VIEW = 1000;

function harness(count, { selected = "p0", repository = {} } = {}) {
  const thumbnails = Array.from({ length: count }, (_, index) => ({
    id: `p${index}`,
    name: `Thumbnail ${index}`,
    url: `p${index}.png`,
    builtin: index === 0,
    managed: index > 0,
    provenance: index === 1 ? "official" : "thumbnail",
    applied: index === 2,
  }));
  const draws = [],
    calls = [],
    host = { hidden: true, innerHTML: "x", querySelector: () => ({ scrollTop: host.offset || 0 }) };
  const Popup = {
    LIST_BAND: { slim: 60 },
    listLayout: (check, kind) => ({ check, kind }),
    listCardBox: (_layout, { band } = {}) => ({ w: 2000, h: band ? VIEW : VIEW + 50 }),
    closed: (target) => {
      target.closedCount = (target.closedCount || 0) + 1;
    },
    listPopup: async (options) => {
      draws.push(options);
      host.hidden = false;
    },
  };
  const env = {
    S,
    P: (key) => key,
    safe: (fn) => fn,
    DEFAULT: "generated",
    OFFICIAL: "official",
    saving: false,
    paused: false,
    Popup,
    switchGrid: (kind, width) => ({ kind, width, columns: 3, pitch: [400, PITCH] }),
    bridge: { pause: (on) => calls.push(["pause", on]), thumbnailChanged: () => calls.push(["thumbnail"]) },
    repositoryRead: async () => {},
    galleryThumbnails: () => thumbnails,
    galleryThumbnail: (id) => thumbnails.find((item) => item.id === id),
    selectedKey: (key) => key,
    normalized: (value) => value,
    Loading: { task: (fn) => fn(() => {}), altName: (e) => e?.alt || "" },
    modJob: () => () => ({ text: "" }),
    sessionAlt: "v",
    waitJob: async () => {},
    call: async (name, params) => {
      calls.push([name, params]);
      return {};
    },
    generatedPNG: async () => "png",
    untestedRetry: async () => false,
    applyGame: async () => {},
    deleteThumbnail: async () => {},
    document: { getElementById: (id) => (id === "popup" ? host : null) },
  };
  const api = new Function(
    "env",
    `const {${Object.keys(env)}}=env;let selected=${JSON.stringify(selected)},modal=null,generation=1,
    repository=${JSON.stringify({ thumbnails: [], app: null, game: {}, applicable: true, ...repository })};
    const GALLERY_HOST='popup',GALLERY_KIND='thumbnail',galleryHost=()=>document.getElementById(GALLERY_HOST);
    ${code};return {galleryReveal,openGallery,restoreGame,galleryDraw,modal:()=>modal,select:id=>{selected=id;},selected:()=>selected};`,
  )(env);
  return { ...api, draws, calls, host, last: () => draws.at(-1) };
}

test("the gallery opens as the Switch pickers' list popup: thumbnails at the right, the chosen one in the details at the left", async () => {
  const h = harness(5, { selected: "p3" });
  await h.openGallery(true);
  const o = h.last();
  assert.equal(o.hostId, "popup");
  assert.equal(o.layoutKind, "thumbnail");
  assert.equal(o.title, "gallery");
  assert.equal(o.empty, "empty");
  assert.deepEqual(
    o.items.map((it) => it.key),
    ["p0", "p1", "p2", "p3", "p4"],
  );
  assert.equal(o.selected, "p3");
  assert.deepEqual(
    o.items.map((it) => it.applied),
    [false, false, true, false, false],
  );
  assert.deepEqual(o.items[2].chips, ["applied"]);
  assert.deepEqual(o.items[0].chips, []);
  assert.equal(o.items[3].big, "p3.png");
  assert.equal(o.items[3].thumb, "p3.png");
  assert.deepEqual(
    o.buttons.map((b) => b.action),
    ["restore", "cancel", "ok"],
  );
  const save = o.buttons[2];
  assert.ok(save.primary);
  assert.equal(save.semantic, "save");
  assert.equal(save.label, "saveSelection");
  const d = o.details(o.items[3]);
  assert.equal(d.title, "thumbnailLabel");
  assert.deepEqual(d.fields, []);
  assert.deepEqual(
    d.actions.map((a) => a[0]),
    ["apply", "export", "delete"],
  );
  assert.equal(d.actions[0][3], "apply");
  assert.equal(d.actions[0][2], false);
  assert.deepEqual(
    o.details(o.items[0]).actions.map((a) => a[0]),
    ["apply", "export"],
    "a thumbnail the app made cannot be deleted",
  );
  assert.deepEqual(
    o.details(o.items[1]).actions.map((a) => a[0]),
    ["apply", "export"],
    "the official one cannot be deleted",
  );
  assert.deepEqual(h.calls[0], ["pause", true]);
});

test("choosing a card shows it in the details and keeps the list where it was", async () => {
  const h = harness(20);
  await h.openGallery(true);
  await h.last().onSelect("p7", 321);
  assert.equal(h.selected(), "p7");
  assert.equal(h.last().selected, "p7");
  assert.equal(h.last().keepScroll, 321);
});

test("opening scrolls only when the chosen row is out of the first view", async () => {
  for (const [index, offset] of [
    [0, 0],
    [5, 0],
    [6, 2 * PITCH * S],
    [19, 6 * PITCH * S],
  ]) {
    const h = harness(20, { selected: `p${index}` });
    await h.openGallery(true);
    assert.equal(h.last().keepScroll, offset, `p${index}`);
  }
  const h = harness(0);
  assert.equal(h.galleryReveal(-1), 0);
});

test("Save selects the thumbnail and closes; Cancel, close and a late redraw leave the popup closed", async () => {
  const h = harness(4, { selected: "p2" });
  await h.openGallery(true);
  await h.last().buttons[2].fn();
  assert.deepEqual(
    h.calls.find((c) => c[0] === "select"),
    ["select", { id: "p2" }],
  );
  assert.ok(h.calls.some((c) => c[0] === "thumbnail"));
  assert.equal(h.modal(), null);
  assert.ok(h.host.hidden);
  assert.equal(h.host.innerHTML, "");
  assert.equal(h.host.closedCount, 1);
  assert.deepEqual(h.calls.at(-1), ["pause", false]);
  const draws = h.draws.length;
  await h.galleryDraw();
  assert.equal(h.draws.length, draws, "nothing draws once it closed");
  await h.openGallery(true);
  h.last().buttons[1].fn();
  assert.equal(h.modal(), null);
  await h.openGallery(true);
  h.last().onClose("close");
  assert.equal(h.modal(), null);
  assert.equal(h.host.closedCount, 3);
});

test("Restore Default greys out when the game already shows the default, and goes back to the top after", async () => {
  const off = harness(3, { repository: { app: "p0", game: { id: "p0" } } });
  await off.openGallery(true);
  assert.ok(
    off.last().buttons[0].greyed && off.last().buttons[0].blocked,
    "the default is the app's and the game's already",
  );
  const on = harness(3, { repository: { app: "p1", game: { id: "p0" } } });
  await on.openGallery(true);
  assert.ok(!on.last().buttons[0].greyed);
  on.host.offset = 900;
  await on.restoreGame();
  assert.equal(on.last().keepScroll, 0);
});

test("Apply is greyed while the game already shows the chosen thumbnail", async () => {
  const h = harness(3, { selected: "p1", repository: { game: { known: true, id: "p1" } } });
  await h.openGallery(true);
  assert.equal(h.last().details(h.last().items[1]).actions[0][2], true);
});

test("the gallery's cards and picture are Chibi's squares, each thumbnail filling its inclined window", async () => {
  const { switchGrid, MIN_COLUMNS } = await import("../../src/bmpt/web/js/mods/switch-layout.js");
  for (const width of [1200, 1500, 1818]) assert.deepEqual(switchGrid("thumbnail", width), switchGrid("chibi", width));
  assert.equal(MIN_COLUMNS.thumbnail, MIN_COLUMNS.chibi);
  const popup = POPUP_MODULES.map((file) =>
    readFileSync(new URL(`../../src/bmpt/web/js/ui/${file}`, import.meta.url), "utf8"),
  ).join("\n");
  // only a chibi's still is shown whole (contain); a thumbnail, like the other kinds, covers the card's and the
  // picture's window
  assert.equal(popup.match(/imgSize: kind === "chibi" \? "contain" : "cover"/g)?.length, 2);
  assert.match(popup, /const chibi = !portraitTexture && \(kind === "chibi" \|\| kind === "thumbnail"\);/);
  assert.match(popup, /: chibi \? 1 : 16 \/ 9;/);
});
