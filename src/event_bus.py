"""
src/event_bus.py — Central event bus for JARVIS.

A lightweight publish/subscribe system.  Any part of the system can:
  - subscribe to an event type and receive a callback when it fires
  - publish an event with arbitrary data

Event types used in JARVIS:
    battery_low         data: {level: int, plugged: bool}
    battery_critical    data: {level: int}
    wifi_disconnected   data: {}
    wifi_connected      data: {ssid: str}
    notification        data: {title: str, message: str, source: str}
    scheduled_task      data: {name: str, command: str}
    tool_executed       data: {tool: str, args: dict, result: dict}
    agent_response      data: {sender_id: str, text: str}
    permission_denied   data: {tool: str, permission: str}

Usage:
    from src.event_bus import get_bus

    bus = get_bus()
    bus.subscribe("battery_low", lambda etype, data: print(f"Low battery: {data}"))
    bus.publish("battery_low", {"level": 15, "plugged": False})
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

logger = logging.getLogger("jarvis.event_bus")


class EventBus:
    """Thread-safe publish/subscribe event bus."""

    def __init__(self):
        self._lock = threading.Lock()
        # event_type → list of (callback, one_shot)
        self._listeners: dict[str, list] = defaultdict(list)

    # ---------------------------------------------------------------- #
    # Subscription                                                      #
    # ---------------------------------------------------------------- #

    def subscribe(
        self,
        event_type: str,
        callback: Callable[[str, dict], None],
        one_shot: bool = False,
    ) -> None:
        """
        Subscribe to an event.

        Args:
            event_type: The event type string to listen for (or "*" for all).
            callback:   Called as callback(event_type, data_dict).
            one_shot:   If True, automatically unsubscribe after first fire.
        """
        with self._lock:
            self._listeners[event_type].append((callback, one_shot))

    def unsubscribe(
        self,
        event_type: str,
        callback: Callable[[str, dict], None],
    ) -> None:
        """Remove a previously registered callback."""
        with self._lock:
            lst = self._listeners.get(event_type, [])
            self._listeners[event_type] = [
                (cb, os_) for cb, os_ in lst if cb is not callback
            ]

    # ---------------------------------------------------------------- #
    # Publishing                                                        #
    # ---------------------------------------------------------------- #

    def publish(self, event_type: str, data: dict | None = None) -> None:
        """
        Fire an event.  Invokes all matching subscribers plus wildcard ("*") subscribers.

        Callbacks are called on the publishing thread. If a callback raises,
        the error is logged and the next callback still executes.
        """
        data = data or {}
        to_call: list = []

        with self._lock:
            for target in (event_type, "*"):
                survivors = []
                for cb, one_shot in self._listeners.get(target, []):
                    to_call.append((cb, event_type, data))
                    if not one_shot:
                        survivors.append((cb, one_shot))
                self._listeners[target] = survivors

        for cb, etype, d in to_call:
            try:
                cb(etype, d)
            except Exception as exc:
                logger.warning("EventBus: callback %s raised: %s", cb, exc)

    # ---------------------------------------------------------------- #
    # Utility                                                           #
    # ---------------------------------------------------------------- #

    def listener_count(self, event_type: str) -> int:
        with self._lock:
            return len(self._listeners.get(event_type, []))

    def clear(self, event_type: str | None = None) -> None:
        """Remove all listeners for event_type (or all events if None)."""
        with self._lock:
            if event_type:
                self._listeners.pop(event_type, None)
            else:
                self._listeners.clear()


# ──────────────────────────────────────────────────────────────────────────── #
# Module-level singleton                                                        #
# ──────────────────────────────────────────────────────────────────────────── #

_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    """Return the global EventBus singleton (created on first call)."""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus
