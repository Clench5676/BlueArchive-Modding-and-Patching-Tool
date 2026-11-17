/**
 * The baseline of a text from a range's rect and measured font metrics. This fallback uses font metrics only;
 * the normal browser path (domBaseline) uses a zero-height inline block whose bottom is the text's actual
 * alphabetic baseline.
 */
export const rangeBaseline = (rect, metrics) =>
  rect.top +
  (rect.height - metrics.fontBoundingBoxAscent - metrics.fontBoundingBoxDescent) / 2 +
  metrics.fontBoundingBoxAscent;

/**
 * The y of the actual alphabetic baseline of the text in an element, in viewport px, measured with a
 * zero-height inline probe.
 */
export function domBaseline(root) {
  const label = root.querySelector?.(".ngui span") || root.querySelector?.(".ngui") || root;
  const probe = document.createElement("span");
  Object.assign(probe.style, {
    display: "inline-block",
    width: "0",
    height: "0",
    padding: "0",
    margin: "0",
    border: "0",
    fontSize: "0",
    lineHeight: "0",
    verticalAlign: "baseline",
  });
  label.appendChild(probe);
  try {
    return probe.getBoundingClientRect().top;
  } finally {
    probe.remove();
  }
}

/**
 * Shift an element vertically so its text baseline lies at a target y of the stage.
 * @param {HTMLElement} element The element to move.
 * @param {number} target The baseline's y in stage px.
 * @param {HTMLElement} stage The stage, for its scale.
 * @param {(element: HTMLElement) => number} [measure] Measures the element's baseline in viewport px.
 * @returns {boolean} False when the stage has no size or the baseline cannot be measured.
 */
export function alignBaseline(element, target, stage, measure = domBaseline) {
  const rect = stage.getBoundingClientRect(),
    scale = rect.width / 1920;
  if (!(scale > 0)) return false;
  const baseline = measure(element);
  if (!Number.isFinite(baseline)) return false;
  const delta = (rect.top + target * scale - baseline) / scale;
  element.baselineShift = (element.baselineShift || 0) + delta;
  element.style.transform = `translateY(${element.baselineShift}px)`;
  return true;
}

/**
 * Align a prepared layer's baseline, show it and put it into the host in place of what was there.
 * @returns {boolean} False when it could not be aligned; nothing is published then.
 */
export function publishAligned(host, next, target, stage, measure = domBaseline) {
  if (!alignBaseline(next, target, stage, measure)) return false;
  next.style.visibility = "";
  host.replaceChildren(next);
  return true;
}
