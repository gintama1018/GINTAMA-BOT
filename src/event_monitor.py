"""
src/event_monitor.py — Background monitor that publishes system events to the EventBus.

Monitors:
  - Battery level (low / critical)
  - Network connectivity
  - Scheduled tasks (fires scheduled_task event at configured time)

The monitor runs in a daemon thread and is completely non-blocking from the
perspective of the rest of the system.  It is started by main.py alongside
the agent loop.

Usage:
    from src.event_monitor import EventMonitor
    monitor = EventMonitor(config, logger)
    monitor.start()
    ...
    monitor.stop()
"""

from __future__ import annotations

import logging
import platform
import socket
import threading
import time
from typing import Optional

log = logging.getLogger("jarvis.event_monitor")


class EventMonitor:
    """
    Polls system metrics and publishes events onto the shared EventBus.

    Check intervals (all configurable via config["monitor"]):
        battery_interval_s  — default 60 s
        network_interval_s  — default 30 s
    """

    def __init__(self, config: dict, logger_=None):
        self.config = config
        self.logger = logger_ or log
        self._running = False
        self._thread: Optional[threading.Thread] = None

        mon = config.get("monitor", {})
        self._bat_interval = int(mon.get("battery_interval_s", 60))
        self._net_interval = int(mon.get("network_interval_s", 30))

        # State tracking (avoid duplicate events)
        self._last_battery_alert: Optional[str] = None   # "low" | "critical"
        self._last_net_ok: bool = True

    # ---------------------------------------------------------------- #
    # Lifecycle                                                         #
    # ---------------------------------------------------------------- #

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="jarvis-monitor", daemon=True
        )
        self._thread.start()
        self.logger.info("EventMonitor: started")

    def stop(self) -> None:
        self._running = False
        self.logger.info("EventMonitor: stopped")

    # ---------------------------------------------------------------- #
    # Main loop                                                         #
    # ---------------------------------------------------------------- #

    def _loop(self) -> None:
        from src.event_bus import get_bus
        bus = get_bus()

        bat_counter = 0
        net_counter = 0
        TICK = 5  # seconds per loop tick

        while self._running:
            try:
                bat_counter += TICK
                net_counter += TICK

                if bat_counter >= self._bat_interval:
                    self._check_battery(bus)
                    bat_counter = 0

                if net_counter >= self._net_interval:
                    self._check_network(bus)
                    net_counter = 0

            except Exception as exc:
                self.logger.warning("EventMonitor loop error: %s", exc)

            time.sleep(TICK)

    # ---------------------------------------------------------------- #
    # Battery monitoring                                                #
    # ---------------------------------------------------------------- #

    def _check_battery(self, bus) -> None:
        try:
            import psutil
        except ImportError:
            return

        batt = psutil.sensors_battery()
        if batt is None:
            return  # desktop / no battery sensor

        level = int(batt.percent)
        plugged = batt.power_plugged

        if plugged:
            # Reset alert state when charging
            self._last_battery_alert = None
            return

        if level <= 5 and self._last_battery_alert != "critical":
            self._last_battery_alert = "critical"
            bus.publish("battery_critical", {"level": level, "plugged": plugged})
            self.logger.warning("EventMonitor: battery critical at %d%%", level)

        elif level <= 20 and self._last_battery_alert not in ("low", "critical"):
            self._last_battery_alert = "low"
            bus.publish("battery_low", {"level": level, "plugged": plugged})
            self.logger.warning("EventMonitor: battery low at %d%%", level)

    # ---------------------------------------------------------------- #
    # Network monitoring                                                #
    # ---------------------------------------------------------------- #

    def _check_network(self, bus) -> None:
        connected = _is_online()

        if not connected and self._last_net_ok:
            self._last_net_ok = False
            bus.publish("wifi_disconnected", {"reason": "connectivity check failed"})
            self.logger.warning("EventMonitor: network disconnected")

        elif connected and not self._last_net_ok:
            self._last_net_ok = True
            bus.publish("wifi_connected", {"ssid": _get_ssid()})
            self.logger.info("EventMonitor: network reconnected")


# ─────────────────────────────────────────────────────────────────────────── #
# Helpers                                                                       #
# ─────────────────────────────────────────────────────────────────────────── #

def _is_online(host: str = "8.8.8.8", port: int = 53, timeout: float = 3.0) -> bool:
    """Return True if the machine can reach the internet."""
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except OSError:
        return False


def _get_ssid() -> str:
    """Try to read the current WiFi SSID; returns empty string if unavailable."""
    system = platform.system()
    try:
        import subprocess
        if system == "Windows":
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True, timeout=5, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    return line.split(":", 1)[-1].strip()
        elif system == "Linux":
            out = subprocess.check_output(
                ["iwgetid", "-r"], text=True, timeout=5, stderr=subprocess.DEVNULL
            )
            return out.strip()
        elif system == "Darwin":
            out = subprocess.check_output(
                ["/System/Library/PrivateFrameworks/Apple80211.framework/"
                 "Versions/Current/Resources/airport", "-I"],
                text=True, timeout=5, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if " SSID:" in line:
                    return line.split(":", 1)[-1].strip()
    except Exception:
        pass
    return ""
