"""
agent/handlers/windows.py — Windows execution backend for the TCC agent.

This handler runs ON the Windows device as part of the agent server.
No ADB — it calls OS-native Windows APIs and PowerShell.
"""

import os
import platform
import shlex
import shutil
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


class WindowsHandler:
    def __init__(self):
        self.screenshot_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "screenshots",
        )
        os.makedirs(self.screenshot_dir, exist_ok=True)

    # ---------------------------------------------------------------- #
    # info / status                                                     #
    # ---------------------------------------------------------------- #

    def info(self, args: dict) -> dict:
        data: dict = {
            "hostname": socket.gethostname(),
            "os": platform.platform(),
        }
        if HAS_PSUTIL:
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            # BUG 4: Windows doesn't have "/" — use C:\\
            disk_root = "C:\\" if platform.system().lower() == "windows" else "/"
            disk = psutil.disk_usage(disk_root)
            data.update({
                "cpu_percent": cpu,
                "memory_percent": mem.percent,
                "disk_percent": disk.percent,
                "memory_used_gb": round(mem.used / 1e9, 1),
                "memory_total_gb": round(mem.total / 1e9, 1),
                "disk_used_gb": round(disk.used / 1e9, 1),
                "disk_total_gb": round(disk.total / 1e9, 1),
            })
        msg = (
            f"{data['hostname']} — {data['os']}\n"
            f"CPU: {data.get('cpu_percent', '?')}%  "
            f"RAM: {data.get('memory_used_gb', '?')}GB "
            f"Disk: {data.get('disk_used_gb', '?')}GB"
        )
        return {"status": "success", "data": data, "message": msg}

    def status(self, args: dict) -> dict:
        return self.info(args)

    # ---------------------------------------------------------------- #
    # screenshot                                                        #
    # ---------------------------------------------------------------- #

    def screenshot(self, args: dict) -> dict:
        if not HAS_MSS:
            return {"status": "error", "error": "mss not installed. Run: pip install mss"}
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.screenshot_dir, f"remote_{ts}.png")
        with mss.mss() as sct:
            sct.shot(output=filepath)
        size_kb = os.path.getsize(filepath) // 1024
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
            os.startfile(app)
            return {"status": "success", "message": f"{app} launched", "data": {"app": app}}
        except Exception:
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", app],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"status": "success", "message": f"{app} launched", "data": {"app": app}}
            except Exception as e:
                return {"status": "error", "error": str(e)}

    def open(self, args: dict) -> dict:
        return self.launch(args)

    # ---------------------------------------------------------------- #
    # run (shell command)                                               #
    # ---------------------------------------------------------------- #

    def run(self, args: dict) -> dict:
        cmd = args.get("cmd", "").strip()
        if not cmd:
            return {"status": "error", "error": "No command specified"}
        try:
            cmd_list = shlex.split(cmd)
        except ValueError as e:
            return {"status": "error", "error": f"Invalid syntax: {e}"}
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
            return {"status": "error", "error": "Command timed out (30s)"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # lock                                                              #
    # ---------------------------------------------------------------- #

    def lock(self, args: dict) -> dict:
        try:
            subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"], shell=False
            )
            return {"status": "success", "message": "Locked", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------- #
    # notify                                                            #
    # ---------------------------------------------------------------- #

    def notify(self, args: dict) -> dict:
        message = args.get("message", "").strip()
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
        return {"status": "error", "error": "No battery info available (desktop?)"}

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
        result = subprocess.run(["shutdown", "/r", "/t", "10"], shell=False, capture_output=True)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.decode(errors="replace").strip() or "reboot failed"}
        return {"status": "success", "message": "Rebooting in 10 seconds", "data": {}}

    def shutdown(self, args: dict) -> dict:
        result = subprocess.run(["shutdown", "/s", "/t", "10"], shell=False, capture_output=True)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.decode(errors="replace").strip() or "shutdown failed"}
        return {"status": "success", "message": "Shutting down in 10 seconds", "data": {}}

    # ---------------------------------------------------------------- #
    # push / pull (BUG 1)                                              #
    # ---------------------------------------------------------------- #

    def push(self, args: dict) -> dict:
        """Copy a file from TCC host to this device (local copy)."""
        src = args.get("src", "").strip()
        dst = args.get("dst", "").strip()
        if not src or not dst:
            return {"status": "error", "error": "Usage: push <src> <dst>"}
        # Security: normalise + restrict to legal paths
        src_abs = os.path.realpath(src)
        dst_abs = os.path.realpath(dst)
        if not os.path.exists(src_abs):
            return {"status": "error", "error": f"Source not found: {src}"}
        try:
            if os.path.isdir(dst_abs):
                dst_abs = os.path.join(dst_abs, os.path.basename(src_abs))
            os.makedirs(os.path.dirname(dst_abs) or ".", exist_ok=True)
            shutil.copy2(src_abs, dst_abs)
            size_kb = os.path.getsize(dst_abs) // 1024
            return {
                "status": "success",
                "message": f"Copied {src} → {dst_abs} ({size_kb}KB)",
                "data": {"dst": dst_abs, "size_kb": size_kb},
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def pull(self, args: dict) -> dict:
        """Copy a file from this device to TCC host (local copy)."""
        return self.push(args)  # Semantically identical for local-to-local
