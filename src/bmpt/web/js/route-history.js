// In-app Back follows page entry points. Student tabs and arrows update the current detail entry, so returning
// from a detail workflow does not traverse every pose.
export const ROOT_ROUTE = "#/hall";
const screen = (route) => route.split("/")[1] || "hall";
/**
 * Start a history of the pages the user entered.
 * @returns
 *     {{current: () => string, entries: () => string[], commit: (route: string, options?: {home?: boolean}) => string, back: (requested?: string) => string|null}}
 *     current: the last route; entries: a copy of all; commit: enter a route (home resets to the root) and
 *     return the route; back: the route to go back to, or the last committed page when the requested one is not
 *     the last, or null at the root.
 */
export function routeHistory(root = ROOT_ROUTE) {
  const entries = [root];
  let returning = null;
  return {
    current: () => entries.at(-1),
    entries: () => [...entries],
    commit(route, { home = false } = {}) {
      route ||= root;
      if (home && route === root) {
        entries.splice(0, entries.length, root);
        returning = null;
        return root;
      }
      if (returning === route) {
        returning = null;
        return route;
      }
      returning = null;
      if (route === entries.at(-1)) return route;
      if (screen(route) === "student" && screen(entries.at(-1)) === "student") entries[entries.length - 1] = route;
      else entries.push(route);
      return route;
    },
    back(requested = entries.at(-1)) {
      // Cancelling an unfinished navigation returns to the last committed page.
      if (requested !== entries.at(-1)) return entries.at(-1);
      if (entries.length === 1) return null;
      entries.pop();
      returning = entries.at(-1);
      return returning;
    },
  };
}
