// Character ownership uses the roster's search and order rules and the FilterSort popup.
import * as Display from "../app/display.js";
import { order, normalizeView } from "./roster-order.js";
import { L, T, language } from "../core/i18n.js";
import { studentName } from "../core/names.js";

/**
 * The students an assignment can be made to, in the roster's order for a view and a search.
 */
export function assignmentChoices(data, view, query = "") {
  return order(data, normalizeView(view), {
    query,
    label: L,
    collator: new Intl.Collator(language(), { numeric: true, sensitivity: "base" }),
  });
}

/**
 * The keys of the alts a view offers: with Group Alts every alt of a student shown, else the cards' own.
 */
export function offeredKeys(data, view, cards) {
  return new Set(
    cards.flatMap((card) =>
      view.merged
        ? (data.students.find((item) => item.key === card.student)?.alts || []).map((alt) => alt.key)
        : [card.key],
    ),
  );
}

/**
 * Fill the character picker's dialog: a list of students with a search, sort and the Group Alts option.
 * @param {object} options The dialog's parts.
 * @param {object} options.data The data of roster.list.
 * @param {string|null} options.selected The selected alt's key.
 * @param {object} options.dialog The dialog, whose scrolling area `refresh` follows.
 * @param {(area: object) => void} [options.refresh] Called when the dialog's scrolling area changed height (its
 *     game scrollbar follows).
 */
export async function characterPicker({
  data,
  selected,
  dialog,
  el,
  button,
  inputControl,
  checkbox,
  radioOptions,
  emptyNote,
  onSelect,
  refresh = () => {},
}) {
  let view = normalizeView({ sort: "name", order: "asc" }),
    query = "",
    page = 0,
    student = null;
  let choice = selected || null;
  await dialog(
    T("import.assignCharacter"),
    { action: T("picker.ok"), cancel: T("popup.cancel"), onAction: () => (choice ? onSelect(choice) : false) },
    (area, controls) => {
      const search = inputControl(T("import.search"), "", { width: 1370, search: true });
      area.appendChild(search.wrap);
      const tools = el("div");
      Object.assign(tools.style, { display: "flex", flexWrap: "wrap", gap: "12px", margin: "14px 0" });
      const filters = button(T("display.title"), "characterDisplay", { width: 255, scale: 0.34 });
      const direction = button(T("roster.asc"), "characterOrder", { width: 170, scale: 0.34 });
      const mods = button(T("seg.all"), "characterMods", { width: 180, scale: 0.34 });
      const recruit = button(T("seg.all"), "characterRecruit", { width: 230, scale: 0.34 });
      const merged = checkbox(T("roster.merge"), false, (value) => {
        view.merged = value;
        student = null;
        page = 0;
        draw();
      });
      Object.assign(merged.label.style, { position: "relative", width: "460px", height: "55px" });
      tools.append(filters, direction, mods, recruit, merged.label);
      area.appendChild(tools);
      // the dialog's area is the one scroller (with the game's scrollbar); a page of 24 is the list's length
      const list = el("div");
      area.appendChild(list);
      const pages = el("div");
      Object.assign(pages.style, { display: "flex", justifyContent: "space-between" });
      // the arrows are the painted captions; the pages they turn name them
      const previous = button("‹", "characterPrevious", { width: 100, scale: 0.34, name: T("photo.previous") });
      const next = button("›", "characterNext", { width: 100, scale: 0.34, name: T("photo.next") });
      pages.append(previous, next);
      area.appendChild(pages);
      const label = (alt) => studentName(alt, L(alt.names)?.full || alt.key);
      const draw = () => {
        const cards = assignmentChoices(data, view, query);
        const values = cards.slice(page * 24, page * 24 + 24).flatMap((card) => {
          if (!view.merged) return [[card.key, card.name]];
          const alts = data.students.find((item) => item.key === card.student)?.alts || [];
          return [
            [`student:${card.student}`, card.name],
            ...(student === card.student ? alts.map((alt) => [alt.key, label(alt)]) : []),
          ];
        });
        list.replaceChildren();
        if (!values.length) {
          // no match: the list says so, as the Hall's pickers do
          const note = el("div");
          Object.assign(note.style, { position: "relative", width: "1370px", height: "100px" });
          emptyNote?.(note, T("hall.noCandidates"));
          list.appendChild(note);
        } else
          radioOptions(
            list,
            values,
            choice,
            (value) => {
              if (value.startsWith("student:")) {
                student = value.slice(8);
                draw();
              } else {
                choice = value;
                controls?.primary(true);
              } // a choice picked from the list is one the view offers
            },
            { columns: 2 },
          );
        // a choice the search or filters hide cannot be confirmed
        controls?.primary(!choice || offeredKeys(data, view, cards).has(choice));
        previous.disabled = page === 0;
        next.disabled = (page + 1) * 24 >= cards.length;
        previous.refresh();
        next.refresh();
        refresh(area);
      };
      const searchChanged = () => {
        query = search.input.value;
        page = 0;
        student = null;
        draw();
      };
      search.input.addEventListener("input", (event) => {
        if (!event.isComposing) searchChanged();
      });
      search.input.addEventListener("compositionend", searchChanged);
      search.clear?.addEventListener("click", () => {
        search.input.value = "";
        searchChanged();
      });
      previous.addEventListener("click", () => {
        page--;
        area.scrollTop = 0;
        draw();
      });
      next.addEventListener("click", () => {
        page++;
        area.scrollTop = 0;
        draw();
      });
      direction.addEventListener("click", () => {
        view.order = view.order === "asc" ? "desc" : "asc";
        direction.caption = T(`roster.${view.order}`);
        direction.setAttribute("aria-label", direction.caption);
        direction.refresh();
        page = 0;
        draw();
      });
      const segment = (control, key, values, labels) =>
        control.addEventListener("click", () => {
          const index = (values.indexOf(view[key]) + 1) % values.length;
          view[key] = values[index];
          control.caption = T(labels[index]);
          control.setAttribute("aria-label", control.caption);
          control.refresh();
          page = 0;
          draw();
        });
      segment(mods, "mods", ["all", "mods", "none"], ["seg.all", "seg.mods", "seg.noMods"]);
      segment(recruit, "recruit", ["all", "yes", "no"], ["seg.all", "seg.recruit", "seg.noRecruit"]);
      filters.addEventListener("click", () =>
        Display.open({
          view,
          count: (draft) => assignmentChoices(data, draft, query).length,
          onDone: (draft) => {
            if (draft) {
              view = normalizeView({ ...view, ...draft });
              page = 0;
              draw();
            }
          },
        }),
      );
      draw();
    },
  );
}
