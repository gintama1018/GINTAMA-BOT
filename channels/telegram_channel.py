"""
channels/telegram_channel.py — JARVIS Telegram Bot (Phase 2 PRIMARY CHANNEL)

Setup:
  1. Create a bot via @BotFather on Telegram → get TELEGRAM_BOT_TOKEN
  2. Get your Telegram user ID via @userinfobot
  3. Set in .env:  TELEGRAM_BOT_TOKEN=your_token
  4. Run: python main.py --telegram

Security:
  - dmPolicy = "pairing" by default (unknown senders get code)
  - allowFrom list in config.toml [channels.telegram] allowlist
  - requireMention = True in group chats
  - Rate limited: 10 msg/min, 100 msg/day per sender

Requires: pip install python-telegram-bot
"""

import asyncio
import logging
import os
import threading
from typing import Optional

from channels.base_channel import BaseChannel

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    CHANNEL_NAME = "telegram"

    def __init__(self, config: dict, agent_loop, pairing_manager=None,
                 rate_limiter=None, jarvis_logger=None):
        super().__init__(config, agent_loop, pairing_manager, jarvis_logger)
        self.rate_limiter = rate_limiter
        self._app = None
        self._bot_loop = None
        self._thread: Optional[threading.Thread] = None

        tg_cfg = config.get("channels", {}).get("telegram", {})
        self.token = (
            os.environ.get("TELEGRAM_BOT_TOKEN")
            or tg_cfg.get("token", "")
        )
        self.dm_policy = tg_cfg.get("dm_policy", "pairing")
        self.require_mention = tg_cfg.get("require_mention", True)
        self.allowlist: set = set(str(x) for x in tg_cfg.get("allowlist", []))
        self.owner_id: str = str(tg_cfg.get("owner_id", ""))

        # Seed pairing manager with config allowlist
        if pairing_manager and self.allowlist:
            for uid in self.allowlist:
                pairing_manager._add_to_allowlist(self.CHANNEL_NAME, uid)
        # Always allow owner
        if pairing_manager and self.owner_id:
            pairing_manager._add_to_allowlist(self.CHANNEL_NAME, self.owner_id)

    # ---------------------------------------------------------------- #
    # Lifecycle                                                         #
    # ---------------------------------------------------------------- #

    def start(self) -> None:
        if not self.token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN not set. "
                "Set it in .env or config.toml [channels.telegram] token"
            )
        self._thread = threading.Thread(target=self._run_async, daemon=True)
        self._thread.start()
        print(f"[Telegram] Bot started. Polling for messages...")
        self._thread.join()  # Block main thread

    def stop(self) -> None:
        if self._app and self._bot_loop:
            asyncio.run_coroutine_threadsafe(
                self._app.stop(), self._bot_loop
            )

    def send_message(self, sender_id: str, text: str, **kwargs) -> None:
        """Non-async send — schedules into the bot's own event loop."""
        if self._app and self._bot_loop:
            asyncio.run_coroutine_threadsafe(
                self._app.bot.send_message(chat_id=sender_id, text=text),
                self._bot_loop
            )

    # ---------------------------------------------------------------- #
    # Async runner                                                      #
    # ---------------------------------------------------------------- #

    def _run_async(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import (
                Application, CommandHandler, MessageHandler,
                filters, ContextTypes
            )
        except ImportError:
            print(
                "[Telegram] python-telegram-bot not installed.\n"
                "Run: pip install python-telegram-bot"
            )
            return

        self._bot_loop = asyncio.get_event_loop()

        app = (
            Application.builder()
            .token(self.token)
            .build()
        )
        self._app = app

        # Command handlers
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("new", self._cmd_new))
        app.add_handler(CommandHandler("reset", self._cmd_new))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("memory", self._cmd_memory))
        app.add_handler(CommandHandler("pairing", self._cmd_pairing_admin))

        # Message handler — all text
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        # Voice messages
        app.add_handler(
            MessageHandler(filters.VOICE, self._on_voice)
        )

        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        # Run until interrupted
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    # ---------------------------------------------------------------- #
    # Auth gate                                                         #
    # ---------------------------------------------------------------- #

    async def _auth_gate(self, update, context) -> bool:
        """Returns True if request is allowed to proceed."""
        from telegram import Update
        user = update.effective_user
        chat = update.effective_chat
        sender_id = str(user.id)
        username = user.username or user.first_name or sender_id

        # Group chat: require @mention
        if chat.type in ("group", "supergroup"):
            if self.require_mention:
                text = update.message.text or ""
                bot_username = context.bot.username
                if f"@{bot_username}" not in text:
                    return False  # silently ignore

        # Rate limiting
        if self.rate_limiter:
            allowed, msg = self.rate_limiter.check(f"telegram:{sender_id}")
            if not allowed:
                await update.message.reply_text(msg)
                return False

        # Pairing / allowlist
        if self.pairing:
            result = self.pairing.check_sender(
                self.CHANNEL_NAME, sender_id, self.dm_policy
            )
            if result == "deny":
                return False
            if result == "code":
                code = self.pairing.create_pairing_code(
                    self.CHANNEL_NAME, sender_id, username
                )
                if code:
                    await update.message.reply_text(
                        self.pairing.pairing_request_message(code)
                    )
                else:
                    await update.message.reply_text(
                        "⚠️ Too many pending requests. Ask the owner to approve existing ones first."
                    )
                return False

        return True

    # ---------------------------------------------------------------- #
    # Message handler                                                   #
    # ---------------------------------------------------------------- #

    async def _on_message(self, update, context) -> None:
        if not await self._auth_gate(update, context):
            return

        user = update.effective_user
        sender_id = str(user.id)
        text = update.message.text or ""

        # Typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )

        # Run agent loop (blocking call in executor to avoid blocking event loop)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.agent_loop.run(
                user_message=text,
                channel=self.CHANNEL_NAME,
                sender_id=sender_id,
            )
        )

        if response:
            # Split long messages (Telegram max 4096 chars)
            for chunk in _split_message(response, 4000):
                await update.message.reply_text(chunk)

    async def _on_voice(self, update, context) -> None:
        """Handle voice messages — download and transcribe with Whisper if available."""
        if not await self._auth_gate(update, context):
            return

        await update.message.reply_text("🎤 Received voice message. Whisper transcription coming in Phase 7!")

    # ---------------------------------------------------------------- #
    # Command handlers                                                  #
    # ---------------------------------------------------------------- #

    async def _cmd_start(self, update, context) -> None:
        user = update.effective_user
        sender_id = str(user.id)
        if self.pairing and not self.pairing.is_allowed(self.CHANNEL_NAME, sender_id):
            code = self.pairing.create_pairing_code(
                self.CHANNEL_NAME, sender_id, user.username or ""
            )
            if code:
                await update.message.reply_text(
                    self.pairing.pairing_request_message(code)
                )
            return
        await update.message.reply_text(
            f"👋 Hello {user.first_name}! I'm JARVIS.\n\n"
            "I can control your devices, search the web, take screenshots, and more.\n\n"
            "Just tell me what to do naturally:\n"
            "• \"take a selfie\"\n"
            "• \"what's my phone battery?\"\n"
            "• \"search for weather in my city\"\n"
            "• \"open YouTube on my phone\"\n\n"
            "Type /help for all commands."
        )

    async def _cmd_help(self, update, context) -> None:
        await update.message.reply_text(
            "JARVIS Commands:\n\n"
            "/new or /reset  — Start fresh session\n"
            "/status         — Show bot status\n"
            "/memory         — Show what I remember\n"
            "/help           — This help\n\n"
            "Or just talk naturally! Examples:\n"
            "\"take a selfie\"\n"
            "\"what's my laptop disk space?\"\n"
            "\"open camera and take a photo\"\n"
            "\"search for Python tutorials\""
        )

    async def _cmd_new(self, update, context) -> None:
        user = update.effective_user
        if self.agent_loop._session_manager:
            self.agent_loop._session_manager.clear_session(
                self.CHANNEL_NAME, str(user.id)
            )
        await update.message.reply_text("✅ Session reset. Starting fresh!")

    async def _cmd_status(self, update, context) -> None:
        sm = self.agent_loop._session_manager
        sessions = sm.list_sessions() if sm else []
        await update.message.reply_text(
            f"JARVIS v3.0 Status\n"
            f"Agent: {'✅ ready' if self.agent_loop.is_available() else '❌ offline'}\n"
            f"Sessions: {len(sessions)}\n"
            f"Channel: {self.CHANNEL_NAME}"
        )

    async def _cmd_memory(self, update, context) -> None:
        user = update.effective_user
        sm = self.agent_loop._session_manager
        if sm:
            mem = sm.get_memory(self.CHANNEL_NAME, str(user.id))
            if mem:
                lines = [f"• {k}: {v}" for k, v in mem.items()]
                await update.message.reply_text(
                    "My notes about you:\n" + "\n".join(lines)
                )
                return
        await update.message.reply_text(
            "Nothing stored yet. Tell me your preferences!\n"
            "Example: \"Remember that I prefer responses in Hindi\""
        )

    async def _cmd_pairing_admin(self, update, context) -> None:
        """Admin command: /pairing list | approve <code> | revoke <id>"""
        user = update.effective_user
        sender_id = str(user.id)

        # Only owner can manage pairing
        if self.owner_id and sender_id != self.owner_id:
            if not self.pairing or not self.pairing.is_allowed(self.CHANNEL_NAME, sender_id):
                return

        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage:\n"
                "/pairing list — show pending requests\n"
                "/pairing approve <code> — approve a request\n"
                "/pairing revoke <user_id> — remove access"
            )
            return

        sub = args[0].lower()
        if sub == "list" and self.pairing:
            pending = self.pairing.list_pending(self.CHANNEL_NAME)
            approved = self.pairing.list_approved(self.CHANNEL_NAME)
            msg = f"Pending ({len(pending)}):\n"
            for p in pending:
                msg += f"  [{p['code']}] {p['sender_id']} ({p['username']}) — {p['expires_in_min']}min left\n"
            msg += f"\nApproved ({len(approved)}): {', '.join(approved) or 'none'}"
            await update.message.reply_text(msg)

        elif sub == "approve" and len(args) > 1 and self.pairing:
            approved_id = self.pairing.approve_code(self.CHANNEL_NAME, args[1])
            if approved_id:
                await update.message.reply_text(f"✅ Approved: {approved_id}")
            else:
                await update.message.reply_text("❌ Invalid or expired code.")

        elif sub == "revoke" and len(args) > 1 and self.pairing:
            ok = self.pairing.revoke(self.CHANNEL_NAME, args[1])
            await update.message.reply_text(
                f"✅ Revoked {args[1]}" if ok else f"❌ {args[1]} not in allowlist"
            )


def _split_message(text: str, max_len: int = 4000) -> list:
    """Split long message into chunks at newline boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
