import assert from "node:assert/strict";
import { test } from "node:test";
import { ROOT_ROUTE, routeHistory } from "../../src/bmpt/web/js/app/route-history.js";

test("menu entry points preserve a return stack rooted at Student Hall", () => {
  const h = routeHistory();
  for (const route of ["#/student-list", "#/student/aru/aru", "#/import", "#/student/hoshino/hoshino/memorialLobby"])
    h.commit(route);
  assert.equal(h.back(), "#/import");
  h.commit("#/import");
  assert.equal(h.back(), "#/student/aru/aru");
  h.commit("#/student/aru/aru");
  assert.equal(h.back(), "#/student-list");
  h.commit("#/student-list");
  assert.equal(h.back(), ROOT_ROUTE);
  h.commit(ROOT_ROUTE);
  assert.equal(h.back(), null);
});

test("detail arrows and tabs replace the current entry without losing its import origin", () => {
  const h = routeHistory();
  h.commit("#/import");
  h.commit("#/student/aru/aru/portrait");
  h.commit("#/student/aru/dress/memorialLobby");
  assert.deepEqual(h.entries(), [ROOT_ROUTE, "#/import", "#/student/aru/dress/memorialLobby"]);
  assert.equal(h.back(), "#/import");
});

test("Back cancels uncommitted navigation and repeated page refreshes add no entries", () => {
  const h = routeHistory();
  h.commit("#/student-list");
  h.commit("#/student-list");
  assert.equal(h.back("#/student/missing/missing"), "#/student-list");
  assert.deepEqual(h.entries(), [ROOT_ROUTE, "#/student-list"]);
  assert.equal(h.back(), ROOT_ROUTE);
});

test("a committed Home action resets the return path to the root", () => {
  const h = routeHistory();
  h.commit("#/import");
  h.commit("#/student/hoshino/hoshino");
  h.commit(ROOT_ROUTE, { home: true });
  assert.deepEqual(h.entries(), [ROOT_ROUTE]);
  h.commit("#/student-list");
  assert.equal(h.back(), ROOT_ROUTE);
});
