/**
 * The viewport of a memorial lobby's still for a card aspect: the same NGUI camera and skeleton placement as
 * memorial-lobby.js / gameassets.memorialLobby. Card aspects crop inside that camera; skeleton bounds never reveal
 * off-camera art.
 * @param {{view?: object, layer?: object}|undefined} info The memorial lobby's information: the camera view and the
 *     skeleton's placement.
 * @param {number} aspect The card's width over its height.
 */
export function memorialLobbyStillViewport(info, aspect) {
  const view = info?.view || { x: -1480, y: -832.5, w: 2960, h: 1665 };
  const layer = info?.layer || { x: 0, y: -962, scale: 1 };
  const width = Math.min(view.w, view.h * aspect),
    height = width / aspect;
  const x = view.x + (view.w - width) / 2,
    y = view.y + (view.h - height) / 2;
  return {
    x: (x - layer.x) / layer.scale,
    y: (y - layer.y) / layer.scale,
    width: width / layer.scale,
    height: height / layer.scale,
    padLeft: 0,
    padRight: 0,
    padTop: 0,
    padBottom: 0,
    transitionTime: 0,
    animations: {},
  };
}
