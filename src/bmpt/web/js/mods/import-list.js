// The import page's list (batch-import.js createImportPage): the rows, one per entry or per merged group, with their
// copy, state badge, details and checkbox; the thumbnails of the incoming and current art; the preview on the left with
// its source and character controls; and the empty-list message. The factory takes the page's state P and the page
// functions it calls, and keeps no state of its own.
import { importStatus } from "./switch-sources.js";
import { importGroups } from "./import-groups.js";
import * as NGUI from "../ui/ngui.js";
import { spineBase, spinePrefix } from "../core/api.js";
import { T, language } from "../core/i18n.js";
import { specialFraming } from "../detail/special-view.js";
import { forget } from "../detail/thumbs.js";
import { S } from "../core/util.js";
import {
  ROW_SCALE,
  ROW_WIDTH,
  ROW_HEIGHT,
  ROW_SELECTION,
  ROW_CHECK_TOP,
  PAPER_CENTRE,
  emptyBlock,
  controlRect,
  badgeRect,
} from "./import-layout.js";
import { hasDetails, detailLines, withoutFile } from "./import-details.js";
import { eligible, missingText, statusText, needsChoose, chooseSize, CHOOSE, sourceFolder } from "./import-state.js";
import { el, place } from "./import-controls.js";

// The list's top in the panel: 12 under Items' grey Inbox (its top edge is at 98). The search field, open, takes
// its place and pushes the rows down by SEARCH_ROOM.
export const LIST_TOP = 110,
  SEARCH_ROOM = 92;
// The list's box in the panel (px): where it starts, how wide it is and where it ends; the search field and the
// tools under it share its width.
export const LIST_X = 26,
  LIST_W = 1220,
  LIST_BOTTOM = 616;
// the caption key of each kind of Mod an entry can be
export const KIND_KEYS = {
  portrait: "tab.portrait",
  memorialLobby: "tab.memorialLobby",
  chibi: "tab.chibi",
  other: "import.other",
};
// the reasons a row can carry that have their own label (import.reason.*)
export const REASONS = new Set([
  "unreadable",
  "no_target",
  "conflict",
  "spine_version",
  "unsupported",
  "source_unknown",
  "source_conflict",
  "platform_unsupported",
  "target_unknown",
  "conversion_names",
  "no_matching_assets",
  "conversion_failed",
  "mod_platform_unknown",
  "library_io_error",
  "mod_target_ambiguous",
  "mod_target_unknown",
  "mod_parts_missing",
  "game_file_conflict",
  "mod_platform_mismatch",
  "mod_unity_version_mismatch",
  "mod_release_mismatch",
  "required_asset_extract_failed",
  "required_asset_apply_failed",
  "required_assets_unmatched",
  "entry_changed",
  "official_invalid",
  "failed",
]);
const OUTCOMES = new Set(["imported", "updated", "unchanged", "failed", "skipped"]);
const sizeText = (size) =>
  size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${Math.ceil((size || 0) / 1024)} KB`;
const listSeparator = () => (/^(zh|ja)/i.test(language()) ? "、" : ", ");
const EMPTY_BOX = [1200, 100]; // each empty-list label's box (px), its text centred in it

/**
 * Make the import page's list.
 * @param {object} P The page's state.
 * @param {object} page The page's import state, services and the page functions the list calls.
 * @returns {object} The list's functions: layoutList, renderRows, renderPreview, prepareVisibleRows, rowCopy,
 *     paragraphCopy, representative, thumbnail, bindTexture, refreshCurrentPreviews and refreshAppliedPreviews.
 */
export function createImportList(P, page) {
  const { importState, ui, popups, allowed, chibiThumbnails, controls } = page;
  const { identified, report, resultNote, tracked, showError, openSource, openCharacter } = page;
  const { paint, textWidget, button, checkbox, bindBar, syncBar } = controls;
  // a refused row's reason is on its badge (stateText); the note line keeps only what a badge cannot say: a
  // missing official copy's repair guidance
  const rowNote = (entry) =>
    entry.state === "blocked" && entry.reason === "official_unavailable" ? report(entry, entry) : "";
  // the badge's short reason: the row's reason label, the error's own text when it needs no parameters, or Import Check
  // Failed (the details give the rest)
  const ANY_PARAMETER = new Proxy({}, { get: () => "\u0000" });
  const badgeReason = (entry) => {
    if (REASONS.has(entry.reason)) return T(`import.reason.${entry.reason}`);
    const key = `error.${entry.reason}`,
      text = T(key, ANY_PARAMETER);
    return text !== key && !text.includes("\u0000") ? text : T("import.reason.failed");
  };
  // A refused row's badge opens its reason and what the check read of its files (import-details.js).
  const detailReason = (entry) => {
    if (entry.state === "incomplete") return statusText(entry.missing);
    const detail = entry.error || { code: entry.reason, params: entry.params || {} },
      label = report(entry, entry);
    const key = `error.${detail.code}`,
      text = detail.code ? withoutFile(T, key, detail.params || {}) : key;
    return text && text !== key && text !== label ? `${label} - ${text}` : label;
  };
  const showDetails = (entries) =>
    popups.message({
      title: T("info.details"),
      scroll: true,
      ok: T("picker.ok"),
      lines: entries.flatMap((entry) =>
        detailLines(entry, { T, separator: listSeparator(), reason: detailReason(entry) }),
      ),
    });

  function layoutList() {
    const top = P.searchOpen ? LIST_TOP + SEARCH_ROOM : LIST_TOP;
    P.dom.searchWrap.hidden = !P.searchOpen;
    P.dom.searchToggle.setAttribute("aria-expanded", String(P.searchOpen));
    place(P.dom.list, LIST_X, top, LIST_W, LIST_BOTTOM - top);
    bindBar(P.dom.list, P.dom.panel, [LIST_X, top, LIST_W, LIST_BOTTOM - top]);
    P.listTop = top;
    placeEmpty();
  }
  // Items' ListEmpty message, with the drop hint under it while nothing is added: one block whose ink centre sits
  // on the middle of the grey frame (diagonal centre, header strip and toolbar included), wherever the list
  // starts.
  function placeEmpty() {
    if (!P.dom.empty) return;
    const [x, y] = PAPER_CENTRE(S),
      { title, note } = P.dom.empty;
    const ink = (part) => part && NGUI.inkBand(part.text, part.px);
    const at = emptyBlock(y - P.listTop, ink(title), ink(note));
    for (const [part, centre] of [
      [title, at.title],
      [note, at.note],
    ]) {
      if (part) place(part.element, x - LIST_X - EMPTY_BOX[0] / 2, centre - EMPTY_BOX[1] / 2, ...EMPTY_BOX);
    }
  }

  // Pictures stay in the actual prefab UITexture widgets; no separate DOM picture/frame is assembled.
  function setTexture(image, source, large) {
    if (!source) return;
    image.dataset.source = source;
    const surface = image.firstElementChild || image;
    Object.assign(surface.style, {
      backgroundImage: `url(${JSON.stringify(source)})`,
      backgroundSize: large ? "contain" : "cover",
      backgroundPosition: large ? "center top" : "center",
      backgroundRepeat: "no-repeat",
    });
  }
  function bindTexture(drawing, path, source, className, large = false) {
    const token = P.generation;
    // Retain the UITexture rectangle while its generated still is pending. No catalog/game texture is shown first.
    drawing
      .then((view) => {
        const image = view.nodes.get(path)?.els[0];
        if (image) {
          image.classList.add(className);
          if (!view.nodes.get(path).node.widgets.some((w) => "clipImage" in w))
            Object.assign(image.style, { overflow: "hidden", clipPath: "inset(0)" });
        }
      })
      .catch(() => {});
    return tracked(
      Promise.all([drawing, source]).then(async ([view, url]) => {
        const image = view.nodes.get(path)?.els[0];
        // Rows may still be in the staging tree. Generation guards prevent a
        // departed page from publishing; detached obsolete rows are never shown.
        if (image && P.visible && token === P.generation) {
          setTexture(image, url, large);
          await ui.decoded(image, 15000, null, { signal: P.scope?.signal });
        }
        return image;
      }),
    );
  }
  function incomingSource(entry) {
    // Analysis previews remain valid for the immutable input session after
    // import. Changing the label/status must not reload the same picture from
    // a new library endpoint (conversion output is validated separately).
    const result = importState.state.results.find((result) => result.id === entry.id);
    if (entry.preview?.kind === "import" && entry.preview.key && (!entry.libraryId || result?.libraryId))
      return entry.preview;
    const libraryId = result?.libraryId || entry.libraryId;
    if (libraryId) return { kind: "mod", key: libraryId };
    return null;
  }
  function incomingBase(entry) {
    const source = incomingSource(entry);
    return source && ["portrait", "memorialLobby"].includes(entry.resourceType)
      ? spineBase(source.kind, source.key)
      : null;
  }
  const representative = (entries) =>
    entries.find((entry) => eligible(entry) && incomingBase(entry)) ||
    entries.find((entry) => eligible(entry) && incomingSource(entry)) ||
    entries.find(eligible) ||
    entries.find((entry) => incomingSource(entry)) ||
    entries[0];
  const liveIdentity = (entry, alt) =>
    JSON.stringify([
      entry.resourceType,
      entry.resourceType === "chibi"
        ? alt?.key || entry.alt
        : entry.resourceType === "portrait"
          ? alt?.portrait || entry.core
          : alt?.memorialLobby || entry.core,
    ]);
  function refreshCurrentPreviews() {
    for (const [key, version] of P.liveVersions) P.liveVersions.set(key, version + 1);
    for (const key of P.thumbnailJobs.keys()) {
      const [kind, base, incoming] = JSON.parse(key);
      if (incoming) continue;
      P.thumbnailJobs.delete(key);
      if (base && ["portrait", "memorialLobby"].includes(kind)) forget(base.slice(0, base.lastIndexOf("/")));
    }
    P.previewKey = null;
  }
  function refreshAppliedPreviews() {
    for (const result of importState.state.results) {
      if (!result.applied || P.refreshedResults.has(result)) continue;
      P.refreshedResults.add(result);
      const entry = importState.state.taskEntries.find((item) => item.id === result.id);
      if (!entry) continue;
      const { alt } = identified(entry),
        key = liveIdentity(entry, alt);
      P.liveVersions.set(key, (P.liveVersions.get(key) || 0) + 1);
      if (["portrait", "memorialLobby"].includes(entry.resourceType)) {
        const core = entry.resourceType === "portrait" ? alt?.portrait || entry.core : alt?.memorialLobby || entry.core;
        if (core) forget(spinePrefix(entry.resourceType, core));
      }
    }
  }
  function thumbnail(entry, { incoming = false, large = false } = {}) {
    const { alt } = identified(entry);
    const signal = P.scope?.signal;
    const kind = entry.resourceType;
    const key =
      kind === "portrait"
        ? alt?.portrait || entry.core
        : kind === "memorialLobby"
          ? alt?.memorialLobby || entry.core
          : null;
    const base = incoming ? incomingBase(entry) : key ? spineBase(kind, key) : null;
    const input = incomingSource(entry),
      framing = specialFraming(alt);
    const frame = large ? (kind === "memorialLobby" ? "landscape" : "portrait") : "card";
    const liveKey = liveIdentity(entry, alt);
    if (!incoming && !P.liveVersions.has(liveKey)) P.liveVersions.set(liveKey, 0);
    const liveVersion = incoming ? 0 : P.liveVersions.get(liveKey);
    const version = JSON.stringify([incoming ? input : "live", entry.files, liveVersion]);
    const identity = JSON.stringify([
      kind,
      kind === "chibi" ? alt?.key || entry.alt : base,
      incoming,
      frame,
      framing,
      alt?.background,
      kind === "chibi" ? version : null,
      liveVersion,
      allowed(),
    ]);
    if (P.thumbnailJobs.has(identity)) return P.thumbnailJobs.get(identity);
    let work = Promise.resolve(null);
    const chibiKey = alt?.key || entry.alt;
    if (kind === "chibi" && chibiKey && P.scope) {
      const source = input;
      if (!incoming || source)
        work = chibiThumbnails(chibiKey, incoming ? `${source.kind}:${source.key}` : "live", {
          signal,
          version,
        });
    } else if (allowed() && base && P.scope) {
      work = P.scope.thumb(base, frame, null, kind === "memorialLobby", framing, alt?.background);
    }
    if (large) work.bump?.();
    const source = Promise.resolve(work)
      .then(async (url) => {
        if (url && ui.load) await ui.load(url, { signal });
        return url;
      })
      .catch(() => null)
      .then((url) => {
        if (!url && P.thumbnailJobs.get(identity) === source) P.thumbnailJobs.delete(identity);
        return url;
      });
    P.thumbnailJobs.set(identity, source);
    return source;
  }

  function prepareVisibleRows(container, records, fallbackHeight = 640) {
    const items = [...records],
      height = container.clientHeight || parseFloat(container.style.height) || fallbackHeight;
    const heights = items.map((record) => parseFloat(record.row.style.height) || record.height || ROW_HEIGHT);
    // A filtered staging tree can be shorter than the currently displayed
    // list. Prepare the viewport the browser will show after it clamps scroll.
    const top = Math.min(
      Math.max(0, container.scrollTop || 0),
      Math.max(0, heights.reduce((sum, h) => sum + h, 0) - height),
    );
    const work = [];
    let y = 0;
    for (const [index, record] of items.entries()) {
      const rowHeight = heights[index];
      if (y < top + height && y + rowHeight > top) work.push(record.prepare());
      y += rowHeight;
    }
    return Promise.allSettled(work).then(() => top);
  }

  function renderPreview() {
    const entry = importState.state.entries.find((item) => item.id === importState.state.selected);
    const key = JSON.stringify([
      entry?.id,
      entry?.libraryId,
      entry?.preview,
      entry?.source,
      entry?.target,
      importState.busy(),
      importState.state.phase,
      importState.state.results,
      allowed(),
    ]);
    if (key === P.previewKey) return;
    P.previewKey = key;
    const next = el("div", "import-detail-content");
    if (entry) {
      const identity = identified(entry);
      const landscape = entry.resourceType === "memorialLobby";
      // two pictures, side by side or stacked, above the name plate, whose foot meets the list's
      const width = landscape ? 444 : 254,
        height = landscape ? 250 : 466,
        headerHeight = landscape ? 30 : 44;
      for (const incoming of [false, true]) {
        const box = place(
          el("div", `import-preview${incoming ? " incoming" : ""}`),
          landscape ? 40 : incoming ? 270 : 0,
          landscape && incoming ? 296 : 0,
          width,
          headerHeight + height,
        );
        const source = thumbnail(entry, { incoming, large: true });
        const drawing = paint(
          box,
          P.art.preview(T(incoming ? "import.incoming" : "import.current"), {
            width: width / 0.46,
            height: height / 0.46,
            headerHeight: headerHeight / 0.46,
          }),
          {
            scale: 0.46,
            origin: [width / 2, 0],
            textures: { Texture: Promise.resolve(null) },
          },
        );
        bindTexture(drawing, "EquipPos/Texture", source, "import-preview-image", true);
        if (incoming && !incomingSource(entry)) {
          const note = place(el("div"), 0, headerHeight + height - 45, width, 45);
          note.style.zIndex = "20";
          box.appendChild(note);
          textWidget(note, T("import.previewUnavailable"), "empty", { scale: 0.4, origin: [width / 2, 23] });
        }
        next.appendChild(box);
      }
      const banner = place(el("div", "import-name-banner"), 0, 587, 524, 120);
      paint(banner, P.art.banner(identity.name, T(KIND_KEYS[entry.resourceType] || KIND_KEYS.other)), {
        scale: 0.42,
        origin: [255, 104],
      });
      next.appendChild(banner);
      if (
        !entry.libraryOnly &&
        entry.target &&
        (!entry.source?.server ||
          !entry.source?.platform ||
          ["server", "platform"].some((key) => entry.source[key] !== entry.target[key]))
      )
        next.appendChild(sourceControl(entry));
      if (
        !["done", "library", "installed"].includes(entry.state) &&
        (!entry.alt ||
          entry.assignedAlt ||
          ["mod_target_unknown", "mod_target_ambiguous", "no_target"].includes(entry.reason))
      ) {
        const assign = button(T("import.assignCharacter"), "assignCharacter", { width: 524, scale: 0.34 });
        place(assign, 0, 765, 524, 55);
        assign.disabled = importState.busy();
        assign.refresh();
        assign.addEventListener("click", () => openCharacter(entry).catch((error) => showError(report(error), error)));
        next.appendChild(assign);
      }
    }
    P.dom.preview.replaceChildren(next);
  }

  function sourceLabel(source) {
    if (!source || !source.platform) return T("import.source.unknown");
    return `${T(`settings.server.${source.server}`)} ${T(`import.source.${source.platform}`)}`;
  }
  function conversionRoutes(entries) {
    return [
      ...new Set(
        entries
          .filter(
            (item) =>
              item.source && item.target && ["server", "platform"].some((key) => item.source[key] !== item.target[key]),
          )
          .map((item) =>
            T("import.source.route", { source: sourceLabel(item.source), target: sourceLabel(item.target) }),
          ),
      ),
    ].join(listSeparator());
  }
  function sourceControl(entry) {
    const group = el("div", "import-source");
    const choose = button(T("import.source.choose"), "source", { width: 524, scale: 0.34 });
    place(choose, 0, 706, 524, 55);
    choose.disabled = importState.busy() || importState.state.phase === "done";
    choose.refresh();
    choose.addEventListener("click", () => openSource(entry).catch((error) => showError(report(error), error)));
    group.append(choose);
    return group;
  }

  const stateText = (entry) =>
    entry.state === "incomplete"
      ? statusText(entry.missing)
      : entry.state === "blocked" && entry.reason !== "source_unknown"
        ? `${T("import.state.blocked")} - ${badgeReason(entry)}`
        : T(
            entry.libraryOnly && eligible(entry)
              ? "import.libraryOnly"
              : entry.reason === "source_unknown"
                ? "import.reason.source_unknown"
                : `import.state.${entry.state}`,
          );

  function rowCopy(group, result = false) {
    const entry = representative(group.entries);
    // A grouped row in the list reads like an ungrouped one: its kinds as text on the metadata line, its states on
    // the status tag.
    const groupedRow = P.merged && !result;
    const kindCounts = () =>
      Object.entries(group.counts)
        .filter(([, n]) => n)
        .map(([kind, n]) => `${T(KIND_KEYS[kind] || KIND_KEYS.other)} ×${n}`)
        .join(listSeparator());
    return {
      result,
      kind: groupedRow ? "" : T(KIND_KEYS[entry.resourceType] || KIND_KEYS.other),
      unidentified: entry.reason === "source_unknown",
      state: groupedRow ? [...new Set(group.entries.map(stateText))].join(listSeparator()) : stateText(entry),
      counts: P.merged && result ? group.counts : null,
      badge: !result,
      meta: result
        ? [
            ...new Set(
              group.entries
                .map((entry) => importState.state.results.find((item) => item.id === entry.id))
                .map((item) => T(`import.outcome.${OUTCOMES.has(item?.outcome) ? item.outcome : "failed"}`)),
            ),
          ].join(listSeparator())
        : groupedRow
          ? kindCounts()
          : T(entry.files?.length === 1 ? "import.metaOne" : "import.meta", {
              id: identified(entry).core,
              n: entry.files?.length || 0,
              size: sizeText(entry.size),
            }),
    };
  }

  function paragraphCopy(group, result = false) {
    const notes = result
      ? group.entries.map((entry) => {
          const item = importState.state.results.find((item) => item.id === entry.id);
          if (item?.applyUnavailable) return T("import.libraryOnly");
          return item?.reason
            ? [item.configured && T(importStatus(item)), resultNote(item, entry)].filter(Boolean).join(" - ")
            : ["imported", "updated", "unchanged"].includes(item?.outcome)
              ? [T(importStatus(item)), item.kept_current && T("mods.keptCurrent")].filter(Boolean).join(" - ")
              : "";
        })
      : group.entries.map(rowNote);
    const omitted = result
      ? group.entries.reduce(
          (count, entry) =>
            count + (importState.state.results.find((item) => item.id === entry.id)?.notTransferred?.length || 0),
          0,
        )
      : 0;
    if (omitted) notes.push(T("import.notTransferredCount", { count: omitted }));
    return {
      ...rowCopy(group, result),
      route: conversionRoutes(group.entries),
      note: [...new Set(notes.filter(Boolean))].join(listSeparator()),
    };
  }

  function renderRows() {
    if (!P.visible || !P.root) return;
    const allGroups = importGroups(importState.state.entries, P.merged, identified);
    const groups = allGroups.filter((group) =>
      group.entries.some(
        (entry) => (P.filter === "all" || entry.state === P.filter) && identified(entry).search.includes(P.query),
      ),
    );
    const textSize = P.art.rowTextSize(groups.map((group) => paragraphCopy(group)));
    const entries = groups.map((group) => group.entries[0]);
    const collator = new Intl.Collator(language(), { numeric: true, sensitivity: "base" });
    groups.sort(
      (a, b) => (P.ascending ? 1 : -1) * collator.compare(identified(a.entries[0]).name, identified(b.entries[0]).name),
    );
    const key = JSON.stringify([P.merged, textSize, groups]);
    if (key !== P.rowsKey) {
      P.rowsKey = key;
      const previous = P.rows;
      P.rows = new Map();
      const token = ++P.listGeneration,
        page = P.generation;
      const contents = [];
      P.dom.empty = null;
      if (!entries.length) {
        const empty = el("div", "import-empty");
        const part = (caption) => {
          const element = el("div"),
            label = P.art.empty(caption).widgets.find((w) => "text" in w);
          textWidget(element, caption, "empty", { scale: 0.5, origin: [EMPTY_BOX[0] / 2, EMPTY_BOX[1] / 2] });
          empty.appendChild(element);
          return { element, text: label.text, px: label.fontSize * 0.5 };
        };
        P.dom.empty = {
          title: part(T(importState.state.entries.length ? "import.noMatch" : "import.empty")),
          note: importState.state.entries.length ? null : part(T("import.drop")),
        };
        placeEmpty();
        contents.push(empty);
      }
      for (const group of groups) {
        const entry = representative(group.entries);
        const existing = previous.get(group.key),
          signature = JSON.stringify([textSize, group]);
        if (existing?.signature === signature) {
          P.rows.set(group.key, existing);
          contents.push(existing.row);
          continue;
        }
        const row = el("div", "import-row");
        row.dataset.entry = entry.id;
        const name = identified(entry).name;
        const chooseEntry = group.entries.find(needsChoose),
          control = chooseEntry ? chooseSize(P.art) : null;
        const tree = P.art.row({
          name,
          ...rowCopy(group),
          textSize,
          stateKey: entry.state,
          route: conversionRoutes(group.entries),
          note: [...new Set(group.entries.map(rowNote).filter(Boolean))].join(listSeparator()),
          muted: !eligible(entry),
          control,
        });
        const drawing = paint(row, tree, {
          scale: ROW_SCALE,
          origin: [ROW_WIDTH / 2, (329 * ROW_SCALE) / 2],
          textures: { Icon_Arona: Promise.resolve(null) },
        });
        // MailboxContent/List_01 or List_02/BG_Icon/Icon_Arona is the incoming Mod's real texture slot.
        const texturePath = `${eligible(entry) ? "List_01" : "List_02"}/BG_Icon/Icon_Arona`;
        let picture;
        const prepare = () =>
          (picture ||= bindTexture(drawing, texturePath, thumbnail(entry, { incoming: true }), "import-thumb"));
        const copy = place(
          el("button", "hit import-row-copy"),
          0,
          0,
          ROW_WIDTH - 72,
          parseFloat(row.style.height || ROW_HEIGHT) - 10,
        );
        copy.type = "button";
        copy.setAttribute("aria-label", name);
        copy.addEventListener("click", () => importState.select(entry.id));
        const detailed = group.entries.filter(hasDetails),
          badgeBox = detailed.length ? badgeRect(tree) : null;
        const badgeHit = badgeBox ? place(el("button", "hit import-row-badge"), ...badgeBox) : null;
        if (badgeHit) {
          badgeHit.type = "button";
          badgeHit.setAttribute("aria-label", T("info.details"));
          badgeHit.addEventListener("click", (event) => {
            event.stopPropagation();
            showDetails(detailed);
          });
        }
        const check = checkbox("", importState.state.checked.has(entry.id), (checked) =>
          importState.toggleGroup(
            group.entries.map((item) => item.id),
            checked,
          ),
        );
        check.input.setAttribute("aria-label", T("import.selectNamed", { name }));
        place(check.label, ROW_WIDTH - 66, ROW_CHECK_TOP, 50, 50);
        const selection = place(
          el("div", "import-selection"),
          ROW_SELECTION.left,
          ROW_SELECTION.top,
          ROW_SELECTION.width,
          ROW_SELECTION.height,
        );
        paint(selection, P.art.selection(), { scale: ROW_SELECTION.scale, origin: ROW_SELECTION.origin });
        row.append(selection, copy, ...(badgeHit ? [badgeHit] : []), check.label);
        if (chooseEntry) {
          const [x, y, width, height] = controlRect(tree);
          const choose = button(missingText("choose"), "chooseFiles", {
            width,
            scale: CHOOSE.scale,
            bodyHeight: height / CHOOSE.scale,
          });
          choose.size = control.size;
          choose.refresh();
          place(choose, x, y, width, height);
          choose.addEventListener("click", () => {
            importState.pick(false, false, sourceFolder(chooseEntry.paths[0]));
          });
          row.appendChild(choose);
        }
        contents.push(row);
        P.rows.set(group.key, {
          row,
          copy,
          selection,
          input: check.input,
          refresh: check.refresh,
          group,
          signature,
          prepare,
          drawing,
          previousRow: existing?.row,
        });
      }
      const staged = [...P.rows.values()];
      tracked(
        Promise.all([
          prepareVisibleRows(P.dom.list, staged),
          Promise.allSettled(staged.map((record) => record.drawing)),
        ]).then(([scrollTop]) => {
          if (!P.visible || page !== P.generation || token !== P.listGeneration) return;
          const replacements = new Map();
          for (const record of staged)
            if (record.previousRow) {
              const previous = record.previousRow,
                next = record.row;
              previous.paintVersion = (previous.paintVersion || 0) + 1;
              previous.replaceChildren(...next.childNodes);
              previous.style.height = next.style.height;
              Object.assign(previous.dataset, next.dataset);
              previous.art = next.art;
              record.row = previous;
              record.previousRow = null;
              replacements.set(next, previous);
            }
          P.dom.list.replaceChildren(...contents.map((row) => replacements.get(row) || row));
          P.dom.list.scrollTop = scrollTop;
          syncBar(P.dom.list);
        }),
      );
    }
    for (const row of P.rows.values()) {
      const ids = row.group.entries.map((entry) => entry.id),
        selectable = row.group.entries.filter(eligible);
      // Items' selection mark is kept on the thumbnail, separate from the import checkbox.
      row.row.dataset.selected = String(ids.includes(importState.state.selected));
      row.selection.hidden = !ids.includes(importState.state.selected);
      row.copy.setAttribute("aria-pressed", String(ids.includes(importState.state.selected)));
      row.input.checked = selectable.length > 0 && selectable.every((entry) => importState.state.checked.has(entry.id));
      row.input.indeterminate =
        !row.input.checked && selectable.some((entry) => importState.state.checked.has(entry.id));
      row.input.disabled =
        importState.busy() || !importState.state.valid || importState.state.phase === "done" || !selectable.length;
      row.refresh();
      const choose = [...row.row.querySelectorAll("button")].find((b) => b.dataset.action === "chooseFiles");
      if (choose) {
        choose.disabled = importState.busy();
        choose.refresh();
      }
    }
  }

  return {
    layoutList,
    renderRows,
    renderPreview,
    prepareVisibleRows,
    rowCopy,
    paragraphCopy,
    representative,
    thumbnail,
    bindTexture,
    refreshCurrentPreviews,
    refreshAppliedPreviews,
  };
}
