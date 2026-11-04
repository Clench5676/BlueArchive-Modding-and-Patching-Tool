// The game's pointer (web/js/cursor.js) is the only pointer of the page: --cursor everywhere, --cursor-press on what a
// press reacts to (the same image), drag handles keep grab and grabbing, text fields the text cursor. Any other cursor
// keyword in the page's own styles is a system cursor over the game's pointer (a flicker on macOS and Windows).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = new URL("../../src/bmpt/web/", import.meta.url).pathname;
const files = (dir, ext) => readdirSync(join(WEB, dir), { recursive: true }).filter((f) => f.endsWith(ext)).map((f) => join(dir, f)).sort();
const read = (file) => readFileSync(join(WEB, file), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, ""); // without comments
const ALLOWED = new Set(["grab", "grabbing", "text", "var(--cursor)", "var(--cursor-press)"]);

// every `cursor: value` of a stylesheet and every `cursor: "value"` or `.cursor = "value"` of a script (custom
// properties such as --cursor are not matched), as [file, value]
function declarations() {
  const found = [];
  for (const file of files("css", ".css")) {
    for (const [, value] of read(file).matchAll(/(?<![-\w])cursor\s*:\s*([^;}\n]+?)\s*(?:!important\s*)?(?=[;}\n])/g)) found.push([file, value]);
  }
  for (const file of files("js", ".js")) {
    if (file === "js/cursor.js") continue;
    for (const [, value] of read(file).matchAll(/(?<![-\w])cursor\s*(?::|=(?!=))\s*["'`]([^"'`]+)["'`]/g)) found.push([file, value]);
  }
  return found;
}

test("the page's own styles use the game's pointer, grab, grabbing or the text cursor", () => {
  const found = declarations();
  assert.ok(found.length > 15, `${found.length} cursor declarations found`);
  // the recovery panel (fallback.css) has no game assets by definition: it keeps the system hand on its buttons
  const stray = found.filter(([file, value]) => !ALLOWED.has(value) && !(file === "css/fallback.css" && value === "pointer"));
  assert.deepEqual(stray, []);
});

test("a hit area or an unseen control never sets a system cursor of its own", () => {
  const app = readFileSync(join(WEB, "css/app.css"), "utf8");
  // the unseen range input over the game's slider (settings.js): the game's pointer, not a resize arrow
  assert.match(app, /\.hit\.slider-range\s*\{[^}]*\}/);
  assert.doesNotMatch(app.match(/\.hit\.slider-range\s*\{[^}]*\}/)[0], /cursor/);
  assert.match(app, /^\.hit\s*\{[^}]*cursor: var\(--cursor-press\)/m);
  // a card that does not open a preview shows the plain game pointer, not the system arrow
  const hall = readFileSync(join(WEB, "js/student-hall-ui.js"), "utf8");
  assert.match(hall, /if \(!interactive\) \{[^}]*hit\.style\.cursor = "var\(--cursor\)"/);
});

test("only the root sets the pointer variables, cursor.js once the game's pointer is read", () => {
  const sets = [];
  for (const file of [...files("css", ".css"), ...files("js", ".js"), "index.html"]) {
    const text = read(file);
    for (const [, name, value] of text.matchAll(/(--cursor(?:-press)?)\s*:\s*([^;}\n]+)/g)) sets.push(`${file} ${name}: ${value.trim()}`);
    if (file === "js/cursor.js") for (const [, name] of text.matchAll(/setProperty\("(--cursor(?:-press)?)"/g)) sets.push(`${file} ${name}`);
  }
  assert.deepEqual(sets, [
    "css/app.css --cursor: default",
    "css/app.css --cursor-press: pointer",
    "js/cursor.js --cursor",
    "js/cursor.js --cursor-press",
  ]);
});

test("camera mode keeps the game's pointer: .photo-active sets no cursor and no pointer variable", () => {
  const css = read("css/photography.css");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([, selector]) => /\.photo-active/.test(selector));
  assert.ok(rules.length > 3, `${rules.length} .photo-active rules found`);
  for (const [, selector, body] of rules) assert.doesNotMatch(body, /cursor/, `${selector.trim()} changes the pointer`);
  // the touch effect rule next to it stays
  assert.match(css, /\.photo-active \.touch-fx\s*\{[^}]*display:\s*none/);
  // only the pan surface changes the pointer in camera mode (grab, the drag handle)
  assert.match(css, /\.photo-pan\s*\{[^}]*cursor:\s*grab/);
});
