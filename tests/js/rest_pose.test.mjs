import assert from "node:assert/strict";
import { test } from "node:test";
import { memorialLobbyIdle } from "../../src/bmpt/web/js/spine/rest-pose.js";
import * as MemorialLobby from "../../src/bmpt/web/js/memorial-lobby/memorial-lobby.js";

const data = (names) => ({
  animations: names.map((name) => ({ name })),
  findAnimation: (n) => (names.includes(n) ? { name: n } : null),
});

test("a memorial lobby rests in the prefab's idle, else Idle_01, never in an intro", () => {
  assert.equal(memorialLobbyIdle(data(["Start_Idle_01", "Idle_01", "Idle_02"]), { idle: "Idle_02" }), "Idle_02");
  assert.equal(memorialLobbyIdle(data(["Start_Idle_01", "Idle_01"])), "Idle_01");
  assert.equal(memorialLobbyIdle(data(["Start_Idle_01", "Idle_01"]), { idle: "Start_Idle_01" }), "Idle_01");
  assert.equal(memorialLobbyIdle(data(["Start_Idle_01", "Dummy", "Loop_00"])), "Loop_00");
  assert.equal(memorialLobbyIdle(data(["Start_Idle_00", "Start_Idle_01"])), null);
});

test("the memorial lobby player and the thumbnails choose the idle with the same rule", () => {
  for (const names of [["Start_Idle_01", "Idle_01"], ["Dummy", "Start_Idle_01", "BG_Loop_00_R"], ["Start_Idle_00"]]) {
    assert.equal(MemorialLobby.idleOf(data(names), { name: "X" }, null), memorialLobbyIdle(data(names)));
  }
});
