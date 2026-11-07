// N-002: the details a refused Import Mod row opens from its badge, in the entry shape of docs/web-ui.md ("bundles", "missing").
import assert from "node:assert/strict";
import { test } from "node:test";
import { hasDetails, detailLines } from "../../src/bmpt/web/js/import-details.js";

const T = (key, params = {}) => `${key}${Object.keys(params).length ? JSON.stringify(params) : ""}`;
const bundle = (file, platforms, unity) => ({ file, platforms, unity, cabs: ["cab-" + "1".repeat(32)], references: 0 });

test("only blocked and incomplete rows have details", () => {
  for (const state of ["blocked", "incomplete"]) assert.ok(hasDetails({ state }));
  for (const state of ["ready", "update", "convert", "library", "installed", "done"]) assert.ok(!hasDetails({ state }));
});

test("a refused row: its reason, then each file with its build target and Unity version", () => {
  const entry = { state: "blocked", bundles: [bundle("a.bundle", ["Android"], ["2021.3"]), bundle("b.bundle", ["Android", "iOS"], [])] };
  assert.deepEqual(detailLines(entry, { reason: "Incompatible Platform", T, separator: "、" }), [
    "Incompatible Platform", 'a.bundle\nimport.detail.platform{"platforms":"Android"}\nimport.detail.unity{"versions":"2021.3"}',
    'b.bundle\nimport.detail.platform{"platforms":"Android、iOS"}']);
});

test("missing files: the unresolved dependencies, then the files of the same slot in its folders", () => {
  const entry = { state: "incomplete", missing: { unresolved: 2, cabs: ["cab-a", "cab-b"], found: [{ name: "c.bundle" }] } };
  assert.deepEqual(detailLines(entry, { reason: "Missing Files (1)", T }), [
    "Missing Files (1)", 'import.detail.unresolved{"n":2}\ncab-a\ncab-b', 'import.missing.promptHead\nimport.missing.promptFile{"name":"c.bundle"}']);
  assert.deepEqual(detailLines({ state: "blocked" }, { T }), [], "nothing read, no reason: no lines");
});

test("an error text loses the file name the details list, where it opens or ends the text", async () => {
  const { withoutFile } = await import("../../src/bmpt/web/js/import-details.js");
  const tables = { a: "{file}，构建平台 {platform}，需要 {expected}", b: "Not a valid bundle: {file}", c: "Cannot extract {name} from {file}: {error}", d: "Incompatible" };
  const T = (key, params = {}) => tables[key].replace(/\{(\w+)\}/g, (m, name) => name in params ? params[name] : m);
  const params = { file: "f.bundle", platform: "Android", expected: "StandaloneWindows64", name: "n", error: "e" };
  assert.equal(withoutFile(T, "a", params), "构建平台 Android，需要 StandaloneWindows64");
  assert.equal(withoutFile(T, "b", params), "Not a valid bundle");
  assert.equal(withoutFile(T, "c", params), "Cannot extract n from f.bundle: e", "a name inside a sentence stays");
  assert.equal(withoutFile(T, "d", params), "Incompatible");
});
