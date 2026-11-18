// The message popup: its lines (text, inline buttons, rules), layout and drawing, the text viewer, and the notices
// for ordinary refusals.
import * as NGUI from "./ngui.js";
import * as Buttons from "./game-buttons.js";
import { T } from "../core/i18n.js";
import { codeOf, noticeNames, withCode } from "../app/error-codes.js";
import { TEXT_VIEWER, frameClass, frameGeometry, popupContent } from "./popup-frames.js";
import { statsBox, valueRoom } from "./stats-box.js";
import {
  CONTROL,
  POPUP_FOOTER,
  POPUP_FRAME,
  POPUP_TYPE,
  SPACE,
  TYPE,
  SMALL_BUTTON,
  XS_BUTTON,
  STATS_BOX,
} from "./ui-tokens.js";
import { S, glyphsOf, wrapPath } from "../core/util.js";
import { container, row, stack as layoutStack, textItem, toNode } from "./ui-grid.js";
import {
  node,
  sprite,
  label,
  find,
  fitText,
  hostOf,
  ready,
  opened,
  closed,
  top,
  hit,
  titleHeight,
  popupFrame,
  closable,
  scrollArea,
} from "./popup.js";
import { FRAME_Y, footerBottom, footerLayout, footerFit } from "./popup-footer.js";
import { CHECK_H, checkbox, wireCheck } from "./popup-checkbox.js";

// --- the message popup ---
export const MSG = {
  title: POPUP_FRAME.contentTop,
  gap: SPACE.l,
  rowButton: XS_BUTTON.h + SPACE.m,
  check: CHECK_H + SPACE.m,
  buttons: POPUP_FOOTER.clearance,
  none: SPACE.xl,
  cap: POPUP_FRAME.maxH,
};

/**
 * A line of a message popup: plain text, or an object for a line with a small button or a special look.
 * @typedef {string | object} MessageLine
 * The object form carries `text`, and optionally: `button: {label, fn, inline, more}`, a small button at the end
 * of the line (fn({close}) runs on a click, where close(button) closes the popup; inline, with `plain`, makes it
 * only as high as the line's text, right after it; more: [{label, fn}] further inline buttons after it, the same
 * size); `fontSize` and `minFontSize`, to keep a row's type size (the tour counter and body); and, with `plain`,
 * `indent` (units, moves the line right), `marker` (hangs a bullet left of its first line, markerGap wide, its font
 * size by default), `gap` (space above it, for the release notes' lists and sections), `italic`, and `rule: true`
 * (the game's dotted divider, Common_Line_Dot, as the light stats box draws it, across the text).
 */

/**
 * Options of message().
 * @typedef {object} MessageOptions
 * @property {string} title The title.
 * @property {MessageLine[]} [lines] The text. With a line that has a small button, every line shows as a row of the
 *     game's light box.
 * @property {string} [ok] The OK button's label; either button may be left out.
 * @property {string} [cancel] The Cancel button's label.
 * @property {{label: string, value?: *, primary?: boolean}[]} [buttons] Named footer choices instead of ok and cancel.
 * @property {{label: string, checked: boolean}} [check] A checkbox; its state comes back in the result.
 * @property {string|HTMLElement} [host] Where it is drawn: #dialog by default, over everything but the message popup.
 * @property {number} [width] The frame's width class or width.
 * @property {number} [height] The least height of the frame.
 * @property {boolean} [close] False for a popup that must be answered with a button (no close button; the backdrop
 *     and Escape do nothing).
 * @property {string[]} [confirmKeys] Keys that press OK while the popup is in front and holds the focus itself (a
 *     focused button takes its own key), as the PC client binds its popups' confirm (["Enter", " "]); none by default.
 * @property {boolean} [scroll] Constrain the lines to the frame; the title and the action buttons stay visible. A
 *     message whose frame would be taller than MSG.cap scrolls by itself, so they stay on the stage.
 * @property {number[]} [fitTogether] Indices of the lines fitted at one shared size, using the smallest fit within
 *     the popup's size floor.
 * @property {boolean} [plain] Left-aligned lines without a box or separators, including lines with a button at the
 *     right.
 * @property {string} [footer] The footer buttons' size, "XS", "S", "M" or "L" (FOOTER); without it the default size,
 *     XS, its bodies as wide as the captions need.
 * @property {string} [captionSite] The wording site, which makes a caption that cannot fit throw.
 * @property {object|string} [error] An error: the popup becomes the Failed notice with an Error Report button.
 */

/**
 * What message() resolves with.
 * @typedef {object} MessageResult
 * @property {"ok"|"cancel"|null} button The button pressed, or null for the close button, the backdrop or Escape.
 * @property {boolean|null} checked The checkbox's state then; null without a checkbox.
 */

/**
 * Show a message on the popup frame, in a host. The popup takes the keyboard focus while it is open (Enter
 * presses nothing under it) and gives it back after. Without the frame's prefab (no game folder) the same
 * message is drawn plain.
 * @param {MessageOptions} opts
 * @returns {Promise<MessageResult>} Settles once the popup is closed, however it is closed.
 */
export function message(opts) {
  if (opts.error || opts.title === T("job.failed")) {
    const error = typeof opts.error === "object" && opts.error ? opts.error : {};
    const event = {
      kind: "popup",
      code: error.code || "internal",
      params: error.params || {},
      message: error.message || (opts.lines || []).filter((line) => typeof line === "string").join("\n"),
    };
    const ref = error.ref || codeOf(event.code);
    if (ref) event.ref = ref;
    const report = () => globalThis.BAMTRecovery?.report(event);
    if (!opts.cancel) {
      opts = { ...opts, cancel: T("recovery.report") };
      return new Promise((resolve) =>
        showMessage(opts, (result) => {
          if (result.button === "cancel") report();
          resolve(result);
        }),
      );
    }
    opts = {
      ...opts,
      lines: [...(opts.lines || []), { text: "", button: { label: T("recovery.report"), fn: report } }],
    };
  }
  return new Promise((resolve) => {
    showMessage(opts, resolve);
  });
}

/**
 * Whether a key presses OK (confirmKeys): alone, not a held key's repeat, not while an input method composes.
 * @param {string[]} [keys]
 * @returns {boolean} True when the key confirms.
 */
export function confirmKey(ev, keys) {
  if (!keys?.length || !ev || ev.defaultPrevented || ev.isComposing || ev.keyCode === 229 || ev.repeat) return false;
  if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return false;
  return keys.includes(ev.key);
}

/**
 * Draw a message without the game's prefab: a plain box on a dark backdrop, the same buttons, marked the same
 * way.
 * @param {MessageOptions & {state?: object|null, dismissable?: boolean}} opts The message, as for message();
 *     state is the checkbox's state, dismissable false for a popup that must be answered with a button.
 * @param {(result: MessageResult) => void} finish
 */
export function plainMessage(
  layer,
  {
    title,
    lines = [],
    ok,
    cancel,
    buttons,
    state = null,
    dismissable = true,
    scroll = false,
    plain = false,
    width,
    height,
    fitTogether = [],
    captionSite = null,
    footer = null,
  },
  finish,
) {
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const button = (caption, fn, action, cls = "message-button") => {
    const b = el("button", cls, caption);
    b.dataset.action = action;
    b.setAttribute("aria-label", caption);
    b.addEventListener("click", fn);
    return b;
  };
  const layout = messageLayout({
    lines,
    ok,
    cancel,
    buttons,
    check: state,
    scroll,
    plain,
    width,
    height,
    fitTogether,
    assets: false,
    footer,
  });
  const frame = { w: layout.width, h: layout.height, cx: 0, cy: FRAME_Y };
  const styleBox = (element, box, fs) => {
    Object.assign(element.style, {
      position: "absolute",
      boxSizing: "border-box",
      margin: "0",
      padding: "0",
      left: `${box.x * S}px`,
      top: `${box.y * S}px`,
      width: `${box.w * S}px`,
      height: `${box.h * S}px`,
      ...(fs != null ? { fontSize: `${fs * S}px`, lineHeight: String(NGUI.LINE_PITCH), whiteSpace: "pre-wrap" } : {}),
    });
    return element;
  };
  const back = el("div", "plain-popup-back");
  if (dismissable) back.addEventListener("click", () => finish(null));
  const box = el("div", "plain-popup");
  Object.assign(box.style, {
    position: "absolute",
    boxSizing: "border-box",
    transform: "none",
    margin: "0",
    padding: "0",
    left: `${960 - (frame.w * S) / 2}px`,
    top: `${540 - (FRAME_Y + frame.h / 2) * S}px`,
    width: `${frame.w * S}px`,
    height: `${frame.h * S}px`,
    maxWidth: "none",
    maxHeight: "none",
  });
  const heading = textItem(NGUI, title, POPUP_TYPE.title, { maxW: frame.w - 2 * SPACE.xxxl, lines: 1 });
  box.appendChild(
    styleBox(
      el("h2", "", heading.text),
      {
        x: SPACE.xxxl,
        y: POPUP_FRAME.titleTop + (titleHeight(title) - heading.h) / 2,
        w: frame.w - 2 * SPACE.xxxl,
        h: heading.h,
      },
      NGUI.fontSizeFor(heading.text, heading.fs),
    ),
  );
  if (dismissable)
    box.appendChild(
      styleBox(
        button("×", () => finish(null), "close"),
        {
          x: frame.w - POPUP_FRAME.close.right - CONTROL.icon.h,
          y: POPUP_FRAME.close.top,
          w: CONTROL.icon.h,
          h: CONTROL.icon.h,
        },
        CONTROL.icon.h,
      ),
    );
  if (dismissable) box.children.at(-1).setAttribute("aria-label", T("display.close"));
  const content = styleBox(el("div", "plain-message-lines"), {
    x: layout.content.x,
    y: FRAME_Y + frame.h / 2 - layout.y0,
    w: layout.content.w,
    h: layout.visible,
  });
  Object.assign(content.style, { overflow: "auto", textAlign: plain ? "left" : "center" });
  const paragraphItems = layout.body.fitted.map(({ r, f, bf, bw, more = [], item: text }) => {
    return {
      r,
      f,
      bf,
      bw,
      more,
      text,
      w: layout.content.w,
      h: r.rule ? RULE.h : Math.max(r.button ? XS_BUTTON.h : 0, text.h) + (plain ? r.gap || 0 : 0),
    };
  });
  const paragraphs = layoutStack(paragraphItems, { w: layout.content.w, gap: SPACE.l });
  paragraphs.boxes.forEach(({ r, f, bf, bw, more, text, x, y, w }, i) => {
    if (r.rule) {
      const line = styleBox(el("div", "plain-message-rule"), {
        x: x + SPACE.xxxl,
        y,
        w: w - 2 * SPACE.xxxl,
        h: RULE.h,
      });
      line.style.borderTop = `${Math.max(1, RULE.h * S)}px dotted ${STATS_BOX.divider}`;
      content.appendChild(line);
      return;
    }
    const indent = plain ? r.indent || 0 : 0;
    const taken = (r.button ? bw + SPACE.m : 0) + more.reduce((n, m) => n + m.bw + SPACE.m, 0);
    const parts = row(
      [
        { ...text, w: w - indent - taken },
        ...(r.button ? [{ w: bw, h: XS_BUTTON.h }] : []),
        ...more.map((m) => ({ w: m.bw, h: XS_BUTTON.h })),
      ],
      { x: x + indent, y: y + (plain ? r.gap || 0 : 0), w: w - indent, gap: SPACE.m, justify: "center" },
    ).boxes;
    const p = styleBox(el("p", "", f.text), parts[0], NGUI.fontSizeFor(f.text, f.fs));
    content.appendChild(p);
    if (plain && r.marker)
      content.appendChild(
        styleBox(
          el("span", "", r.marker),
          {
            x: parts[0].x - (r.markerGap ?? f.fs),
            y: parts[0].y,
            w: r.markerGap ?? f.fs,
            h: NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH,
          },
          NGUI.fontSizeFor(f.text, f.fs),
        ),
      );
    if (r.button) {
      const b = styleBox(
        button(bf.text, () => r.button.fn?.({ close: finish }), `line${i}`, "plain-line-button"),
        parts[1],
        NGUI.fontSizeFor(bf.text, bf.fs),
      );
      b.setAttribute("aria-label", r.button.label);
      content.appendChild(b);
    }
    more.forEach((m, k) => {
      const b = styleBox(
        button(m.bf.text, () => m.button.fn?.({ close: finish }), `line${i}_${k + 1}`, "plain-line-button"),
        parts[2 + k],
        NGUI.fontSizeFor(m.bf.text, m.bf.fs),
      );
      b.setAttribute("aria-label", m.button.label);
      content.appendChild(b);
    });
  });
  // Use the measured body height for the scroll viewport, including the native stats-box row envelope.
  if (paragraphs.h > layout.visible) content.style.overflowY = "auto";
  box.appendChild(content);
  if (state) {
    const check = button(
      layout.checkCaption.text,
      () => {
        state.checked = !state.checked;
        check.setAttribute("aria-checked", String(state.checked));
      },
      "check",
      "plain-check",
    );
    check.setAttribute("role", "checkbox");
    check.setAttribute("aria-checked", String(state.checked));
    check.setAttribute("aria-label", state.label);
    box.appendChild(
      styleBox(check, layout.checkBox, NGUI.fontSizeFor(layout.checkCaption.text, layout.checkCaption.fs)),
    );
  }
  const actions = buttons?.length
    ? buttons
    : [...(cancel ? [{ label: cancel, value: "cancel" }] : []), ...(ok ? [{ label: ok, value: "ok" }] : [])];
  if (actions.length) {
    const {
      kind: size,
      control,
      bodyW,
      room,
    } = footerLayout(
      actions.map((action) => action.label),
      frame.w,
      footer,
    );
    const fits = footerFit(
      actions.map((action) => action.label),
      { site: captionSite, width: room, caption: size.caption },
    );
    const actionRow = el("div", "message-actions");
    styleBox(actionRow, { x: 0, y: frame.h - footerBottom(control) - control.h, w: frame.w, h: control.h });
    const boxes = row(
      actions.map(() => ({ w: bodyW, h: control.h })),
      { w: frame.w, gap: POPUP_FRAME.footer.gap, justify: "center" },
    ).boxes;
    actions.forEach((action, i) => {
      const b = styleBox(
        button(fits[i].text, () => finish(action.value), action.value),
        boxes[i],
        NGUI.fontSizeFor(fits[i].text, fits[i].fs),
      );
      b.style.padding = `0 ${control.pad * S}px`;
      b.setAttribute("aria-label", action.label);
      actionRow.appendChild(b);
    });
    box.appendChild(actionRow);
  }
  layer.replaceChildren(back, box);
  return { box, content, layout };
}

// a fixed size (max equals min) only wraps, on as many lines as it needs, and never shrinks below that size.
// strict: no size under min either way (the light box's rows): text that cannot keep it within `lines` lines
// breaks at separators and between characters, down to min, on as many lines as it needs
export function messageText(text, max, min, room, lines = 4, strict = false) {
  const fixed = max === min;
  const fitted = fitText(text, max, min, room, fixed ? Infinity : lines);
  if (fitted.fs >= min && fitted.text.split("\n").every((line) => glyphsOf(line, fitted.fs) <= room)) return fitted;
  return strict ? wrapPath(text, max, min, room, lines) : wrapPath(text, min, min, room, Infinity);
}
// a fitted text with its lines counted as drawn (a line break in the text itself counts)
const counted = (fitted) => ({ ...fitted, lines: fitted.text.split("\n").length });

const placeholderButton = (name, x, y, caption, { w, h = XS_BUTTON.h }) =>
  node(name, [x, y], [sprite("Common_Btn_BG", w, h)]);
// the inline buttons are the XS small button: the XS body's height over the S one's
const XS_RATIO = XS_BUTTON.h / SMALL_BUTTON.h;
// a plain message's divider row ({rule: true}): the light stats box's dotted line, its height
export const RULE = Object.freeze({ h: 4 });
// a plain line's inline button (button.inline): the small button, shadow included, within its line's text from
// top to bottom (``height`` of the type size: the ink of a line, not its pitch), its caption at most ``share``
// of the type size pad: the small button fits its caption again within its width less XS_BUTTON.fitInset, so the
// pad is at least that
export const INLINE_BUTTON = Object.freeze({
  height: 0.9 * XS_RATIO,
  share: 0.55 * XS_RATIO,
  fsMin: 20,
  pad: XS_BUTTON.fitInset,
});
// button.inline "line": the button's face as high as its line of text (the line's pitch, the small button's shadow
// inset added around it), its caption ``share`` of the type size, ``fsMin`` at least (the compatibility result's
// Details)
export const LINE_BUTTON = Object.freeze({
  share: 0.6 * XS_RATIO,
  height: XS_RATIO,
  fsMin: TYPE.badge.min,
  pad: XS_BUTTON.fitInset,
});
function inlineButton(r, fs, w) {
  const first = oneInlineButton(r, r.button, fs);
  // more inline buttons after the first (r.more: [{label, fn}]), the same size, SPACE.m apart
  const more = (r.more || []).map((button) => ({
    button,
    ...oneInlineButton(r, { ...button, inline: r.button.inline }, fs),
  }));
  const taken = [first, ...more].reduce((n, b) => n + b.bw + SPACE.m, 0);
  return { bf: first.bf, bw: first.bw, bh: first.bh, more, room: w - 2 * SPACE.xxxl - taken - (r.indent || 0) };
}
function oneInlineButton(r, button, fs) {
  const line = button.inline === "line";
  const bh = line
    ? Math.round(fs * NGUI.LINE_PITCH * LINE_BUTTON.height) + XS_BUTTON.boxInsetH
    : Math.round(fs * INLINE_BUTTON.height);
  const cap = line
    ? Math.max(LINE_BUTTON.fsMin, Math.round(fs * LINE_BUTTON.share))
    : Math.min(Math.round(fs * INLINE_BUTTON.share), bh - XS_BUTTON.boxInsetH);
  // one line: as high as the text beside it, a two-word caption (Go to) never folds
  const ib = fitText(
    button.label,
    cap,
    Math.min(cap, line ? LINE_BUTTON.fsMin : INLINE_BUTTON.fsMin),
    XS_BUTTON.w - XS_BUTTON.fitInset,
    1,
  );
  const ibw = glyphsOf(ib.text, ib.fs) + (line ? LINE_BUTTON.pad : INLINE_BUTTON.pad);
  return { bf: ib, bw: ibw, bh };
}

function messageLines(lines, w, fitTogether, plain, assets = true) {
  const rows = lines.map((l) => (typeof l === "string" ? { text: l } : l));
  const boxed = !plain && rows.some((r) => r.button);
  // paragraphs at POPUP_TYPE.text, light stats rows their own range (38 down to 26); the width selection allows
  // six lines
  const min = boxed ? STATS_BOX.value.fsMin : POPUP_TYPE.text.min,
    max = boxed ? STATS_BOX.value.fs : POPUP_TYPE.text.fs,
    most = 6;
  const fitted = rows.map((r) => {
    if (r.rule) return { r, room: w - 2 * SPACE.xxxl, f: counted({ text: "", fs: min }) };
    // a line button is the small button: 136 high, as wide as its caption needs and 90 more (240 at least), the
    // caption 48 down to 34
    const bf = r.button
      ? fitText(r.button.label, XS_BUTTON.fs, XS_BUTTON.fsMin, XS_BUTTON.w - XS_BUTTON.fitInset)
      : null;
    const bw = bf ? Math.max(XS_BUTTON.minW, glyphsOf(bf.text, bf.fs) + XS_BUTTON.glyphPad) : 0;
    const room = boxed
      ? valueRoom("frame", w, { reserve: bw ? bw + SPACE.m : 0 })
      : w - 2 * SPACE.xxxl - (bw ? bw + SPACE.m : 0) - (plain ? r.indent || 0 : 0);
    const f = messageText(r.text, r.fontSize ?? max, r.minFontSize ?? min, room, most, boxed);
    if (plain && r.button?.inline) {
      // no higher than its line's text
      const inline = inlineButton(r, f.fs, w);
      return { r, ...inline, f: counted(messageText(r.text, f.fs, f.fs, inline.room, most, boxed)) };
    }
    return { r, bf, bw, room, f: counted(f) };
  });
  const group = fitTogether.map((i) => fitted[i]).filter(Boolean);
  if (group.length) {
    const fs = Math.max(min, Math.min(...group.map(({ f }) => f.fs)));
    // Rewrap at the shared size; extra lines keep the popup's minimum instead of shrinking below it. An inline button
    // follows its line's new size.
    for (const s of group) {
      if (s.bh) Object.assign(s, inlineButton(s.r, fs, w));
      s.f = boxed
        ? counted(wrapPath(s.r.text, fs, fs, s.room, Infinity))
        : wrapPath(s.r.text, fs, fs, s.room, Infinity);
    }
  }
  // Text is already wrapped and fitted. textItem measures that fit without repeating the line-break search.
  for (const s of fitted)
    s.item = s.r.rule
      ? { w: s.room, h: RULE.h }
      : textItem(
          { ...NGUI, fitText: () => s.f },
          s.f.text,
          { fs: s.f.fs, min: s.f.fs },
          { maxW: s.room, lines: Infinity },
        );
  const buttons = [];
  if (boxed) {
    // rows on the game's light stats box: text at the left, the line's button at the right; a row with a button
    // holds it, in 156 at least (the button and 10 above and below)
    const box = statsBox({
      holder: "frame",
      width: w,
      name: "Box",
      names: { value: "Row", divider: "Dot" },
      rows: fitted.map(({ r, f, bf, bw }, i) => ({
        value: f,
        colour: `${r.color || "#2d4664"}/1.00`,
        minH: bf ? MSG.rowButton : STATS_BOX.rowMin,
        reserve: bw ? bw + SPACE.m : 0,
        slot: bf
          ? ({ right, y }) => {
              buttons.push([i, r.button]);
              const buttonRow = row([{ w: bw, h: XS_BUTTON.h }], {
                x: right - bw,
                w: bw,
                gap: SPACE.m,
                justify: "center",
              });
              return [
                (assets ? Buttons.smallButton : placeholderButton)(
                  `Btn_${i}`,
                  buttonRow.boxes[0].x + bw / 2,
                  y,
                  r.button.label,
                  { w: bw, fs: XS_BUTTON.fs, min: XS_BUTTON.fsMin, depth: 8 },
                ),
              ];
            }
          : undefined,
      })),
    });
    return { tree: node("Lines", [0, 0], [], box.nodes), height: box.height, buttons, fitted };
  }
  const out = [],
    textBox = container({ w, h: 0, padding: [0, SPACE.xxxl] }).inner;
  const sized = fitted.map((s) => {
    if (s.r.rule) {
      const lineRow = row([{ w: textBox.w, h: RULE.h }], { x: textBox.x, w: textBox.w, justify: "start" });
      return { ...s, lineRow, w: textBox.w, h: lineRow.h };
    }
    const indent = plain ? s.r.indent || 0 : 0;
    // an inline button follows its text; another sits at the line's right end
    const inline = !!s.bh;
    const lineRow = row(
      [
        { ...s.item, w: inline ? s.item.w : s.room },
        ...(s.bf ? [{ w: s.bw, h: s.bh ?? XS_BUTTON.h }] : []),
        ...(s.more || []).map((m) => ({ w: m.bw, h: s.bh })),
      ],
      { x: textBox.x + indent, w: textBox.w - indent, gap: SPACE.m, justify: inline ? "start" : "center" },
    );
    return { ...s, lineRow, w: textBox.w, h: lineRow.h + (plain ? s.r.gap || 0 : 0) };
  });
  const paragraphs = layoutStack(sized, { x: textBox.x, w: textBox.w, gap: SPACE.l });
  paragraphs.boxes.forEach(({ r, f, bf, bw, bh, more = [], room, y, lineRow: measured }, i) => {
    const lineRow = {
      ...measured,
      boxes: measured.boxes.map((b) => ({ ...b, y: b.y + y + (plain ? r.gap || 0 : 0) })),
    };
    const local = { w, h: 0, cx: 0, cy: 0 },
      colour = `${r.color || "#2d4664"}/1.00`,
      box = lineRow.boxes[0];
    if (r.rule) {
      out.push(
        node(`Rule_${i}`, toNode(box, 3, local), [
          sprite("Common_Line_Dot", box.w, RULE.h, { type: 2, depth: 7, color: `${STATS_BOX.divider}/1.00`, pivot: 3 }),
        ]),
      );
      return;
    }
    out.push(
      node(`Line_${i}`, toNode(box, plain ? 3 : 4, local), [
        label(f.text, f.fs, colour, room, box.h, {
          pivot: plain ? 3 : 4,
          depth: 8,
          ...(plain && r.italic ? { italic: true } : {}),
        }),
      ]),
    );
    if (plain && r.marker) {
      const marker = { x: box.x - (r.markerGap ?? f.fs), y: box.y, w: r.markerGap ?? f.fs, h: f.fs * NGUI.LINE_PITCH };
      out.push(
        node(`Mark_${i}`, toNode(marker, 3, local), [
          label(r.marker, f.fs, colour, marker.w, marker.h, { pivot: 3, depth: 8 }),
        ]),
      );
    }
    if (bf) {
      const [bx, by] = toNode(lineRow.boxes[1], 4, local);
      out.push(
        (assets ? Buttons.smallButton : placeholderButton)(
          `Btn_${i}`,
          bx,
          by,
          r.button.label,
          bh
            ? { w: bw, h: bh, fs: bf.fs, min: bf.fs, depth: 8 }
            : { w: bw, fs: XS_BUTTON.fs, min: XS_BUTTON.fsMin, depth: 8 },
        ),
      );
      buttons.push([i, r.button]);
    }
    more.forEach((m, k) => {
      const [mx, my] = toNode(lineRow.boxes[2 + k], 4, local);
      out.push(
        (assets ? Buttons.smallButton : placeholderButton)(`Btn_${i}_${k + 1}`, mx, my, m.button.label, {
          w: m.bw,
          h: bh,
          fs: m.bf.fs,
          min: m.bf.fs,
          depth: 8,
        }),
      );
      buttons.push([`${i}_${k + 1}`, m.button]);
    });
  });
  return { tree: node("Lines", [0, 0], [], out), height: paragraphs.h, buttons, fitted, boxes: paragraphs.boxes };
}

/**
 * Lay out a message: the frame, the content box and the lines' rows. Ordinary messages and text-only tours share
 * it, including row fitting, frame padding and footer clearance. `scroll` in the result tells whether the
 * lines scroll in the frame: asked for, or forced because the frame would be taller than MSG.cap. Non-scrolling
 * bodies, including their check row, centre in the content box.
 * @param {MessageOptions & {assets?: boolean}} [opts] The message, as for message(); assets false measures
 *     without the game's art.
 * @returns
 *     {{body: object, width: number, height: number, visible: number, y0: number, content: Box, checkBox: object, checkCaption: object, scroll: boolean}}
 *     The lines' fit, the frame's size, the height of the visible part, the top of the body, the content box,
 *     the checkbox's place and caption, and whether the lines scroll.
 */
export function messageLayout({
  lines = [],
  width,
  ok = null,
  cancel = null,
  buttons = null,
  check = null,
  fitTogether = [],
  scroll = false,
  plain = false,
  assets = true,
  height: minimumHeight = 0,
} = {}) {
  let size;
  if (width != null) size = frameClass(width === 2100 ? "L" : width);
  else
    size =
      ["S", "M", "L"]
        .map((key) => frameClass(key))
        .find((candidate) =>
          messageLines(lines, candidate.w, fitTogether, plain, false).fitted.every(({ f }) => f.lines <= 6),
        ) || POPUP_FRAME.L;
  const body = messageLines(lines, size.w, fitTogether, plain, assets),
    hasButtons = !!(ok || cancel || buttons?.length);
  // as high as the lines and the footer need (no taller minimum frame, so no padding round a short message)
  const need = MSG.title + body.height + (check ? MSG.check : 0) + (hasButtons ? MSG.buttons : MSG.none);
  const { w, h: height } = frameGeometry(size.w, Math.max(need, minimumHeight)),
    content = popupContent(w, height, { text: true, footer: hasButtons });
  const available = Math.max(0, content.h - (check ? MSG.check : 0));
  // A body that fits keeps its own height and centres, scrolling or not; only an overflowing one fills the content box.
  const overflow = body.height > available + 1e-9,
    scrolls = scroll || overflow;
  const visible = overflow ? available : body.height;
  const bodyTop = content.y + Math.max(0, available - visible) / 2;
  const y0 = toNode({ ...content, y: bodyTop }, 1, { w, h: height, cx: 0, cy: FRAME_Y })[1];
  let checkBox = null,
    checkCaption = null;
  if (check) {
    const cb = checkbox("Check", 0, 0, check.label, !!check.checked, content.w);
    checkCaption = cb.caption;
    checkBox = row([{ w: cb.w, h: CHECK_H }], {
      x: content.x,
      y: bodyTop + visible + SPACE.m,
      w: content.w,
      gap: SPACE.m,
      justify: "center",
    }).boxes[0];
  }
  return { body, width: w, height, visible, y0, content, checkBox, checkCaption, scroll: scrolls };
}

async function showMessage(
  {
    title = "",
    lines = [],
    ok = null,
    cancel = null,
    buttons = null,
    check = null,
    host: hostArg = "dialog",
    width,
    close: dismissable = true,
    confirmKeys = null,
    fitTogether = [],
    scroll = false,
    plain = false,
    captionSite = null,
    footer = null,
  },
  resolve,
) {
  const host = hostOf(hostArg);
  const layer = document.createElement("div");
  layer.className = "popup-layer";
  layer.tabIndex = -1;
  host.appendChild(layer);
  host.hidden = false;
  const before = document.activeElement;
  const state = check ? { label: check.label, checked: !!check.checked } : null;
  let ended = false;
  const finish = (button) => {
    if (ended) return;
    ended = true;
    const focused = layer.contains(document.activeElement);
    layer.remove();
    closed(host, layer);
    if (!host.children.length) host.hidden = true;
    if (focused && before?.isConnected) before.focus?.({ preventScroll: true });
    resolve({ button, checked: state ? state.checked : null });
  };
  opened(host, dismissable ? () => finish(null) : null, { el: layer });
  // the confirm keys, while this popup is in front and the focus is on the popup itself
  if (ok && confirmKeys) {
    layer.addEventListener("keydown", (ev) => {
      if (ev.target !== layer || top()?.el !== layer || !confirmKey(ev, confirmKeys)) return;
      ev.preventDefault();
      finish("ok");
    });
  }
  try {
    await ready();
  } catch (err) {
    console.warn("message popup without the game's frame", err);
    if (ended) return;
    plainMessage(
      layer,
      { title, lines, ok, cancel, buttons, state, dismissable, scroll, plain, width, fitTogether, captionSite, footer },
      finish,
    );
    layer.focus?.({ preventScroll: true });
    return;
  }
  try {
    const {
      body,
      width: w,
      height,
      visible,
      y0,
      content,
      checkBox,
      scroll: scrolls,
    } = messageLayout({ lines, width, ok, cancel, buttons, check: state, fitTogether, scroll, plain });
    const t = popupFrame(title, w, height, { ok, cancel, buttons, close: dismissable, captionSite, footer });
    const popup = find(t, "Popup");
    // The body and check row centre together; a scrolling body fills the available content box.
    if (!scrolls) {
      body.tree.pos = [0, y0];
      popup.children.push(body.tree);
    }
    if (state) {
      const cb = checkbox("Check", 0, 0, state.label, state.checked, content.w);
      cb.tree.pos = toNode(checkBox, 3, { w, h: height, cx: 0, cy: FRAME_Y });
      popup.children.push(cb.tree);
    }
    const next = document.createElement("div");
    const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 });
    const bodyHost = scrolls
      ? scrollArea(next, [960 - (w * S) / 2, 540 - y0 * S, w * S, visible * S], body.height * S)
      : next;
    await bodyHost.ready;
    const bodyView = scrolls
      ? await NGUI.render(body.tree, bodyHost, { scale: S, origin: [(w * S) / 2, 0], z: 10 })
      : view;
    if (ended) return;
    for (const [index, action] of (buttons || []).entries())
      hit(next, view, `Center/Popup/Btn/Choice_${index}/BG (1)`, () => finish(action.value), {
        name: action.label,
        action: action.value,
      });
    if (ok) hit(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", () => finish("ok"), { name: ok, action: "ok" });
    if (cancel)
      hit(next, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", () => finish("cancel"), { name: cancel, action: "cancel" });
    if (dismissable)
      hit(next, view, "Center/Popup/Base/BG/Btn_Close", () => finish(null), {
        name: T("display.close"),
        action: "close",
      });
    for (const [i, b] of body.buttons) {
      hit(bodyHost, bodyView, `${scrolls ? "Lines" : "Center/Popup/Lines"}/Btn_${i}`, () => b.fn?.({ close: finish }), {
        name: b.label,
        action: `line${i}`,
      });
    }
    if (state) wireCheck(next, view, "Center/Popup/Check", state);
    closable(view, host, dismissable ? () => finish(null) : null, { el: layer });
    layer.replaceChildren(...next.childNodes);
    layer.focus?.({ preventScroll: true });
  } catch (err) {
    console.error("message popup", err);
    finish(null);
  }
}

/**
 * Show a scrollable text document on the shared popup frame. Text stays literal, selectable and in its original
 * order.
 * @param {{title: string, text: string, copy: string, close?: string, onCopy?: () => void}} options The title,
 *     the text, the copy button's label, the close button's label and the copy handler.
 * @returns {Promise<void>} Settles when the viewer is closed.
 */
export function textViewer({ title, text, copy, close = T("display.close"), onCopy }) {
  return new Promise((resolve) => {
    const host = hostOf("dialog"),
      layer = document.createElement("div"),
      before = document.activeElement;
    layer.className = "popup-layer";
    layer.tabIndex = -1;
    host.hidden = false;
    host.appendChild(layer);
    let ended = false;
    const finish = () => {
      if (ended) return;
      ended = true;
      layer.remove();
      closed(host, layer);
      if (!host.children.length) host.hidden = true;
      if (before?.isConnected) before.focus?.({ preventScroll: true });
      resolve();
    };
    opened(host, finish, { el: layer });
    const pre = document.createElement("pre");
    pre.className = "log-text";
    pre.textContent = text;
    pre.tabIndex = 0;
    pre.setAttribute("aria-label", title);
    const draw = async () => {
      try {
        await ready();
        if (ended) return;
        const next = document.createElement("div");
        const view = await NGUI.render(
          popupFrame(title, TEXT_VIEWER.w, TEXT_VIEWER.h, { ok: copy, cancel: close }),
          next,
          { scale: S, origin: [960, 540], z: 10 },
        );
        if (ended) return;
        hit(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", onCopy, { name: copy, action: "copy" });
        hit(next, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", finish, { name: close, action: "cancel" });
        hit(next, view, "Center/Popup/Base/BG/Btn_Close", finish, { name: close, action: "close" });
        closable(view, host, finish, { el: layer });
        const box = TEXT_VIEWER.box;
        Object.assign(pre.style, {
          position: "absolute",
          boxSizing: "border-box",
          margin: "0",
          left: `${960 - (box.width / 2) * S}px`,
          top: `${540 - box.top * S}px`,
          width: `${box.width * S}px`,
          height: `${box.height * S}px`,
          zIndex: 40,
        });
        next.appendChild(pre);
        layer.replaceChildren(...next.childNodes);
      } catch (err) {
        if (ended) return;
        console.warn("text popup without the game's frame", err);
        const fallback = plainMessage(
          layer,
          {
            title,
            width: TEXT_VIEWER.w,
            height: TEXT_VIEWER.h,
            scroll: true,
            buttons: [
              { label: copy, value: "copy" },
              { label: close, value: "close" },
            ],
          },
          (action) => (action === "copy" ? onCopy() : finish()),
        );
        // The document box keeps the viewer's own top and height, whatever the empty message body measures.
        Object.assign(fallback.content.style, {
          top: `${(FRAME_Y + TEXT_VIEWER.h / 2 - TEXT_VIEWER.box.top) * S}px`,
          height: `${TEXT_VIEWER.box.height * S}px`,
        });
        Object.assign(pre.style, {
          position: "absolute",
          boxSizing: "border-box",
          margin: "0",
          left: "0px",
          top: "0px",
          width: `${TEXT_VIEWER.box.width * S}px`,
          height: `${TEXT_VIEWER.box.height * S}px`,
        });
        fallback.content.replaceChildren(pre);
      }
      pre.scrollTop = pre.scrollHeight;
      layer.focus?.({ preventScroll: true });
    };
    draw();
  });
}

// --- ordinary refusals ---
// A refusal declines an action because of a state or a rule (a task still running, the game open, no game
// folder, a Hall rule): nothing broke, so it reads as the game's neutral notice (title quit.title, Confirm
// only), without Failed and without an Error Report button. Every other code is a real failure and keeps both,
// including codes that report something unreadable or unknown (game_status_unavailable, mod_configuration_unreadable).
export const REFUSALS = Object.freeze(noticeNames());
/**
 * Whether an error is an ordinary refusal.
 * @returns {boolean} True for a refusal code.
 */
export const isRefusal = (error) => REFUSALS.includes(error?.code);

/**
 * Show the neutral notice: the game's caption title (as the quit prompt), one line, Confirm only.
 * @returns {Promise<MessageResult>} Settles when it is closed.
 */
export function notice(text) {
  return message({ title: T("quit.title"), lines: [text], ok: T("picker.ok") });
}

// Only errors and refusals use a notification popup: a refusal as a notice, anything else as Failed with the
// Error Report. Rich results call message() explicitly.
let deferred = () => false;
/**
 * Hold automatic notifications while blocked() is true (loading.js: a strict hold covers every popup), so they
 * never open beneath the loading screen where they could not be closed.
 * @param {() => boolean} blocked True while notifications must wait.
 */
export function deferWhile(blocked) {
  deferred = blocked;
}
/**
 * Show an error or refusal notification.
 * @param {string} text The message; nothing is shown for an empty text or a kind other than "error".
 * @param {{kind?: string, error?: object|null}} [options] kind: "error" shows it; error: the error, which
 *     decides between a notice and the Failed popup.
 */
export function notify(text, { kind = "info", error = null } = {}) {
  if (!text || kind !== "error") return;
  if (deferred()) {
    setTimeout(() => notify(text, { kind, error }), 100);
    return;
  }
  if (isRefusal(error)) {
    void notice(text);
    return;
  }
  void message({ title: T("job.failed"), lines: [withCode(text, error, T)], ok: T("picker.ok"), error });
}
