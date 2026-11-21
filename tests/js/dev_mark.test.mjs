// Tests for web/js/app/dev-mark.js: the [DEV] prefix the page keeps on its titles on a dev run's page.
import test from "node:test";
import assert from "node:assert/strict";

import { DEV_PREFIX, isDevPage, pageTitle } from "../../src/bmpt/web/js/app/dev-mark.js";

const page = (badge) => ({ querySelector: (selector) => (badge && selector === ".dev-mark" ? {} : null) });

test("a dev run's page is told by its badge", () => {
  assert.equal(isDevPage(page(true)), true);
  assert.equal(isDevPage(page(false)), false);
  assert.equal(isDevPage(undefined), false);
});

test("titles keep a single [DEV] prefix on a dev run's page only", () => {
  assert.equal(DEV_PREFIX, "[DEV] ");
  assert.equal(pageTitle("学生", true), "[DEV] 学生");
  assert.equal(pageTitle("[DEV] 学生", true), "[DEV] 学生");
  assert.equal(pageTitle("学生", false), "学生");
});
