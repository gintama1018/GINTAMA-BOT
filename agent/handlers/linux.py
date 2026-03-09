"""
agent/handlers/linux.py — Linux / macOS execution backend for the TCC agent.

Runs on Linux desktops, servers, or macOS machines.
Uses standard shell commands (shell=False always).
"""

import os
import platform
import shlex
import socket
import subprocess
from datetime import datetime

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


class LinuxHandler:
    def __init__(self):
        self.os_type = platform.system().lower()
        self.screenshot_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "screenshots",
        )
        os.makedirs(self.screenshot_dir, exist_ok=True)

    # ---------------------------------------------------------------- #
    # info / status                                                     #
    # ---------------------------------------------------------------- #

    def info(self, args: dict) -> dict:
        data: dict = {"hostname": socket.gethostname(), "os": platform.platform()}
        if HAS_PSUTIL:
            data.update({
                "cpu_percent": psutil.cpu_percent(interval=0.5),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage("/").percent,
            })
        return {
            "status": "success",
            "message": f"{data['hostname']} — {data['os']}",
            "data": data,
        }

    def status(self, args: dict) -> dict:
        return self.info(args)

    # ---------------------------------------------------------------- #
    # screenshot                                                        #
    # ---------------------------------------------------------------- #

    def screenshot(self, args: dict) -> dict:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.screenshot_dir, f"remote_{ts}.png")

        if HAS_MSS:
            with mss.mss() as sct:
                sct.shot(output=filepath)
        else:
            # Try platform-native tools
            for tool, t_args in [
                ("scrot", [filepath]),
                ("gnome-screenshot", ["-f", filepath]),
                ("import", ["-window", "root", filepath]),
            ]:
                try:
                    r = subprocess.run(
                        [tool] + t_args, shell=False, capture_output=True
                    )
                    if r.returncode == 0:
                        break
                except FileNotFoundError:
                    continue
            else:
                return {
                    "status": "error",
                    "error": "No screenshot tool; install mss or scrot",
                }

        size_kb = os.path.getsize(filepath) // 1024 if os.path.exists(filepath) else 0
        return {
            "status": "success",
            "message": f"Screenshot: {filepath} ({size_kb}KB)",
            "data": {"file": filepath, "size_kb": size_kb},
        }

    # ---------------------------------------------------------------- #
    # open / launch                                                     #
    # ---------------------------------------------------------------- #

    def launch(self, args: dict) -> dict:
        app = args.get("app", "").strip()
        if not app:
            return {"status": "error", "error": "No app specified"}
        try:
            if self.os_type == "darwin":
                subprocess.Popen(["open", "-a", app], shell=False)
            else:
                subprocess.Popen(["xdg-open", app], shell=False)
            return {"status": "success", "message": f"{app} launched", "data": {"app": app}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def open(self, args: dict) -> dict:
        return self.launch(args)

    # ---------------------------------------------------------------- #
    # run                                                               #
    # ---------------------------------------------------------------- #

    def run(self, args: dict) -> dict:
        cmd = args.get("cmd", "").strip()
        if not cmd:
            return {"status": "error", "error": "No command specified"}
        try:
            cmd_list = shlex.split(cmd)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        try:
            result = subprocess.run(
                cmd_list, shell=False, capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            return {
                "status": "success" if result.returncode == 0 else "error",
                "message": output,
                "data": {"stdout": result.stdout, "stderr": result.stderr},
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "Timed out (30s)"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # lock                                                              #
    # ---------------------------------------------------------------- #

    def lock(self, args: dict) -> dict:
        try:
            if self.os_type == "darwin":
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
            return {"status": "success", "message": "Locked", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # notify                                                            #
    # ---------------------------------------------------------------- #

    def notify(self, args: dict) -> dict:
        message = args.get("message", "").strip()
        try:
            subprocess.run(
                ["notify-send", "TCC JARVIS", message], shell=False, timeout=5
            )
        except Exception:
            try:
                import plyer
                plyer.notification.notify(title="TCC JARVIS", message=message, timeout=5)
            except Exception:
                pass
        return {"status": "success", "message": f"Notification: {message}", "data": {}}

    # ---------------------------------------------------------------- #
    # battery                                                           #
    # ---------------------------------------------------------------- #

    def battery(self, args: dict) -> dict:
        if HAS_PSUTIL:
            bat = psutil.sensors_battery()
            if bat:
                level = round(bat.percent)
                return {
                    "status": "success",
                    "message": f"Battery: {level}% │ Charging: {'Yes' if bat.power_plugged else 'No'}",
                    "data": {"level": level, "charging": bat.power_plugged},
                }
        try:
            result = subprocess.run(
                ["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"],
                shell=False, capture_output=True, text=True,
            )
            return {"status": "success", "message": result.stdout, "data": {}}
        except Exception:
            return {"status": "error", "error": "No battery info available"}

    # ---------------------------------------------------------------- #
    # ls                                                                #
    # ---------------------------------------------------------------- #

    def ls(self, args: dict) -> dict:
        path = args.get("path", ".").strip()
        try:
            entries = sorted(os.listdir(path))
            return {
                "status": "success",
                "message": "\n".join(entries),
                "data": {"entries": entries},
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # reboot / shutdown                                                 #
    # ---------------------------------------------------------------- #

    def reboot(self, args: dict) -> dict:
        subprocess.run(["reboot"], shell=False)
        return {"status": "success", "message": "Rebooting...", "data": {}}

    def shutdown(self, args: dict) -> dict:
        subprocess.run(["shutdown", "-h", "now"], shell=False)
        return {"status": "success", "message": "Shutting down...", "data": {}}
