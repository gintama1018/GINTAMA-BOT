"""
src/cli.py — Interactive REPL shell for TCC JARVIS.

Handles:
  - Startup banner + device status
  - Command-line prompt with history and auto-suggest
  - Routing parsed intents to executor / router / skill runner
  - Special commands (devices, logs, skills, help, exit)
  - Natural language fallback via LLM adapter
  - Skill trigger matching
  - Background scheduler
"""

import os
import sys
import time
import tomllib
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style

from src.parser import parse, Intent
from src.executor import LocalExecutor
from src.router import CommandRouter
from src.logger import StructuredLogger
from src.discovery import DeviceDiscovery
from src.llm import LLMAdapter
from src.scheduler import Scheduler
from src.agent_loop import AgentLoop
from src.session_manager import SessionManager


PROMPT_STYLE = Style.from_dict({"": "bold cyan"})

BANNER = """\
╔══════════════════════════════════════════╗
║     TCC — Terminal Command Center        ║
║     Codename: JARVIS  v3.0               ║
║     Agent Loop: Gemini Function Calling  ║
╚══════════════════════════════════════════╝"""


class TCC_CLI:
    def __init__(self):
        self.console = Console()
        self.config = self._load_config()

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.logger = StructuredLogger(
            log_dir=os.path.join(
                project_root, self.config.get("tcc", {}).get("log_dir", "logs")
            ),
            level=self.config.get("tcc", {}).get("log_level", "INFO"),
        )
        self.discovery = DeviceDiscovery(self.config, self.logger)
        self.executor = LocalExecutor(self.config, self.logger)
        self.router = CommandRouter(self.config, self.logger)
        self.llm = LLMAdapter(self.config, self.logger)  # kept for simple single-intent fallback
        self.sessions = SessionManager()
        self.agent_loop = AgentLoop(
            self.config,
            self.logger,
            executor=self.executor,
            router=self.router,
            session_manager=self.sessions,
        )
        self.scheduler = Scheduler(self.config, self.logger, self._run_skill_by_name)
        self._project_root = project_root
        self._skill_triggers: Optional[dict] = None  # BUG 5: cache

    # ---------------------------------------------------------------- #
    # Config loading                                                    #
    # ---------------------------------------------------------------- #

    def _load_config(self) -> dict:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.toml"
        )
        try:
            with open(config_path, "rb") as f:
                return tomllib.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            print(f"[Warning] Could not load config.toml: {e}")
            return {}

    # ---------------------------------------------------------------- #
    # Main run loop                                                     #
    # ---------------------------------------------------------------- #

    def run(self) -> None:
        self._print_banner()
        self.scheduler.start()

        history_path = os.path.join(self._project_root, "logs", ".history")
        os.makedirs(os.path.dirname(history_path), exist_ok=True)

        session = PromptSession(
            history=FileHistory(history_path),
            auto_suggest=AutoSuggestFromHistory(),
        )

        while True:
            try:
                raw = session.prompt("> ", style=PROMPT_STYLE).strip()
                if not raw:
                    continue
                intent = parse(raw)
                self._execute_command(intent)

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Ctrl+C — type 'exit' to quit.[/yellow]")
            except EOFError:
                self.console.print("[yellow]Goodbye.[/yellow]")
                break
            except SystemExit:
                raise
            except Exception as e:
                self.console.print(f"[red]Internal error: {e}[/red]")
                self.logger.error(f"Unhandled exception: {e}")

    # ---------------------------------------------------------------- #
    # Command dispatch                                                  #
    # ---------------------------------------------------------------- #

    def _execute_command(self, intent: Intent) -> None:
        if intent.error:
            self.console.print(f"[red]Parse error: {intent.error}[/red]")
            return

        # Special commands (devices, logs, skills, help, exit, clear)
        if intent.special:
            self._handle_special(intent)
            return

        # Skill trigger check (e.g. "good morning")
        triggers = self._build_skill_triggers()
        normalized = intent.raw.lower().strip()
        if normalized in triggers:
            self._run_skill(triggers[normalized])
            return

        # NLP route — route through the Agent Loop (Gemini function calling)
        if intent.target == "__nlp__":
            if self.agent_loop.is_available():
                self._run_agent_loop(intent.args.get("text", intent.raw))
            elif self.llm.is_available():
                # Fallback: old single-intent extraction
                nlp_intent = self.llm.extract_intent(intent.args.get("text", intent.raw))
                if nlp_intent and nlp_intent.target not in ("__nlp__", None, ""):
                    intent = nlp_intent
                else:
                    self.console.print(
                        "[yellow]Could not understand command. Type [bold]help[/bold] for reference.[/yellow]"
                    )
                    return
            else:
                self.console.print(
                    "[yellow]Unknown command. "
                    "Tip: use [bold]system open <app>[/bold] to launch apps, "
                    "or set [bold]llm.enabled=true[/bold] in config.toml for natural language. "
                    "Type [bold]help[/bold] for all commands.[/yellow]"
                )
            return

        # Route to system executor or remote device
        start = time.perf_counter()

        # S5: Require confirmation for the raw 'run' action (destructive)
        if intent.action == "run":
            cmd_preview = intent.args.get("cmd", intent.args.get("text", ""))
            self.console.print(
                f"[yellow]Execute [bold]{cmd_preview!r}[/bold] on [bold]{intent.target}[/bold]? [[bold]y[/bold]/N] [/yellow]",
                end="",
            )
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                self.console.print("[dim]Cancelled.[/dim]")
                return

        if intent.target == "system":
            result = self.executor.execute(intent)
        else:
            result = self.router.route(intent)

        latency_ms = int((time.perf_counter() - start) * 1000)

        # Display result
        if result.get("status") == "success":
            msg = result.get("message", "OK")
            self.console.print(
                f"[green]✓[/green] {msg}  [dim]\\[{latency_ms}ms][/dim]"
            )
        elif result.get("status") == "partial":
            msg = result.get("message", "Partial success")
            self.console.print(
                f"[yellow]~[/yellow] {msg}  [dim]\\[{latency_ms}ms][/dim]"
            )
        else:
            err = result.get("error", "Command failed")
            self.console.print(
                f"[red]✗[/red] {err}  [dim]\\[{latency_ms}ms][/dim]"
            )

        # Log the execution
        self.logger.log_command(
            command=intent.raw,
            parsed={"target": intent.target, "action": intent.action, "args": intent.args},
            device_ip=result.get("device_ip", "local"),
            transport=result.get("transport", "local"),
            status=result.get("status", "error"),
            latency=latency_ms,
            response=result.get("data"),
            error=result.get("error"),
        )

    # ---------------------------------------------------------------- #
    # Agent loop handler                                               #
    # ---------------------------------------------------------------- #

    def _run_agent_loop(self, text: str) -> None:
        """Run the Gemini agent loop for a natural-language command, with live tool display."""
        self.console.print(f"[dim]🤖 JARVIS thinking...[/dim]")

        def on_tool_call(tool_name: str, args: dict) -> None:
            # Prettify the tool name for display
            display = tool_name.replace("_", " ")
            args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
            self.console.print(f"  [cyan]⚙[/cyan] [bold]{display}[/bold]({args_str})")

        def on_tool_result(tool_name: str, result: dict) -> None:
            status = result.get("status", "?")
            if status == "ok" or status == "success":
                # Show a brief success indicator
                msg = result.get("message", result.get("data", ""))
                if isinstance(msg, dict):
                    msg = str(msg)
                snippet = str(msg)[:80] if msg else "ok"
                self.console.print(f"  [green]✓[/green] {snippet}")
            elif status in ("error", "blocked"):
                err = result.get("error", "failed")
                self.console.print(f"  [red]✗[/red] {err}")

        start = time.perf_counter()
        response = self.agent_loop.run(
            user_message=text,
            channel="terminal",
            sender_id="local",
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)

        # Print final response
        if response and response.strip():
            self.console.print(f"\n[bold green]JARVIS:[/bold green] {response}  [dim][{latency_ms}ms][/dim]")
        else:
            self.console.print(f"[dim]Done  [{latency_ms}ms][/dim]")

    # ---------------------------------------------------------------- #
    # Special command handlers                                          #
    # ---------------------------------------------------------------- #

    def _handle_special(self, intent: Intent) -> None:
        a = intent.action
        if a in ("exit", "quit"):
            self.console.print("[yellow]Goodbye.[/yellow]")
            sys.exit(0)
        elif a == "clear":
            os.system("cls" if os.name == "nt" else "clear")
            self._print_banner()
        elif a == "devices":
            self._cmd_devices(intent)
        elif a == "logs":
            self._cmd_logs(intent)
        elif a == "skills":
            self._cmd_skills()
        elif a == "help":
            self._cmd_help()
        elif a == "sessions":
            self._cmd_sessions()
        elif a == "memory":
            self._cmd_memory()

    def _cmd_devices(self, intent: Intent) -> None:
        if "refresh" in intent.flags:
            self.console.print("[dim]Refreshing device status...[/dim]")
            self.discovery.refresh()

        devices = self.discovery.list_devices()
        if not devices:
            self.console.print(
                "[yellow]No devices registered. Add them to config.toml.[/yellow]"
            )
            return

        table = Table(title="TCC — Connected Devices", border_style="blue", show_lines=True)
        table.add_column("Name", style="bold cyan", min_width=12)
        table.add_column("IP Address", style="white", min_width=16)
        table.add_column("Type", style="yellow", min_width=9)
        table.add_column("Status", min_width=8)

        online = 0
        for name, info in devices.items():
            status = info.get("status", "unknown")
            if status == "online":
                status_cell = "[green]online[/green]"
                online += 1
            elif "adb" in status:
                status_cell = "[cyan]adb[/cyan]"
                online += 1
            else:
                status_cell = "[red]offline[/red]"
            table.add_row(
                name,
                info.get("ip", "—"),
                info.get("type", "—"),
                status_cell,
            )

        self.console.print(table)
        total = len(devices)
        offline = total - online
        self.console.print(
            f"[dim]{total} device(s) registered │ {online} online │ {offline} offline[/dim]"
        )

    def _cmd_logs(self, intent: Intent) -> None:
        try:
            n = int(intent.flags.get("last", 50))
        except (ValueError, TypeError):
            self.console.print("[yellow]Expected a number after --last, defaulting to 50.[/yellow]")
            n = 50
        level_filter = intent.flags.get("level")
        device_filter = intent.flags.get("device")
        since_hours_raw = intent.flags.get("since")
        since_hours = None
        if since_hours_raw and isinstance(since_hours_raw, str):
            try:
                since_hours = float(since_hours_raw.rstrip("h"))
            except ValueError:
                pass
        entries = self.logger.get_recent(n=n, level_filter=level_filter, device_filter=device_filter, since_hours=since_hours)
        if not entries:
            self.console.print("[dim]No log entries found.[/dim]")
            return
        for entry in entries:
            color = "red" if "ERROR" in entry else "dim"
            self.console.print(f"[{color}]{entry}[/{color}]")

    def _cmd_skills(self) -> None:
        import yaml
        skills_dir = os.path.join(self._project_root, "skills")
        table = Table(title="Available Skills", border_style="blue", show_lines=True)
        table.add_column("Name", style="bold cyan", min_width=12)
        table.add_column("Trigger Words", style="yellow")
        table.add_column("Steps", style="white", min_width=5)

        try:
            for fname in sorted(os.listdir(skills_dir)):
                if fname.endswith(".yaml"):
                    with open(os.path.join(skills_dir, fname), "r") as f:
                        skill = yaml.safe_load(f)
                    triggers = ", ".join(skill.get("trigger", [])[:3])
                    steps = str(len(skill.get("steps", [])))
                    table.add_row(skill.get("name", fname), triggers, steps)
        except FileNotFoundError:
            self.console.print("[yellow]skills/ directory not found.[/yellow]")
            return

        self.console.print(table)

    def _cmd_help(self) -> None:
        self.console.print(
            Panel(
                HELP_TEXT,
                title="[bold cyan]TCC — Command Reference[/bold cyan]",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # ---------------------------------------------------------------- #
    # Skills                                                            #
    # ---------------------------------------------------------------- #

    def _build_skill_triggers(self) -> dict:
        """Return {trigger_phrase: skill_name} mapping (cached for session)."""
        # BUG 5: Build once and cache — skills don't change while running
        if self._skill_triggers is not None:
            return self._skill_triggers
        import yaml
        triggers = {}
        skills_dir = os.path.join(self._project_root, "skills")
        try:
            for fname in os.listdir(skills_dir):
                if fname.endswith(".yaml"):
                    try:
                        with open(os.path.join(skills_dir, fname), "r") as f:
                            skill = yaml.safe_load(f)
                        skill_name = skill.get("name", fname.replace(".yaml", ""))
                        for phrase in skill.get("trigger", []):
                            triggers[phrase.lower()] = skill_name
                    except Exception as e:
                        # Q5: Warn on bad YAML so user knows why a skill isn't triggering
                        self.logger.warning(f"Could not load skill '{fname}': {e}")
                        self.console.print(f"[yellow]⚠ Skill '{fname}' has a YAML error: {e}[/yellow]")
        except FileNotFoundError:
            pass
        self._skill_triggers = triggers
        return self._skill_triggers

    def _run_skill(self, skill_name: str) -> None:
        import yaml
        skills_dir = os.path.join(self._project_root, "skills")
        skill_file = os.path.join(skills_dir, f"{skill_name}.yaml")
        try:
            with open(skill_file, "r") as f:
                skill = yaml.safe_load(f)
        except FileNotFoundError:
            self.console.print(f"[red]Skill file not found: {skill_file}[/red]")
            return

        self.console.print(f"[cyan]✓ Running skill: [bold]{skill_name}[/bold][/cyan]")
        start_total = time.perf_counter()

        for step in skill.get("steps", []):
            self.console.print(f"  [dim]→ {step}[/dim]")
            step_intent = parse(step)
            self._execute_command(step_intent)

        total_s = time.perf_counter() - start_total
        self.console.print(
            f"[green]✓ Skill complete[/green]  [dim]\\[{total_s:.1f}s total][/dim]"
        )

    def _run_skill_by_name(self, skill_name: str) -> None:
        """Called by scheduler."""
        self._run_skill(skill_name)

    # ---------------------------------------------------------------- #
    # Banner                                                            #
    # ---------------------------------------------------------------- #

    def _cmd_sessions(self) -> None:
        """List all agent loop sessions with last active time."""
        sessions = self.sessions.list_sessions()
        if not sessions:
            self.console.print("[dim]No sessions yet. Start chatting to create one.[/dim]")
            return
        table = Table(title="JARVIS Sessions", border_style="blue", show_lines=True)
        table.add_column("Session ID", style="bold cyan", min_width=20)
        table.add_column("Channel", style="yellow", min_width=10)
        table.add_column("Last Active", style="white", min_width=20)
        for s in sessions:
            table.add_row(s["id"], s["channel"], s["last_active"])
        self.console.print(table)

    def _cmd_memory(self) -> None:
        """Show user memory stored for this terminal session."""
        mem = self.sessions.get_memory("terminal", "local")
        if not mem:
            self.console.print("[dim]No memory stored yet. Tell JARVIS your preferences![/dim]")
            self.console.print("[dim]Example: \"remember that I prefer responses in Hindi\"[/dim]")
            return
        self.console.print("[bold cyan]JARVIS Memory:[/bold cyan]")
        for k, v in mem.items():
            self.console.print(f"  [yellow]{k}:[/yellow] {v}")

    # ---------------------------------------------------------------- #
    # Banner                                                            #
    # ---------------------------------------------------------------- #

    def _print_banner(self) -> None:
        online = self.discovery.count_online()
        ts_status = "ON" if self.discovery.tailscale_available() else "OFF"
        ts_color = "green" if ts_status == "ON" else "red"
        agent_status = "ON" if self.agent_loop.is_available() else ("ON" if self.llm.is_available() else "OFF")
        agent_color = "green" if agent_status == "ON" else "yellow"

        body = (
            f"[bold cyan]TCC — Terminal Command Center[/bold cyan]\n"
            f"[bold yellow]Codename: JARVIS  v3.0  [Agent Loop][/bold yellow]\n\n"
            f"[green]{online}[/green] device(s) online  │  "
            f"Tailscale: [{ts_color}]{ts_status}[/{ts_color}]  │  "
            f"Agent: [{agent_color}]{agent_status}[/{agent_color}]\n\n"
            f"[dim]Speak naturally or type [bold]help[/bold] for commands.[/dim]"
        )
        self.console.print(Panel(body, border_style="bold blue", padding=(1, 3)))


HELP_TEXT = """\
[bold]TARGETS[/bold]     system  phone  laptop  server  all

[bold]ACTIONS[/bold]
  info                     Device status (CPU, RAM, disk)
  screenshot               Capture screen
  launch / open <app>      Open an application
  lock / unlock            Lock or unlock screen
  volume <0-15>            Set volume level
  brightness <0-255>       Set screen brightness
  battery                  Battery level and charging status
  push <src> <dst>         Transfer file TO device
  pull <src> <dst>         Retrieve file FROM device
  ls <path>                List directory contents
  run "<cmd>"              Execute shell command
  notify <message>         Send a notification
  reboot / shutdown        Restart or power off

[bold]SPECIAL[/bold]
  devices                  List registered devices + status
  devices --refresh        Force re-scan
  logs                     Show last 50 log entries
  logs --last 20           Show last N entries
  logs --level ERROR       Filter by log level
  logs --device phone      Filter by device
  skills                   List automation skills
  sessions                 Show all conversation sessions
  memory                   Show what JARVIS remembers about you
  help                     Show this reference
  exit / quit              Close TCC

[bold]NATURAL LANGUAGE (Agent Loop)[/bold]
  Just type anything — JARVIS uses Gemini to understand:
  "take a selfie"
  "what's my phone battery"
  "open youtube on my phone"
  "take a screenshot of my laptop"
  "search the web for Python tutorials"
  "open camera and take a photo"

[bold]EXAMPLES[/bold]
  phone screenshot
  phone battery
  phone volume 8
  phone launch youtube
  phone push ./report.pdf /sdcard/Documents/
  system info
  system open chrome
  laptop run "df -h"
  all notify "Dinner is ready"
  good morning             (skill trigger — runs morning routine)
"""

