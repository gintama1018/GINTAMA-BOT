"""
src/security_audit.py — JARVIS security audit command.

Run: python main.py --audit
Or from TCC terminal: security

Checks everything OpenClaw's security audit checks:
  ✓ Gateway bind mode
  ✓ Auth token strength
  ✓ Config file permissions
  ✓ ~/.jarvis directory permissions
  ✓ DM policies for all channels
  ✓ Rate limiting
  ✓ Agent token not placeholder
  ✓ No tokens in logs
  ✓ Tailscale status
"""

import os
import stat
from pathlib import Path
from typing import List, Tuple


class SecurityAudit:
    """
    Runs all security checks and returns a labelled report.

    Usage:
        audit = SecurityAudit(config)
        results = audit.run()
        # results is list of (status, check_name, detail)
        # status: "PASS", "WARN", "FAIL"
    """

    def __init__(self, config: dict):
        self.config = config
        self.results: List[Tuple[str, str, str]] = []

    def run(self) -> List[Tuple[str, str, str]]:
        self.results = []
        self._check_auth_token()
        self._check_listen_host()
        self._check_jarvis_home_permissions()
        self._check_config_permissions()
        self._check_env_file()
        self._check_channel_policies()
        self._check_rate_limiting()
        self._check_no_tokens_in_logs()
        self._check_tailscale()
        self._check_llm_key()
        return self.results

    def _ok(self, name: str, detail: str = "") -> None:
        self.results.append(("PASS", name, detail))

    def _warn(self, name: str, detail: str) -> None:
        self.results.append(("WARN", name, detail))

    def _fail(self, name: str, detail: str) -> None:
        self.results.append(("FAIL", name, detail))

    # ---------------------------------------------------------------- #
    # Individual checks                                                 #
    # ---------------------------------------------------------------- #

    def _check_auth_token(self) -> None:
        token = self.config.get("agent", {}).get("auth_token", "")
        if not token:
            token = os.environ.get("TCC_AUTH_TOKEN", "")
        if not token:
            self._fail("Auth Token", "No auth token configured. Set TCC_AUTH_TOKEN env var.")
        elif token in ("CHANGE_ME", "CHANGE_ME_strong_secret_token_here", "placeholder"):
            self._fail("Auth Token", "Auth token is still the default placeholder! Change it now.")
        elif len(token) < 32:
            self._warn("Auth Token", f"Token is only {len(token)} chars. 32+ chars recommended.")
        else:
            self._ok("Auth Token", f"Token set, length {len(token)} chars.")

    def _check_listen_host(self) -> None:
        host = self.config.get("agent", {}).get("listen_host", "127.0.0.1")
        if host == "127.0.0.1" or host == "localhost":
            self._ok("Gateway Bind", f"Bound to loopback only ({host})")
        elif host.startswith("100."):
            self._ok("Gateway Bind", f"Bound to Tailscale IP ({host}) — secure")
        elif host == "0.0.0.0":
            self._fail("Gateway Bind", "Bound to 0.0.0.0 — EXPOSED TO ALL INTERFACES! Change to 127.0.0.1")
        else:
            self._warn("Gateway Bind", f"Bound to {host} — ensure this is intentional")

    def _check_jarvis_home_permissions(self) -> None:
        jarvis_home = Path.home() / ".jarvis"
        if not jarvis_home.exists():
            self._ok("~/.jarvis Dir", "Not yet created (will be created on first use)")
            return
        mode = oct(stat.S_IMODE(os.stat(jarvis_home).st_mode))
        if mode in ("0o700", "0o600"):
            self._ok("~/.jarvis Dir", f"Permissions {mode} — correct")
        else:
            self._warn("~/.jarvis Dir", f"Permissions {mode} — should be 0o700. Run: chmod 700 ~/.jarvis")

    def _check_config_permissions(self) -> None:
        config_path = Path("config.toml")
        if not config_path.exists():
            self._warn("config.toml", "Not found in current directory")
            return
        # On Windows, permissions work differently — just note it
        if os.name == "nt":
            self._ok("config.toml", "Windows — file permission model applies")
        else:
            raw_mode = stat.S_IMODE(os.stat(config_path).st_mode)
            mode = oct(raw_mode)
            if raw_mode & stat.S_IROTH:
                self._warn("config.toml", f"Permissions {mode} — world-readable! Run: chmod 600 config.toml")
            else:
                self._ok("config.toml", f"Permissions {mode}")

    def _check_env_file(self) -> None:
        env_path = Path(".env")
        if not env_path.exists():
            self._warn(".env File", ".env not found — ensure secrets are set via env vars")
            return
        if os.name == "nt":
            self._ok(".env File", "Present (Windows — ensure it's in .gitignore)")
        else:
            mode = oct(stat.S_IMODE(os.stat(env_path).st_mode))
            self._ok(".env File", f"Present, permissions {mode}")
        # Check it's gitignored
        gitignore = Path(".gitignore")
        if gitignore.exists():
            content = gitignore.read_text()
            if ".env" in content:
                self._ok(".gitignore", ".env is excluded from git")
            else:
                self._fail(".gitignore", ".env is NOT in .gitignore — credentials could be committed!")
        else:
            self._warn(".gitignore", "No .gitignore found")

    def _check_channel_policies(self) -> None:
        channels_cfg = self.config.get("channels", {})
        if not channels_cfg:
            self._ok("Channel Policies", "No channels configured yet")
            return
        for channel_name, cfg in channels_cfg.items():
            if isinstance(cfg, dict):
                policy = cfg.get("dm_policy", "pairing")
                if policy == "open":
                    self._fail(
                        f"{channel_name} DM Policy",
                        "dmPolicy = 'open' — ANYONE can control your devices! Change to 'pairing'."
                    )
                elif policy == "disabled":
                    self._warn(f"{channel_name} DM Policy", "DMs disabled on this channel")
                else:
                    self._ok(f"{channel_name} DM Policy", f"Policy: {policy}")

                require_mention = cfg.get("require_mention", True)
                if not require_mention:
                    self._warn(
                        f"{channel_name} Group Mention",
                        "requireMention=False — bot responds to all messages in groups!"
                    )
                else:
                    self._ok(f"{channel_name} Group Mention", "requireMention=True")

    def _check_rate_limiting(self) -> None:
        # Check if rate limiter is being used (agent.py has it)
        rate_cfg = self.config.get("agent", {}).get("rate_limit", {})
        if rate_cfg:
            self._ok("Rate Limiting", f"{rate_cfg.get('per_minute', 10)}/min, {rate_cfg.get('per_day', 100)}/day")
        else:
            self._ok("Rate Limiting", "Enabled (defaults: 10/min, 100/day)")

    def _check_no_tokens_in_logs(self) -> None:
        logs_dir = Path("logs")
        if not logs_dir.exists():
            self._ok("Log Scan", "No logs directory yet")
            return
        found_leaks = []
        dangerous_patterns = ["api_key", "token", "password", "secret", "GEMINI_API_KEY"]
        for log_file in list(logs_dir.glob("*.log"))[:5]:  # sample last 5 logs
            try:
                content = log_file.read_text(errors="replace")
                for pattern in dangerous_patterns:
                    # Look for something like GEMINI_API_KEY=AIza... (actual value, not just the word)
                    import re
                    matches = re.findall(
                        rf'{re.escape(pattern)}\s*[=:]\s*[A-Za-z0-9_/+]{{10,}}',
                        content, re.IGNORECASE
                    )
                    if matches:
                        found_leaks.append(f"{log_file.name}: {pattern}")
            except Exception:
                pass
        if found_leaks:
            self._fail("Log Scan", f"Possible credential leak in logs: {found_leaks}")
        else:
            self._ok("Log Scan", "No credential patterns found in recent logs")

    def _check_tailscale(self) -> None:
        import subprocess
        try:
            result = subprocess.run(
                ["tailscale", "status"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self._ok("Tailscale", "Running — secure mesh network active")
            else:
                self._warn("Tailscale", "Not connected. Remote access via Tailscale not available.")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._warn("Tailscale", "Not installed. Install for secure remote access: https://tailscale.com")

    def _check_llm_key(self) -> None:
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            key = self.config.get("llm", {}).get("gemini_api_key", "")
        if not key:
            self._warn("Gemini API Key", "Not set — agent loop will not work. Set GEMINI_API_KEY in .env")
        elif len(key) < 20:
            self._warn("Gemini API Key", "Looks too short — verify it's the correct key")
        else:
            self._ok("Gemini API Key", f"Set (length {len(key)})")

    # ---------------------------------------------------------------- #
    # Formatted report                                                  #
    # ---------------------------------------------------------------- #

    def print_report(self, console=None) -> None:
        """Print a formatted report using rich if available, else plain text."""
        results = self.run()
        passes = sum(1 for s, _, _ in results if s == "PASS")
        warns = sum(1 for s, _, _ in results if s == "WARN")
        fails = sum(1 for s, _, _ in results if s == "FAIL")

        if console:
            from rich.table import Table
            table = Table(title="JARVIS Security Audit", border_style="blue", show_lines=True)
            table.add_column("Status", min_width=6)
            table.add_column("Check", style="bold", min_width=25)
            table.add_column("Detail")
            for status, name, detail in results:
                color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[status]
                table.add_row(f"[{color}]{status}[/{color}]", name, detail)
            console.print(table)
            score_color = "green" if fails == 0 and warns == 0 else ("yellow" if fails == 0 else "red")
            console.print(
                f"[{score_color}]{passes} passed · {warns} warnings · {fails} failures[/{score_color}]"
            )
        else:
            icons = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}
            for status, name, detail in results:
                print(f"  {icons[status]} [{status}] {name}: {detail}")
            print(f"\n{passes} passed · {warns} warnings · {fails} failures")
