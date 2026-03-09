"""
src/discovery.py — Device discovery via config registry, LAN ping, and Tailscale.

Priority order:
  1. Config registry   (always available)
  2. HTTP /health ping (checks if agent is actually responding)
  3. TCP port check    (fallback — port is open but maybe not agent)
  4. Tailscale API     (optional — requires tailscale CLI installed)
"""

import subprocess
import socket
import threading
import requests
from typing import Dict


class DeviceDiscovery:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        self._cache: Dict[str, dict] = {}
        self._tailscale_ok: bool | None = None
        self._load_from_config()
        # Non-blocking background health check
        threading.Thread(target=self._check_all, daemon=True).start()

    # ---------------------------------------------------------------- #
    # Public API                                                        #
    # ---------------------------------------------------------------- #

    def refresh(self) -> None:
        """Force re-check of all devices."""
        self._load_from_config()
        self._check_all()

    def list_devices(self) -> Dict[str, dict]:
        return dict(self._cache)

    def count_online(self) -> int:
        return sum(1 for d in self._cache.values() if d.get("status") == "online")

    def get_device(self, name: str) -> dict:
        return self._cache.get(name, {})

    def tailscale_available(self) -> bool:
        if self._tailscale_ok is None:
            try:
                result = subprocess.run(
                    ["tailscale", "status"],
                    capture_output=True, text=True, timeout=3, shell=False
                )
                self._tailscale_ok = result.returncode == 0
            except Exception:
                self._tailscale_ok = False
        return bool(self._tailscale_ok)

    # ---------------------------------------------------------------- #
    # Internal                                                          #
    # ---------------------------------------------------------------- #

    def _load_from_config(self) -> None:
        for name, info in self.config.get("devices", {}).items():
            self._cache[name] = {
                "name": name,
                "ip": info.get("ip", ""),
                "type": info.get("type", "unknown"),
                "port": int(info.get("port", 7070)),
                "transport": info.get("transport", "lan"),
                "status": self._cache.get(name, {}).get("status", "checking"),
            }

    def _check_all(self) -> None:
        threads = []
        for name, info in list(self._cache.items()):
            t = threading.Thread(
                target=self._check_device, args=(name, info), daemon=True
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=5)

    def _check_device(self, name: str, info: dict) -> None:
        ip = info.get("ip", "")
        port = info.get("port", 7070)

        if not ip:
            self._cache[name]["status"] = "no-ip (adb mode)"
            self._cache[name]["ip"] = "ADB"
            return

        # Try HTTP /health endpoint first
        try:
            resp = requests.get(f"http://{ip}:{port}/health", timeout=2)
            if resp.status_code == 200:
                self._cache[name]["status"] = "online"
                return
        except Exception:
            pass

        # Fallback: TCP connect
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            rc = sock.connect_ex((ip, port))
            sock.close()
            self._cache[name]["status"] = "online" if rc == 0 else "offline"
        except Exception:
            self._cache[name]["status"] = "offline"
