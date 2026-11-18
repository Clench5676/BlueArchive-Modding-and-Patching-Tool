// The dialogues and sounds of one memorial lobby instance (memorial-lobby.js createMemorialLobby): the talk clips with
// the game's own tracks and mixes, their voices and one subtitle line per voice cue (lines and voices come from Kivo,
// setLines), play all, and the sounds the clips' sound keys play. The functions take the instance's state S and
// functions L; this module keeps no state of its own.
import { language } from "../core/i18n.js";
import { clamp } from "./memorial-lobby-measure.js";

const TALK_GUARD_MS = 500; // a tap does not skip a dialogue that started this recently

/**
 * The dialogue and sound functions of a memorial lobby instance.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The functions this part adds to L.
 */
export function talkPart(S, L) {
  // --- dialogues: the talk clip (Talk_NN_M, its sync Talk_NN_A) with the game's mixes, the voices and one subtitle
  // line per voice cue ---
  /**
   * Play a dialogue: the talk clip (Talk_NN_M, its sync Talk_NN_A) with the game's mixes, the voices and one
   * subtitle line per voice cue, then back to idle. A dialogue starts at once, cutting off whatever was playing.
   * @param {string} anim The talk animation's name.
   * @param {object} [options]
   * @param {boolean} [options.fromChain] The dialogue follows another in play all.
   * @returns {object|null} The dialogue's sequence; null when it cannot play (no memorial lobby, or suspended).
   */
  function play(anim, { fromChain = false } = {}) {
    const m = S.current?.meta;
    if (!m || S.current?.suspended || S.current?.backgroundPaused || S.current?.loadingPaused) return null;
    S.current.intro?.skip(); // a dialogue comes after the intro, as a tap that skips it
    if (!fromChain) S.chain = null;
    const i = m.sequences.findIndex((s) => s.anim === anim),
      seq = m.sequences[i],
      p = L.mainPlayer();
    if (!seq || !p?.animationState) return null;
    endTalk(false); // the new talk takes over tracks 1 and 2
    S.Idle.interrupt();
    L.returnToIdle(p);
    S.talkIndex = i;
    const my = (S.talk = { seq, started: performance.now(), cues: [] });
    for (const cue of seq.cues) {
      S.cueTimers.push(
        L.setTimeout(() => {
          if (S.talk !== my) return;
          my.cues.push(cue.clip);
          const text = S.lineOf(S.lines[cue.clip]);
          if (text) subtitle(text);
          if (!S.muted && S.lines[cue.clip]?.voice) voice(cue.clip);
        }, cue.t * 1000),
      );
    }
    S.cueTimers.push(L.setTimeout(() => finishTalk(my), seq.duration * 1000 + 300));
    L.playClip(p, seq.anim);
    emit();
    return seq;
  }
  function voice(clip) {
    if (S.current?.suspended || S.current?.backgroundPaused || S.current?.loadingPaused) return;
    const a = new Audio(`/asset/voice/${encodeURIComponent(S.current.alt)}/${encodeURIComponent(clip)}.ogg`);
    a.volume = S.volume;
    S.audios.push(a);
    a.play().catch(() => {});
  }
  function finishTalk(my) {
    if (S.talk !== my) return;
    endTalk(false);
    L.returnToIdle(L.mainPlayer());
    const run = S.chain;
    if (run && S.current?.meta) {
      const seq = S.current.meta.sequences[++run.i];
      if (seq) {
        L.setTimeout(() => {
          if (S.chain === run) play(seq.anim, { fromChain: true });
        }, 200);
        return;
      }
      S.chain = null;
    }
    emit();
  }
  function endTalk(fade) {
    for (const t of S.cueTimers) L.clearTimeout(t);
    S.cueTimers = [];
    silence();
    subtitle("");
    if (S.talk && fade) L.clearClip(L.mainPlayer(), S.talk.seq.anim);
    S.talk = null;
  }

  /** Stop at once: the talk tracks fade back to idle, voices and subtitle go. */
  function stop() {
    const was = !!S.talk || !!S.chain; // play all may sit between two dialogues
    S.chain = null;
    const talking = !!S.talk;
    endTalk(true);
    if (talking) L.returnToIdle(L.mainPlayer());
    if (was) emit();
  }
  /**
   * Play the next dialogue after the last one played (a tap on the body); a dialogue that has only just started
   * is not cut.
   */
  function next() {
    const seqs = S.current?.meta?.sequences;
    if (
      !seqs?.length ||
      S.current?.suspended ||
      S.current?.backgroundPaused ||
      S.current?.loadingPaused ||
      (S.talk && performance.now() - S.talk.started < TALK_GUARD_MS)
    )
      return;
    S.chain = null;
    play(seqs[(S.talkIndex + 1) % seqs.length].anim);
  }
  /**
   * Play every dialogue in order, each starting when the previous one ends.
   * @param {string|null} [selected]
   */
  function playAll(selected = null) {
    const seqs = S.current?.meta?.sequences;
    if (!seqs?.length || S.current?.suspended || S.current?.backgroundPaused || S.current?.loadingPaused) return;
    const run = {
      i: Math.max(
        0,
        seqs.findIndex((seq) => seq.anim === selected),
      ),
    };
    S.chain = run;
    play(seqs[run.i].anim, { fromChain: true });
  }
  /** Whether play all is running. */
  const playingAll = () => !!S.chain;
  /**
   * The dialogue in play.
   * @returns {string|null} Its talk animation, or null.
   */
  const playing = () => S.talk?.seq.anim || null;
  /**
   * The shown memorial lobby's meta (frames, sequences).
   * @returns {object|null} The meta, or null.
   */
  const meta = () => S.current?.meta || null;
  /**
   * Subscribe to dialogues: cb(anim or null, playingAll) is called whenever a dialogue starts, ends or stops.
   * @returns {() => void} Unsubscribes the listener.
   */
  function onTalk(cb) {
    S.listeners.add(cb);
    return () => S.listeners.delete(cb);
  }
  function emit() {
    const anim = S.talk?.seq.anim || null,
      all = !!S.chain;
    for (const cb of S.listeners)
      try {
        cb(anim, all);
      } catch (e) {
        console.error(e);
      }
  }

  function silence() {
    for (const a of S.audios) {
      a.pause();
      S.current?.resumeAudio?.delete(a);
    }
    S.audios = [];
  }

  /**
   * Set the lines by clip id ({clip: {lang: text, voice}}: Kivo's Chinese and Japanese, the Global server's
   * English and others) and the picker of the UI language's line ("" when it has none: a cue without one leaves
   * the subtitle as it is, since one row of the game's text can cover two clips).
   * @param {Object<string, object>} table
   * @param {(line: object) => string} pick Picks the UI language's text.
   */
  function setLines(table, pick) {
    // Undefined changes only the picker while translated rows load; keep voice availability for upcoming cues.
    if (table !== undefined) S.lines = table || {};
    if (pick) S.lineOf = pick;
    // Re-evaluate only cues already reached. A text row may cover several voice cues, so a blank later cue keeps
    // the latest translated row. Playback, voice audio and the remaining cue timers continue unchanged.
    let text = "";
    for (const clip of S.talk?.cues || []) text = S.lineOf(S.lines[clip]) || text;
    subtitle(text, true);
  }
  /**
   * Set the voice volume, which the clips' sounds follow too; muted never plays voices or sounds.
   * @param {number} value The volume, 0 to 1.
   */
  function setVolume(value, mute) {
    S.volume = Math.max(0, Math.min(1, value));
    S.muted = !!mute;
    for (const a of S.audios) a.volume = S.volume;
    S.Sfx.follow();
    if (S.muted) {
      silence();
      S.Sfx.stop();
    }
  }

  const sfx = (id) => S.Sfx.play(id);
  // a track entry's sound keys (on every entry a clip sets, as in the game), their clips fetched ahead
  function soundsOf(p, entry, c) {
    if (!entry || !c?.sounds?.length || !p?.bamtSounds) return;
    p.bamtSounds.watch(entry, c.sounds);
    if (!S.muted && S.volume > 0) for (const [, id] of c.sounds) S.Sfx.prefetch(id);
  }
  /**
   * For checks: the sound keys played lately.
   * @returns {object[]}
   */
  const soundLog = () => S.Sfx.log.slice();
  /**
   * A sequence's lines, one per voice clip.
   * @param {{cues: {clip: string}[]}} seq
   * @returns {string[]}
   */
  const textOf = (seq) => seq.cues.map((c) => S.lineOf(S.lines[c.clip])).filter(Boolean);

  // one subtitle line at a time: the old one leaves upwards and the next rises in from below
  function subtitle(text, immediate = false) {
    if (!S.current) return;
    if (!text) {
      S.current.captionLine = ["", immediate];
      S.current.caption?.show(...S.current.captionLine);
      return;
    }
    // A Student Hall fallback may use another language than the page. Preserve the selected row's actual language.
    const preferred = { "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" }[language()] || language();
    const rows = (S.talk?.cues || []).map((clip) => S.lines[clip]).reverse();
    const row = rows.find((value) => value && Object.values(value).includes(text));
    const lang =
      row?.[preferred] === text ? preferred : Object.keys(row || {}).find((key) => row[key] === text) || preferred;
    S.current.captionLine = [text, immediate, lang];
    S.current.caption?.show(...S.current.captionLine);
  }

  return {
    play,
    endTalk,
    stop,
    next,
    playAll,
    playingAll,
    playing,
    meta,
    onTalk,
    setLines,
    setVolume,
    sfx,
    soundsOf,
    soundLog,
    textOf,
  };
}

/**
 * The sounds of a memorial lobby instance's clips (SpineClip.SoundKeys, played by memorial-lobby-sync.js): an
 * AnimationAudioEvent plays one of its clips at random, at its volume and at its pitch, a playback rate as Unity's
 * pitch is. The clips come from the game files (memorial-lobby.json sounds, /asset/spine/memorialLobby/<core>/sfx_<clip>.wav),
 * fetched once a clip with sound keys starts and kept as blobs for the memorial lobby; the game's SFX volume is the
 * app's voice volume.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The instance's Sfx: load, prefetch, play, follow, stop, clear, and what plays and played.
 */
export function createSfx(S, L) {
  return {
    urls: new Map(),
    playing: new Set(),
    log: [],
    load(clip) {
      if (!this.urls.has(clip) && S.current) {
        this.urls.set(
          clip,
          fetch(`${S.current.base}sfx_${encodeURIComponent(clip)}.wav`)
            .then((r) => (r.ok ? r.blob() : null))
            .then((b) => (b ? URL.createObjectURL(b) : null))
            .catch(() => null),
        );
      }
      return this.urls.get(clip) || Promise.resolve(null);
    },
    prefetch(id) {
      for (const clip of S.current?.info?.sounds?.[id]?.clips || []) this.load(clip);
    },
    // also: the set the audio joins while it plays (the intro's, which a skip stops)
    play(id, also = null) {
      if (L.paused()) {
        S.current?.pendingSounds.push([id, also]);
        return;
      }
      const ev = S.current?.info?.sounds?.[id];
      this.log.push({ event: id, name: ev?.name || null, at: Math.round(performance.now()) });
      if (this.log.length > 40) this.log.shift();
      if (!ev?.clips?.length || S.muted || !(S.volume > 0)) return;
      const clip = ev.clips[Math.floor(Math.random() * ev.clips.length)],
        my = S.token;
      this.load(clip).then((url) => {
        if (!url || my !== S.token || S.muted || also?.closed) return; // a skipped intro's sound still loading never plays
        const a = new Audio(url);
        a.bamtOwnerSet = also;
        a.bamtVolume = ev.volume ?? 1;
        a.volume = clamp(a.bamtVolume * S.volume, 0, 1);
        a.preservesPitch = a.webkitPreservesPitch = a.mozPreservesPitch = false;
        a.defaultPlaybackRate = a.playbackRate = ev.pitch || 1;
        this.playing.add(a);
        also?.add(a);
        a.onended = () => {
          this.playing.delete(a);
          also?.delete(a);
        };
        if (L.paused()) S.current.resumeAudio.add(a);
        else
          a.play().catch(() => {
            if (!S.current?.resumeAudio?.has(a)) {
              this.playing.delete(a);
              also?.delete(a);
            }
          });
      });
    },
    follow() {
      for (const a of this.playing) a.volume = clamp(a.bamtVolume * S.volume, 0, 1);
    }, // a new voice volume
    stop() {
      for (const a of this.playing) {
        a.pause();
        S.current?.resumeAudio?.delete(a);
      }
      this.playing.clear();
    },
    clear() {
      this.stop();
      for (const url of this.urls.values()) url.then((u) => u && URL.revokeObjectURL(u));
      this.urls.clear();
      this.log = [];
    },
  };
}
