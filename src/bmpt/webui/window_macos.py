"""M1: public AppKit standard buttons and window-scoped native drag events."""

from __future__ import annotations

import logging
import math
import time
from collections import deque

from .window_chrome import Rect, union, vertical_offsets

logger = logging.getLogger(__name__)


class PointerProbe:
    """What AppKit shows as the pointer while it moves over the window, for the session log (BL-080): the system arrow,
    a hand, the text beam, or an image (the page's game pointer). Logs a burst of changes (the two flickering) and a system
    pointer that stays over the page; quiet otherwise."""
    WINDOW, BURST, STUCK, QUIET = 10.0, 8, 5.0, 60.0
    PAGE = ("image", "I-beam")

    def __init__(self, api, clock=time.monotonic):
        self.api, self.clock = api, clock
        self.kind, self.since, self.changes = None, 0.0, deque()
        self.burst_at = self.stuck_at = -math.inf
        self.stuck_logged = False

    def kind_of(self, cursor):
        if cursor is None:
            return "none"
        known = self.api.NSCursor
        for name, factory in (("arrow", "arrowCursor"), ("open hand", "openHandCursor"), ("closed hand", "closedHandCursor"),
                              ("I-beam", "IBeamCursor"), ("pointing hand", "pointingHandCursor")):
            if cursor == getattr(known, factory)():
                return name
        return "image"

    def moved(self, y, over_page):
        now, kind = self.clock(), self.kind_of(self.api.NSCursor.currentCursor())
        if kind != self.kind:
            if self.kind is not None:
                self.changes.append(now)
            self.kind, self.since, self.stuck_logged = kind, now, False
        while self.changes and now - self.changes[0] > self.WINDOW:
            self.changes.popleft()
        if len(self.changes) >= self.BURST and now - self.burst_at >= self.WINDOW:
            self.burst_at = now
            logger.info("Native pointer changed %d times in %.0f s, now %s", len(self.changes), self.WINDOW, kind)
        if (over_page and kind not in self.PAGE and not self.stuck_logged and now - self.since >= self.STUCK
                and now - self.stuck_at >= self.QUIET):
            self.stuck_logged, self.stuck_at = True, now
            logger.info("Native pointer: %s for %.0f s over the page (y %.0f)", kind, now - self.since, y)


class CocoaHost:
    # A missing content view is measured again this often, this many times (pywebview swaps it while it loads).
    RETRY_DELAY, RETRY_LIMIT = 0.25, 8
    # The buttons start hidden and stay so until the page says where its top bar is (its first motion), so no coloured
    # dots show on the black start screen; this long after install they appear anyway, should the page never speak.
    HOLD_LIMIT = 8.0
    held = False
    # The top bar's offset the buttons share (points up, chrome-motion.js), the motion playing it, where they were
    # aligned (origins) and rest (content rect), and what of the offset and hiding is on them now.
    offset, motion, base, resting, applied, hid = 0.0, 0, (), None, 0.0, False
    # the toolbar centre line the buttons were last aligned to; the pointer's log
    centre, probe, pointer_monitor = None, None, None

    def __init__(self, window, owner, *, api=None, dispatch=None, later=None):
        if api is None:
            import AppKit as api
        if dispatch is None or later is None:
            from PyObjCTools import AppHelper
            dispatch = dispatch or AppHelper.callAfter
            later = later or AppHelper.callLater
        self.api, self.dispatch, self.later = api, dispatch, later
        self.window, self.owner = window.native, owner
        self.buttons, self.original, self.observers = [], [], []
        self.monitor = None
        self.transition = False
        self.retries = 0
        self.ignored = ""

    def mouse_down(self, event):
        # A local monitor runs before WKWebView hit testing, including after Cocoa
        # replaces contentView. Rectangles arrive in viewport CSS pixels (AppKit points).
        if event.window() != self.window or not self.owner.active:
            return event
        content = self.window.contentView()
        if content is None:
            return event
        point = content.convertPoint_fromView_(event.locationInWindow(), None)
        bounds = content.bounds()
        x = point.x - bounds.origin.x
        y = point.y - bounds.origin.y if content.isFlipped() else bounds.origin.y + bounds.size.height - point.y
        regions = self.owner.layout["drag"]
        reason = ("invalid" if not self.owner.valid else "transition" if self.transition else
                  "no drag regions" if not regions else "")
        if reason and 0 <= y < self.owner.layout["height"]:
            # A press on the top bar that cannot drag: logged once per reason, so a report shows why (BL-078).
            if reason != self.ignored:
                self.ignored = reason
                logger.info("Native window chrome ignored a top bar press: %s", reason)
        if reason or not any(r.contains(x, y) for r in regions):
            return event
        self.ignored = ""
        if event.clickCount() == 2:
            action = self.api.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick")
            if action == "Minimize":
                self.window.performMiniaturize_(None)
            elif action not in ("None", "Do Nothing"):
                self.window.performZoom_(None)
        else:
            self.window.performWindowDragWithEvent_(event)
        return None

    def _rect(self, button, view):
        frame = button.convertRect_toView_(button.bounds(), view)
        top = frame.origin.y if view.isFlipped() else view.bounds().size.height - frame.origin.y - frame.size.height
        return Rect(frame.origin.x, top, frame.origin.x + frame.size.width, top + frame.size.height)

    def install(self):
        api, window = self.api, self.window
        # This content policy is independent of M1 button alignment/rollback.
        # AppKit applies it to interactive resizing, leaving fullscreen to the OS.
        window.setContentAspectRatio_((16, 9))
        for kind in (api.NSWindowCloseButton, api.NSWindowMiniaturizeButton, api.NSWindowZoomButton):
            button = window.standardWindowButton_(kind)
            if button is None:
                raise RuntimeError("AppKit standard window button missing")
            self.buttons.append(button)
            self.original.append((button.frame().origin, button.isHidden()))
            button.setHidden_(True)
        self.held = True
        self.later(self.HOLD_LIMIT, self.release)
        self.monitor = api.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(api.NSEventMaskLeftMouseDown, self.mouse_down)
        self.probe = PointerProbe(api)
        self.pointer_monitor = api.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(api.NSEventMaskMouseMoved,
                                                                                         self.pointer_moved)
        centre = api.NSNotificationCenter.defaultCenter()
        for name in ("NSWindowDidResizeNotification", "NSWindowDidMoveNotification", "NSWindowDidBecomeKeyNotification",
                     "NSWindowDidResignKeyNotification", "NSWindowDidMiniaturizeNotification", "NSWindowDidDeminiaturizeNotification",
                     "NSWindowWillEnterFullScreenNotification", "NSWindowWillExitFullScreenNotification",
                     "NSWindowDidEnterFullScreenNotification", "NSWindowDidExitFullScreenNotification"):
            def changed(note, name=name):
                if "Will" in name:
                    self.transition = True
                elif "FullScreen" in name:
                    self.transition = False
                self.owner.refresh()
            self.observers.append(centre.addObserverForName_object_queue_usingBlock_(getattr(api, name), window, None, changed))

    def pointer_moved(self, event):
        # Only reads AppKit's pointer for the log; the event goes on unchanged whatever happens here.
        try:
            content = self.window.contentView()
            if event.window() == self.window and content is not None and self.probe is not None:
                point = content.convertPoint_fromView_(event.locationInWindow(), None)
                bounds = content.bounds()
                y = point.y - bounds.origin.y if content.isFlipped() else bounds.origin.y + bounds.size.height - point.y
                self.probe.moved(y, y >= self.owner.layout["height"])
        except Exception:  # never let the log stand in the way of a move
            pass
        return event

    def _placed(self, centre):
        """The buttons still where the last alignment and the offset put them, for the same centre line."""
        if not self.base or self.resting is None or centre != self.centre:
            return False
        for button, (x, y) in zip(self.buttons, self.base):
            up = -1 if button.superview().isFlipped() else 1
            origin = button.frame().origin
            if abs(origin.x - x) > .01 or abs(origin.y - (y + up * self.applied)) > .01:
                return False
        return True

    def release(self):
        """End the start hold: the buttons take the place the offset gives them (hidden when it is wholly above the window)."""
        if not self.held:
            return
        self.held = False
        self.hid = True  # _place() decides the visibility from the offset now
        self._place()

    def slide(self, frames, period):
        """Play the page's top bar offsets on the buttons, one per period; a newer motion replaces this one."""
        self.motion += 1
        token = self.motion
        if self.held:
            # the page's first word on its top bar: the buttons take that place at once, for they were never shown at the
            # place the motion starts from
            self.offset = frames[-1]
            self.release()
            return

        def step(index):
            if token != self.motion:
                return
            self.offset = frames[index]
            self._place()
            if index + 1 < len(frames):
                self.later(period, lambda: step(index + 1))
        step(0)

    def _place(self):
        """The buttons at their aligned place moved up by the offset, hidden once wholly above the window. macOS
        fullscreen shows and hides them itself: there they only get back what this changed."""
        if self.held or not self.base or self.transition or self.window.contentView() is None:
            return
        fullscreen = self.state()["fullscreen"]
        offset = 0.0 if fullscreen else self.offset
        hide = bool(offset and self.resting is not None and offset >= self.resting.bottom)
        for button, (x, y) in zip(self.buttons, self.base):
            up = -1 if button.superview().isFlipped() else 1
            origin = button.frame().origin
            # in fullscreen AppKit may have laid the buttons out again: move only those still where this put them. A move
            # to where a button already is would still make AppKit reset the pointer: skip it.
            if ((not fullscreen or abs(origin.y - (y + up * self.applied)) < .5)
                    and (abs(origin.x - x) > .01 or abs(origin.y - (y + up * offset)) > .01)):
                button.setFrameOrigin_((x, y + up * offset))
            if (hide or self.hid) and button.isHidden() != hide:
                button.setHidden_(hide)
        self.applied, self.hid = offset, hide

    def _within_container(self, dy):
        """The downward move (content points) closest to ``dy`` that keeps every button inside its container; 0 when none
        does."""
        low, high = -float("inf"), float("inf")
        for button in self.buttons:
            frame, parent = button.frame(), button.superview()
            bounds = parent.bounds()
            # the move allowed in the container's own coordinates, then turned into a downward move
            first = bounds.origin.y - frame.origin.y
            last = bounds.origin.y + bounds.size.height - (frame.origin.y + frame.size.height)
            if not parent.isFlipped():
                first, last = -last, -first
            low, high = max(low, first), min(high, last)
        if low > high:
            return 0
        return min(max(dy, low), high)

    def state(self):
        return {"maximized": bool(self.window.isZoomed()), "minimized": bool(self.window.isMiniaturized()),
                "fullscreen": bool(self.window.styleMask() & self.api.NSWindowStyleMaskFullScreen)}

    def measure(self, layout):
        content = self.window.contentView()
        # pywebview's close handler removes the web view, which is the content view, before AppKit posts the window's
        # last notifications: nothing to measure then, and no reason to give the native chrome up. No notification
        # follows when a content view arrives, so measure again a few times rather than wait for a resize or a move.
        if content is None:
            if self.retries < self.RETRY_LIMIT:
                self.retries += 1
                self.later(self.RETRY_DELAY, self.owner.refresh)
            return None
        self.retries = 0
        if self.window.isMiniaturized() or self.transition:
            return None
        fullscreen = self.state()["fullscreen"]
        if not fullscreen:
            # Every page layout measures again. Buttons already aligned (and moved by the top bar's offset) stay put: each
            # real move makes AppKit reset the pointer, which flickered against the page's game pointer.
            if self._placed(layout["centre"]):
                if self.offset != self.applied:
                    self._place()
                return self.resting
            before = [self._rect(button, content) for button in self.buttons]
            wanted = vertical_offsets(before, layout["centre"])
            # The title bar container is only so high (32 pt with the current SDK). On a wide window the toolbar's centre line
            # sits lower than the buttons can go inside it: they go as low as it allows rather than giving the controls up.
            dy = self._within_container(wanted[0].top - before[0].top)
            targets = [Rect(r.left, r.top + dy, r.right, r.bottom + dy) for r in before]
            for button in self.buttons:
                frame, parent = button.frame(), button.superview()
                button.setFrameOrigin_((frame.origin.x, frame.origin.y + (dy if parent.isFlipped() else -dy)))
                frame, bounds = button.frame(), parent.bounds()
                if not self.api.NSContainsRect(bounds, frame):
                    raise RuntimeError("M1 button would be clipped by its AppKit container")
            after = [self._rect(button, content) for button in self.buttons]
            for source, target, actual in zip(before, targets, after):
                if abs(source.left - actual.left) > .5 or abs(target.top - actual.top) > .5:
                    raise RuntimeError("M1 AppKit relayout rejected vertical button alignment")
            self.base = [(button.frame().origin.x, button.frame().origin.y) for button in self.buttons]
            self.applied, self.centre = 0.0, layout["centre"]
        else:
            # AppKit lays them out in fullscreen: they get back what the offset changed, and are aligned again on the way out
            self.centre = None
            if self.applied or self.hid:
                self._place()
        # Fullscreen visibility/reveal belongs to AppKit; never force buttons visible there.
        visible = [self._rect(button, content) for button in self.buttons]
        if not visible:
            return None
        bounds = union(visible)
        if fullscreen:
            return bounds
        # where the buttons rest, though the top bar's motion may have them elsewhere now
        self.resting = bounds
        if self.offset or self.hid:
            self._place()
        return self.resting

    def command(self, action):
        selectors = {"minimize": self.window.performMiniaturize_, "maximize": self.window.performZoom_,
                     "fullscreen": self.window.toggleFullScreen_}
        selectors[action](None)

    def dispose(self):
        self.motion += 1
        self.held = False
        centre = self.api.NSNotificationCenter.defaultCenter()
        for observer in self.observers:
            centre.removeObserver_(observer)
        self.observers.clear()
        if self.monitor is not None:
            self.api.NSEvent.removeMonitor_(self.monitor)
            self.monitor = None
        if self.pointer_monitor is not None:
            self.api.NSEvent.removeMonitor_(self.pointer_monitor)
            self.pointer_monitor = None
        for button, (origin, hidden) in zip(self.buttons, self.original):
            button.setFrameOrigin_(origin)
            button.setHidden_(hidden)
