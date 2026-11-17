"""Native chrome lifecycle and CSS-pixel geometry, with injectable platform hosts."""

from __future__ import annotations

import logging
import math
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)
# Why the page sent no drag regions, for the log: an immersive scene, or chrome the page does not take as valid yet.
LAYOUT_STATES = ("", "immersive", "waiting")
# The top bar's motion the window buttons play (chrome-motion.js): at most this many offsets, each at most this far.
MOTION_FRAMES, MOTION_REACH = 120, 4096


@dataclass(frozen=True)
class Rect:
    """A rectangle by its edges. The unit is the caller's: CSS pixels (AppKit points) for the chrome's geometry."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def valid(self):
        """Whether every edge is finite and the rectangle is not empty."""
        return (
            all(math.isfinite(v) for v in asdict(self).values()) and self.right > self.left and self.bottom > self.top
        )

    def contains(self, x, y):
        """Whether a point is inside; the left and top edges count, the right and bottom ones do not."""
        return self.left <= x < self.right and self.top <= y < self.bottom


def union(rects):
    """Return the smallest rectangle that covers all of ``rects``.

    Raises:
        ValueError: ``rects`` is empty or has an invalid rectangle.
    """
    if not rects or not all(rect.valid for rect in rects):
        raise ValueError("Native control bounds unavailable")
    return Rect(
        min(r.left for r in rects), min(r.top for r in rects), max(r.right for r in rects), max(r.bottom for r in rects)
    )


def vertical_offsets(rects, centre):
    """Move a group of rectangles vertically, keeping each one's x, size and spacing.

    Args:
        rects: The rectangles, such as the window buttons'.
        centre: The y the group's bounding box is centred on afterwards.

    Returns:
        The moved rectangles, in the order given.
    """
    bounds = union(rects)
    delta = centre - (bounds.top + bounds.bottom) / 2
    return [Rect(r.left, r.top + delta, r.right, r.bottom + delta) for r in rects]


class WindowChrome:
    """The native window's chrome, as far as the page's top bar needs to know it.

    It holds the window state and the page's toolbar layout, measures the native window buttons through a platform
    host (AppKit or Windows) and publishes the result as ``window.chrome`` events. Without a usable host the
    page falls back to its own buttons.
    """

    def __init__(self, window, events, *, platform=sys.platform, host=None):
        """Create the chrome, not yet installed.

        Args:
            window: The pywebview window.
            events: The ``EventBus`` that receives ``window.chrome``.
            platform: A ``sys.platform`` value; it picks the host (macOS and Windows have one).
            host: A platform host to use instead of the one ``platform`` picks, for tests.
        """
        self.window, self.events, self.platform, self.host = window, events, platform, host
        self.active = False
        self.reason = "not-installed"
        self.bounds = None
        self.valid = False
        self.state = {"maximized": False, "fullscreen": False, "minimized": False}
        self.layout = {"centre": 22, "height": 48, "drag": []}
        self.sequence = 0
        self.rejected = ""
        self.empty = ""
        self.updates, self.busy_at = deque(), -math.inf

    def install(self):
        """Create and install the platform host and publish the first measure; any failure ends in ``fail``."""
        try:
            if self.host is None:
                if self.platform == "darwin":
                    from .window_macos import CocoaHost

                    self.host = CocoaHost(self.window, self)
                elif self.platform == "win32":
                    from .window_windows import WindowsHost

                    self.host = WindowsHost(self.window, self)
                else:
                    self.reason = "unsupported-host"
                    return
            self.host.install()
            self.active = True
            self.reason = ""
            logger.info("Native window chrome active: %s", self.platform)
            self.refresh()
        except Exception as error:
            self.fail(error)

    def fail(self, error):
        """Give up the native chrome: dispose of the host, publish the reason and leave the page to its own buttons.

        Args:
            error: What went wrong; its text becomes the published ``reason``.
        """
        logger.warning("Native window chrome inactive: %s", error, exc_info=True)
        if self.host:
            self.host.dispose()
        self.active, self.valid = False, False
        self.bounds = None
        self.reason = str(error)
        self.publish()

    def snapshot(self):
        """Return what the page gets in a ``window.chrome`` event.

        Returns:
            ``active``, ``platform``, ``reason`` (why it is not active), ``mode`` (``standard`` on Windows,
            ``integrated`` elsewhere), the buttons' ``bounds`` (CSS pixels, or None), ``valid``, the window
            ``state`` (maximized, fullscreen, minimized) and a ``sequence`` number that grows with each event.
        """
        return {
            "active": self.active,
            "platform": self.platform,
            "reason": self.reason,
            "mode": "standard" if self.platform == "win32" else "integrated",
            "bounds": asdict(self.bounds) if self.bounds else None,
            "valid": self.valid,
            "state": dict(self.state),
            "sequence": self.sequence,
        }

    def publish(self):
        """Publish the current snapshot as a ``window.chrome`` event."""
        self.sequence += 1
        self.events.publish("window.chrome", self.snapshot())

    def refresh(self):
        """Read the window state and measure the buttons again, then publish; any failure ends in ``fail``."""
        if not self.active:
            return
        try:
            self.state = self.host.state()
            bounds = self.host.measure(self.layout)
            valid = self.platform == "win32" or (bounds is not None and bounds.valid)
            if valid != self.valid:
                # A report must show whether the chrome ever became usable.
                logger.info("Native window chrome %s", "valid" if valid else "waiting")
            self.valid = valid
            if self.valid:
                self.bounds = bounds
            self.publish()
        except Exception as error:
            self.fail(error)

    def update(self, centre, height, drag, state=""):
        """Take the page's toolbar layout and have the host measure again.

        A layout that is rejected leaves the previous one in place.

        Args:
            centre: The y of the toolbar's centre line in CSS pixels, which the native buttons are aligned to.
            height: The toolbar's height in CSS pixels, at most 512 and more than ``centre``.
            drag: The regions of the toolbar that drag the window, up to 64 dicts with ``left``, ``top``,
                ``right`` and ``bottom`` in CSS pixels, inside the toolbar.
            state: Why ``drag`` is empty, for the log: ``immersive`` (a full-screen scene), ``waiting`` (chrome
                the page does not take as valid yet) or empty.

        Raises:
            ValueError: ``state``, the geometry or a drag region is invalid.
            TypeError: A drag region has no usable edges.
        """
        try:
            if state not in LAYOUT_STATES:
                raise ValueError("Invalid toolbar state")
            if (
                not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (centre, height)
                )
                or not 0 < centre < height <= 512
            ):
                raise ValueError("Invalid toolbar geometry")
            if not isinstance(drag, list) or len(drag) > 64:
                raise ValueError("Invalid toolbar drag regions")
            regions = [Rect(**rect) for rect in drag]
            if any(not rect.valid or rect.top < 0 or rect.bottom > height or rect.left < 0 for rect in regions):
                raise ValueError("Invalid toolbar drag region")
        except (ValueError, TypeError) as error:
            # A rejected layout leaves the previous drag regions in place: say so once per distinct reason.
            if str(error) != self.rejected:
                self.rejected = str(error)
                logger.warning(
                    "Native window chrome rejected the toolbar layout: %s (%s drag regions)",
                    error,
                    len(drag) if isinstance(drag, list) else "no",
                )
            raise
        self.rejected = ""
        # A page laying the toolbar out again and again makes the native side measure again and again: say so.
        now = time.monotonic()
        self.updates.append(now)
        while now - self.updates[0] > 10:
            self.updates.popleft()
        if len(self.updates) >= 30 and now - self.busy_at >= 60:
            self.busy_at = now
            logger.info("Native window chrome: %d toolbar layouts in 10 s", len(self.updates))
        empty = "" if regions else state or "covered"
        if bool(regions) != bool(self.layout["drag"]) or (not regions and empty != self.empty):
            logger.info("Native window chrome drag regions: %d%s", len(regions), f" ({empty})" if empty else "")
        self.empty = empty
        self.layout = {"centre": centre, "height": height, "drag": regions}
        if self.active:
            self.host.dispatch(self.refresh)

    def motion(self, frames, period):
        """Play the top bar's motion on the native window buttons, which follow it.

        Only hosts whose buttons sit in the page's toolbar play it.

        Args:
            frames: The top bar's offsets in points up from where the buttons rest, one per period; 1 to 120
                values from 0 to 4096.
            period: Seconds between two offsets, more than 0 and at most 0.1.

        Raises:
            ValueError: ``frames`` or ``period`` is invalid.
        """
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        if (
            not isinstance(frames, list)
            or not 1 <= len(frames) <= MOTION_FRAMES
            or not all(number(v) and 0 <= v <= MOTION_REACH for v in frames)
        ):
            raise ValueError("Invalid window button motion")
        if not number(period) or not 0 < period <= 0.1:
            raise ValueError("Invalid window button motion period")
        if self.active and hasattr(self.host, "slide"):
            frames = [float(v) for v in frames]
            self.host.dispatch(lambda: self.host.slide(frames, float(period)))

    def command(self, action):
        """Do a window action with the native button's behaviour.

        Args:
            action: ``minimize``, ``maximize`` (which restores a maximized window) or ``fullscreen``.
        """
        if self.active:
            self.host.dispatch(lambda: self.host.command(action))
        elif action == "minimize":
            self.window.minimize()
        elif action == "fullscreen":
            self.window.toggle_fullscreen()
        elif self.state["maximized"]:
            self.window.restore()
        else:
            self.window.maximize()

    def fallback_state(self, key, value):
        """Take a window state change reported by the window's own events.

        The host is asked for the state when there is one; otherwise ``value`` is recorded.

        Args:
            key: The state that changed, ``maximized`` or ``minimized``.
            value: Its new value.
        """
        if self.active:
            self.host.dispatch(self.refresh)
        elif self.host is not None:

            def read_state():
                self.state = self.host.state()
                self.publish()

            self.host.dispatch(read_state)
        else:
            self.state[key] = value
            if key == "maximized":
                self.state["minimized"] = False
            self.publish()

    def close(self):
        """Close the window; on macOS through ``performClose:``, which keeps the closing delegate."""
        # Cocoa destroy() skips its delegate. performClose: preserves that delegate.
        if self.platform == "darwin" and getattr(self.window, "native", None) is not None:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self.window.native.performClose_, None)
        else:
            self.window.destroy()

    def dispose(self):
        """Dispose of the host."""
        if self.host:
            self.host.dispose()
