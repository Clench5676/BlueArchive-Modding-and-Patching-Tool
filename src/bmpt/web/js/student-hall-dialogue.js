// Ordinary LobbyCommon/ChatDialog/Talk balloon; original NGUI sprite, font and padding follow the clicked actor.
import * as NGUI from "./ui/ngui.js";
import { rpc as defaultRpc } from "./core/api.js";
import { language } from "./core/i18n.js";
import { S } from "./core/util.js";
import { talkExpression } from "./student-hall-portraits.js";
import { spriteBorders, sliceAxis, slicePoint } from "./ui/sprite-slices.js";
import { dialogueText } from "./detail/dialogue-text.js";
import { freezeWhileLoading } from "./detail/preview-lifecycle.js";

const LANGS = { "en-US": "en", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" };
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const PREPARE_TIMEOUT = 25000,
  TEXT_LIFETIME = 5000;
const UNAVAILABLE_BACKOFF = 30000;
const MISSING_DIALOGUE = new Set(["lines_unavailable", "catalog_variant_not_found", "variant_not_found"]);
// Common/Lobby_balloon's 136 x 146 atlas silhouette: the rounded body is [38,4]..[128,129] at half alpha.
// Its left-pointing tail and translucent outer shadow are outside this rectangle. These are geometry, not pixels.
const BALLOON_BODY = {
  width: 136,
  height: 146,
  rect: [38, 4, 128, 129],
  borderLeft: 80,
  borderRight: 50,
  borderTop: 84,
  borderBottom: 60,
};

/**
 * Make a bounded in-memory cache of results by key, shared by calls in flight.
 * @param {number} [maxBytes] The most bytes kept.
 * @returns
 *     {{get: (key: string, read: (signal?: AbortSignal) => Promise<*>, size?: (value: *) => number, signal?: AbortSignal|null) => Promise<*>}}
 *     get reads a value once per key, or gives the cached one.
 */
export function memoryMemo(maxBytes = 32 * 1024 * 1024, maxEntries = 128) {
  const values = new Map(),
    flights = new Map();
  let bytes = 0,
    alive = true;
  return {
    async get(key, read, size = (value) => JSON.stringify(value).length * 2, signal = null) {
      const cached = values.get(key);
      if (cached && cached.expires > Date.now()) {
        values.delete(key);
        values.set(key, cached);
        return cached.value;
      }
      if (cached) {
        values.delete(key);
        bytes -= cached.size;
      }
      const running = flights.get(key);
      if (running && !running.signal?.aborted) return running.promise;
      const pending = Promise.resolve()
        .then(() => {
          if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
          return read();
        })
        .then((value) => {
          const length = size(value);
          if (alive && !signal?.aborted && length <= maxBytes) {
            values.set(key, {
              value,
              size: length,
              expires:
                value?.pending || value?.animationPending
                  ? Date.now() + 1500
                  : value?.unavailable
                    ? Date.now() + UNAVAILABLE_BACKOFF
                    : Infinity,
            });
            bytes += length;
            while (bytes > maxBytes || values.size > maxEntries) {
              const first = values.keys().next().value;
              bytes -= values.get(first).size;
              values.delete(first);
            }
          }
          return value;
        })
        .finally(() => {
          if (flights.get(key)?.promise === pending) flights.delete(key);
        });
      flights.set(key, { promise: pending, signal });
      return pending;
    },
    forgetPending(key) {
      const cached = values.get(key);
      if (cached?.value?.pending || cached?.value?.animationPending) {
        values.delete(key);
        bytes -= cached.size;
      }
    },
    clear() {
      alive = false;
      values.clear();
      flights.clear();
      bytes = 0;
    },
    size: () => bytes,
  };
}

/**
 * Break a dialogue's text into lines that fit a width.
 * @param {(line: string, fontSize: number) => number} measure
 * @param {string} [lang] The language tag, for the break rules.
 * @returns {string[]}
 */
export function wrapDialogue(text, fontSize, maxWidth, measure, lang = "en") {
  const segmenter = typeof Intl.Segmenter === "function" ? new Intl.Segmenter(lang, { granularity: "word" }) : null;
  const graphemes = typeof Intl.Segmenter === "function" ? new Intl.Segmenter(lang, { granularity: "grapheme" }) : null;
  const lines = [];
  for (const paragraph of String(text).split(/\r?\n/)) {
    const words = segmenter ? [...segmenter.segment(paragraph)].map((item) => item.segment) : [...paragraph];
    let line = "";
    for (const word of words) {
      const parts =
        measure(word, fontSize) > maxWidth
          ? graphemes
            ? [...graphemes.segment(word)].map((item) => item.segment)
            : [...word]
          : [word];
      for (const part of parts) {
        if (
          line &&
          measure(line + part, fontSize) > maxWidth &&
          !/^[\u3001\u3002\uff0c\uff01\uff1f\uff1b\uff1a,.!?;:)]/.test(part)
        ) {
          lines.push(line.trimEnd());
          line = part.trimStart();
        } else line += part;
      }
    }
    lines.push(line.trimEnd());
  }
  return lines;
}

/**
 * Place a Talk balloon beside a portrait's rect, inside the bounds.
 * @param {{x: number, y: number, width: number, height: number}} rect The portrait's rect on the stage.
 * @param {{width: number, height: number}} size
 * @param {{left: number, top: number, right: number, bottom: number}} [bounds] The room the balloon may take.
 * @returns {{x: number, y: number, side: string}} The balloon's position and the side of the portrait it is on.
 */
export function balloonPosition(rect, size, bounds = { left: 24, top: 36, right: 1896, bottom: 1044 }) {
  const right = bounds.right - rect.x - rect.width,
    left = rect.x - bounds.left;
  const flip = right < size.width + 16 && left > right;
  return {
    x: clamp(flip ? rect.x - size.width - 16 : rect.x + rect.width + 16, bounds.left, bounds.right - size.width),
    y: clamp(rect.y + rect.height * 0.28 - size.height * 0.25, bounds.top, bounds.bottom - size.height),
    flip,
  };
}

/**
 * The size and flip of a balloon's body sprite.
 * @param {{w_: number, h_: number, flip?: number}} sprite
 * @param {{width: number, height: number}} [meta] The atlas sprite's size.
 */
export function balloonBody(sprite, meta = BALLOON_BODY) {
  const [top, right, bottom, left] = spriteBorders(meta);
  const x = sliceAxis(meta.width, left, right, sprite.w_ * S, S);
  const y = sliceAxis(meta.height, top, bottom, sprite.h_ * S, S);
  const [x0, y0, x1, y1] = BALLOON_BODY.rect;
  const box = {
    left: slicePoint((x0 * meta.width) / BALLOON_BODY.width, x) / S,
    right: slicePoint((x1 * meta.width) / BALLOON_BODY.width, x) / S,
    top: slicePoint((y0 * meta.height) / BALLOON_BODY.height, y) / S,
    bottom: slicePoint((y1 * meta.height) / BALLOON_BODY.height, y) / S,
  };
  if ((sprite.flip || 0) & 1) [box.left, box.right] = [sprite.w_ - box.right, sprite.w_ - box.left];
  return box;
}

/**
 * Build a Talk balloon's tree for a text, placed beside a rect.
 * @param {{x: number, y: number, width: number, height: number}} rect
 * @param {object} [options]
 * @param {object} [options.ui] The NGUI module, for the tests.
 * @param {object} [options.bounds] The room the balloon may take.
 */
export function prepareBalloon(template, text, rect, { ui = NGUI, lang = "en", bounds, at } = {}) {
  const tree = structuredClone(template),
    spriteNode = ui.find(tree, "Sprite"),
    labelNode = ui.find(tree, "Label");
  const sprite = spriteNode.widgets.find((widget) => "sprite" in widget),
    label = labelNode.widgets.find((widget) => "text" in widget);
  const left = labelNode.pos[0] - spriteNode.pos[0],
    top = spriteNode.pos[1] - labelNode.pos[1];
  const right = sprite.w_ - left - label.w_,
    bottom = sprite.h_ - top - label.h_;
  const fontSize = label.fontSize;
  const lineHeight = Math.max(
    label.h_ / Math.max(1, String(label.text).split("\n").length),
    (ui.fontSizeFor?.(text, fontSize) || fontSize) * 1.15,
  );
  const measure = ui.glyphWidth;
  const natural = Math.max(
    ...String(text)
      .split(/\r?\n/)
      .map((line) => measure(line, fontSize)),
  );
  let width = clamp(natural > 600 ? Math.ceil(natural / 2) : natural, 260, 840);
  let lines = wrapDialogue(text, fontSize, width, measure, lang);
  while (!String(text).includes("\n") && lines.length > 2 && width < 840 && natural <= 1680) {
    width = Math.min(840, width + fontSize);
    lines = wrapDialogue(text, fontSize, width, measure, lang);
  }
  const contentWidth = Math.max(90, ...lines.map((line) => measure(line, fontSize)));
  const contentHeight = Math.max(lineHeight, lineHeight * lines.length);
  label.text = lines.join("\n");
  label.w_ = contentWidth;
  label.h_ = contentHeight;
  label.lang = lang;
  sprite.w_ = left + contentWidth + right;
  sprite.h_ = top + contentHeight + bottom;
  const size = { width: sprite.w_ * S, height: sprite.h_ * S };
  const position = at ? clampBalloon(at, size, bounds) : balloonPosition(rect, size, bounds);
  sprite.flip = position.flip ? (sprite.flip || 0) ^ 1 : sprite.flip || 0;
  const body = balloonBody(sprite, ui.atlasIndex?.()?.[sprite.atlas]?.[sprite.sprite]);
  // A centred UILabel invokes NGUI's existing font-baseline correction. The original top-left pivot cannot
  // compensate for the browser's taller line box and leaves the visible text below the balloon's body centre.
  label.pivot = 4;
  label.align = 2;
  labelNode.pos = [spriteNode.pos[0] + (body.left + body.right) / 2, spriteNode.pos[1] - (body.top + body.bottom) / 2];
  tree.pos = [0, 0];
  return { tree, ...size, ...position };
}

/**
 * Make the Hall's dialogue player: the talk caption, voice and balloon for a placed portrait.
 * @param {object} [options]
 * @param {HTMLElement} options.host The Hall's element the balloons go into.
 * @param {(entry: object) => object|null} options.actorOf Gives a placed portrait's player.
 * @param {(entry: object) => object|null} [options.choiceOf] Gives a portrait's source choice.
 * @param {() => object} [options.info] Gives the Hall's information.
 * @param {() => object} [options.prefs]
 * @param {(error: Error) => void} [options.onError]
 * @param {(method: string, params: object) => Promise<*>} [options.call] The RPC call, for the tests.
 * @param {object} [options.ui] The NGUI module, for the tests.
 * @param {(url: string) => HTMLAudioElement} [options.audioFactory] Makes an audio element, for the tests.
 * @param {Function} [options.expressions] Picks the face a talk plays, for the tests.
 * @param {() => string} [options.currentLanguage]
 * @param {object} [options.bounds] The room the balloons may take.
 * @param {(error: Error, entry: object) => void} [options.onDiagnostic]
 * @returns
 *     {{play: Function, stop: Function, dispose: Function, reposition: Function, relabel: () => Promise<void>}}
 *     The controls.
 */
export function createDialogue({
  host,
  actorOf,
  choiceOf = () => null,
  info = () => ({}),
  prefs = () => ({}),
  onError = () => {},
  signal,
  call = defaultRpc,
  ui = NGUI,
  audioFactory = (url) => new Audio(url),
  expressions = talkExpression,
  currentLanguage = language,
  bounds,
  onDiagnostic = (error, entry) => console.warn("Portrait dialogue unavailable", entry.variant, error),
} = {}) {
  const linesCache = memoryMemo(4 * 1024 * 1024, 128),
    audioCache = memoryMemo(),
    turns = new Map();
  const unavailable = new Map();
  let token = 0,
    drawSequence = 0,
    disposed = false,
    busy = false,
    audio = null,
    url = null,
    clearTimer = null,
    prepareTimer = null;
  let speaker = null,
    bubble = null,
    bubbleFlip = false,
    positionTimer = null,
    expressionKey = null;
  let template = null,
    layoutFlight = null,
    current = null,
    requests = null;
  const preferences = () => (typeof prefs === "function" ? prefs() : prefs);
  const information = () => (typeof info === "function" ? info() : info);
  const muted = () => information()?.mute || preferences()?.voiceMuted || (preferences()?.voiceVolume ?? 80) === 0;
  // under Now Loading the voice and the caption's lifetime stop, and go on from where they were afterwards
  let clearDue = 0,
    clearLeft = null,
    resumeVoice = false;
  const loadingFreeze = freezeWhileLoading({
    shown: () => !disposed && (!!(audio && !audio.paused) || !!clearTimer),
    freeze: () => {
      if (audio && !audio.paused) {
        audio.pause();
        resumeVoice = true;
      }
      if (clearTimer) {
        clearTimeout(clearTimer);
        clearTimer = null;
        clearLeft = Math.max(0, clearDue - performance.now());
      }
    },
    thaw: () => {
      if (resumeVoice && audio) audio.play().catch(() => {});
      resumeVoice = false;
      if (clearLeft != null && !disposed) {
        clearDue = performance.now() + clearLeft;
        clearTimer = setTimeout(stop, clearLeft);
      }
      clearLeft = null;
    },
  });
  function releaseAudio() {
    if (audio) {
      audio.onended = audio.onerror = audio.ontimeupdate = audio.onseeked = null;
      audio.pause();
      audio = null;
    }
    if (url) {
      URL.revokeObjectURL(url);
      url = null;
    }
  }
  function stop() {
    token++;
    drawSequence++;
    busy = false;
    requests?.abort();
    requests = null;
    clearTimeout(clearTimer);
    clearTimeout(prepareTimer);
    clearTimeout(positionTimer);
    clearTimer = prepareTimer = positionTimer = null;
    releaseAudio();
    clearLeft = null;
    resumeVoice = false;
    if (speaker) expressions(speaker, false);
    speaker = current = expressionKey = null;
    bubble?.remove();
    bubble = null;
    host?.replaceChildren();
  }
  function finishText(talking = false) {
    // Cancel preparation without releasing the Hall-wide turn until the readable text expires.
    token++;
    requests?.abort();
    requests = new AbortController();
    clearTimeout(prepareTimer);
    prepareTimer = null;
    releaseAudio();
    if (talking && current) applyExpression(current.row);
    else if (speaker) {
      expressions(speaker, false);
      expressionKey = null;
    }
    clearTimeout(clearTimer);
    resumeVoice = false;
    if (loadingFreeze.held()) {
      clearTimer = null;
      clearLeft = TEXT_LIFETIME;
    } else {
      clearDue = performance.now() + TEXT_LIFETIME;
      clearTimer = setTimeout(stop, TEXT_LIFETIME);
    }
  }
  function applyExpression(row, player = null) {
    const elapsed = Number(player?.currentTime) || 0;
    const cue = row.expressionCues?.filter((value) => Number.isFinite(value.time) && value.time <= elapsed).at(-1);
    const expression = cue?.motion || cue?.expression || row.expression;
    if (!expression || !speaker) return;
    const key = JSON.stringify(expression);
    if (key !== expressionKey) {
      expressions(speaker, true, expression);
      expressionKey = key;
    }
  }
  const actor = (entry) => {
    const found = actorOf?.(entry);
    return {
      actor: found?.actor || found,
      rect: found?.rect || {
        x: entry.x - 195 * entry.scale,
        y: entry.y - 850 * entry.scale,
        width: 390 * entry.scale,
        height: 850 * entry.scale,
      },
    };
  };
  const waitPending = (milliseconds, requestSignal) =>
    new Promise((resolve) => {
      if (requestSignal.aborted) {
        resolve(false);
        return;
      }
      let timer;
      const finish = () => {
        clearTimeout(timer);
        requestSignal.removeEventListener("abort", finish);
        resolve(!requestSignal.aborted);
      };
      timer = setTimeout(finish, milliseconds);
      requestSignal.addEventListener("abort", finish, { once: true });
    });
  async function draw(entry, row, my, requestSignal) {
    if (my !== token || disposed || signal?.aborted || requestSignal?.aborted) return;
    const version = ++drawSequence;
    const selected = dialogueText(row, LANGS[currentLanguage()] || "en");
    if (!selected) {
      bubble?.remove();
      bubble = null;
      host.replaceChildren();
      return;
    }
    if (!template && (!layoutFlight || layoutFlight.signal?.aborted)) {
      const flight = { signal: requestSignal };
      flight.promise = Promise.resolve()
        .then(() => call("hall.dialogueLayout", {}, { signal: requestSignal }))
        .then((value) => {
          if (!requestSignal?.aborted) template = value;
          return value;
        })
        .finally(() => {
          if (layoutFlight === flight) layoutFlight = null;
        });
      layoutFlight = flight;
    }
    const saved = template || (await layoutFlight.promise);
    if (my !== token || version !== drawSequence || disposed || signal?.aborted) return;
    await ui.init?.();
    if (my !== token || version !== drawSequence || disposed || signal?.aborted) return;
    const latest = dialogueText(row, LANGS[currentLanguage()] || "en");
    if (!latest || latest.text !== selected.text || latest.language !== selected.language)
      return draw(entry, row, my, requestSignal);
    const placement = prepareBalloon(saved, selected.text, actor(entry).rect, { ui, lang: selected.language, bounds });
    const scratch = document.createElement("div");
    Object.assign(scratch.style, {
      position: "absolute",
      left: `${placement.x}px`,
      top: `${placement.y}px`,
      width: `${placement.width}px`,
      height: `${placement.height}px`,
      pointerEvents: "none",
    });
    scratch.setAttribute("role", "status");
    scratch.setAttribute("aria-label", selected.text);
    await ui.render(placement.tree, scratch, { scale: S, origin: [0, 0] });
    if (my !== token || version !== drawSequence || disposed || signal?.aborted) return;
    const after = dialogueText(row, LANGS[currentLanguage()] || "en");
    if (!after || after.text !== selected.text || after.language !== selected.language)
      return draw(entry, row, my, requestSignal);
    bubble?.remove();
    bubble = scratch;
    bubbleFlip = placement.flip;
    host.replaceChildren(scratch);
  }
  function reposition() {
    if (!current || !bubble || disposed) return;
    const placement = balloonPosition(
      actor(current.entry).rect,
      { width: parseFloat(bubble.style.width), height: parseFloat(bubble.style.height) },
      bounds,
    );
    Object.assign(bubble.style, { left: `${placement.x}px`, top: `${placement.y}px` });
    clearTimeout(positionTimer);
    positionTimer = null;
    if (placement.flip !== bubbleFlip) {
      const my = token;
      positionTimer = setTimeout(() => {
        positionTimer = null;
        if (my === token && current)
          draw(current.entry, current.row, my, requests?.signal).catch((error) => onError(error));
      }, 120);
    }
  }
  async function play(entry) {
    if (busy || disposed || signal?.aborted) return;
    const lang = LANGS[currentLanguage()] || "en",
      cacheKey = `${entry.variant}:${lang}`;
    if ((unavailable.get(cacheKey) || 0) > Date.now()) return;
    unavailable.delete(cacheKey);
    busy = true;
    const controller = (requests = new AbortController()),
      requestSignal = controller.signal;
    const my = token,
      active = () => my === token && !disposed && !signal?.aborted && !requestSignal.aborted;
    const noDialogue = (error = null) => {
      unavailable.set(cacheKey, Date.now() + UNAVAILABLE_BACKOFF);
      while (unavailable.size > 128) unavailable.delete(unavailable.keys().next().value);
      stop();
      if (error) onDiagnostic(error, entry);
    };
    // Network, image decoding and autoplay promises can all stall. Once audio has started only its end/error
    // releases the turn; stalled preparation retains any caption already obtained for one bounded reading period.
    prepareTimer = setTimeout(() => {
      if (!active()) return;
      if (current) finishText();
      else noDialogue({ code: "lines_unavailable", reason: "prepare_timeout" });
    }, PREPARE_TIMEOUT);
    try {
      const delays = [500, 1500, 4000, 9000];
      const caption = (value) => JSON.stringify(dialogueText(value, LANGS[currentLanguage()] || "en"));
      let clip = null,
        row = null,
        metadataPending = false;
      for (let attempt = 0; ; attempt++) {
        let response;
        try {
          response = await linesCache.get(
            cacheKey,
            () => call("hall.lines", { variant: entry.variant, lang }, { signal: requestSignal }),
            undefined,
            requestSignal,
          );
        } catch (error) {
          if (row && active()) break;
          throw error;
        }
        if (!active()) return;
        metadataPending = !!response.pending || !!response.animationPending;
        const table = response.lines || response,
          keys = Object.keys(table)
            .filter(
              (key) =>
                /_lobby_\d/.test(key) &&
                (table[key]?.voice ||
                  Object.values(LANGS).some(
                    (lang) => typeof table[key]?.[lang] === "string" && table[key][lang].trim(),
                  )),
            )
            .sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
        if (!clip && keys.length) {
          const index = turns.get(entry.id) || 0;
          clip = keys[index % keys.length];
          turns.set(entry.id, index + 1);
          speaker = actor(entry).actor;
        }
        if (clip && !table[clip]) {
          // Text-only official groups can acquire their actual voice-name prefix once the source finishes loading.
          const suffix = clip.match(/_lobby_\d+(?:_\d+)?$/)?.[0];
          const equivalent = keys.filter((key) => suffix && key.endsWith(suffix));
          if (equivalent.length === 1) clip = equivalent[0];
        }
        if (clip && table[clip]) {
          const next = table[clip],
            changed = !row || caption(row) !== caption(next);
          row = next;
          current = { entry, row, clip };
          if (changed) await draw(entry, row, my, requestSignal);
        }
        if (!active()) return;
        if (row?.voice || (row && muted()) || !response.pending || attempt >= delays.length) break;
        if (!(await waitPending(delays[attempt], requestSignal)) || !active()) return;
        // Recheck in-flight backend metadata, even while its short frontend pending TTL has not elapsed.
        linesCache.forgetPending(cacheKey);
      }
      if (!active()) return;
      if (!row) {
        noDialogue();
        return;
      }
      if (!row.voice || muted()) {
        finishText(true);
        return;
      }
      let data;
      try {
        data = await audioCache.get(
          `${entry.variant}:${clip}`,
          async () => {
            const response = await call("hall.voice", { variant: entry.variant, clip }, { signal: requestSignal });
            return { bytes: Uint8Array.from(atob(response.audio), (char) => char.charCodeAt(0)), mime: response.mime };
          },
          (value) => value.bytes.byteLength,
          requestSignal,
        );
      } catch {
        if (active()) finishText();
        return;
      }
      if (!active()) return;
      const now = preferences();
      if (muted()) {
        finishText();
        return;
      }
      url = URL.createObjectURL(new Blob([data.bytes], { type: data.mime }));
      const player = (audio = audioFactory(url));
      player.volume = clamp((now?.voiceVolume ?? 80) / 100, 0, 1);
      player.onended = () => {
        if (active()) stop();
      };
      player.onerror = () => {
        if (active()) finishText();
      };
      player.ontimeupdate = player.onseeked = () => {
        if (active()) applyExpression(current.row, player);
      };
      try {
        await player.play();
        if (!active()) {
          player.pause();
          return;
        }
        if (loadingFreeze.held() && audio === player) {
          player.pause();
          resumeVoice = true;
        }
        clearTimeout(prepareTimer);
        prepareTimer = null;
        applyExpression(row, player);
        // Captions and local/Kivo audio may be ready before the Global animation metadata. Enrich this same
        // playing line without restarting its voice, replacing its bubble or consuming another dialogue turn.
        if (metadataPending)
          void (async () => {
            for (const delay of delays) {
              if (!(await waitPending(delay, requestSignal)) || !active()) return;
              linesCache.forgetPending(cacheKey);
              const response = await linesCache.get(
                cacheKey,
                () => call("hall.lines", { variant: entry.variant, lang }, { signal: requestSignal }),
                undefined,
                requestSignal,
              );
              if (!active()) return;
              const next = response.lines?.[clip];
              if (next) {
                const before = caption(current.row);
                current.row = { ...current.row, ...next };
                applyExpression(current.row, player);
                if (caption(current.row) !== before) await draw(entry, current.row, my, requestSignal);
              }
              if (!response.pending && !response.animationPending) return;
            }
          })().catch((error) => {
            if (active()) onDiagnostic(error, entry);
          });
      } catch {
        if (active()) finishText();
      }
    } catch (error) {
      if (active()) {
        if (MISSING_DIALOGUE.has(error?.code)) noDialogue(error);
        else {
          stop();
          onError(error, entry);
        }
      }
    }
  }
  function dispose() {
    if (disposed) return;
    stop();
    disposed = true;
    loadingFreeze.dispose();
    signal?.removeEventListener("abort", dispose);
    linesCache.clear();
    audioCache.clear();
    turns.clear();
    unavailable.clear();
    template = layoutFlight = null;
  }
  signal?.addEventListener("abort", dispose, { once: true });
  return {
    play,
    stop,
    dispose,
    reposition,
    relabel: async () => {
      if (!current) return;
      const shown = current,
        my = token,
        requestSignal = requests?.signal,
        lang = LANGS[currentLanguage()] || "en";
      try {
        await draw(shown.entry, shown.row, my, requestSignal);
        if (my !== token || disposed || requestSignal?.aborted || lang !== (LANGS[currentLanguage()] || "en")) return;
        const response = await linesCache.get(
          `${shown.entry.variant}:${lang}`,
          () => call("hall.lines", { variant: shown.entry.variant, lang }, { signal: requestSignal }),
          undefined,
          requestSignal,
        );
        if (my !== token || disposed || requestSignal?.aborted || lang !== (LANGS[currentLanguage()] || "en")) return;
        shown.row = response.lines?.[shown.clip] || shown.row;
        await draw(shown.entry, shown.row, my, requestSignal);
      } catch {
        /* The current line remains readable when another translation is unavailable. */
      }
    },
    preferencesChanged() {
      const settings = preferences();
      if (busy && muted()) {
        if (current) finishText();
        else stop();
      } else if (audio) audio.volume = clamp((settings?.voiceVolume ?? 80) / 100, 0, 1);
    },
  };
}

/**
 * Convert a lobby point from prefab-root coordinates (gameassets/lobby.py's single transform walk) to the
 * stage.
 * @param {{x: number, y: number}} point The point in lobby units.
 * @param {{x: number, y: number, w: number, h: number}} view
 * @returns {{x: number, y: number}} The point in stage pixels, y down.
 */
export const lobbyPoint = (point, view) => ({
  x: ((point.x - view.x) * 1920) / view.w,
  y: ((view.y + view.h - point.y) * 1080) / view.h,
});
/**
 * Keep a balloon inside bounds.
 * @param {{x: number, y: number}} at
 * @param {{width: number, height: number}} size
 * @param {{left: number, top: number, right: number, bottom: number}} bounds
 */
export function clampBalloon(at, size, bounds) {
  return {
    x: clamp(at.x, bounds.left, bounds.right - size.width),
    y: clamp(at.y, bounds.top, bounds.bottom - size.height),
    flip: false,
  };
}
const FULL_STAGE = Object.freeze({ left: 0, top: 0, right: 1920, bottom: 1080 });
/**
 * Build a Talk balloon for a memorial lobby. Fullscreen keeps it by the head: the detail panel is gone, so the
 * whole stage bounds it.
 * @param {object} options
 * @param {object} options.bounds The room the balloon may take.
 * @param {object} [options2]
 * @param {object} [options2.ui] The NGUI module, for the tests.
 * @returns {object} The balloon: tree, size and position.
 */
export function lobbyBalloon(
  template,
  text,
  { info, meta, view, framing, full, bounds },
  { ui = NGUI, lang = "en" } = {},
) {
  const point = info?.dialog?.Talk;
  const at = !framing && !meta.sparse && point?.length === 2 ? lobbyPoint({ x: point[0], y: point[1] }, view) : null;
  const touch = lobbyPoint(meta.touch, view);
  return {
    ...prepareBalloon(
      template,
      text,
      { ...touch, width: 0, height: 0 },
      {
        ui,
        lang,
        bounds: full ? FULL_STAGE : bounds,
        at: at && Number.isFinite(at.x) && Number.isFinite(at.y) ? at : null,
      },
    ),
    scale: S,
  };
}

/**
 * Make the memorial lobby's dialogue player: the memorial's leave and enter sequence, rendering the same Talk
 * helper as the Hall in every view.
 * @param {object} [options]
 * @param {HTMLElement} options.host The element the balloon goes into.
 * @param {() => object} options.geometry Gives the lobby's geometry (info, meta, view, framing, full, bounds).
 * @param {(method: string, params: object) => Promise<*>} [options.call] The RPC call, for the tests.
 * @param {object} [options.ui] The NGUI module, for the tests.
 * @param {Function} [options.later] setTimeout, for the tests.
 * @param {Function} [options.cancel] clearTimeout, for the tests.
 * @param {Function} [options.raf] requestAnimationFrame, for the tests.
 */
export function createLobbyDialogue({
  host,
  geometry,
  call = defaultRpc,
  ui = NGUI,
  later = setTimeout,
  cancel = clearTimeout,
  raf = requestAnimationFrame,
} = {}) {
  let template = null;
  let text = "",
    lang = "en",
    timer = null,
    version = 0,
    disposed = false,
    rendered = null,
    key = "",
    geometryKey = "",
    entering = false,
    motion = 0;
  host.setAttribute("role", "status");
  host.style.pointerEvents = "none";
  async function draw() {
    const g = geometry();
    if (!text || !g?.meta || !g.view || disposed) return;
    const value = text,
      language = lang,
      turn = version;
    template ||= Promise.resolve(call("hall.dialogueLayout", {})).catch((error) => {
      template = null;
      throw error;
    });
    const talk = await template;
    await ui.init?.();
    if (disposed || turn !== version || value !== text || language !== lang) return;
    const placement = lobbyBalloon(talk, text, g, { ui, lang });
    const nextKey = JSON.stringify([text, lang, placement.flip, placement.width, placement.height]);
    const position = { left: `${placement.x}px`, top: `${placement.y}px` };
    if (key === nextKey) {
      if (rendered) Object.assign(rendered.style, position);
      return;
    }
    key = nextKey;
    const my = ++version;
    const scratch = document.createElement("div");
    Object.assign(scratch.style, { position: "absolute", ...position, pointerEvents: "none" });
    await ui.render(placement.tree, scratch, { scale: placement.scale, origin: [0, 0] });
    if (disposed || my !== version) return;
    rendered = scratch;
    host.replaceChildren(scratch);
    if (entering) {
      entering = false;
      const transition = motion;
      raf(() =>
        raf(() => {
          if (!disposed && transition === motion) host.classList.remove("in");
        }),
      );
    }
  }
  const redraw = () => {
    void draw().catch((error) => {
      key = "";
      console.warn("Lobby dialogue unavailable", error);
    });
  };
  function content(value, language, animate = false) {
    entering = !!value && animate;
    motion++;
    version++;
    text = value;
    lang = language;
    key = "";
    rendered = null;
    host.replaceChildren();
    host.setAttribute("aria-label", text);
    host.hidden = !text;
    if (text) redraw();
  }
  return {
    show(value, immediate = false, language = "en") {
      cancel(timer);
      timer = null;
      if (immediate) {
        content(value, language);
        host.classList.remove("in", "out");
        return;
      }
      const enter = () => {
        content(value, language, true);
        host.classList.remove("out");
        host.classList.add("in");
      };
      if (!host.hidden && text) {
        host.classList.add("out");
        timer = later(() => {
          content("", language);
          if (value) enter();
        }, 260);
      } else if (value) enter();
      else content("", language);
    },
    reposition() {
      const g = geometry(),
        next = JSON.stringify([
          !!g?.full,
          g?.view,
          g?.bounds,
          g?.meta?.touch,
          g?.info?.dialog?.Talk,
          g?.framing,
          g?.meta?.sparse,
        ]);
      if (next !== geometryKey) {
        geometryKey = next;
        redraw();
      }
    },
    dispose() {
      disposed = true;
      version++;
      cancel(timer);
      host.replaceChildren();
    },
  };
}
