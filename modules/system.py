"""
modules/system.py — Local machine control module.

Handles all 'system' target commands:
  info, screenshot, open/launch, run, notify, battery, lock, ls, status
"""

import os
import sys
import platform
import subprocess
import shlex
import socket
from datetime import datetime
from typing import Any

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False


class SystemModule:
    """Controls the local machine that TCC is running on."""

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        self.os_type = platform.system().lower()   # "windows" | "linux" | "darwin"

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scr_dir = config.get("tcc", {}).get("screenshot_dir", "screenshots")
        self.screenshot_dir = os.path.join(project_root, scr_dir)

    # ---------------------------------------------------------------- #
    # info / status                                                     #
    # ---------------------------------------------------------------- #

    def info(self, args: dict) -> dict:
        if not HAS_PSUTIL:
            return {
                "status": "error",
                "error": "psutil not installed. Run: pip install psutil",
            }

        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        data = {
            "hostname": socket.gethostname(),
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_percent": cpu,
            "memory_total_gb": round(mem.total / 1e9, 1),
            "memory_used_gb": round(mem.used / 1e9, 1),
            "memory_percent": mem.percent,
            "disk_total_gb": round(disk.total / 1e9, 1),
            "disk_used_gb": round(disk.used / 1e9, 1),
            "disk_percent": disk.percent,
        }

        msg = (
            f"[bold]{data['hostname']}[/bold] — {data['os']}\n"
            f"CPU: {cpu}%  │  "
            f"RAM: {data['memory_used_gb']}GB/{data['memory_total_gb']}GB ({mem.percent}%)  │  "
            f"Disk: {data['disk_used_gb']}GB/{data['disk_total_gb']}GB ({disk.percent}%)"
        )
        return {"status": "success", "message": msg, "data": data}

    def status(self, args: dict) -> dict:
        return self.info(args)

    # ---------------------------------------------------------------- #
    # screenshot                                                        #
    # ---------------------------------------------------------------- #

    def screenshot(self, args: dict) -> dict:
        if not HAS_MSS:
            return {
                "status": "error",
                "error": "mss not installed. Run: pip install mss",
            }

        os.makedirs(self.screenshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.screenshot_dir, f"system_{ts}.png")

        with mss.mss() as sct:
            sct.shot(output=filepath)

        size_kb = os.path.getsize(filepath) // 1024
        return {
            "status": "success",
            "message": f"Screenshot saved → {filepath}  ({size_kb}KB)",
            "data": {"file": filepath, "size_kb": size_kb},
        }

    # ---------------------------------------------------------------- #
    # open / launch                                                     #
    # ---------------------------------------------------------------- #

    def open(self, args: dict) -> dict:
        app = args.get("app", "").strip().lower()
        if not app:
            return {
                "status": "error",
                "error": "No app specified. Usage: system open <app>",
            }

        try:
            if self.os_type == "windows":
                # os.startfile is Windows-only, safe, no shell injection
                os.startfile(app)
            elif self.os_type == "darwin":
                subprocess.Popen(["open", "-a", app], shell=False)
            else:
                subprocess.Popen(["xdg-open", app], shell=False)

            return {
                "status": "success",
                "message": f"{app.title()} launched on system",
                "data": {"app": app},
            }
        except FileNotFoundError:
            # Windows startfile raises FileNotFoundError for unknown apps
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", app],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {
                    "status": "success",
                    "message": f"{app.title()} launched on system",
                    "data": {"app": app},
                }
            except Exception as e:
                return {"status": "error", "error": f"Could not open '{app}': {e}"}
        except Exception as e:
            return {"status": "error", "error": f"Could not open '{app}': {e}"}

    def launch(self, args: dict) -> dict:
        return self.open(args)

    # ---------------------------------------------------------------- #
    # run (shell command)                                               #
    # ---------------------------------------------------------------- #

    def run(self, args: dict) -> dict:
        """Execute a shell command with injection prevention."""
        cmd = args.get("cmd", "").strip()
        if not cmd:
            return {
                "status": "error",
                "error": "No command specified. Usage: system run \"<cmd>\"",
            }

        # Parse command string into list — prevents shell injection
        try:
            cmd_list = shlex.split(cmd)
        except ValueError as e:
            return {"status": "error", "error": f"Invalid command syntax: {e}"}

        try:
            result = subprocess.run(
                cmd_list,
                shell=False,       # SECURITY: never True
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            return {
                "status": "success" if result.returncode == 0 else "error",
                "message": output,
                "data": {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "Command timed out (30s limit)"}
        except FileNotFoundError:
            return {
                "status": "error",
                "error": f"Command not found: '{cmd_list[0]}'",
            }
        except PermissionError:
            return {"status": "error", "error": f"Permission denied: '{cmd_list[0]}'"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # notify                                                            #
    # ---------------------------------------------------------------- #

    def notify(self, args: dict) -> dict:
        message = args.get("message", args.get("msg", "")).strip()
        if not message:
            return {
                "status": "error",
                "error": "No message. Usage: system notify <message>",
            }

        try:
            import plyer
            plyer.notification.notify(
                title="TCC — JARVIS",
                message=message,
                timeout=5,
            )
        except Exception:
            pass  # silently fall through — message already shown in terminal

        return {
            "status": "success",
            "message": f"Notification: {message}",
            "data": {"message": message},
        }

    # ---------------------------------------------------------------- #
    # battery                                                           #
    # ---------------------------------------------------------------- #

    def battery(self, args: dict) -> dict:
        if not HAS_PSUTIL:
            return {"status": "error", "error": "psutil not installed"}

        bat = psutil.sensors_battery()
        if bat is None:
            return {
                "status": "error",
                "error": "No battery detected (desktop system?)",
            }

        level = round(bat.percent)
        charging = bat.power_plugged
        return {
            "status": "success",
            "message": f"System │ Battery: {level}% │ Charging: {'Yes' if charging else 'No'}",
            "data": {"level": level, "charging": charging},
        }

    # ---------------------------------------------------------------- #
    # lock                                                              #
    # ---------------------------------------------------------------- #

    def lock(self, args: dict) -> dict:
        try:
            if self.os_type == "windows":
                subprocess.run(
                    ["rundll32.exe", "user32.dll,LockWorkStation"],
                    shell=False,
                )
            elif self.os_type == "darwin":
                subprocess.run(
                    [
                        "/System/Library/CoreServices/Menu Extras/"
                        "User.menu/Contents/Resources/CGSession",
                        "-suspend",
                    ],
                    shell=False,
                )
            else:
                subprocess.run(["loginctl", "lock-session"], shell=False)

            return {"status": "success", "message": "System locked", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # ls                                                                #
    # ---------------------------------------------------------------- #

    def ls(self, args: dict) -> dict:
        path = args.get("path", ".").strip()
        try:
            entries = sorted(os.listdir(path))
            lines = []
            for e in entries:
                full = os.path.join(path, e)
                marker = "/" if os.path.isdir(full) else ""
                lines.append(f"{e}{marker}")
            return {
                "status": "success",
                "message": "\n".join(lines) or "(empty directory)",
                "data": {"entries": entries, "path": path},
            }
        except FileNotFoundError:
            return {"status": "error", "error": f"Path not found: '{path}'"}
        except PermissionError:
            return {"status": "error", "error": f"Permission denied: '{path}'"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # reboot / shutdown                                                 #
    # ---------------------------------------------------------------- #

    def reboot(self, args: dict) -> dict:
        try:
            if self.os_type == "windows":
                subprocess.run(["shutdown", "/r", "/t", "10"], shell=False)
            else:
                subprocess.run(["reboot"], shell=False)
            return {"status": "success", "message": "System rebooting...", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def shutdown(self, args: dict) -> dict:
        try:
            if self.os_type == "windows":
                subprocess.run(["shutdown", "/s", "/t", "10"], shell=False)
            else:
                subprocess.run(["shutdown", "-h", "now"], shell=False)
            return {"status": "success", "message": "System shutting down...", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}
