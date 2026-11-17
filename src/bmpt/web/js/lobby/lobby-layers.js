// The files of a memorial lobby: where its art and its prefab layout are served, and the layers they make, each
// skeleton paired with its atlas and placed as the prefab places it. Stateless, so every lobby instance shares it.
import { forgetSpineAssets, spineBase, spineAssetBase } from "../core/api.js";
// every wait of the lobby is a preview's: counted in the foreground, without the time a read waits for the library
import { withPreviewTimeout as withTimeout } from "../core/async.js";
import { atStep } from "../detail/preview-error.js";

const isBg = (n) => /background|_bg\b|bg_/i.test(n);

/**
 * Resolve the revision URLs of a lobby's art and of its prefab layout.
 * @param {string|null} core The lobby's core.
 * @param {string|null} mod The mod whose art is shown, or null for the game's.
 * @param {boolean} [refresh] Forget the manifests read before, so a shown tab reads its source afresh.
 * @param {AbortSignal|null} [signal] Cancels the reads.
 * @param {boolean} [official] Show the official lobby, not the installed one.
 * @param {boolean} [camera] The mod is a lobby camera, whose layout is its own prefab's
 *     (/asset/spine/mod/<id>/lobby.json).
 * @returns {Promise<{artBase: string|null, base: string|null, official: boolean}>} The art's and the layout's folders.
 */
export async function sourcesOf(core, mod, refresh = false, signal = null, official = false, camera = false) {
  const artSource = spineBase(mod ? "mod" : official ? "official-lobby" : "lobby", mod || core);
  const layoutSource = mod && camera ? artSource : core ? spineBase("lobby", core) : null;
  // A shown tab must not inherit a manifest request that belonged to a cancelled or previous source.
  if (refresh)
    for (const source of new Set([artSource, layoutSource].filter(Boolean)))
      forgetSpineAssets(source.slice(0, source.lastIndexOf("/")));
  const art = spineAssetBase(artSource, { signal });
  const layout = !layoutSource
    ? Promise.resolve(null)
    : (mod && !camera) || official
      ? spineAssetBase(layoutSource, { signal })
      : art;
  const dir = (base) => base?.slice(0, base.lastIndexOf("/") + 1) || null;
  const [artBase, base] = await Promise.all([art, layout]);
  return { artBase: dir(artBase), base: dir(base), official };
}

/**
 * Read the layers of a lobby in the prefab's draw order with its offsets and scales; skeletons the prefab keeps
 * inactive (talk-only scenes) are left out. Without prefab data every skeleton sits at the standard place,
 * backgrounds first.
 * @param {object|null} [mod]
 * @param {object|null} [sources]
 * @param {object|null} [framing]
 * @param {object} [options]
 * @param {boolean} [options.camera] Read the camera's art.
 * @returns {Promise<object[]>} The layers: name, offset, scale, data and files.
 */
export async function layersOf(
  core,
  mod = null,
  sources = null,
  framing = null,
  { signal, official = false, camera = false } = {},
) {
  const { artBase, base } = sources || (await sourcesOf(core, mod, false, signal, official, camera));
  if (!artBase) throw new Error(`Lobby source unavailable: ${core}`);
  const read = (url) =>
    withTimeout(
      fetch(url, { signal }).then((r) => {
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
      }),
      15000,
      url,
      { signal },
    ).catch((err) => {
      throw atStep(err, url, "metadata");
    });
  const [files, info] = await Promise.all([read(`${artBase}files.json`), base ? read(`${base}lobby.json`) : null]);
  if (!Array.isArray(files) || files.some((f) => typeof f !== "string")) throw new Error("Invalid lobby file list");
  const atlasOf = pairAtlases(files),
    skels = [...atlasOf.keys()];
  const match = (n) => skels.find((s) => s.toLowerCase() === n.toLowerCase());
  const defaults = (name, index) => ({
    name,
    file: name,
    x: 0,
    y: framing ? 0 : -962,
    scale: 1,
    index,
    skin: null,
    animation: null,
  });
  const unpaired = files.filter((f) => f.endsWith(".skel") && !atlasOf.has(f.slice(0, -5)));
  if (unpaired.length) throw new Error(`No unambiguous lobby atlas: ${unpaired.join(", ")}`);
  // equal sorting orders (akari_home before akari_bg in the prefab): background-named skeletons first; the scenes only
  // the intro shows (Akari's, Aru's) are loaded too, hidden until it shows them (introOnly)
  let list = (info?.layers || [])
    .filter((l) => (l.active || l.intro) && match(l.name))
    .map((l) => ({ ...l, file: match(l.name), introOnly: !l.active }))
    .sort((a, b) => a.order - b.order || isBg(b.name) - isBg(a.name) || a.index - b.index);
  if (!list.length) {
    list = skels.sort((a, b) => isBg(b) - isBg(a) || a.localeCompare(b)).map(defaults);
  } else {
    // Repacked mods can rename or add their character while retaining the official background.
    const known = new Set((info?.layers || []).map((l) => match(l.name)));
    const added = skels.filter((s) => !known.has(s));
    const missing = (info?.layers || []).filter((l) => l.active && !match(l.name));
    if (missing.length && !added.length)
      throw new Error(`Missing lobby layers: ${missing.map((l) => l.name).join(", ")}`);
    list.push(...added.map(defaults));
  }
  for (const l of list)
    if (![l.x, l.y, l.scale].every(Number.isFinite) || l.scale <= 0)
      throw new Error(`Invalid lobby layer transform: ${l.name}`);
  return {
    info,
    layers: list.map((l) => ({
      ...l,
      spine: `${artBase}${encodeURIComponent(l.file)}`,
      atlas: `${artBase}${encodeURIComponent(atlasOf.get(l.file))}.atlas`,
    })),
  };
}
/**
 * Pair a lobby's skeleton files with their atlases: the atlas of the same name, else of the same name in
 * another case (Juri: juri_home.skel, Juri_home.atlas), else the one atlas left for the one skeleton left
 * (CH9996_home.skel with CH0996_home.atlas); the game pairs them through the SkeletonDataAsset, not by name.
 * Skeletons without one are left out.
 * @param {string[]} files
 * @returns {Map<string, string>} Skeleton name to atlas name.
 */
export function pairAtlases(files) {
  const skels = files.filter((f) => f.endsWith(".skel")).map((f) => f.slice(0, -5));
  const atlases = files.filter((f) => f.endsWith(".atlas")).map((f) => f.slice(0, -6));
  const out = new Map(),
    free = new Set(atlases);
  for (const s of skels)
    if (free.has(s)) {
      out.set(s, s);
      free.delete(s);
    }
  for (const s of skels) {
    if (out.has(s)) continue;
    const a = [...free].find((x) => x.toLowerCase() === s.toLowerCase());
    if (a) {
      out.set(s, a);
      free.delete(a);
    }
  }
  const left = skels.filter((s) => !out.has(s));
  if (left.length === 1 && free.size === 1) out.set(left[0], [...free][0]);
  return out;
}
