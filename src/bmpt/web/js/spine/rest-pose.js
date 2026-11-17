// The animation a memorial lobby skeleton rests in, shared by the lobby player (lobby-clips.js) and the thumbnails
// (thumbs.js).

/**
 * The animations a lobby skeleton may rest in once its intro is over, in order of preference, so a still of a
 * lobby shows the pose the lobby settles into, never its first frame. The looping idle is the SpineCharacter's
 * idle clip, else Idle_01, else the prefab's start animation, else the first other animation (Dummy last);
 * never an intro (Hasumi_home's prefab starts "Start_Idle", which must not loop). A skeleton with intros only
 * gets none: Shigure's Shigure_00 and Shigure_01, the scenes of her lobby's intro timeline, rest in their setup
 * pose as spine-unity leaves them when the timeline's clip ends.
 * @param {object} [options] What the prefab says; a thumbnail has only the skeleton, which is Idle_01 on every
 *     lobby of the game.
 * @param {string|null} [options.idle] The prefab's idle clip for the skeleton (lobby.json idle).
 * @param {string|null} [options.animation] Its start animation, where known.
 * @returns {string[]} The animation names; empty when the skeleton has intros only.
 */
export function lobbyIdles(data, { idle = null, animation = null } = {}) {
  const ok = (n) => n && !/^start_/i.test(n) && data.findAnimation(n);
  const names = [
    idle,
    "Idle_01",
    animation,
    ...data.animations.filter((a) => /idle|loop|rest/i.test(a.name)).map((a) => a.name),
    ...data.animations.filter((a) => a.name !== "Dummy").map((a) => a.name),
    "Dummy",
  ];
  const candidates = [...new Set(names.filter(ok))];
  // Repackers sometimes keep an empty placeholder under the original idle's name.
  return candidates
    .filter((n) => data.findAnimation(n).timelines?.length !== 0)
    .concat(candidates.filter((n) => data.findAnimation(n).timelines?.length === 0));
}
/**
 * The animation a lobby skeleton rests in: the first of lobbyIdles.
 * @returns {string|null} The name, or null.
 */
export const lobbyIdle = (data, options) => lobbyIdles(data, options)[0] || null;
