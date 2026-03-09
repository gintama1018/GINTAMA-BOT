"""
src/tool_registry.py — Tool definitions and executors for the JARVIS agent loop.

Two parts:
  1. TOOL_DECLARATIONS  — Gemini function_declarations (what the AI sees)
  2. ToolExecutor class — Python implementations (what actually runs)

The agent loop calls:
  get_declarations()       → passes to Gemini as the tools list
  ToolExecutor.execute()   → runs the real function, returns a result dict

Tools map directly to existing TCC modules (modules/phone.py, modules/system.py,
modules/files.py) plus the router for remote devices.
"""

import os
import re
import subprocess
from typing import Optional

# --------------------------------------------------------------------------- #
# 1. GEMINI FUNCTION DECLARATIONS                                              #
# These tell Gemini what tools are available and what parameters they take.   #
# --------------------------------------------------------------------------- #

TOOL_DECLARATIONS = [
    # ── Phone tools ──────────────────────────────────────────────────────── #
    {
        "name": "phone_launch",
        "description": "Launch an app on the Android phone via ADB.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "App name to launch, e.g. 'camera', 'youtube', 'spotify', "
                        "'instagram', 'whatsapp', 'chrome', 'maps', 'calendar'"
                    ),
                }
            },
            "required": ["app"],
        },
    },
    {
        "name": "phone_screenshot",
        "description": "Take a screenshot of the phone screen and save it locally.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "phone_battery",
        "description": "Get the current battery level of the phone.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "phone_volume",
        "description": "Set the volume on the phone (0-15).",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "description": "Volume level as a string, 0 (silent) to 15 (max)",
                }
            },
            "required": ["level"],
        },
    },
    {
        "name": "phone_lock",
        "description": "Lock the phone screen.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "phone_notify",
        "description": "Send a toast notification to the phone screen.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification message to display on phone",
                }
            },
            "required": ["message"],
        },
    },
    # ── System (local PC) tools ───────────────────────────────────────────── #
    {
        "name": "system_info",
        "description": "Get system information about the local PC: CPU, RAM, disk, OS version.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "system_screenshot",
        "description": "Take a screenshot of the local PC screen and save it to screenshots/.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "system_open",
        "description": "Open an application on the local PC.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "Application to open, e.g. 'chrome', 'notepad', "
                        "'calculator', 'camera', 'explorer', 'terminal'"
                    ),
                }
            },
            "required": ["app"],
        },
    },
    {
        "name": "system_notify",
        "description": "Send a desktop notification popup on the local PC.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification message body",
                },
                "title": {
                    "type": "string",
                    "description": "Notification title (default: JARVIS)",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "system_run",
        "description": (
            "Run a safe, non-destructive shell command on the local PC and return output. "
            "Do NOT use for destructive commands like delete, format, or shutdown."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                }
            },
            "required": ["command"],
        },
    },
    # ── Remote device tools ───────────────────────────────────────────────── #
    {
        "name": "device_info",
        "description": "Get system information from a registered remote device.",
        "parameters": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name as registered in config.toml: 'laptop', 'server', 'phone'",
                }
            },
            "required": ["device"],
        },
    },
    # ── File tools ────────────────────────────────────────────────────────── #
    {
        "name": "file_ls",
        "description": "List files and folders in a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path. Use '.' for current directory or '~' for home.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_read",
        "description": "Read the contents of a text file (max 10KB returned).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                }
            },
            "required": ["path"],
        },
    },
    # ── Web search ────────────────────────────────────────────────────────── #
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo and return top results with snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                }
            },
            "required": ["query"],
        },
    },
    # ── Scheduler ─────────────────────────────────────────────────────────── #
    {
        "name": "schedule_task",
        "description": "Schedule a recurring task (skill) to run at a specific time daily.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Task identifier, e.g. 'morning', 'backup'",
                },
                "time": {
                    "type": "string",
                    "description": "Time to run in HH:MM format, e.g. '07:00'",
                },
                "command": {
                    "type": "string",
                    "description": "Skill name or command to execute at that time",
                },
            },
            "required": ["name", "time", "command"],
        },
    },
]


def get_declarations():
    """Return TOOL_DECLARATIONS (used by agent_loop to build Gemini Tool objects)."""
    return TOOL_DECLARATIONS


# --------------------------------------------------------------------------- #
# 2. TOOL EXECUTOR                                                             #
# Maps tool names → real Python functions using existing TCC modules.         #
# --------------------------------------------------------------------------- #

# Commands that should never be run via system_run (security)
_BLOCKED_COMMANDS = frozenset([
    "rm -rf", "format", "mkfs", ":(){:|:&}",
    "del /f /s /q", "rmdir /s /q", "rd /s /q",
])


class ToolExecutor:
    """
    Executes tool calls by routing to existing TCC modules.

    All methods return a dict with at minimum {"status": "ok"|"error"}.
    Successful results include relevant data fields.
    """

    def __init__(self, config: dict, logger, executor=None, router=None):
        self.config = config
        self.logger = logger
        self._executor = executor  # LocalExecutor instance
        self._router = router      # CommandRouter instance

    def execute(self, tool_name: str, args: dict) -> dict:
        """Dispatch tool_name to its handler. Returns result dict."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return {"status": "error", "error": f"Unknown tool: {tool_name}"}
        try:
            return handler(args)
        except Exception as exc:
            self.logger.warning(f"Tool '{tool_name}' raised: {exc}")
            return {"status": "error", "error": str(exc)}

    # ---------------------------------------------------------------- #
    # Internal helpers                                                  #
    # ---------------------------------------------------------------- #

    def _route_phone(self, action: str, args: Optional[dict] = None) -> dict:
        """Route a phone action through the existing router or PhoneModule."""
        from src.parser import Intent
        intent = Intent(raw="", target="phone", action=action, args=args or {})
        if self._router:
            return self._router.route(intent)
        # Fallback: call PhoneModule directly (ADB)
        from modules.phone import PhoneModule
        pm = PhoneModule(self.config, self.logger)
        handler = getattr(pm, action, None)
        if handler:
            return handler(args or {})
        return {"status": "error", "error": f"No phone handler for action '{action}'"}

    def _route_system(self, action: str, args: Optional[dict] = None) -> dict:
        """Route a system action through the LocalExecutor."""
        from src.parser import Intent
        intent = Intent(raw="", target="system", action=action, args=args or {})
        if self._executor:
            return self._executor.execute(intent)
        return {"status": "error", "error": "No local executor configured"}

    # ---------------------------------------------------------------- #
    # Phone tools                                                        #
    # ---------------------------------------------------------------- #

    def _tool_phone_launch(self, args: dict) -> dict:
        return self._route_phone("launch", args)

    def _tool_phone_screenshot(self, args: dict) -> dict:
        return self._route_phone("screenshot")

    def _tool_phone_battery(self, args: dict) -> dict:
        return self._route_phone("battery")

    def _tool_phone_volume(self, args: dict) -> dict:
        return self._route_phone("volume", args)

    def _tool_phone_lock(self, args: dict) -> dict:
        return self._route_phone("lock")

    def _tool_phone_notify(self, args: dict) -> dict:
        return self._route_phone("notify", args)

    # ---------------------------------------------------------------- #
    # System (local PC) tools                                           #
    # ---------------------------------------------------------------- #

    def _tool_system_info(self, args: dict) -> dict:
        return self._route_system("info")

    def _tool_system_screenshot(self, args: dict) -> dict:
        return self._route_system("screenshot")

    def _tool_system_open(self, args: dict) -> dict:
        return self._route_system("launch", args)

    def _tool_system_notify(self, args: dict) -> dict:
        args.setdefault("title", "JARVIS")
        return self._route_system("notify", args)

    def _tool_system_run(self, args: dict) -> dict:
        command = args.get("command", "").strip()
        if not command:
            return {"status": "error", "error": "No command provided"}
        # Block dangerous patterns
        lower = command.lower()
        for blocked in _BLOCKED_COMMANDS:
            if blocked in lower:
                return {
                    "status": "blocked",
                    "error": f"Command blocked for safety: contains '{blocked}'",
                }
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "status": "ok",
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:500],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "Command timed out after 30 seconds"}

    # ---------------------------------------------------------------- #
    # Remote device tools                                               #
    # ---------------------------------------------------------------- #

    def _tool_device_info(self, args: dict) -> dict:
        device = args.get("device", "laptop")
        from src.parser import Intent
        intent = Intent(raw="", target=device, action="info", args={})
        if self._router:
            return self._router.route(intent)
        return {"status": "error", "error": "No router configured"}

    # ---------------------------------------------------------------- #
    # File tools                                                         #
    # ---------------------------------------------------------------- #

    def _tool_file_ls(self, args: dict) -> dict:
        raw_path = args.get("path", ".").strip()
        abs_path = os.path.realpath(os.path.expanduser(raw_path))
        # Security: only allow home dir or cwd subtrees
        home = os.path.realpath(os.path.expanduser("~"))
        cwd = os.path.realpath(os.getcwd())
        if not (abs_path.startswith(home) or abs_path.startswith(cwd)):
            return {"status": "error", "error": "Access denied: path outside allowed directories"}
        try:
            entries = sorted(os.listdir(abs_path))[:200]
            result = []
            for name in entries:
                full = os.path.join(abs_path, name)
                result.append({
                    "name": name,
                    "type": "dir" if os.path.isdir(full) else "file",
                    "size": os.path.getsize(full) if os.path.isfile(full) else None,
                })
            return {"status": "ok", "path": abs_path, "entries": result, "count": len(result)}
        except PermissionError:
            return {"status": "error", "error": "Permission denied"}
        except FileNotFoundError:
            return {"status": "error", "error": f"Path not found: {raw_path}"}

    def _tool_file_read(self, args: dict) -> dict:
        raw_path = args.get("path", "").strip()
        if not raw_path:
            return {"status": "error", "error": "No path provided"}
        abs_path = os.path.realpath(os.path.expanduser(raw_path))
        home = os.path.realpath(os.path.expanduser("~"))
        cwd = os.path.realpath(os.getcwd())
        if not (abs_path.startswith(home) or abs_path.startswith(cwd)):
            return {"status": "error", "error": "Access denied: path outside allowed directories"}
        try:
            size = os.path.getsize(abs_path)
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(10240)
            return {
                "status": "ok",
                "path": abs_path,
                "content": content,
                "truncated": size > 10240,
            }
        except PermissionError:
            return {"status": "error", "error": "Permission denied"}
        except FileNotFoundError:
            return {"status": "error", "error": f"File not found: {raw_path}"}

    # ---------------------------------------------------------------- #
    # Web search                                                         #
    # ---------------------------------------------------------------- #

    def _tool_web_search(self, args: dict) -> dict:
        query = args.get("query", "").strip()
        if not query:
            return {"status": "error", "error": "No query provided"}
        # Try duckduckgo-search library first
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=5))
            results = [
                {
                    "title": h.get("title", ""),
                    "url": h.get("href", ""),
                    "snippet": h.get("body", "")[:400],
                }
                for h in hits
            ]
            return {"status": "ok", "query": query, "results": results}
        except ImportError:
            pass
        except Exception as exc:
            self.logger.warning(f"duckduckgo-search failed: {exc}")
        # Fallback: basic DuckDuckGo HTML scrape
        return self._web_search_fallback(query)

    def _web_search_fallback(self, query: str) -> dict:
        import requests as _req
        try:
            r = _req.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            raw_snippets = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL
            )
            snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in raw_snippets[:5]]
            return {
                "status": "ok",
                "query": query,
                "results": [{"snippet": s} for s in snippets],
            }
        except Exception as exc:
            return {"status": "error", "error": f"Web search failed: {exc}"}

    # ---------------------------------------------------------------- #
    # Scheduler                                                          #
    # ---------------------------------------------------------------- #

    def _tool_schedule_task(self, args: dict) -> dict:
        name = args.get("name", "").strip()
        time_str = args.get("time", "").strip()
        command = args.get("command", "").strip()
        if not all([name, time_str, command]):
            return {"status": "error", "error": "'name', 'time', and 'command' are all required"}
        return {
            "status": "ok",
            "message": (
                f"Task '{name}' noted for {time_str} daily running '{command}'. "
                f"To persist, add to config.toml: [schedule]  {name} = \"{time_str} daily\""
            ),
        }
