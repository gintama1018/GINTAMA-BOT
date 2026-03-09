"""
channels/discord_channel.py — JARVIS Discord Bot (Phase 5)

Setup:
  1. Create bot at https://discord.com/developers/ → get DISCORD_BOT_TOKEN
  2. Enable "Message Content Intent" in bot settings
  3. Set in .env: DISCORD_BOT_TOKEN=your_token
  4. Run: python main.py --discord

Security:
  - requireMention = True in servers (bot ignores messages without @mention)
  - allowlist of user IDs in config.toml [channels.discord]
  - Rate limited: 10 msg/min, 100 msg/day per sender

Requires: pip install discord.py
"""

import asyncio
import os
import threading
from typing import Optional

from channels.base_channel import BaseChannel


class DiscordChannel(BaseChannel):
    CHANNEL_NAME = "discord"

    def __init__(self, config: dict, agent_loop, pairing_manager=None,
                 rate_limiter=None, jarvis_logger=None):
        super().__init__(config, agent_loop, pairing_manager, jarvis_logger)
        self.rate_limiter = rate_limiter
        self._client = None
        self._thread: Optional[threading.Thread] = None

        dc_cfg = config.get("channels", {}).get("discord", {})
        self.token = (
            os.environ.get("DISCORD_BOT_TOKEN")
            or dc_cfg.get("token", "")
        )
        self.dm_policy = dc_cfg.get("dm_policy", "pairing")
        self.require_mention = dc_cfg.get("require_mention", True)
        self.allowlist: set = set(str(x) for x in dc_cfg.get("allowlist", []))
        self.guild_allowlist: set = set(str(x) for x in dc_cfg.get("guild_allowlist", []))

        if pairing_manager and self.allowlist:
            for uid in self.allowlist:
                pairing_manager._add_to_allowlist(self.CHANNEL_NAME, uid)

    # ---------------------------------------------------------------- #
    # Lifecycle                                                         #
    # ---------------------------------------------------------------- #

    def start(self) -> None:
        if not self.token:
            raise RuntimeError(
                "DISCORD_BOT_TOKEN not set. Set it in .env or config.toml."
            )
        self._thread = threading.Thread(target=self._run_async, daemon=True)
        self._thread.start()
        print("[Discord] Bot started.")
        self._thread.join()

    def stop(self) -> None:
        if self._client:
            asyncio.run_coroutine_threadsafe(
                self._client.close(), asyncio.get_event_loop()
            )

    def send_message(self, sender_id: str, text: str, **kwargs) -> None:
        pass  # Discord uses channel references, not user IDs directly

    # ---------------------------------------------------------------- #
    # Async runner                                                      #
    # ---------------------------------------------------------------- #

    def _run_async(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        try:
            import discord
            from discord.ext import commands
        except ImportError:
            print(
                "[Discord] discord.py not installed.\n"
                "Run: pip install discord.py"
            )
            return

        intents = discord.Intents.default()
        intents.message_content = True

        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            print(f"[Discord] Logged in as {client.user} (ID: {client.user.id})")
            print(f"[Discord] Serving {len(client.guilds)} guild(s)")

        @client.event
        async def on_message(message: discord.Message):
            if message.author.bot:
                return
            await self._on_message(message, client)

        await client.start(self.token)

    # ---------------------------------------------------------------- #
    # Message handling                                                  #
    # ---------------------------------------------------------------- #

    async def _on_message(self, message, client) -> None:
        import discord
        sender_id = str(message.author.id)
        text = message.content or ""

        # In servers: require @mention
        is_dm = isinstance(message.channel, discord.DMChannel)
        if not is_dm and self.require_mention:
            if not (client.user.mentioned_in(message)):
                return
            # Strip the mention from message
            text = text.replace(f"<@{client.user.id}>", "").strip()
            text = text.replace(f"<@!{client.user.id}>", "").strip()

        # Guild allowlist
        if not is_dm and self.guild_allowlist:
            if str(message.guild.id) not in self.guild_allowlist:
                return

        # Rate limiting
        if self.rate_limiter:
            allowed, msg = self.rate_limiter.check(f"discord:{sender_id}")
            if not allowed:
                await message.channel.send(msg)
                return

        # Pairing / allowlist
        if self.pairing:
            result = self.pairing.check_sender(
                self.CHANNEL_NAME, sender_id, self.dm_policy
            )
            if result == "deny":
                return
            if result == "code":
                code = self.pairing.create_pairing_code(
                    self.CHANNEL_NAME, sender_id, str(message.author)
                )
                if code:
                    await message.channel.send(
                        self.pairing.pairing_request_message(code)
                    )
                return

        # Handle slash commands
        if text.startswith("/"):
            response = self._handle_slash(sender_id, text)
            if response:
                await message.channel.send(response)
            return

        # Show typing
        async with message.channel.typing():
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
            # Discord max 2000 chars per message
            for chunk in _discord_split(response):
                await message.channel.send(chunk)


def _discord_split(text: str, max_len: int = 1990) -> list:
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
