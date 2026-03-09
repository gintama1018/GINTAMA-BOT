"""
src/permission_registry.py — Permission gating for sensitive JARVIS tools.

Every "sensitive" tool requires an explicit permission key.
Granted permissions are persisted to ~/.jarvis/permissions.json so the
user only has to approve once per machine.

Permission keys:
    PHONE_ACCESS    — all phone/ADB tools
    CAMERA_ACCESS   — take_photo, phone_screenshot
    SCREEN_ACCESS   — system_screenshot, screen_record
    FILE_ACCESS     — file_read, file_ls, file_write
    SHELL_ACCESS    — system_run (arbitrary shell commands)
    NETWORK_ACCESS  — web_search, browser_open

Usage:
    from src.permission_registry import get_registry

    reg = get_registry()

    # Check before executing a tool:
    ok, reason = reg.check("system_run")
    if not ok:
        return {"status": "permission_denied", "error": reason}

    # Grant permission interactively or via /permit command:
    reg.grant("SHELL_ACCESS")

    # List all:
    print(reg.list_permissions())
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.permissions")

# ─────────────────────────────────────────────────────────────────────────── #
#  Tool → permission mapping                                                   #
# ─────────────────────────────────────────────────────────────────────────── #

#: Maps tool_name → required permission key.  Tools absent from this dict
#: are always allowed (no sensitive access needed).
TOOL_PERMISSIONS: dict[str, str] = {
    # Phone / Android
    "phone_launch":      "PHONE_ACCESS",
    "phone_screenshot":  "PHONE_ACCESS",
    "phone_battery":     "PHONE_ACCESS",
    "phone_volume":      "PHONE_ACCESS",
    "phone_lock":        "PHONE_ACCESS",
    "phone_notify":      "PHONE_ACCESS",
    # Camera
    "camera_take_photo": "CAMERA_ACCESS",
    # Screen
    "system_screenshot": "SCREEN_ACCESS",
    "screen_record":     "SCREEN_ACCESS",
    # File system
    "file_read":         "FILE_ACCESS",
    "file_ls":           "FILE_ACCESS",
    "file_write":        "FILE_ACCESS",
    # Shell
    "system_run":        "SHELL_ACCESS",
    "system_open":       "SHELL_ACCESS",
    # Network / Web
    "web_search":        "NETWORK_ACCESS",
    "browser_open":      "NETWORK_ACCESS",
}

#: Human-readable description for each permission key.
PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "PHONE_ACCESS":   "Control your Android phone via ADB (launch apps, screenshot, etc.)",
    "CAMERA_ACCESS":  "Access device cameras to capture photos",
    "SCREEN_ACCESS":  "Capture screenshots or record the screen",
    "FILE_ACCESS":    "Read files and list directories on the local machine",
    "SHELL_ACCESS":   "Execute arbitrary shell commands on the local machine",
    "NETWORK_ACCESS": "Perform web searches and open browser pages",
}

_PERM_FILE = Path.home() / ".jarvis" / "permissions.json"


# ─────────────────────────────────────────────────────────────────────────── #
#  Registry class                                                              #
# ─────────────────────────────────────────────────────────────────────────── #

class PermissionRegistry:
    """
    Manages which permission keys have been granted.

    Granted permissions are persisted to disk so they survive restarts.
    """

    def __init__(self, perm_file: Path = _PERM_FILE):
        self._file = perm_file
        self._lock = threading.Lock()
        self._granted: set[str] = set()
        self._load()

    # ---------------------------------------------------------------- #
    # Core API                                                          #
    # ---------------------------------------------------------------- #

    def check(self, tool_name: str) -> tuple[bool, Optional[str]]:
        """
        Return (True, None) if tool_name is allowed, or
        (False, reason_string) if it requires a permission that has not been granted.
        """
        perm = TOOL_PERMISSIONS.get(tool_name)
        if perm is None:
            return True, None  # no restriction

        with self._lock:
            granted = perm in self._granted

        if granted:
            return True, None

        desc = PERMISSION_DESCRIPTIONS.get(perm, perm)
        reason = (
            f"Permission '{perm}' required for tool '{tool_name}'.\n"
            f"  Purpose: {desc}\n"
            f"  To grant: type '/permit {perm}' or call registry.grant('{perm}')"
        )
        return False, reason

    def grant(self, permission: str) -> None:
        """Grant a permission key permanently (persisted to disk)."""
        permission = permission.upper().strip()
        with self._lock:
            self._granted.add(permission)
            self._save()
        log.info("PermissionRegistry: granted %s", permission)

    def revoke(self, permission: str) -> None:
        """Revoke a previously granted permission."""
        permission = permission.upper().strip()
        with self._lock:
            self._granted.discard(permission)
            self._save()
        log.info("PermissionRegistry: revoked %s", permission)

    def is_granted(self, permission: str) -> bool:
        with self._lock:
            return permission.upper() in self._granted

    def grant_all(self) -> None:
        """Grant every known permission (useful for trusted local installs)."""
        with self._lock:
            self._granted.update(PERMISSION_DESCRIPTIONS.keys())
            self._save()
        log.info("PermissionRegistry: ALL permissions granted")

    # ---------------------------------------------------------------- #
    # Introspection                                                     #
    # ---------------------------------------------------------------- #

    def list_permissions(self) -> dict:
        """Return dict with granted list and full catalogue."""
        with self._lock:
            granted = sorted(self._granted)
        return {
            "granted": granted,
            "all": {k: v for k, v in PERMISSION_DESCRIPTIONS.items()},
            "required_by_tool": TOOL_PERMISSIONS,
        }

    def required_for(self, tool_name: str) -> Optional[str]:
        return TOOL_PERMISSIONS.get(tool_name)

    # ---------------------------------------------------------------- #
    # Persistence                                                       #
    # ---------------------------------------------------------------- #

    def _load(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                self._granted = set(data.get("granted", []))
                log.debug(
                    "PermissionRegistry: loaded %d grants from %s",
                    len(self._granted), self._file
                )
            except Exception as exc:
                log.warning("PermissionRegistry: failed to load %s: %s", self._file, exc)
                self._granted = set()

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps(
                    {"granted": sorted(self._granted)},
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("PermissionRegistry: failed to save: %s", exc)


# ─────────────────────────────────────────────────────────────────────────── #
#  Module-level singleton                                                       #
# ─────────────────────────────────────────────────────────────────────────── #

_registry: Optional[PermissionRegistry] = None
_reg_lock = threading.Lock()


def get_registry() -> PermissionRegistry:
    """Return the global PermissionRegistry singleton."""
    global _registry
    if _registry is None:
        with _reg_lock:
            if _registry is None:
                _registry = PermissionRegistry()
    return _registry


# ─────────────────────────────────────────────────────────────────────────── #
#  CLI helper (handle /permit and /revoke commands)                            #
# ─────────────────────────────────────────────────────────────────────────── #

def handle_permission_command(text: str) -> Optional[str]:
    """
    If text is a /permit or /revoke command, handle it and return a response.
    Returns None if text is not a permission command.

    Examples:
        /permit SHELL_ACCESS     → grants SHELL_ACCESS
        /permit all              → grants all permissions
        /revoke FILE_ACCESS      → revokes FILE_ACCESS
        /permissions             → list current state
    """
    text = text.strip()
    lower = text.lower()

    if lower.startswith("/permit "):
        key = text.split(" ", 1)[1].strip().upper()
        reg = get_registry()
        if key == "ALL":
            reg.grant_all()
            return "✅ All permissions granted."
        if key not in PERMISSION_DESCRIPTIONS:
            known = ", ".join(sorted(PERMISSION_DESCRIPTIONS.keys()))
            return f"Unknown permission '{key}'. Known: {known}"
        reg.grant(key)
        return f"✅ Permission '{key}' granted: {PERMISSION_DESCRIPTIONS[key]}"

    if lower.startswith("/revoke "):
        key = text.split(" ", 1)[1].strip().upper()
        get_registry().revoke(key)
        return f"🔒 Permission '{key}' revoked."

    if lower in ("/permissions", "/perms"):
        reg = get_registry()
        info = reg.list_permissions()
        granted = info["granted"] or ["(none)"]
        lines = ["**Current permissions:**"]
        for perm, desc in info["all"].items():
            mark = "✅" if perm in info["granted"] else "❌"
            lines.append(f"  {mark} {perm} — {desc}")
        lines.append(f"\nTo grant: `/permit <PERMISSION>`")
        lines.append(f"To revoke: `/revoke <PERMISSION>`")
        lines.append(f"To grant all: `/permit all`")
        return "\n".join(lines)

    return None
