"""
src/rate_limiter.py — Per-sender rate limiting for channels.

Limits:
  10 messages per minute per sender (configurable)
  100 messages per day per sender (configurable)
  Blocked senders get a "too many requests" message and are silenced.

In-memory only (resets on restart). No Redis needed for personal use.
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """
    Token bucket + sliding window rate limiter per sender.

    Usage:
        rl = RateLimiter(per_minute=10, per_day=100)
        allowed, msg = rl.check("telegram:12345")
        if not allowed:
            send_message(msg)
            return
    """

    def __init__(self, per_minute: int = 10, per_day: int = 100):
        self.per_minute = per_minute
        self.per_day = per_day
        self._lock = threading.Lock()
        # {sender_key: deque of timestamps (last minute)}
        self._minute_window: dict = defaultdict(deque)
        # {sender_key: deque of timestamps (last day)}
        self._day_window: dict = defaultdict(deque)
        # {sender_key: blocked_until timestamp}
        self._blocked: dict = {}

    def check(self, sender_key: str) -> tuple[bool, str]:
        """
        Check if this sender is within rate limits.

        Returns:
            (True, "")               — allowed, proceed
            (False, "message")       — blocked, send this message to user
        """
        now = time.time()

        with self._lock:
            # Check explicit block
            blocked_until = self._blocked.get(sender_key, 0)
            if now < blocked_until:
                wait_min = int((blocked_until - now) / 60) + 1
                return False, f"⏳ Too many requests. Please wait {wait_min} minute(s)."

            # Slide minute window
            minute_q = self._minute_window[sender_key]
            cutoff_min = now - 60
            while minute_q and minute_q[0] < cutoff_min:
                minute_q.popleft()

            # Slide day window
            day_q = self._day_window[sender_key]
            cutoff_day = now - 86400
            while day_q and day_q[0] < cutoff_day:
                day_q.popleft()

            # Check limits
            if len(minute_q) >= self.per_minute:
                self._blocked[sender_key] = now + 120  # block for 2 minutes
                return False, (
                    f"⏳ Slow down! Limit is {self.per_minute} messages per minute. "
                    f"You're unblocked in 2 minutes."
                )

            if len(day_q) >= self.per_day:
                return False, (
                    f"📊 Daily limit of {self.per_day} messages reached. "
                    f"Resets in {int((cutoff_day + 86400 - now) / 3600) + 1} hours."
                )

            # Record this message
            minute_q.append(now)
            day_q.append(now)

        return True, ""

    def reset(self, sender_key: str) -> None:
        """Remove all limits for a sender (admin action)."""
        with self._lock:
            self._minute_window.pop(sender_key, None)
            self._day_window.pop(sender_key, None)
            self._blocked.pop(sender_key, None)

    def status(self, sender_key: str) -> dict:
        """Return current usage stats for a sender."""
        now = time.time()
        with self._lock:
            min_q = self._minute_window.get(sender_key, deque())
            day_q = self._day_window.get(sender_key, deque())
            return {
                "sender": sender_key,
                "last_minute": len(min_q),
                "today": len(day_q),
                "per_minute_limit": self.per_minute,
                "per_day_limit": self.per_day,
                "blocked_until": self._blocked.get(sender_key, 0),
            }
