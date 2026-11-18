// The clips of one memorial lobby instance (memorial-lobby.js createMemorialLobby): an animation played the way the
// memorial lobby's clip asset says (track, loop, mixes, chain, sync clips), the return to idle a dialogue starts and
// ends with, and the random idle clips. The functions take the instance's state S and functions L; this module keeps no
// state of its own.
import { memorialLobbyIdle } from "../spine/rest-pose.js";
import * as Sync from "./memorial-lobby-sync.js";
import { idleClips } from "./memorial-lobby-measure.js";

/**
 * The clip functions of a memorial lobby instance.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The functions this part adds to L.
 */
export function clipsPart(S, L) {
  /**
   * The looping idle of a layer (rest-pose.js, which the thumbnails choose with too).
   * @returns {string|null}
   */
  function idleOf(data, layer, info = S.current?.info) {
    if (layer.rest) return layer.rest.name;
    return memorialLobbyIdle(data, { idle: info?.idle?.[layer.name], animation: layer.animation });
  }

  // --- clips: an animation played the way the memorial lobby's clip asset says (track, loop, mixes, chain, sync clips)
  // ---
  function clipOf(p, name) {
    const c = S.current?.info?.clips?.[p.bamtLayer?.name]?.[name];
    if (c) return c;
    // without the clip asset (a mod, or no prefab data): the usual layout of the memorial lobby clips
    const sync = /_M$/.test(name) ? name.replace(/_M$/, "_A") : null,
      isA = /_A$/.test(name),
      talky = /^Talk/.test(name);
    const mix = talky ? 0.3 : p.animationState.data.defaultMix;
    return {
      track: isA ? 2 : 1,
      loop: /^(Pat|Look|Pinch|HandFollow|Touch)_\d+_[MA]$/.test(name),
      finish: 2,
      intro: isA && !talky ? 0 : mix,
      outro: mix,
      next: null,
      sync: sync && p.skeleton.data.findAnimation(sync) ? [sync] : [],
    };
  }
  // As the game plays a clip: a clip of mode 2 waits for its random turn (memorial-lobby-sync.js), a random idle clip
  // (mode 3) plays on its own schedule (Idle), the others play at once; then the clip's sync clips.
  function playClip(p, name, depth = 0) {
    const st = p?.animationState,
      data = p?.skeleton?.data;
    if (!st || !data.findAnimation(name) || depth > 4) return null;
    const c = clipOf(p, name),
      t = c.track,
      cur = st.tracks[t];
    let entry = null;
    if (c.mode === 2) p.bamtRuns?.start(name);
    else if (c.mode !== 3) {
      if (!cur || (cur.animation.name === "<empty>" && !cur.mixingFrom)) {
        // an idle track: fade in over the lower tracks (setAnimation on an empty track would ignore the mix)
        st.setEmptyAnimation(t, 0);
        entry = st.addAnimation(t, name, c.loop, 0);
      } else entry = st.setAnimation(t, name, c.loop);
      entry.mixDuration = c.intro;
      L.soundsOf(p, entry, c);
      if (!c.loop) {
        // the outro or the next clip: so that it ends with the clip (outroAt null), else from that long after its end
        const at = c.outroAt == null ? 0 : data.findAnimation(name).duration + c.outroAt;
        if ((c.finish === 1 || c.finish === 3) && c.next && data.findAnimation(c.next))
          enqueue(p, t, c.next, at, entry);
        else if (c.finish === 2) st.addEmptyAnimation(t, c.outro, at);
      }
    }
    for (const s of c.sync || []) playClip(p, s, depth + 1);
    return entry;
  }
  // the clip after another on its track, delay as for addAnimation; a random-timing one starts its
  // wait once the clip before it completes (Hoshino's WhaleMove_01_R after Start_WhaleMove_01_R)
  function enqueue(p, t, name, delay, after) {
    const st = p.animationState,
      n = clipOf(p, name);
    if (n.mode === 2) {
      Sync.listen(after, {
        complete: () => {
          if (p.animationState) playClip(p, name);
        },
      });
      return;
    }
    const e = st.addAnimation(t, name, n.loop, delay);
    if (delay <= 0) e.delay = Math.max(0, e.delay + e.mixDuration - n.intro); // its mix ends with the clip before
    e.mixDuration = n.intro;
    L.soundsOf(p, e, n);
  }
  function clearClip(p, name, depth = 0) {
    if (!p?.animationState || depth > 4) return;
    const c = clipOf(p, name);
    if (c.mode === 2) p.bamtRuns?.stop(name);
    p.animationState.setEmptyAnimation(c.track, c.outro || 0.2);
    for (const s of c.sync || []) clearClip(p, s, depth + 1);
  }
  // a dialogue starts or ends: the game returns the character to its idle keeping the idle's clips,
  // and a random-timing one that has not played yet starts its wait again
  function returnToIdle(p) {
    if (!p?.bamtRuns || !p.skeleton) return;
    const idle = idleOf(p.skeleton.data, p.bamtLayer);
    p.bamtRuns.returnToIdle(idleClips(S.current?.info, p.bamtLayer.name, idle, (n) => clipOf(p, n)));
  }
  const mainPlayer = () =>
    S.players.find((p) => p.bamtLayer?.name === S.current?.meta?.main) || S.players[S.players.length - 1];
  // a bone of a layer's skeleton in its current pose, for the particle systems that follow it (SkeletonUtilityBone)
  function boneOf(skeleton, name) {
    const bone = S.players.find((p) => p.bamtLayer?.name === skeleton)?.skeleton?.findBone(name);
    return bone && bone.active !== false ? bone : null;
  }
  /**
   * For checks: the particle effects' live particles, systems, loaded textures, GL contexts and time per frame.
   * @returns {object|null} The statistics; null without effects.
   */
  const effects = (opts) => S.current?.fx?.stats(opts) || null;
  /**
   * For checks: the layers' players, bottom first.
   * @returns {object[]}
   */
  const layerPlayers = () => S.players.filter((p) => p.bamtLayer);
  /**
   * For checks: each layer's random-timing clips (seconds until each plays next) and tracks, and the sound keys
   * played lately (also while muted).
   * @returns {object[]}
   */
  const clipRuns = () =>
    S.players
      .filter((p) => p.bamtLayer)
      .map((p) => ({
        layer: p.bamtLayer.name,
        time: +(p.bamtRuns?.time || 0).toFixed(3),
        runs: p.bamtRuns?.list() || [],
        tracks: p.animationState?.tracks.map((t) => t?.animation?.name || null) || [],
      }));

  return {
    idleOf,
    clipOf,
    playClip,
    clearClip,
    returnToIdle,
    mainPlayer,
    boneOf,
    effects,
    layerPlayers,
    clipRuns,
  };
}

/**
 * The random idle clips of a memorial lobby instance (SpineCharacter.idleBaseRandomClipPool): blinks, ear twitches,
 * expression changes, each after a random wait; held back while a dialogue or a drag runs. Without them the newer
 * memorial lobbies never blink.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The instance's Idle: start(players), stop(), pause(), resume(), interrupt() and blocking.
 */
export function createIdle(S, L) {
  return {
    timers: new Set(),
    paused: 0,
    blocking: 0,
    playing: [],
    start(list) {
      this.stop();
      for (const p of list)
        for (const name of S.current?.info?.randomIdle?.[p.bamtLayer.name] || []) this.schedule(p, name);
    },
    later(fn, ms) {
      const id = L.setTimeout(() => {
        this.timers.delete(id);
        fn();
      }, ms);
      this.timers.add(id);
    },
    schedule(p, name) {
      if (!p.skeleton?.data.findAnimation(name)) return;
      const r = L.clipOf(p, name).random || { min: 8, max: 15 };
      this.later(() => this.fire(p, name), Math.max(1, r.min + Math.random() * Math.max(0, r.max - r.min)) * 1000);
    },
    fire(p, name) {
      if (!S.current || !p.animationState) return;
      if (this.paused || S.talk) {
        this.schedule(p, name);
        return;
      }
      const c = L.clipOf(p, name),
        r = c.random || { loops: [0, 0] },
        st = p.animationState;
      const [lo, hi] = r.loops || [0, 0],
        times = 1 + lo + Math.floor(Math.random() * (Math.max(lo, hi) - lo + 1));
      st.setEmptyAnimation(c.track, 0);
      st.addAnimation(c.track, name, false, 0).mixDuration = c.intro;
      for (let i = 1; i < times; i++) st.addAnimation(c.track, name, false, 0).mixDuration = 0;
      // the last frame must not stay on a track above the face tracks
      st.addEmptyAnimation(c.track, c.outro || 0, 0);
      const ms = p.skeleton.data.findAnimation(name).duration * times * 1000,
        entry = { p, track: c.track, outro: c.outro };
      this.playing.push(entry);
      if (c.block) this.blocking += 1;
      this.later(() => {
        this.playing = this.playing.filter((x) => x !== entry);
        if (c.block) this.blocking = Math.max(0, this.blocking - 1);
        this.schedule(p, name);
      }, ms);
    },
    // a dialogue or a drag takes over: running random clips fade out
    interrupt() {
      for (const { p, track, outro } of this.playing) p.animationState?.setEmptyAnimation(track, outro || 0.2);
    },
    pause() {
      this.paused += 1;
    },
    resume() {
      this.paused = Math.max(0, this.paused - 1);
    },
    stop() {
      for (const id of this.timers) L.clearTimeout(id);
      this.timers.clear();
      this.paused = 0;
      this.blocking = 0;
      this.playing = [];
    },
  };
}
