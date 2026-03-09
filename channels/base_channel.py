"""
channels/base_channel.py — Abstract base class for all JARVIS channels.

Every channel (Telegram, Discord, WhatsApp, Voice, WebChat) implements this
interface so the agent loop is channel-agnostic.

Flow:
  channel receives message
    → validates sender (pairing/allowlist)
    → calls AgentLoop.run(message, channel=..., sender_id=...)
    → sends response back via send_message()
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseChannel(ABC):
    """Abstract base for Telegram, Discord, Slack, Voice, etc."""

    CHANNEL_NAME: str = "base"  # override in subclass

    def __init__(self, config: dict, agent_loop, pairing_manager=None, logger=None):
        self.config = config
        self.agent_loop = agent_loop
        self.pairing = pairing_manager
        self.logger = logger

    # ---------------------------------------------------------------- #
    # Subclasses must implement these                                   #
    # ---------------------------------------------------------------- #

    @abstractmethod
    def start(self) -> None:
        """Start the channel listener (blocking or spawns background thread)."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Gracefully stop the channel."""
        raise NotImplementedError

    @abstractmethod
    def send_message(self, sender_id: str, text: str, **kwargs) -> None:
        """Send a message back to the sender on this channel."""
        raise NotImplementedError

    # ---------------------------------------------------------------- #
    # Shared logic                                                      #
    # ---------------------------------------------------------------- #

    def handle_message(self, sender_id: str, text: str, username: str = "") -> Optional[str]:
        """
        Common message handling pipeline.
        Returns the response text, or None if the message should be silently ignored.
        """
        if not text or not text.strip():
            return None

        # Slash commands
        stripped = text.strip()
        if stripped.startswith("/"):
            return self._handle_slash(sender_id, stripped)

        # Run agent loop
        log = self.logger
        if log:
            log.debug(f"[{self.CHANNEL_NAME}] {sender_id}: {text[:80]}")

        response = self.agent_loop.run(
            user_message=text,
            channel=self.CHANNEL_NAME,
            sender_id=sender_id,
        )
        return response

    def _handle_slash(self, sender_id: str, command: str) -> str:
        """Handle /new, /reset, /status, /help slash commands."""
        cmd = command.lower().split()[0]
        if cmd in ("/new", "/reset"):
            if self.agent_loop._session_manager:
                self.agent_loop._session_manager.clear_session(self.CHANNEL_NAME, sender_id)
            return "Session reset. Starting fresh!"
        elif cmd == "/status":
            sessions = []
            if self.agent_loop._session_manager:
                sessions = self.agent_loop._session_manager.list_sessions()
            return (
                f"JARVIS v3.0 running\n"
                f"Channel: {self.CHANNEL_NAME}\n"
                f"Agent: {'ready' if self.agent_loop.is_available() else 'offline'}\n"
                f"Sessions: {len(sessions)}"
            )
        elif cmd == "/memory":
            sm = self.agent_loop._session_manager
            if sm:
                mem = sm.get_memory(self.CHANNEL_NAME, sender_id)
                if mem:
                    lines = [f"{k}: {v}" for k, v in mem.items()]
                    return "My notes about you:\n" + "\n".join(lines)
                return "Nothing stored yet. Tell me your preferences!"
            return "Memory not available."
        elif cmd == "/help":
            return (
                "JARVIS Commands:\n"
                "/new or /reset  - Start fresh session\n"
                "/status         - Show bot status\n"
                "/memory         - Show what I remember\n"
                "/help           - This help message\n\n"
                "Or just talk naturally:\n"
                "\"take a selfie\"\n"
                "\"what's my phone battery\"\n"
                "\"search for Python tutorials\"\n"
                "\"open YouTube on my phone\""
            )
        return f"Unknown command: {command}. Type /help for commands."
