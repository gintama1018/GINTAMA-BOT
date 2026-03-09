"""
modules/location.py — JARVIS Location Module

Sources:
  1. IP geolocation (ipinfo.io) — works everywhere
  2. Android ADB GPS (if device connected)
"""

import json
import logging
import socket
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# Trusted IP geolocation endpoint only
_IPINFO_URL = "https://ipinfo.io/json"


class LocationModule:
    """Get location from IP geolocation or Android GPS."""

    def get_location(self, source: str = "ip") -> dict:
        """
        source: "ip" | "android" | "auto"
        Returns {"ok": True, "lat": ..., "lon": ..., "city": ..., "country": ..., "source": ...}
        """
        if source == "android":
            return self._android_gps()
        if source == "auto":
            result = self._android_gps()
            if result.get("ok"):
                return result
        return self._ip_geo()

    def _ip_geo(self) -> dict:
        """IP geolocation via ipinfo.io."""
        try:
            req = urllib.request.Request(
                _IPINFO_URL,
                headers={"Accept": "application/json", "User-Agent": "JARVIS/3.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
                data = json.loads(resp.read())

            loc = data.get("loc", "")
            lat, lon = (loc.split(",") + ["", ""])[:2]
            return {
                "ok": True,
                "lat": lat.strip(),
                "lon": lon.strip(),
                "city": data.get("city", ""),
                "region": data.get("region", ""),
                "country": data.get("country", ""),
                "ip": data.get("ip", ""),
                "org": data.get("org", ""),
                "source": "ip_geolocation",
            }
        except Exception as exc:
            return {"ok": False, "error": f"IP location failed: {exc}"}

    def _android_gps(self) -> dict:
        """Get GPS coords from Android device via ADB."""
        import subprocess
        try:
            # Try dumpsys location
            result = subprocess.run(
                ["adb", "shell", "dumpsys", "location"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout

            # Parse lat/lon from dumpsys output
            import re
            match = re.search(
                r"(\-?\d+\.\d+),(\-?\d+\.\d+)",
                output
            )
            if match:
                return {
                    "ok": True,
                    "lat": match.group(1),
                    "lon": match.group(2),
                    "source": "android_gps",
                }
            return {"ok": False, "error": "GPS coords not found in dumpsys"}

        except FileNotFoundError:
            return {"ok": False, "error": "ADB not found"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ADB timeout"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def format_location(self, loc: dict) -> str:
        """Format location dict as human-readable string."""
        if not loc.get("ok"):
            return f"Location unavailable: {loc.get('error', 'unknown error')}"
        parts = [x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x]
        text = ", ".join(parts) or "Unknown location"
        if loc.get("lat") and loc.get("lon"):
            text += f" ({loc['lat']}, {loc['lon']})"
        text += f" [via {loc.get('source', '?')}]"
        return text
