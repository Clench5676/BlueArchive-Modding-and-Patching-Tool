/**
 * Place the top bar's actions: reuse the game's sibling boxes; only the Student Hall's Settings position changes.
 * Tasks and its divider, when given, sit one slot further left than Settings and its divider; in the Student Hall
 * they take the slots Settings and its divider leave.
 * @param {{home: number[], option: number[], line: number[], tasks?: number[], taskLine?: number[]}} boxes The
 *     boxes [right, top, width, height] of the Home, Settings, divider, Tasks and Tasks divider controls.
 * @param {boolean} Student Hall Whether the Student Hall is shown: Settings takes Home's place.
 * @param {number} [safeRight] The right inset the native window controls need.
 * @returns {{home: number[], option: number[], line: number[], tasks?: number[], taskLine?: number[]}} The
 *     placed boxes.
 */
export function topActionLayout(boxes, studentHall, safeRight = 0) {
  const result = structuredClone(boxes),
    home = boxes.home;
  if (studentHall) {
    const option = result.option;
    option[0] = home[0] + (home[2] - option[2]) / 2;
    option[1] = home[1] + (home[3] - option[3]) / 2;
    if (result.tasks) {
      result.tasks[0] = boxes.option[0] + (boxes.option[2] - result.tasks[2]) / 2;
      result.tasks[1] = boxes.option[1] + (boxes.option[3] - result.tasks[3]) / 2;
    }
    if (result.taskLine) result.taskLine.splice(0, 2, boxes.line[0], boxes.line[1]);
  }
  const shift = Math.max(0, safeRight - (studentHall ? result.option[0] : home[0]));
  for (const box of Object.values(result)) box[0] += shift;
  return result;
}
/**
 * How far the actions must stand in from the right so they clear the native window controls.
 * @param {{width: number, left: number, right: number, top: number}|undefined} stage The bar's or the stage's
 *     rect.
 * @param {{left: number, right: number, top: number, bottom: number, width?: number}|undefined} controls
 * @param {number[]} box The action's box [right, top, width, height].
 * @param {number} [headerScale] Pixels per stage unit; from the stage's width by default.
 * @returns {number} The clearance in stage units; 0 when the controls do not overlap.
 */
export function nativeRightClearance(stage, controls, box, headerScale) {
  if (!stage?.width || !(controls?.right > controls?.left || controls?.width)) return 0;
  const scale = headerScale || stage.width / 1920,
    top = stage.top + box[1] * scale,
    bottom = top + box[3] * scale;
  if (controls.bottom <= top || controls.top >= bottom || controls.left < stage.left + stage.width / 2) return 0;
  return Math.max(0, (stage.right - controls.left + 8) / scale);
}
