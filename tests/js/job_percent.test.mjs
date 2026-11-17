// The percentage after a job's progress line (job-progress.js): its wording in every locale, how it moves within an
// item and from one item to the next, and that the widest 3D model line still fits the loading label.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { installFitPage, LOCALES } from "./helpers/fit_canvas.mjs";

installFitPage();
const NGUI = await import("../../src/bmpt/web/js/ngui.js");
const { setStrings, T } = await import("../../src/bmpt/web/js/core/i18n.js");
const { jobProgress, percentText, progressName, steadyFraction, withPercent } = await import(
  "../../src/bmpt/web/js/core/job-progress.js"
);
const { S } = await import("../../src/bmpt/web/js/core/util.js");

const flat = (data, prefix = "", out = {}) => {
  for (const [key, value] of Object.entries(data)) {
    if (value && typeof value === "object") flat(value, `${prefix}${key}.`, out);
    else out[prefix + key] = value;
  }
  return out;
};
const strings = (locale) =>
  JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8"));
const use = (locale) => {
  setStrings(locale, flat(strings(locale)));
  globalThis.document.documentElement.lang = locale;
};
const catalog = JSON.parse(readFileSync(new URL("../../src/bmpt/catalog/data/catalog.json", import.meta.url)));

for (const locale of LOCALES)
  test(`the percentage follows the line after one space in ${locale}`, () => {
    use(locale);
    assert.equal(strings(locale).webui.progress.percent, "{percent}%");
    const name = progressName(
      {
        key: "hina",
        names: { en: { full: "Hina", short: "Original" } },
        family: { key: "hina", base: { en: "Hina" } },
      },
      T("tab.model"),
    );
    const line = jobProgress({ action: "progress.actionApply", name, current: 1, total: 3 });
    assert.equal(
      jobProgress({ action: "progress.actionApply", name, current: 1, total: 3, fraction: 0.42 }),
      `${line} 42%`,
    );
    const stepped = jobProgress({
      action: "progress.actionApply",
      name,
      current: 1,
      total: 3,
      step: "import.step.crc",
    });
    assert.equal(
      jobProgress({
        action: "progress.actionApply",
        name,
        current: 1,
        total: 3,
        step: "import.step.crc",
        fraction: 0.425,
      }),
      `${stepped} 42%`,
    );
    assert.ok(!line.includes("·") && !line.includes("・"));
    // no fraction, nothing new: the line stays as it was
    for (const fraction of [null, undefined, 0, -1, NaN, "0.5"]) {
      assert.equal(jobProgress({ action: "progress.actionApply", name, current: 1, total: 3, fraction }), line);
    }
    // whole percents, never 100 while the item runs
    assert.equal(percentText(0.999), "99%");
    assert.equal(percentText(1), "99%");
    assert.equal(percentText(0.005), "0%");
    assert.equal(withPercent("x", null), "x");
  });

test("the shown fraction only grows within an item and starts again with the next", () => {
  const steady = steadyFraction();
  assert.equal(steady({ current: 1, total: 3 }), null);
  assert.equal(steady({ current: 1, total: 3, fraction: 0.4 }), 0.4);
  assert.equal(steady({ current: 1, total: 3, fraction: 0.2 }), 0.4); // an older reading
  assert.equal(steady({ current: 1, total: 3, step: "import.step.crc" }), 0.4); // a snapshot without one keeps it
  assert.equal(steady({ current: 1, total: 3, fraction: 0.6 }), 0.6);
  assert.equal(steady({ current: 2, total: 3 }), null); // the next item: from nothing again
  assert.equal(steady({ current: 2, total: 3, fraction: 0.1 }), 0.1);
  assert.equal(steady({ current: 2, total: 3, fraction: 7 }), 1); // clamped; percentText still shows 99
  assert.equal(steady({ current: 2, total: 5, fraction: 0.05 }), 0.05); // another count is another item
  assert.equal(steadyFraction()(), null);
});

// The loading label (loading.js, ngui-label.js loadingLabel): 1000 stage px wide, in the font of UICharacterDetail's
// Inventory_ListControl/LeftInfo/CharInfo/Char_Target/Text_Lv/Text_Lv_Exp (fontSize 32, scale 1.001), which ellipsizes
// what does not fit. The widest 3D model line of the catalog, with the percentage, keeps it whole.
const LABEL_WIDTH = 1000,
  LABEL_FONT = 32 * 1.001;
for (const locale of LOCALES)
  test(`the widest 3D model progress line with its percentage fits the loading label in ${locale}`, () => {
    use(locale);
    let widest = { width: 0, line: "" };
    for (const [key, entry] of Object.entries(catalog.variants)) {
      const student = catalog.students[entry.student_id];
      const variant = { key, names: entry.names, family: { key: student.base, base: student.names } };
      for (const action of [
        "progress.actionOptimize",
        "progress.actionApply",
        "progress.actionRestore",
        "progress.actionRepair",
      ]) {
        const line = jobProgress({
          action,
          name: progressName(variant, T("tab.model")),
          current: 10,
          total: 10,
          fraction: 0.99,
        });
        const width = NGUI.textWidth(line, LABEL_FONT) * S;
        if (width > widest.width) widest = { width, line };
      }
    }
    assert.ok(widest.line.endsWith(" 99%"), widest.line);
    assert.ok(widest.width <= LABEL_WIDTH, `${locale}: ${widest.line} (${widest.width.toFixed(0)} px)`);
  });
