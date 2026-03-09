"""
src/session_manager.py — SQLite-backed session and memory store.

Each sender (identified by channel + sender_id) gets an ISOLATED session:
  - Conversation history (last 50 messages loaded per LLM call)
  - User memory (persistent preferences injected into system prompt)

Database: ~/.jarvis/sessions.db
Sessions table: id, channel, sender_id, started_at, last_active
Messages table: id, session_id, role, content, tool_calls, tool_results, timestamp
Memory table:   sender_key, data, updated_at
"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

JARVIS_HOME = Path.home() / ".jarvis"
DB_PATH = JARVIS_HOME / "sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    channel     TEXT NOT NULL,
    sender_id   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    last_active TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    tool_calls   TEXT,
    tool_results TEXT,
    timestamp    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS memory (
    sender_key TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""


class SessionManager:
    """
    Manages per-sender isolated sessions and persistent user memory.

    Thread-safe via a per-instance lock. Each TCC_CLI creates one instance.
    Multiple channel adapters (Telegram, Discord, etc.) can share one instance.
    """

    def __init__(self):
        JARVIS_HOME.mkdir(mode=0o700, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(DB_PATH), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ---------------------------------------------------------------- #
    # Session management                                               #
    # ---------------------------------------------------------------- #

    def _key(self, channel: str, sender_id: str) -> str:
        return f"{channel}:{sender_id}"

    def get_or_create_session(self, channel: str, sender_id: str) -> str:
        """Return session id (channel:sender_id), creating it if not present."""
        key = self._key(channel, sender_id)
        now = datetime.utcnow().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (key,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO sessions (id, channel, sender_id, started_at, last_active) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, channel, sender_id, now, now),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET last_active = ? WHERE id = ?", (now, key)
                )
        return key

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: Optional[list] = None,
        tool_results: Optional[list] = None,
    ) -> None:
        """Append a message to the session history."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, tool_calls, tool_results, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(tool_calls) if tool_calls else None,
                    json.dumps(tool_results) if tool_results else None,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_history(self, session_id: str, limit: int = 50) -> list:
        """Return last `limit` messages for this session, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, tool_calls, tool_results FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        history = []
        for row in reversed(rows):
            history.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                    "tool_calls": (
                        json.loads(row["tool_calls"]) if row["tool_calls"] else None
                    ),
                    "tool_results": (
                        json.loads(row["tool_results"]) if row["tool_results"] else None
                    ),
                }
            )
        return history

    def clear_session(self, channel: str, sender_id: str) -> None:
        """Delete all messages for a session and remove the session record."""
        key = self._key(channel, sender_id)
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (key,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (key,))

    def list_sessions(self) -> list:
        """Return all sessions sorted by last_active descending."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, channel, sender_id, started_at, last_active "
                "FROM sessions ORDER BY last_active DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- #
    # Memory                                                           #
    # ---------------------------------------------------------------- #

    def get_memory(self, channel: str, sender_id: str) -> dict:
        """Return stored preferences for this sender."""
        key = self._key(channel, sender_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM memory WHERE sender_key = ?", (key,)
            ).fetchone()
        return json.loads(row["data"]) if row else {}

    def update_memory(self, channel: str, sender_id: str, updates: dict) -> None:
        """Merge `updates` into this sender's memory."""
        key = self._key(channel, sender_id)
        current = self.get_memory(channel, sender_id)
        current.update(updates)
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory (sender_key, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(sender_key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (key, json.dumps(current), now),
            )

    def clear_memory(self, channel: str, sender_id: str) -> None:
        """Wipe all memory for this sender."""
        key = self._key(channel, sender_id)
        with self._lock:
            self._conn.execute("DELETE FROM memory WHERE sender_key = ?", (key,))
