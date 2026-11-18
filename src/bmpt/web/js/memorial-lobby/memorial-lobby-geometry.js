// The measured geometry of one memorial lobby instance (memorial-lobby.js createMemorialLobby): the painted rect P, the
// zoom-out limits over every clip, the camera framing's limits, and the frame, head-pat point and talk sequences of the
// memorial lobby shown. The pure measuring is in memorial-lobby-measure.js; the functions here take the instance's
// state S (the memorial lobby shown and the instance's own caches) and functions L. This module keeps no state of its
// own.
import { specialFramingOf, specialView } from "../detail/special-view.js";
import { artBounds, containingView, overlaps, validRect, worldBounds } from "./memorial-lobby-health.js";
import * as Pins from "../spine/skeleton-pins.js";
import {
  CELL,
  SAMPLES,
  gridOf,
  paint,
  sceneBox,
  around,
  unitsOf,
  BAND,
  SLICE_MS,
  clipPoses,
  shrinkRect,
  outerOf,
  boundsRect,
  framingLimits,
  fit,
  leftView,
  rectOf,
  union,
} from "./memorial-lobby-measure.js";

// voice clip names as Kivo keys them: "Images/sound/Hoshino_MemorialLobby_3_1.ogg" -> "hoshino_memoriallobby_3_1"
const clipId = (name) =>
  name
    .toLowerCase()
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.(ogg|wav|mp3)$/, "")
    .replace(/_new$/, "");

/**
 * The measuring functions of a memorial lobby instance.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The functions this part adds to L.
 */
export function geometryPart(S, L) {
  // --- painted rect P: opaque, normal-blend triangles of every layer, over a few idle poses, on a 16-unit grid ---
  function geometryKey(loaded, G) {
    const { artBase, base, framing, info } = S.current || {};
    // Only immutable revision URLs identify parsed skeleton/atlas content; unversioned inputs are measured afresh.
    if (!/\/[a-f0-9]{64}\/$/.test(artBase || "") || (base && !/\/[a-f0-9]{64}\/$/.test(base))) return null;
    // Keep every authored layer field and the resolved rest pose, including skin, transforms and ignorePins.
    // The complete layout also covers pins, idle/clip selection and any future framing inputs.
    return JSON.stringify([artBase, base, framing, G, info, loaded.map(({ player, ...layer }) => layer)]);
  }
  function cachedPaintedRect(loaded, G) {
    const key = geometryKey(loaded, G),
      cached = S.paintedRects.get(key);
    if (cached) return cached;
    const value = paintedRect(loaded, G);
    S.paintedRects.set(key, value);
    return value;
  }
  // a reusable skeleton of a layer (its skin set), posed by tracks [track, animation, time, loop] with no mixing
  function poser(l) {
    const data = l.player.skeleton.data,
      sk = new spine.Skeleton(data),
      st = new spine.AnimationState(new spine.AnimationStateData(data));
    if (l.skin)
      try {
        sk.setSkinByName(l.skin);
      } catch {
        /* the default skin */
      }
    return (tracks) => {
      st.clearTracks();
      sk.setToSetupPose();
      for (const [track, name, time, loop] of tracks) {
        const e = st.setAnimation(track, name, loop);
        e.mixDuration = 0;
        e.trackTime = time;
      }
      if (tracks.length) {
        st.update(0);
        st.apply(sk);
      }
      // the bones the prefab pins, as the players draw them
      Pins.pin(sk, l.name, l.ignorePins ? null : S.current?.info?.pins);
      sk.updateWorldTransform(spine.Physics.reset); // physics at rest, as a new skeleton's: no state from the last pose
      return sk;
    };
  }
  function paintedRect(loaded, G) {
    const grid = gridOf(G),
      { X0, Y0, gw, gh } = grid,
      all = [[0, gw - 1, 0, gh - 1]];
    const acc = new Uint8Array(gw * gh).fill(1),
      cur = new Uint8Array(gw * gh),
      any = new Uint8Array(gw * gh);
    const posers = loaded.map((l) => [l, poser(l), L.idleOf(l.player.skeleton.data, l)]);
    for (let k = 0; k < SAMPLES; k++) {
      cur.fill(0);
      for (const [l, pose, anim] of posers) {
        const time = anim
          ? (l.rest?.time || 0) + (l.player.skeleton.data.findAnimation(anim).duration * k) / SAMPLES
          : 0;
        paint(grid, l, pose(anim ? [[0, anim, time, true]] : []), [[cur, all]]);
      }
      for (let i = 0; i < acc.length; i++) {
        acc[i] &= cur[i];
        any[i] |= cur[i];
      }
    }
    const full = (i0, i1, j0, j1) => {
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) if (!acc[j * gw + i]) return false;
      return true;
    };
    const r = {
      i0: Math.floor((G.x + 1 - X0) / CELL),
      i1: Math.floor((G.x + G.w - 1 - X0) / CELL),
      j0: Math.floor((G.y + 1 - Y0) / CELL),
      j1: Math.floor((G.y + G.h - 1 - Y0) / CELL),
    };
    const covered = full(r.i0, r.i1, r.j0, r.j1);
    // art that does not cover the game view (a mod): start from the covered middle of it
    while (!full(r.i0, r.i1, r.j0, r.j1) && r.i1 - r.i0 > 8 && r.j1 - r.j0 > 8) {
      r.i0++;
      r.i1--;
      r.j0++;
      r.j1--;
    }
    // then grow each side while the next column or row is fully painted; parts parked off screen behind a transparent
    // gap (intro props, a second stage set) are never reached
    for (let moved = true; moved; ) {
      moved = false;
      if (r.i0 > 0 && full(r.i0 - 1, r.i0 - 1, r.j0, r.j1)) {
        r.i0--;
        moved = true;
      }
      if (r.i1 < gw - 1 && full(r.i1 + 1, r.i1 + 1, r.j0, r.j1)) {
        r.i1++;
        moved = true;
      }
      if (r.j0 > 0 && full(r.i0, r.i1, r.j0 - 1, r.j0 - 1)) {
        r.j0--;
        moved = true;
      }
      if (r.j1 < gh - 1 && full(r.i0, r.i1, r.j1 + 1, r.j1 + 1)) {
        r.j1++;
        moved = true;
      }
    }
    // and the box of the scene's art in the idle poses (the Unlocked limit, until the clips widen it)
    const box = sceneBox(any, gw, gh, r);
    return { ...unitsOf(grid, r), covered, cells: r, any, whole: { ...unitsOf(grid, box), cells: box } };
  }

  // --- the zoom-out limit, safe for every clip: P, measured on idle poses, shrunk until no clip the memorial lobby
  // plays uncovers a cell near its edge (Kurumi's last dialogue moves the scene and showed a blank strip at the widest
  // view); its cache (S.safeRects) has the same source and geometry identity as the initial painted rect ---
  // one pose per step: the cells near P's sides that the pose leaves unpainted (Full Art), and the cells outside P that
  // it paints (Unlocked); every layer is drawn, the others idle
  function* measureSafe(loaded, meta, G) {
    const grid = gridOf(G),
      P = meta.painted.cells,
      K = BAND;
    const bands = [
      [P.i0, Math.min(P.i1, P.i0 + K - 1), P.j0, P.j1],
      [Math.max(P.i0, P.i1 - K + 1), P.i1, P.j0, P.j1],
      [P.i0, P.i1, P.j0, Math.min(P.j1, P.j0 + K - 1)],
      [P.i0, P.i1, Math.max(P.j0, P.j1 - K + 1), P.j1],
    ];
    const bad = new Uint8Array(grid.gw * grid.gh),
      cur = new Uint8Array(grid.gw * grid.gh),
      any = meta.any.slice();
    // `any` adds up over the idle and every pose, up to K cells past what the idle paints: a dialogue that moves or
    // scales the whole scene (CH0243, Airi's CH0251) does not open the limit to its whole path
    const W = meta.painted.whole.cells,
      reach = {
        i0: Math.max(0, W.i0 - K),
        i1: Math.min(grid.gw - 1, W.i1 + K),
        j0: Math.max(0, W.j0 - K),
        j1: Math.min(grid.gh - 1, W.j1 + K),
      };
    const targets = [
      [cur, bands],
      [any, around(P, reach)],
    ];
    const layers = loaded.map((l) => ({
      ...l,
      data: l.player.skeleton.data,
      idle: L.idleOf(l.player.skeleton.data, l),
      main: l.name === meta.main,
    }));
    const posers = layers.map(poser);
    const plan = clipPoses(
      layers,
      S.current?.info,
      meta.sequences.map((s) => s.anim),
      (l, name) => L.clipOf(l.player, name),
    );
    for (const [index, tracks] of plan) {
      cur.fill(0);
      const t = tracks[tracks.length - 1][2];
      layers.forEach((l, j) => {
        const idleDur = l.idle ? l.data.findAnimation(l.idle).duration : 0;
        const pose = j === index ? tracks : l.idle ? [[0, l.idle, idleDur ? t % idleDur : 0, true]] : [];
        paint(grid, l, posers[j](pose), targets);
      });
      for (const [i0, i1, j0, j1] of bands)
        for (let j = j0; j <= j1; j++)
          for (let i = i0; i <= i1; i++) if (!cur[j * grid.gw + i]) bad[j * grid.gw + i] = 1;
      yield;
    }
    const s = sceneBox(any, grid.gw, grid.gh, P),
      box = {
        i0: Math.max(s.i0, reach.i0),
        i1: Math.min(s.i1, reach.i1),
        j0: Math.max(s.j0, reach.j0),
        j1: Math.min(s.j1, reach.j1),
      };
    return {
      ...unitsOf(grid, shrinkRect(P, bad, grid.gw)),
      poses: plan.length,
      whole: { ...unitsOf(grid, box), cells: box },
    };
  }
  // measure the limits over the clips in the background (once per memorial lobby in a session), then narrow Full Art to
  // what every clip keeps painted and widen Unlocked to what any of them paints
  function measureClips() {
    const c = S.current,
      m = c?.meta;
    if (!m || m.sparse || m.safe || c.measuring || !c.loaded) return;
    const key = geometryKey(c.loaded, L.game());
    const done = (rect) => {
      if (S.current !== c) return;
      c.measuring = false;
      m.safe = rect;
      m.outer = outerOf(rect, m.full, false);
      m.whole = union(rect.whole, rectOf(m.full));
      L.reclamp();
    };
    const cached = S.safeRects.get(key);
    if (cached) {
      done(cached);
      return;
    }
    c.measuring = true;
    const job = measureSafe(c.loaded, m, L.game());
    let busy = 0;
    const slice = () => {
      if (S.current !== c) return;
      const from = performance.now(),
        until = from + SLICE_MS;
      let step;
      try {
        do step = job.next();
        while (!step.done && performance.now() < until);
      } catch (err) {
        console.warn("zoom-out limit over the clips", err); // keep the idle poses' limit
        c.measuring = false;
        return;
      }
      busy += performance.now() - from;
      if (!step.done) {
        L.setTimeout(slice, 0);
        return;
      }
      const rect = { ...step.value, ms: Math.round(busy) };
      S.safeRects.set(key, rect);
      done(rect);
    };
    L.setTimeout(slice, 0);
  }

  // --- the camera framing: a 16:9 view, centred across, zoomed and moved up or down, that the shutter keeps as
  // the memorial lobby's resting view; it may go wider than the game's view as far as no pose shows past the art ---
  // Every pose the memorial lobby rests in is painted: the idle and every clip (clipPoses). The intro does not count:
  // some intros move the whole scene (Mika (Swimsuit)'s lifts it off her feet for seconds) and the view the user frames
  // is the memorial lobby at rest, so such an intro may show the camera's clear colour past the art. A cell outside the
  // game's view that some pose leaves unpainted is `bad`: a view may take in no bad cell. Cells inside the game's
  // view never count: what the game itself shows there is the memorial lobby as made.
  function* measureFraming(all, loaded, meta, G, info) {
    const grid = gridOf(G),
      { gw, gh, X0, Y0 } = grid,
      n = gw * gh;
    const bad = new Uint8Array(n),
      cur = new Uint8Array(n),
      whole = [[cur, [[0, gw - 1, 0, gh - 1]]]];
    const outside = new Uint8Array(n);
    for (let j = 0; j < gh; j++)
      for (let i = 0; i < gw; i++) {
        const x = X0 + (i + 0.5) * CELL,
          y = Y0 + (j + 0.5) * CELL;
        outside[j * gw + i] = x < G.x || x > G.x + G.w || y < G.y || y > G.y + G.h ? 1 : 0;
      }
    const mark = () => {
      for (let k = 0; k < n; k++) if (outside[k] && !cur[k]) bad[k] = 1;
    };
    const layers = loaded.map((l) => ({
      ...l,
      data: l.player.skeleton.data,
      idle: L.idleOf(l.player.skeleton.data, l),
      main: l.name === meta.main,
    }));
    const posers = new Map(layers.map((l) => [l.name, poser(l)]));
    const idlePose = (l, t) => {
      const d = l.idle ? l.data.findAnimation(l.idle)?.duration || 0 : 0;
      return l.idle ? [[0, l.idle, d ? t % d : 0, true]] : [];
    };
    // the memorial lobby at rest and through its clips
    const plan = [
      [-1, []],
      ...clipPoses(
        layers,
        info,
        meta.sequences.map((s) => s.anim),
        (l, name) => L.clipOf(l.player, name),
      ),
    ];
    let poses = 0;
    for (const [index, tracks] of plan) {
      cur.fill(0);
      const t = tracks.length ? tracks[tracks.length - 1][2] : 0;
      layers.forEach((l, j) => paint(grid, l, posers.get(l.name)(j === index ? tracks : idlePose(l, t)), whole));
      mark();
      poses++;
      yield;
    }
    return { ...framingLimits(G, grid, bad), poses };
  }
  // the camera's limits, measured once per geometry key for the session (S.framingCache)
  function measureCamera() {
    const c = S.current,
      m = c?.meta;
    if (!m || c.camera?.lim?.sum || c.camera?.measuring || !c.loaded) return;
    const G = L.game(),
      key = geometryKey(c.all || c.loaded, G);
    c.camera = { lim: { G, grid: null, sum: null, wMax: G.w }, measuring: true, started: performance.now() };
    const done = (lim) => {
      if (S.current !== c) return;
      c.camera = { lim, measuring: false, ms: lim.ms };
      L.reclamp();
    };
    const cached = key && S.framingCache.get(key);
    if (cached) {
      done(cached);
      return;
    }
    const job = measureFraming(c.all || c.loaded, c.loaded, m, G, c.info);
    let busy = 0;
    const slice = () => {
      if (S.current !== c) return;
      const from = performance.now(),
        until = from + SLICE_MS;
      let step;
      try {
        do step = job.next();
        while (!step.done && performance.now() < until);
      } catch (err) {
        console.warn("camera framing limits", err); // keep the game's view as the limit
        c.camera.measuring = false;
        return;
      }
      busy += performance.now() - from;
      if (!step.done) {
        L.setTimeout(slice, 0);
        return;
      }
      const lim = { ...step.value, ms: Math.round(busy) };
      if (key) S.framingCache.set(key, lim);
      done(lim);
    };
    L.setTimeout(slice, 0);
  }

  // the memorial lobby's frame, head-pat point and talk sequences
  function measure(loaded) {
    if (S.current.framing) return measureSpecial(loaded);
    const info = S.current.info,
      G = L.game();
    const main =
      loaded.find((l) => l.name === info?.main) ||
      loaded.find((l) => l.player.skeleton.data.findBone("Touch_Point")) ||
      loaded[loaded.length - 1];
    const data = main.player.skeleton.data;
    const { any, ...painted } = cachedPaintedRect(loaded, G); // any: the cells some idle pose paints
    // a memorial lobby whose art covers less than half the view (a character-only mod): the game view with the old edge
    // extension and feather, measured on the skeletons' bounds
    const area = (r) => Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);
    const sparse = !painted.covered && area(painted) < 0.5 * G.w * G.h;
    const edge = sparse ? boundsRect(loaded) : painted;
    // the head-pat point in the idle pose (Touch_Point, else the prefab's pat box, else the top third of the view)
    const sk = new spine.Skeleton(data),
      idle = L.idleOf(data, main);
    sk.setToSetupPose();
    if (main.skin) {
      sk.setSkinByName(main.skin);
      sk.setSlotsToSetupPose();
    }
    if (idle) {
      const st = new spine.AnimationState(new spine.AnimationStateData(data));
      st.setAnimation(0, idle, true).trackTime = main.rest?.time || 0;
      st.update(0);
      st.apply(sk);
    }
    Pins.pin(sk, main.name, main.ignorePins ? null : info?.pins);
    sk.updateWorldTransform(spine.Physics.update);
    const bone = sk.findBone("Touch_Point") || sk.findBone("Touch_Eye");
    const pat = info?.drags?.find((d) => d.kind === "pat")?.rect;
    let touch = bone
      ? { x: main.x + bone.worldX * main.scale, y: main.y + bone.worldY * main.scale }
      : pat
        ? { x: (pat[0] + pat[2]) / 2, y: (pat[1] + pat[3]) / 2 }
        : { x: 0, y: G.y + G.h * 0.66 };
    const full = sparse
      ? overlaps(edge, rectOf(G))
        ? G
        : containingView(edge)
      : fit(painted, G.w, G.x + G.w / 2, G.y + G.h / 2);
    if (
      !Number.isFinite(touch.x) ||
      !Number.isFinite(touch.y) ||
      touch.x < edge.x0 ||
      touch.x > edge.x1 ||
      touch.y < edge.y0 ||
      touch.y > edge.y1
    )
      touch = { x: (edge.x0 + edge.x1) / 2, y: edge.y0 + (edge.y1 - edge.y0) * 0.66 };
    const sequences = data.animations
      .map((a) => /^Talk_(\d+)_M$/.exec(a.name))
      .filter(Boolean)
      .map((m) => {
        const anim = m[0],
          a = data.findAnimation(anim);
        // the voice cues: the "Talk" event whose string names the clip (every memorial lobby), or an event named after
        // the clip (older memorial lobbies only), with its time
        const cues = [];
        for (const e of a.timelines.filter((tl) => tl instanceof spine.EventTimeline).flatMap((tl) => tl.events)) {
          const s = e.stringValue ?? e.data.stringValue ?? "";
          const id =
            /^talk$/i.test(e.data.name) && /memoriallobby/i.test(s)
              ? clipId(s)
              : /memoriallobby/i.test(e.data.name)
                ? clipId(e.data.name)
                : null;
          if (id && !cues.some((c) => c.clip === id)) cues.push({ t: e.time, clip: id });
        }
        cues.sort((x, y) => x.t - y.t);
        return { anim, n: Number(m[1]), duration: a.duration, cues };
      })
      .sort((a, b) => a.n - b.n);
    // the painted rect counts a 16-unit cell as painted by its centre, so the art can end a few units before its
    // border: the left view keeps one cell inside it
    const inner = sparse
      ? edge
      : { ...edge, x0: edge.x0 + CELL, x1: edge.x1 - CELL, y0: edge.y0 + CELL, y1: edge.y1 - CELL };
    // the zoom-out limits, until measureClips has narrowed Full Art to what every clip keeps painted and widened
    // Unlocked to what any of them paints (sparse art: its skeletons' bounds)
    const outer = outerOf(edge, full, sparse);
    const whole = union(sparse ? edge : painted.whole, rectOf(full));
    return {
      main: main.name,
      painted,
      any,
      edge,
      sparse,
      touch,
      full,
      outer,
      whole,
      safe: null,
      left: leftView(inner, touch, G),
      sequences,
    };
  }

  // Special slots have no memorial camera: derive their frame from settled, drawable attachments.
  function measureSpecial(loaded) {
    const edge = loaded
      .map((l) => worldBounds(artBounds(l.player.skeleton), l))
      .filter(validRect)
      .reduce((a, b) => (a ? union(a, b) : b), null);
    const framing = specialFramingOf(S.current.framing, edge, loaded.length);
    const full = specialView(edge, framing);
    const left = specialView(edge, framing, 16 / 9, framing === "character" ? 0.25 : 0.5);
    S.current.specialGame = full;
    return {
      main: loaded.at(-1).name,
      edge,
      painted: edge,
      sparse: framing === "character",
      touch: { x: (edge.x0 + edge.x1) / 2, y: edge.y0 + (edge.y1 - edge.y0) * 0.8 },
      full,
      left,
      outer: rectOf(full),
      whole: union(edge, rectOf(full)),
      safe: null,
      sequences: [],
    };
  }

  return { measureClips, measureCamera, measure };
}
