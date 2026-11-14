# Layout primitives (`ui-grid.js`)

Boxes are `{ x, y, w, h }` in a container's own frame (x right, y down from its top left).

- `container({ x, y, w, h, padding })`: the outer box and the content box inside the padding (`inset` for any box).
- `grid(box)`: `span(n, offset)` and `at(n)` of the 12-column grid.
- `row(items, { x, y, w, h, gap, align, justify })`: items side by side; `align` centre (default, rule 1), start or end;
  `justify` start, end, centre or between; items with `grow` share the width left over.
- `stack(items, { x, y, w, gap, align })`: items one under the other; `align` start, centre, end or stretch.
- `textItem(ui, text, TYPE.role, { maxW, lines })`: a fitted text with its box, ink band and reference centre.
- `controlWidth(CONTROL.kind, caption, measure)`: a caption-sized control's width.
- `toNode(box, pivot, frame)`: the NGUI position of a widget with that pivot filling the box, for a prefab clone.

A screen measures its parts (texts with `textItem`, controls from `CONTROL`), lays them out with `row`, `stack` and
`grid` inside a `container`, and moves each prefab clone to `toNode` of its box. Its tests then check the rules on the
boxes instead of pixel offsets.
