"""
src/logger.py — Structured logging with rotation and query support.

Log format (human-readable):
    [2026-03-09 12:05:33] INFO  phone screenshot       success     118ms
    [2026-03-09 12:06:01] ERROR phone launch badapp    error        42ms  ERROR: App not found
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from typing import Optional


class StructuredLogger:
    def __init__(self, log_dir: str = "logs", level: str = "INFO"):
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "tcc.log")
        self._level_str = level.upper()
        numeric_level = getattr(logging, self._level_str, logging.INFO)

        self._logger = logging.getLogger("tcc")
        self._logger.setLevel(numeric_level)

        if not self._logger.handlers:
            # Rotating file handler — daily, keep 7 days
            fh = TimedRotatingFileHandler(
                self.log_path, when="midnight", backupCount=7, encoding="utf-8"
            )
            fh.setLevel(numeric_level)
            fh.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(fh)

            # Console: only WARNING and above
            ch = logging.StreamHandler()
            ch.setLevel(logging.WARNING)
            ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            self._logger.addHandler(ch)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def log_command(
        self,
        command: str,
        parsed: dict,
        device_ip: str,
        transport: str,
        status: str,
        latency: int,
        response=None,
        error: Optional[str] = None,
    ) -> None:
        """Write one structured command-execution log entry."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level = "ERROR" if status == "error" else "INFO"
        line = f"[{ts}] {level:<5}  {command:<35} {status:<10} {latency:>5}ms"
        if error:
            line += f"  ERR: {error}"
        getattr(self._logger, level.lower())(line)

        # High-latency alert
        if latency > 500:
            self._logger.warning(f"[{ts}] WARN   HIGH LATENCY {latency}ms for: {command}")

    def info(self, msg: str) -> None:
        self._logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] INFO   {msg}")

    def warning(self, msg: str) -> None:
        self._logger.warning(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] WARN   {msg}")

    def error(self, msg: str) -> None:
        self._logger.error(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR  {msg}")

    def debug(self, msg: str) -> None:
        self._logger.debug(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] DEBUG  {msg}")

    def get_recent(
        self,
        n: int = 50,
        level_filter: Optional[str] = None,
        device_filter: Optional[str] = None,
        since_hours: Optional[float] = None,
    ) -> list:
        """Return list of recent log lines matching optional filters."""
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []

        results = []
        for line in lines:
            line = line.rstrip()
            if not line:
                continue
            if level_filter and f"] {level_filter.upper()}" not in line:
                continue
            if device_filter and device_filter.lower() not in line.lower():
                continue
            results.append(line)

        return results[-n:]
