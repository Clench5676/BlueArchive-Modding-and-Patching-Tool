// Scrolling areas fade out at an edge while there is more to scroll that way (owner, 2026-11-11, BL-176: every area that
// scrolls, "如果上下还有内容，应有虚化"): a mask, opaque inside, transparent at the edge over FADE px, on the axis the area
// scrolls along; an edge with nothing past it stays sharp. One listener (scroll, captured) and one observer (new areas, a
// change of content) keep every area that matches SCROLLERS current.
export const SCROLLERS = ".popup-cards, .settings-rows, .import-list, .import-dialog-content, .plain-message-lines, .log-text, .repair-notes, .expr-clip.expanded";
// the fade's depth in CSS px: SPACE.l (28 units) at the stage's scale, rounded
export const FADE = 18;

// the mask for an area's scroll state (pure: the tests give it numbers)
export function fadeMask({ start, max, horizontal = false }, size = FADE) {
  const before = start > 1, after = start < max - 1;
  if (!before && !after) return "";
  const dir = horizontal ? "to right" : "to bottom";
  return `linear-gradient(${dir}, ${before ? "transparent" : "#000"} 0, #000 ${size}px, #000 calc(100% - ${size}px), ${after ? "transparent" : "#000"} 100%)`;
}

export function fade(el) {
  const vertical = el.scrollHeight - el.clientHeight > 1, horizontal = !vertical && el.scrollWidth - el.clientWidth > 1;
  const state = horizontal ? { start: el.scrollLeft, max: el.scrollWidth - el.clientWidth, horizontal: true }
    : { start: el.scrollTop, max: el.scrollHeight - el.clientHeight };
  const mask = fadeMask(state);
  if (el.style.maskImage !== mask) { el.style.maskImage = mask; el.style.webkitMaskImage = mask; }
}

let installed = false;
export function installScrollFades(doc = globalThis.document) {
  // a page (not a test's stand-in document) only
  if (installed || typeof doc?.addEventListener !== "function" || typeof doc.querySelectorAll !== "function" || !doc.body) return;
  installed = true;
  doc.addEventListener("scroll", (event) => { if (event.target?.matches?.(SCROLLERS)) fade(event.target); }, { capture: true, passive: true });
  let queued = false;
  const all = () => { queued = false; for (const el of doc.querySelectorAll(SCROLLERS) || []) fade(el); };
  const later = () => { if (!queued) { queued = true; (globalThis.requestAnimationFrame || setTimeout)(all); } };
  if (globalThis.MutationObserver && doc.body) new MutationObserver(later).observe(doc.body, { childList: true, subtree: true });
  globalThis.addEventListener?.("resize", later);
  later();
}
