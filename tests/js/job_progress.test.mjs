import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { jobProgress, progressName } from "../../src/bmpt/web/js/core/job-progress.js";
import { NAME_LANG, setStrings, T } from "../../src/bmpt/web/js/core/i18n.js";

globalThis.document = { documentElement: {} };
const locales = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const catalog = JSON.parse(readFileSync(new URL("../../src/bmpt/catalog/data/catalog.json", import.meta.url)));
function catalogAlt(key) {
  const entry = catalog.variants[key],
    student = catalog.students[entry.student_id];
  return { key, names: entry.names, student: { key: student.base, studentNames: student.names } };
}
const flat = (data, prefix = "", out = {}) => {
  for (const [key, value] of Object.entries(data)) {
    if (value && typeof value === "object") flat(value, `${prefix}${key}.`, out);
    else out[prefix + key] = value;
  }
  return out;
};
for (const locale of locales)
  test(`student job identities use one bracket and omit repeated steps in ${locale}`, () => {
    setStrings(
      locale,
      flat(JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url)))),
    );
    const names = { full: "Yoshimi (Band)", short: "Band" },
      alt = { key: "ch0220", names: { en: names }, student: { key: "yoshimi", studentNames: { en: "Yoshimi" } } },
      kind = T("tab.lobby");
    const name = progressName(alt, kind);
    assert.equal(name, T("progress.subject", { name: "Yoshimi", parts: ["Band", kind].join(T("progress.comma")) }));
    assert.equal([...name.matchAll(/[（(]/g)].length, 1);
    const base = progressName(
      {
        key: "azusa",
        names: { en: { full: "Azusa", short: "Original" } },
        student: { key: "azusa", studentNames: { en: "Azusa" } },
      },
      kind,
    );
    assert.equal(base, T("progress.subject", { name: "Azusa", parts: kind }));
    for (const [base, full, short] of [
      ["Shiroko", "Shiroko*Terror", "Terror"],
      ["Arona", "Arona - Tasks - Daytime", "Tasks - Daytime"],
    ]) {
      const student = { key: base, studentNames: { en: base } },
        v = { key: full, names: { en: { full, short } }, student };
      assert.equal(
        progressName(v, kind),
        T("progress.subject", { name: base, parts: [short, kind].join(T("progress.comma")) }),
      );
      student.customAliases = ["Legacy base alias"];
      assert.equal(
        progressName(v, kind),
        T("progress.subject", { name: base, parts: [short, kind].join(T("progress.comma")) }),
      );
      v.customAliases = ["Custom"];
      assert.equal(progressName(v, kind), T("progress.subject", { name: "Custom", parts: kind }));
    }
    const line = jobProgress({ action: "import.phase.add", name, current: 2, total: 3, step: "import.step.add" });
    assert.ok(!line.includes(" - "));
    assert.ok(!jobProgress({ action: "progress.actionExtract", name, step: "import.step.extract" }).includes(" - "));
    assert.ok(line.includes("2 / 3"));
    assert.ok(line.includes(name));
    assert.ok(
      jobProgress({ action: "import.phase.update", name, step: "import.step.crc" }).endsWith(
        T("progress.step", { step: T("import.step.crc") }),
      ),
    );
  });

for (const locale of locales) {
  for (const key of ["ch9996", "ch9997", "ch9998", "ch9999"])
    test(`catalog base ${key} omits Original in ${locale}`, () => {
      setStrings(
        locale,
        flat(JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url)))),
      );
      const alt = catalogAlt(key),
        student = alt.student,
        base = student.studentNames[NAME_LANG[locale]];
      assert.equal(alt.key, student.key);
      for (const kind of [T("tab.portrait"), T("tab.lobby"), T("tab.model")]) {
        const expected = T("progress.subject", { name: base, parts: kind });
        assert.equal(
          progressName({ ...alt, student: { key: student.key, names: student.studentNames } }, kind),
          expected,
        );
        assert.equal(progressName(alt, kind), expected);
        assert.equal(progressName({ ...alt, student: student.key }, kind, "", student), expected);
        assert.equal(progressName(alt, ""), base);
        alt.customAliases = ["Custom"];
        assert.equal(progressName(alt, kind), T("progress.subject", { name: "Custom", parts: kind }));
        delete alt.customAliases;
      }
    });
  test(`catalog alts retain their labels in ${locale}`, () => {
    setStrings(
      locale,
      flat(JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url)))),
    );
    for (const key of ["ch0220", "ch0263", "np0168", "np0169", "np0170"]) {
      const alt = catalogAlt(key),
        lang = NAME_LANG[locale],
        kind = T("tab.lobby");
      assert.notEqual(alt.key, alt.student.key);
      assert.equal(
        progressName(alt, kind),
        T("progress.subject", {
          name: alt.student.studentNames[lang],
          parts: [alt.names[lang].short, kind].join(T("progress.comma")),
        }),
      );
    }
  });
}

// A job over mods (library/mod_progress.py): one line per item at hand, the parts the bottom-left line shortens.
import { fitLine, modLine } from "../../src/bmpt/web/js/core/job-progress.js";
const nameOf = ({ alt }) => ({ ch0063: "Hoshino (Swimsuit)", ch0145: "Hina" })[alt] || alt;
for (const locale of locales)
  test(`a job over mods names the item at hand, its kind, own name, percentage and step in ${locale}`, () => {
    setStrings(
      locale,
      flat(JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url)))),
    );
    const sep = T("progress.sep");
    const line = modLine(
      {
        action: "process",
        current: 3,
        total: 13,
        alt: "ch0063",
        resourceType: "chibi",
        name: "My Swim Edit",
        step: "import.step.save",
        fraction: 0.427,
      },
      { nameOf },
    );
    assert.equal(
      line.text,
      `${T("progress.modCount", { action: T("progress.modsProcess"), current: 3, total: 13 })}Hoshino (Swimsuit)` +
        `${sep}${T("tab.model")}${sep}My Swim Edit${sep}${T("progress.percent", { percent: 42 })}${sep}${T("import.step.save")}`,
    );
    assert.equal(line.parts.map(([text]) => text).join(""), line.text);
    // the count is of the item at hand: the first of 13 reads 1/13, never 0/13
    assert.ok(
      modLine({ current: 1, total: 13, alt: "ch0063", resourceType: "portrait" }, { nameOf }).text.includes("1"),
    );
    // a costume swap names the alt whose costume it takes; one item leaves the count out; no fraction, no
    // percentage
    const swap = modLine(
      {
        action: "apply",
        current: 1,
        total: 1,
        alt: "ch0063",
        resourceType: "chibi",
        source: "ch0145",
        step: "import.step.write",
      },
      { nameOf },
    );
    assert.equal(
      swap.text,
      `${T("progress.modHead", { action: T("progress.modsApply") })}Hoshino (Swimsuit)${sep}${T("tab.model")}` +
        `${sep}${T("progress.from", { name: "Hina" })}${sep}${T("import.step.write")}`,
    );
    // a name that only repeats who it is is left out
    assert.equal(
      modLine({ alt: "ch0063", resourceType: "memorialLobby", name: "Hoshino (Swimsuit)" }, { nameOf }).parts.length,
      3,
    );
    for (const kind of ["portrait", "memorialLobby", "chibi"])
      assert.ok(
        modLine({ alt: "ch0063", resourceType: kind }, { nameOf }).text.endsWith(
          T(`tab.${{ portrait: "portrait", memorialLobby: "lobby", chibi: "model" }[kind]}`),
        ),
      );
    for (const key of ["readModel", "fitCostume", "buildModel", "write", "restore", "save", "check"]) {
      assert.notEqual(T(`import.step.${key}`), `import.step.${key}`, `${locale} ${key}`);
    }
    for (const key of ["modsProcess", "modsApply", "modsRestore", "modsCheck", "modCount", "modHead", "sep", "from"]) {
      assert.notEqual(T(`progress.${key}`), `progress.${key}`, `${locale} ${key}`);
    }
    // progress of no mod item is not a mod line
    assert.equal(modLine({ current: 1, total: 2, label: "Checking game files" }), null);
  });

test("a long line shortens the mod's own name first, then who it is, and keeps the count, percentage and step", () => {
  setStrings("zh-CN", flat(JSON.parse(readFileSync(new URL("../../src/bmpt/locales/zh-CN.json", import.meta.url)))));
  const width = (text) => Array.from(text).length;
  const line = modLine(
    {
      action: "process",
      current: 3,
      total: 13,
      alt: "ch0063",
      resourceType: "chibi",
      name: "一个特别特别长的自定义名字一个特别特别长的自定义名字",
      step: "import.step.save",
      fraction: 0.42,
    },
    { nameOf },
  );
  const full = width(line.text);
  assert.equal(fitLine(line.parts, width, full), line.text);
  const fitted = fitLine(line.parts, width, full - 10);
  assert.equal(width(fitted), full - 10);
  assert.ok(fitted.includes("Hoshino (Swimsuit)") && fitted.includes("…") && fitted.endsWith("42% - 保存文件"), fitted);
  assert.ok(fitted.startsWith("处理 Mod（3/13）："), fitted);
  // the own name down to the ellipsis alone, then who it is shortened
  const tight = fitLine(line.parts, width, full - 30);
  assert.ok(tight.includes(" - …") && tight.includes("Hoshino") && !tight.includes("Hoshino (Swimsuit)"), tight);
  assert.ok(width(tight) <= full - 30 && tight.endsWith("42% - 保存文件"), tight);
  // nothing left to shorten: both at the ellipsis, the label's own ellipsis clips the rest
  const none = fitLine(line.parts, width, 5);
  assert.ok(none.includes("：…") && none.endsWith("42% - 保存文件"), none);
});

// A one-mod job (a student page's, Settings' or a repair's action): who it is stays, the step and share follow the job.
import { modJob } from "../../src/bmpt/web/js/core/job-progress.js";
import { readdirSync } from "node:fs";
test("a one-mod job keeps who it is, shows no count and keeps its step through a report of files", () => {
  setStrings("zh-CN", flat(JSON.parse(readFileSync(new URL("../../src/bmpt/locales/zh-CN.json", import.meta.url)))));
  const sep = T("progress.sep");
  const line = modJob(
    { alt: "ch0063", resourceType: "portrait", core: "ch0063_spr", name: "ch0063_spr" },
    { nameOf, action: "progress.actionRepair" },
  );
  const head = `${T("progress.modHead", { action: T("progress.actionRepair") })}Hoshino (Swimsuit)${sep}${T("tab.portrait")}`;
  assert.equal(line().text, head); // a name that only repeats the file is left out
  assert.equal(
    line({ step: "import.step.replace", fraction: 0.3 }).text,
    `${head}${sep}30%${sep}${T("import.step.replace")}`,
  );
  assert.equal(
    line({ current: 2, total: 3, label: "file.bundle" }).text,
    `${head}${sep}30%${sep}${T("import.step.replace")}`,
  );
  // a student thumbnail is its own kind
  assert.ok(
    modJob({ alt: "ch0063", resourceType: "thumbnail" }, { nameOf, action: "progress.actionApply" })().text.endsWith(
      `${sep}${T("photo.modName")}`,
    ),
  );
});

test("every loading task of the web UI says what it does: a line of its own, or none for a screen being prepared", () => {
  const dir = new URL("../../src/bmpt/web/js/", import.meta.url);
  const files = (url) =>
    readdirSync(url, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(new URL(`${e.name}/`, url)) : e.name.endsWith(".js") ? [new URL(e.name, url)] : [],
    );
  const missing = [];
  for (const file of files(dir)) {
    if (file.pathname.endsWith("/loading.js")) continue;
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(/Loading\.task\(/g)) {
      // the call's own arguments: up to its closing parenthesis
      let depth = 0,
        end = match.index + "Loading.task".length;
      for (; end < source.length; end++) {
        if (source[end] === "(") depth++;
        else if (source[end] === ")" && --depth === 0) break;
      }
      if (!/\{\s*label\s*:/.test(source.slice(match.index, end)))
        missing.push(`${file.pathname.split("/js/")[1]}:${source.slice(0, match.index).split("\n").length}`);
    }
  }
  assert.deepEqual(missing, []);
});
