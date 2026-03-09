"""
src/pairing_manager.py — DM pairing codes and sender allowlists.

Mirrors OpenClaw's pairing system exactly:

  Unknown sender → send 6-digit code (expires 1 hour)
  Owner approves via CLI: jarvis pairing approve telegram <code>
  Approved sender ID written to ~/.jarvis/credentials/<channel>-allowlist.json

dmPolicy options (per channel in config.toml):
  "pairing"   — unknown senders get a code to request access (DEFAULT)
  "allowlist" — unknown senders silently blocked
  "open"      — anyone can use (NEVER in production)
  "disabled"  — ignore all DMs

At most 3 pending requests per channel (anti-spam).
"""

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Optional

JARVIS_HOME = Path.home() / ".jarvis" / "credentials"
MAX_PENDING = 3
CODE_TTL_SECONDS = 3600  # 1 hour


class PairingManager:
    """
    Manages sender allowlists and pairing codes for all channels.

    Thread-safe. Allowlists are persisted to ~/.jarvis/credentials/.
    Pending codes are in-memory (lost on restart — by design; keeps things simple).
    """

    def __init__(self, logger=None):
        self.logger = logger
        self._lock = threading.Lock()
        JARVIS_HOME.mkdir(parents=True, mode=0o700, exist_ok=True)
        # pending_codes[channel][code] = {sender_id, expires_at, username}
        self._pending: dict = {}

    # ---------------------------------------------------------------- #
    # Main gate                                                         #
    # ---------------------------------------------------------------- #

    def check_sender(self, channel: str, sender_id: str, policy: str = "pairing") -> str:
        """
        Verify a sender against the allowlist.

        Returns:
            "allow"  — sender is approved, process the message
            "deny"   — silently reject
            "code"   — new pairing code was sent (caller should forward to sender)

        Call this BEFORE running the agent loop. If result is not "allow", stop.
        """
        sender_id = str(sender_id)

        if policy == "open":
            return "allow"
        if policy == "disabled":
            return "deny"

        if self.is_allowed(channel, sender_id):
            return "allow"

        if policy == "allowlist":
            return "deny"

        # policy == "pairing"
        return "code"

    def is_allowed(self, channel: str, sender_id: str) -> bool:
        """Return True if sender_id is in the channel's allowlist."""
        allowlist = self._load_allowlist(channel)
        return str(sender_id) in allowlist

    # ---------------------------------------------------------------- #
    # Pairing code flow                                                 #
    # ---------------------------------------------------------------- #

    def create_pairing_code(
        self, channel: str, sender_id: str, username: str = ""
    ) -> Optional[str]:
        """
        Generate a new 6-digit pairing code for this sender.
        Returns the code string, or None if too many pending requests.
        """
        sender_id = str(sender_id)
        with self._lock:
            pending = self._pending.setdefault(channel, {})

            # Remove expired codes
            now = time.time()
            expired = [c for c, v in pending.items() if v["expires_at"] < now]
            for c in expired:
                del pending[c]

            # Check if this sender already has a pending code
            for code, data in pending.items():
                if data["sender_id"] == sender_id:
                    return code  # reuse existing code

            if len(pending) >= MAX_PENDING:
                return None  # too many pending

            code = str(random.randint(100000, 999999))
            pending[code] = {
                "sender_id": sender_id,
                "expires_at": now + CODE_TTL_SECONDS,
                "username": username,
                "channel": channel,
            }
            if self.logger:
                self.logger.info(
                    f"Pairing code {code} created for {channel}:{sender_id} ({username})"
                )
            return code

    def approve_code(self, channel: str, code: str) -> Optional[str]:
        """
        Approve a pending pairing code. Returns the approved sender_id or None.
        Call this from CLI: jarvis pairing approve telegram 123456
        """
        with self._lock:
            pending = self._pending.get(channel, {})
            data = pending.get(str(code))
            if not data:
                return None
            if time.time() > data["expires_at"]:
                del pending[str(code)]
                return None
            sender_id = data["sender_id"]
            del pending[str(code)]

        self._add_to_allowlist(channel, sender_id)
        if self.logger:
            self.logger.info(f"Approved {channel}:{sender_id}")
        return sender_id

    def revoke(self, channel: str, sender_id: str) -> bool:
        """Remove a sender from the allowlist."""
        allowlist = self._load_allowlist(channel)
        sender_id = str(sender_id)
        if sender_id in allowlist:
            allowlist.discard(sender_id)
            self._save_allowlist(channel, allowlist)
            return True
        return False

    def list_pending(self, channel: str) -> list:
        """Return list of pending pairing requests for a channel."""
        now = time.time()
        with self._lock:
            pending = self._pending.get(channel, {})
            return [
                {
                    "code": code,
                    "sender_id": v["sender_id"],
                    "username": v["username"],
                    "expires_in_min": int((v["expires_at"] - now) / 60),
                }
                for code, v in pending.items()
                if v["expires_at"] > now
            ]

    def list_approved(self, channel: str) -> list:
        """Return list of approved sender IDs for a channel."""
        return sorted(self._load_allowlist(channel))

    # ---------------------------------------------------------------- #
    # Pairing request message                                           #
    # ---------------------------------------------------------------- #

    def pairing_request_message(self, code: str) -> str:
        return (
            f"🔒 JARVIS Pairing Request\n\n"
            f"Your access code is: {code}\n"
            f"(expires in 1 hour)\n\n"
            f"Ask the owner to run:\n"
            f"  jarvis pairing approve <channel> {code}\n\n"
            f"Or in the TCC terminal:\n"
            f"  pairing approve {code}"
        )

    # ---------------------------------------------------------------- #
    # Persistence                                                       #
    # ---------------------------------------------------------------- #

    def _allowlist_path(self, channel: str) -> Path:
        return JARVIS_HOME / f"{channel}-allowlist.json"

    def _load_allowlist(self, channel: str) -> set:
        path = self._allowlist_path(channel)
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return set(data.get("allowed", []))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_allowlist(self, channel: str, allowlist: set) -> None:
        path = self._allowlist_path(channel)
        with open(path, "w") as f:
            json.dump({"allowed": sorted(allowlist)}, f, indent=2)
        # Secure file permissions
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def _add_to_allowlist(self, channel: str, sender_id: str) -> None:
        allowlist = self._load_allowlist(channel)
        allowlist.add(str(sender_id))
        self._save_allowlist(channel, allowlist)
