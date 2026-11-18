"""Server-Sent Events: services publish events, every open page receives them on /api/events."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Callable, Iterator

from .contract import wire_event

HEARTBEAT_SECONDS = 15.0

logger = logging.getLogger(__name__)


class EventBus:
    """Fan out events to subscribers, each with its own bounded queue.

    Observers on the Python side (the tray) are called in ``publish`` itself and are not counted as listeners.
    """

    def __init__(self, max_queue: int = 2000):
        """Create a bus without subscribers.

        Args:
            max_queue: Events a subscriber's queue holds; when a page falls that far behind, its oldest event is
                dropped for each new one.
        """
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._observers: list[Callable[[str, Any], Any]] = []
        self._max_queue = max_queue
        self._closed = threading.Event()

    def observe(self, fn: Callable[[str, Any], Any]) -> Callable[[], None]:
        """Call a function on the publishing thread for every event.

        Args:
            fn: ``fn(event, data)``. It must return at once; real work belongs on another thread. An exception it
                raises is logged and does not stop other observers or subscribers.

        Returns:
            The function that stops the observation.
        """
        with self._lock:
            self._observers.append(fn)

        def stop() -> None:
            with self._lock:
                if fn in self._observers:
                    self._observers.remove(fn)

        return stop

    def publish(self, event: str, data: Any = None) -> None:
        """Deliver an event to the observers and queue it for every subscriber.

        Args:
            event: The event's name, such as ``mods.state``.
            data: The event's payload, JSON-serializable; None is sent as ``{}``.
        """
        message = (event, data if data is not None else {})
        with self._lock:
            subscribers = list(self._subscribers)
            observers = list(self._observers)
        for fn in observers:
            try:
                fn(*message)
            except Exception:
                logger.exception("event observer failed on %s", event)
        message = (event, wire_event(event, message[1]))
        for q in subscribers:
            try:
                q.put_nowait(message)
            except queue.Full:
                # a stalled page drops old events rather than blocking the services
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except (queue.Empty, queue.Full):
                    pass

    def subscribe(self) -> queue.Queue:
        """Add a subscriber and return its queue of ``(event, data)`` pairs; ``unsubscribe`` removes it."""
        q: queue.Queue = queue.Queue(self._max_queue)
        with self._lock:
            self._subscribers.append(q)
        return q

    def listeners(self) -> int:
        """How many pages are connected to /api/events."""
        with self._lock:
            return len(self._subscribers)

    def unsubscribe(self, q: queue.Queue) -> None:
        """Remove a subscriber; a queue that is not subscribed is ignored.

        Args:
            q: The queue ``subscribe`` returned.
        """
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def close(self) -> None:
        """End every stream: publish ``server.closing``, after which ``stream`` returns."""
        self._closed.set()
        self.publish("server.closing")

    def stream(self, heartbeat: float = HEARTBEAT_SECONDS) -> Iterator[bytes]:
        """Stream the events to one client as Server-Sent Events; ends when the bus closes.

        Args:
            heartbeat: Seconds of silence after which a keep-alive comment is sent, so the connection stays open.

        Yields:
            The encoded event messages and keep-alive comments.
        """
        q = self.subscribe()
        try:
            yield b"retry: 1000\n\n"
            while not self._closed.is_set():
                try:
                    event, data = q.get(timeout=heartbeat)
                except queue.Empty:
                    yield b": keep-alive\n\n"
                    continue
                payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                yield f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
                if event == "server.closing":
                    return
        finally:
            self.unsubscribe(q)
