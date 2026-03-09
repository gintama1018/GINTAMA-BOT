#!/usr/bin/env python3
"""
TCC — Terminal Command Center
Codename: JARVIS v3.0

Entry point. Run this to start JARVIS in any mode.

Usage:
    python main.py                  — Interactive terminal (default)
    python main.py --agent          — Start device agent
    python main.py --telegram       — Start Telegram bot (Phase 2)
    python main.py --discord        — Start Discord bot  (Phase 5)
    python main.py --web            — Start Web UI at http://127.0.0.1:7071
    python main.py --voice          — Voice mode (Whisper + TTS)
    python main.py --audit          — Run security audit and exit
    python main.py --all            — Start all channels simultaneously
"""

import sys
import os
import threading

# Load .env file before anything else — sets GEMINI_API_KEY etc.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # dotenv optional — env vars can be set manually too

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_config() -> dict:
    """Load config.toml, return as dict."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            try:
                import toml as tomllib  # type: ignore
            except ImportError:
                return {}
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _build_shared_components(config: dict):
    """Build AgentLoop, PairingManager, RateLimiter — shared across channels."""
    from src.agent_loop import AgentLoop
    from src.pairing_manager import PairingManager
    from src.rate_limiter import RateLimiter
    from src.logger import setup_logger
    from src.session_manager import SessionManager

    jarvis_logger = setup_logger(config.get("tcc", {}).get("log_level", "INFO"))
    session_manager = SessionManager()
    agent_loop = AgentLoop(session_manager=session_manager)
    pairing_manager = PairingManager()
    rate_limiter = RateLimiter()
    return agent_loop, pairing_manager, rate_limiter, jarvis_logger


def main():
    args = set(sys.argv[1:])

    # ── Device agent mode ────────────────────────────────────────── #
    if "--agent" in args:
        from agent.agent import start_agent
        start_agent()
        return

    # ── Security audit ───────────────────────────────────────────── #
    if "--audit" in args:
        config = _load_config()
        from src.security_audit import SecurityAudit
        audit = SecurityAudit(config)
        try:
            from rich.console import Console
            console = Console()
        except ImportError:
            console = None
        print("\nJARVIS Security Audit\n" + "─" * 40)
        audit.print_report(console)
        print()
        return

    # ── Multi-channel --all ──────────────────────────────────────── #
    if "--all" in args:
        args = {"--telegram", "--discord", "--web"}

    # ── Channel modes (can combine e.g. --telegram --web) ─────────── #
    channel_modes = args & {"--telegram", "--discord", "--web", "--voice"}

    if not channel_modes:
        # Default: interactive terminal
        from src.cli import TCC_CLI
        cli = TCC_CLI()
        cli.run()
        return

    # Build shared components once
    config = _load_config()
    agent_loop, pairing_manager, rate_limiter, jarvis_logger = _build_shared_components(config)

    threads = []

    if "--web" in channel_modes:
        from gateway.web_gateway import start_web_server
        t = threading.Thread(
            target=start_web_server,
            args=(agent_loop, config, rate_limiter, pairing_manager),
            daemon=True,
            name="WebGateway"
        )
        threads.append(t)

    if "--discord" in channel_modes:
        from channels.discord_channel import DiscordChannel
        dc = DiscordChannel(config, agent_loop, pairing_manager, rate_limiter, jarvis_logger)
        t = threading.Thread(target=dc.start, daemon=True, name="DiscordChannel")
        threads.append(t)

    if "--voice" in channel_modes:
        from channels.voice_channel import VoiceChannel
        vc = VoiceChannel(config, agent_loop, pairing_manager, rate_limiter, jarvis_logger)
        t = threading.Thread(target=vc.start, daemon=True, name="VoiceChannel")
        threads.append(t)

    if "--telegram" in channel_modes:
        # Telegram blocks (polling) — run in main thread if it's the only channel,
        # or in a thread if combining with others
        from channels.telegram_channel import TelegramChannel
        tg = TelegramChannel(config, agent_loop, pairing_manager, rate_limiter, jarvis_logger)
        if len(channel_modes) == 1:
            tg.start()  # blocking
            return
        else:
            t = threading.Thread(target=tg.start, daemon=True, name="TelegramChannel")
            threads.append(t)

    # Start all threads
    for t in threads:
        t.start()
        print(f"[Main] Started: {t.name}")

    # Block until KeyboardInterrupt
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[Main] Shutting down JARVIS...")


if __name__ == "__main__":
    main()
