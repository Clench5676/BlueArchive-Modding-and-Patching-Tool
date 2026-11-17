"""Where the classic window goes: rectangles, screens and the placement maths of each platform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .protocol import CLASSIC_SIZE, MAC_TITLE_BAR


@dataclass(frozen=True)
class Rect:
    """A rectangle with its origin at the top left (Tk's and Win32's coordinates), unless a caller says otherwise."""

    x: float
    y: float
    w: float
    h: float

    @property
    def centre(self) -> tuple[float, float]:
        """The rectangle's centre point."""
        return (self.x + self.w / 2, self.y + self.h / 2)

    def contains(self, px: float, py: float) -> bool:
        """Whether a point is inside; the left and top edges count, the right and bottom ones do not."""
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def rounded(self) -> "Rect":
        """Return the rectangle with every value rounded to a whole number."""
        return Rect(round(self.x), round(self.y), round(self.w), round(self.h))


def centre_rect(parent: Rect, work: Rect, size: tuple[float, float]) -> Rect:
    """Centre a window on another, keeping it inside the screen's work area.

    Args:
        parent: The rectangle to centre on.
        work: The screen's work area; the window is shrunk to fit it and moved inside it.
        size: The wanted width and height.

    Returns:
        The window's rectangle, rounded.
    """
    w, h = min(size[0], work.w), min(size[1], work.h)
    x = parent.x + (parent.w - w) / 2
    y = parent.y + (parent.h - h) / 2
    x = min(max(x, work.x), work.x + work.w - w)
    y = min(max(y, work.y), work.y + work.h - h)
    return Rect(x, y, w, h).rounded()


def screen_for(point: tuple[float, float], screens: Sequence[tuple[Rect, Rect]]) -> Rect:
    """Find the work area of the screen that holds a point.

    Args:
        point: The point, in the screens' coordinates.
        screens: ``(frame, work area)`` pairs, one per screen.

    Returns:
        The work area of the screen that holds ``point``, else of the nearest one.
    """
    for frame, work in screens:
        if frame.contains(*point):
            return work

    def distance(frame: Rect) -> float:
        dx = max(frame.x - point[0], 0, point[0] - (frame.x + frame.w))
        dy = max(frame.y - point[1], 0, point[1] - (frame.y + frame.h))
        return dx * dx + dy * dy

    return min(screens, key=lambda s: distance(s[0]))[1]


def cocoa_to_tk(rect: Rect, primary_height: float) -> Rect:
    """Convert a Cocoa rectangle to Tk's coordinates (origin at the top left of the primary screen, y down).

    Args:
        rect: The rectangle, with its origin at the bottom left of the primary screen and y up.
        primary_height: The height of the primary screen, ``NSScreen.screens()[0]``, not the window's own screen.
    """
    return Rect(rect.x, primary_height - (rect.y + rect.h), rect.w, rect.h)


def mac_placement(
    window: Rect,
    screens: Sequence[tuple[Rect, Rect]],
    size: tuple[int, int] = CLASSIC_SIZE,
    title_bar: int = MAC_TITLE_BAR,
) -> Rect:
    """Decide where the classic window goes on macOS.

    Args:
        window: The BAMT window's frame, in Cocoa coordinates.
        screens: Each screen's frame and visible frame, in Cocoa coordinates, the primary screen first.
        size: The classic window's content size, in logical pixels.
        title_bar: The height of the title bar above the content, in points; it fits on the screen too.

    Returns:
        The rectangle in Tk's coordinates: its position is the frame's top left and its size the content's
        (``wm geometry``).
    """
    primary = screens[0][0].h
    frame = cocoa_to_tk(window, primary)
    work = screen_for(frame.centre, [(cocoa_to_tk(f, primary), cocoa_to_tk(v, primary)) for f, v in screens])
    outer = centre_rect(frame, work, (size[0], size[1] + title_bar))
    return Rect(outer.x, outer.y, outer.w, outer.h - title_bar)


def win_placement(window: Rect, work: Rect, dpi: int, size: tuple[int, int] = CLASSIC_SIZE) -> Rect:
    """Decide where the classic window goes on Windows.

    Both rectangles are in physical pixels, as pywebview makes the web UI DPI aware. The Tk child is not, so its
    logical size is scaled by the monitor's DPI before it is fitted.

    Args:
        window: The BAMT window's bounds.
        work: The work area of its monitor (``rcWork``).
        dpi: The monitor's DPI; 0 counts as 96.
        size: The classic window's logical size.

    Returns:
        The window's rectangle in physical pixels.
    """
    scale = (dpi or 96) / 96
    return centre_rect(window, work, (round(size[0] * scale), round(size[1] * scale)))
