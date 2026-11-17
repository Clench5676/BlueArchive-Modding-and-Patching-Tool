// The players of one memorial lobby instance (lobby.js createLobby): showing a lobby in a host (its sources, one
// spine-player per layer, drawn in linear light with the game's materials and effects, framed and started), recovering
// a lost context, reporting a lobby that cannot be shown, and hiding it with everything it holds. The functions take
// the instance's state S and functions L; this module keeps no state of its own.
import { loadPlayer } from "../spine/spine-load.js";
import { ensureSpine } from "../spine/spine-runtime.js";
import { spineBase } from "../core/api.js";
// every wait of the lobby is a preview's: counted in the foreground, without the time a read waits for the library
import { withPreviewTimeout as withTimeout } from "../core/async.js";
import { atStep, previewError } from "../detail/preview-error.js";
import { T } from "../core/i18n.js";
import { resumeRest, settle, validRect } from "./lobby-health.js";
import * as Linear from "../core/linear.js";
import * as Materials from "../spine/skeleton-materials.js";
import * as Particles from "./particles-gl.js";
import * as SlotColours from "../spine/slot-colours.js";
import * as Stage from "./lobby-stage.js";
import * as Pins from "../spine/skeleton-pins.js";
import * as Touch from "./lobby-touch.js";
import * as Sync from "./lobby-sync.js";
import * as Intro from "./lobby-intro.js";
import { freezeWhileLoading, onScreen, previewLifecycle } from "../detail/preview-lifecycle.js";
import { idleClips, layerViewport, rectOf } from "./lobby-measure.js";
import { layersOf, sourcesOf } from "./lobby-layers.js";
import { EXT_H, EXT_W, STAGE } from "./lobby-view.js";
const dialogueModule = () => import("../hall/student-hall-dialogue.js");

/**
 * The player functions of a lobby instance.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The functions this part adds to L.
 */
export function playersPart(S, L) {
  function visualImage(url) {
    const c = S.current;
    if (!c) return Promise.reject(new Error("Lobby image has no owner"));
    if (!c.visualLoads.has(url)) {
      const image = new Image();
      const request = withTimeout(
        new Promise((resolve, reject) => {
          image.onload = () => resolve(image);
          image.onerror = () => reject(new Error(`Lobby image unavailable: ${url}`));
          image.src = url;
        }),
        15000,
        url,
        { signal: c.signal },
      ).finally(() => {
        image.onload = image.onerror = null;
      });
      request.catch(() => {}); // The complete-frame barrier reports failures, including those swallowed by a renderer.
      c.visualLoads.set(url, request);
    }
    return c.visualLoads.get(url);
  }

  // share: the lobby's bottom player, whose context this one draws with (lobby-stage.js), or null
  function makePlayer(host, layer, fx, share, signal) {
    const div = document.createElement("div");
    div.className = "lobby-layer";
    if (layer.introOnly) {
      div.style.visibility = "hidden";
      div.classList.add("lobby-off");
    }
    host.appendChild(div);
    return loadPlayer(
      (config) => Stage.make(share, () => new spine.SpinePlayer(div, config)),
      {
        skeleton: `${layer.spine}.skel`,
        atlas: layer.atlas || `${layer.spine}.atlas`,
        animation: undefined,
        premultipliedAlpha: false,
        alpha: true,
        backgroundColor: "#00000000",
        showControls: false,
        showLoading: false,
        preserveDrawingBuffer: true,
        defaultMix: S.current?.info?.defaultMix?.[layer.name] ?? 0.2,
        viewport: layerViewport(L.game(), layer),
        // every frame: the frames of the players drawn with this one's context (lobby-stage.js), the random-timing
        // clips that are due (before the animation update, as the game's coroutines run before the next Update), the
        // shared view, no spine-player viewport transitions of its own
        frame: (p, delta) => {
          // SpinePlayer starts with paused=true, then calls play() AFTER success. Capture its settled playback
          // state here, not the constructor's temporary pause, so activation does not restore that loading state.
          if (L.paused() && !S.current?.preparingFrame) {
            L.freezePlayer(p, S.current);
            delta = 0;
          }
          if (!L.paused() || S.current?.preparingFrame) S.current?.intro?.frame(p, delta);
          // A timeline may finish by emptying track 0. Its idle must resume once that mix has ended.
          if (!S.current?.intro?.pending() && !S.talk) resumeRest(p, layer);
          Stage.frame(p);
          if (!L.paused()) p.bamtRuns?.tick(delta);
          const v = L.viewNow();
          if (v) {
            p.currentViewport = layerViewport(v, layer);
            p.previousViewport = null;
          }
          Linear.begin(p);
        },
        // in linear light like the game (linear.js): the slot colours as its shader sees them, then the frame drawn
        // into its framebuffer goes onto the canvas; the particle effects that sort before the skeleton draw after the
        // clear, the others after it
        update: (p, delta) => {
          Linear.tint(p);
          fx?.below(p, L.paused() || S.current?.preparingFrame ? 0 : delta);
        },
        draw: (p) => {
          fx?.above(p);
          Linear.end(p);
          if (S.current && !Stage.hostOf(p)) S.current.drawnAt = performance.now();
        },
        // after the animation, before the world transform: the sound keys its track entries have reached, and the
        // dragged bones follow the pointer
        updateWorldTransform: (p, delta) => {
          if (!L.paused()) p.bamtSounds?.tick();
          Pins.apply(p, layer.ignorePins ? null : S.current?.info?.pins);
          if (L.ownsInput() && !L.paused()) Touch.apply(p, delta);
          p.skeleton.updateWorldTransform(spine.Physics.update);
        },
        success: (player) => {
          try {
            player.bamtLayer = layer;
            L.clocked(player);
            Stage.ready(player);
            if (layer.introOnly) Stage.setShown(player, false); // a scene only the intro shows
            // linear light; a player drawn with another's context once every player is in (show)
            if (!Stage.hostOf(player)) lightUp(player);
            if (layer.skin)
              try {
                player.skeleton.setSkinByName(layer.skin);
                player.skeleton.setSlotsToSetupPose();
              } catch {
                /* the default skin */
              }
            // the idle, and with it the clips it starts (its sync clips, the rest of its pool), as the game's OnEnable
            player.bamtSounds = new Sync.SoundKeys(L.sfx);
            player.bamtRuns = new Sync.Runs(player.animationState, {
              clip: (name) => L.clipOf(player, name),
              duration: (name) => player.skeleton.data.findAnimation(name)?.duration || 0,
              onEntry: (entry, c) => L.soundsOf(player, entry, c),
            });
            // its Spine events reach the particle effects, which some switch on and off (SpineEventToggleObjects)
            if (fx)
              player.animationState.addListener({
                event: (_entry, e) => fx.spineEvent(layer.name, e.data.name, e.intValue),
              });
            const idle = settle(player, layer, S.current?.info, Pins.pin);
            if (idle) L.soundsOf(player, player.animationState.getCurrent(0), L.clipOf(player, idle));
            for (const name of idleClips(S.current?.info, layer.name, idle, (n) => L.clipOf(player, n)))
              if (L.clipOf(player, name).track !== 0) L.playClip(player, name);
            if (L.paused()) player.stopRendering?.();
          } catch (err) {
            console.error("Lobby player setup failed", layer.name, err);
            throw err;
          }
        },
      },
      {
        signal,
        manual: !!S.current?.deferPlayback,
        onResume: () => {
          const c = S.current;
          if (!c || c.signal !== signal || L.paused()) return false;
          c.drawnAt = performance.now();
          c.introTime = undefined;
          c.pixelsChecked = false;
          c.viewAt = null;
          if (c.revealed) L.reveal(false);
          return !S.players.some((p) => p.bamtLayer === layer && Stage.hostOf(p));
        },
        onRecover: () => {
          if (S.current?.signal === signal) recoverView(S.current);
        },
        onError: (err) => {
          if (!S.current || signal.aborted) return;
          S.current.error = err;
          if (S.current.meta) failView(err, S.current.host, `${layer.spine}.skel`, "render");
        },
      },
    );
  }

  // A loaded player in linear light like the game (linear.js), with the game camera's post-processing (the prefab's
  // volumes) placed in the camera's view whatever the player shows, the slot colours as each page's shader takes them
  // and the game's page materials. A player drawn with another's context (lobby-stage.js) gets it only with that one
  // in linear light, whose pass it shares.
  function lightUp(player) {
    const layer = player.bamtLayer;
    if (!Linear.attach(player, Stage.hostOf(player))) return;
    Linear.setPost(player, S.current?.info?.post);
    Linear.setScreen(player, layerViewport(L.game(), layer));
    Linear.setCurves(player, SlotColours.curvesOf(layer));
    // Keep Aoi's authored glow blends out of the memorial lobby's fallback to Normal.
    Materials.attach(player, layer, {
      screen: () => layerViewport(L.game(), layer),
      base: S.current.base,
      load: S.current.deferPlayback ? visualImage : undefined,
      slotBlends: typeof S.current.core === "string" && S.current.core.toLowerCase() === "np0032_npc",
    });
  }

  // --- show(core, host) -> Promise<meta> (frame, sequences); resolves once every layer is in and framed ---
  /**
   * Show a lobby in a host: load every layer, frame it and start it.
   * @param {object} [options]
   * @param {object|null} [options.mod]
   * @param {object|null} [options.framing]
   * @param {boolean} [options.camera] Show the camera's art.
   * @param {boolean} [options.deferPlayback] Do not start playback until reveal().
   * @returns {Promise<object>} The lobby's meta (frames, sequences); settles once every layer is in and framed.
   */
  function show(
    core,
    host,
    variant,
    { mod = null, framing = null, official = false, camera = false, deferPlayback = false } = {},
  ) {
    const current = S.current;
    if (
      current &&
      (current.core !== core ||
        current.mod !== mod ||
        !!current.official !== !!official ||
        current.framing !== framing ||
        !current.meta)
    ) {
      S.lobbyLoads.cancel();
      clearView(host);
    }
    return S.sourceLoads.run(async (sourceSignal) => {
      if (!(await ensureSpine())) {
        if (!sourceSignal.aborted) clearView(host);
        return null;
      }
      if (sourceSignal.aborted) return null;
      let stopped = false;
      const url = spineBase(mod ? "mod" : official ? "official-lobby" : "lobby", mod || core);
      const abort = () => S.lobbyLoads.cancel();
      sourceSignal.addEventListener("abort", abort, { once: true });
      const run = async () => {
        let sources = await sourcesOf(core, mod, true, sourceSignal, official, camera);
        for (let attempt = 0; ; attempt++) {
          if (stopped || sourceSignal.aborted) return null;
          if (!sources.artBase)
            throw atStep(new Error("Lobby source unavailable"), url.replace(/skeleton$/, "manifest.json"), "manifest");
          const current = S.current;
          if (
            current?.meta &&
            current.artBase === sources.artBase &&
            current.base === sources.base &&
            current.framing === framing &&
            current.host === host &&
            !current.signal.aborted &&
            S.players.length &&
            !current.error
          )
            return current.ready;
          try {
            return await S.lobbyLoads.run((signal) =>
              showNow(core, host, variant, mod, sources, signal, framing, deferPlayback),
            );
          } catch (err) {
            if (sourceSignal.aborted || attempt) throw err;
            const fresh = await sourcesOf(core, mod, true, sourceSignal, official, camera);
            if (stopped || sourceSignal.aborted) return null;
            if (fresh.artBase === sources.artBase && fresh.base === sources.base) throw err;
            console.warn("Lobby source changed during load", url, err);
            sources = fresh;
          }
        }
      };
      try {
        return await withTimeout(run(), 15000, "Detail lobby", { signal: sourceSignal });
      } catch (err) {
        stopped = true;
        if (!sourceSignal.aborted) {
          S.lobbyLoads.cancel();
          failView(err, host, url, "load");
        }
        throw err;
      } finally {
        sourceSignal.removeEventListener("abort", abort);
      }
    });
  }
  async function showNow(core, host, variant, mod, sources, signal, framing, deferPlayback = false) {
    clearView(host);
    if (!sources.artBase) throw new Error("Lobby source unavailable");
    const { base } = sources;
    const my = ++S.token;
    host.innerHTML =
      '<canvas class="lobby-extend" hidden></canvas><div class="lobby-view"></div><canvas class="lobby-fade"></canvas><div class="lobby-frost"></div><div class="lobby-subtitle" hidden></div>';
    const ext = host.querySelector(".lobby-extend"),
      viewHost = host.querySelector(".lobby-view"),
      fade = host.querySelector(".lobby-fade");
    ext.width = EXT_W;
    ext.height = EXT_H;
    fade.width = STAGE.w;
    fade.height = STAGE.h;
    S.current = {
      core,
      mod,
      ...sources,
      variant,
      host,
      signal,
      framing,
      deferPlayback,
      suspended: deferPlayback,
      inputWasInert: !!host.inert,
      info: null,
      meta: null,
      view: null,
      target: null,
      viewAt: null,
      visualLoads: new Map(),
      resumeAudio: new Set(),
      pendingSounds: [],
    };
    if (deferPlayback) host.inert = true;
    if (deferPlayback) {
      const c = S.current;
      c.lifecycle = previewLifecycle({
        suspend: () => {
          if (S.current === c) {
            c.backgroundPaused = true;
            L.freezeScene(c);
          }
        },
        resume: () => {
          if (S.current === c) {
            c.backgroundPaused = false;
            if (!c.suspended) L.resumeScene(c);
          }
        },
      });
      c.backgroundPaused = !c.lifecycle.active();
    }
    {
      // a lobby on screen stops under Now Loading (its voices, timers and intro included) and goes on afterwards
      const c = S.current;
      c.loadingFreeze = freezeWhileLoading({
        shown: () => S.current === c && !!c.revealed && !c.signal.aborted && onScreen(c.host),
        freeze: () => {
          if (S.current === c) {
            c.loadingPaused = true;
            L.freezeScene(c);
          }
        },
        thaw: () => {
          if (S.current === c) {
            c.loadingPaused = false;
            L.resumeScene(c);
          }
        },
      });
    }
    // Cancel the composition before individual load listeners, releasing shared-context guests first.
    S.current.cancel = () => {
      if (S.current?.signal === signal) clearView(host);
    };
    signal.addEventListener("abort", S.current.cancel, { once: true });
    S.talkIndex = -1;
    S.current.ready = (async () => {
      const owner = S.current;
      void dialogueModule()
        .then(({ createLobbyDialogue }) => {
          if (signal.aborted || my !== S.token) return;
          owner.caption = createLobbyDialogue({
            host: host.querySelector(".lobby-subtitle"),
            later: L.setTimeout,
            cancel: L.clearTimeout,
            geometry: () => ({
              info: owner.info,
              meta: owner.meta,
              view: owner.view,
              framing: owner.framing,
              full: S.dialogueFullscreen ?? S.mode === "full",
              bounds: S.dialogueViewBounds,
            }),
          });
          if (owner.captionLine) owner.caption.show(...owner.captionLine);
        })
        .catch((error) => {
          if (!signal.aborted && my === S.token) console.warn("Lobby dialogue unavailable", error);
        });
      const { info, layers } = await layersOf(core, mod, sources, framing, { signal });
      if (signal.aborted || my !== S.token) return null;
      if (!layers.length) throw new Error("No lobby skeletons found");
      S.current.info = info;
      L.setView(L.game(), true); // the first frames are already in the game's framing
      const fx = (S.current.fx =
        info?.fx?.systems?.length || info?.fx?.renderers?.length || info?.fx?.sprites?.length
          ? Particles.create(info.fx, { base, bone: L.boneOf, load: deferPlayback ? visualImage : undefined })
          : null);
      // every layer drawn with the bottom one's context (share), or each with its own
      const build = async (share) =>
        (
          await Promise.all(
            layers.map(async (layer) => {
              const load = makePlayer(viewHost, layer, fx, share ? S.players[0] : null, signal);
              if (load.player) {
                S.players.push(load.player);
                S.playerLoads.set(load.player, load);
              }
              try {
                return (await load.ready) ? { ...layer, player: load.player } : null;
              } catch (err) {
                throw atStep(err, `${layer.spine}.skel`, "player");
              }
            }),
          )
        ).filter(Boolean);
      const all = await build(layers.length > 1);
      if (signal.aborted || my !== S.token) return null;
      // the lobby as it rests: without the scenes only its intro shows
      const loaded = all.filter((l) => !l.introOnly);
      if (!loaded.length) throw new Error("No lobby skeletons loaded");
      if (S.current.error) throw S.current.error;
      Stage.install(
        all.map((l) => l.player),
        fx,
      );
      for (const l of all) if (Stage.hostOf(l.player)) lightUp(l.player); // the guests, with their host loaded
      S.current.meta = L.measure(loaded);
      for (const v of [S.current.meta.left, S.current.meta.full])
        if (!validRect(rectOf(v))) throw new Error("Invalid lobby framing");
      S.current.loaded = loaded;
      S.current.all = all;
      // the bottom layer over the camera's clear colour (graded with PP on); sparse art keeps the page behind it, for
      // the edge extension
      Linear.setClear(Stage.hostOf(loaded[0].player) || loaded[0].player, !S.current.meta.sparse);
      L.setView(L.viewOf(S.current.meta), true);
      L.feather(S.current.view);
      const main = loaded.find((l) => l.name === S.current.meta.main)?.player;
      S.current.touch = {
        players: loaded.map((l) => l.player),
        main,
        info,
        toWorld: L.toWorld,
        zoomAt: L.zoomAt,
        panBy: L.panBy,
        playClip: L.playClip,
        clearClip: L.clearClip,
        next: L.next,
        viewTarget: L.viewTarget,
        setViewTarget: L.setViewTarget,
        talking: () => !!S.talk,
        stopTalk: L.stop,
        blocked: () => S.Idle.blocking > 0,
        pause: () => S.Idle.pause(),
        resume: () => S.Idle.resume(),
        interrupt: () => S.Idle.interrupt(),
      };
      if (!deferPlayback) L.restoreTouch();
      S.Idle.start(loaded.map((l) => l.player));
      L.openEdge();
      // the intro, from the first frame the lobby is revealed on screen (reveal); with the setting off, its end at once
      if (info?.intro) {
        if (!Number.isFinite(info.intro.duration) || info.intro.duration < 0 || info.intro.duration > 300)
          throw new Error("Invalid lobby intro duration");
        const c = S.current;
        c.intro = Intro.create(info.intro, {
          layers: all,
          fx,
          post: info.post,
          host,
          bone: L.boneOf,
          sfx: (id, set) => S.Sfx.play(id, set),
          screen: (p) => Linear.setScreen(p, layerViewport(L.game(), p.bamtLayer)),
          // one target for the skeletons drawn with the bottom player's context: its clear stays
          clear: (p, on) => Linear.setClear(Stage.hostOf(p) || p, on && !c.meta.sparse),
          show: (p, on) => Stage.setShown(p, on),
          visible: () =>
            !!c.preparingFrame ||
            (!L.paused() &&
              !!c.revealed &&
              (host.checkVisibility ? host.checkVisibility({ opacityProperty: true }) : !host.hidden)),
        });
        if (!S.introOn) c.intro.skip();
        else if (!S.muted && S.volume > 0) for (const s of info.intro.sounds || []) S.Sfx.prefetch(s.id); // in time for it
      }
      // with Full Art or Unlocked: the limits over every clip, once the screen is up
      L.setTimeout(() => {
        if (L.measuresClips() && my === S.token) L.measureClips();
      }, 400);
      return S.current.meta;
    })();
    try {
      return await S.current.ready;
    } catch (err) {
      if (my === S.token) clearView(host);
      throw atStep(err, `${sources.artBase}files.json`, "compose");
    }
  }

  function recoverView(c) {
    if (S.current !== c || c.recovering || c.signal.aborted) return;
    c.recovering = true;
    const revealed = c.revealed;
    S.lobbyLoads
      .run(async (signal) => {
        try {
          const meta = await showNow(
            c.core,
            c.host,
            c.variant,
            c.mod,
            { base: c.base, artBase: c.artBase, official: c.official },
            signal,
            c.framing,
            c.deferPlayback,
          );
          if (meta && !signal.aborted && revealed) {
            if (c.deferPlayback && !c.suspended) L.resume();
            else if (!c.deferPlayback) L.reveal();
          }
          return meta;
        } catch (err) {
          if (!signal.aborted) failView(err, c.host, `${c.artBase}files.json`, "context recovery");
        }
      })
      .catch((err) => console.error("Lobby recovery failed", err));
  }

  function failView(err, host, url, step) {
    previewError(err, url, step);
    clearView(host);
    const message = document.createElement("div");
    message.className = "lobby-unavailable";
    message.setAttribute("role", "status");
    message.textContent = T("detail.failed");
    host.appendChild(message);
  }
  /** Show that the lobby cannot be shown. */
  function unavailable(err, host, url, step = "detail") {
    S.sourceLoads.cancel();
    S.lobbyLoads.cancel();
    failView(err, host, url, step);
  }

  // the lobby's players and what each holds on the GPU, gone
  function dropPlayers() {
    for (const p of S.players) p.stopRendering?.();
    // Guests release their resources before the shared context's owner.
    for (const p of [...S.players].reverse()) {
      try {
        S.current?.fx?.detach(p);
        Materials.detach(p);
        Linear.detach(p);
      } catch (err) {
        console.error(err);
      } finally {
        try {
          S.playerLoads.get(p)?.dispose();
        } catch (err) {
          console.error(err);
        }
        p.parent?.remove();
      }
    }
    S.players = [];
  }

  /** Hide the lobby in a host and free its players. */
  function hide(host) {
    S.sourceLoads.cancel();
    S.lobbyLoads.cancel();
    clearView(host);
  }
  function clearView(host) {
    S.token += 1;
    L.clearTimeout(S.current?.watchdog);
    if (S.current?.cancel) S.current.signal.removeEventListener("abort", S.current.cancel);
    L.releaseTouch();
    S.current?.lifecycle?.dispose();
    S.current?.loadingFreeze?.dispose();
    if (S.current?.host) S.current.host.inert = !!S.current.inputWasInert;
    S.Idle.stop();
    S.current?.intro?.dispose(); // its sounds, its depth of field's targets
    dropPlayers();
    S.current?.fx?.dispose();
    S.current?.caption?.dispose();
    S.current = null;
    clearInterval(S.extendTimer);
    S.chain = null;
    L.endTalk(false);
    S.Sfx.clear();
    L.clearTimers();
    if (host) {
      host.innerHTML = "";
      host.classList.remove("open-edge");
    }
  }

  return { makePlayer, show, recoverView, failView, unavailable, hide };
}
