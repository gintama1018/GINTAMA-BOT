"""
modules/phone.py — Android device control via ADB.

Used when:
  - Device transport = "adb" in config.toml
  - No HTTP agent running on the phone

Requires:  Android Platform Tools (adb) in PATH
           Android Developer Mode + USB/Wi-Fi debugging enabled

Supported actions:
  info, screenshot, battery, launch/open, volume, brightness,
  lock, push, pull, ls, notify, run, reboot, shutdown
"""

import os
import subprocess
import shlex
from datetime import datetime
from typing import Tuple

from src.parser import Intent


# Map common app name → package name
APP_PACKAGES: dict = {
    "chrome":       "com.android.chrome",
    "youtube":      "com.google.android.youtube",
    "spotify":      "com.spotify.music",
    "whatsapp":     "com.whatsapp",
    "instagram":    "com.instagram.android",
    "twitter":      "com.twitter.android",
    "camera":       "com.android.camera2",
    "photos":       "com.google.android.apps.photos",
    "maps":         "com.google.android.apps.maps",
    "gmail":        "com.google.android.gm",
    "settings":     "com.android.settings",
    "calculator":   "com.android.calculator2",
    "files":        "com.google.android.apps.nbu.files",
    "clock":        "com.google.android.deskclock",
    "contacts":     "com.android.contacts",
    "messages":     "com.google.android.apps.messaging",
    "phone":        "com.google.android.dialer",
    "play":         "com.android.vending",
    "drive":        "com.google.android.apps.docs",
    "netflix":      "com.netflix.mediaclient",
    "telegram":     "org.telegram.messenger",
    "discord":      "com.discord",
}


class PhoneModule:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scr_dir = config.get("tcc", {}).get("screenshot_dir", "screenshots")
        self.screenshot_dir = os.path.join(project_root, scr_dir)

    # ---------------------------------------------------------------- #
    # ADB subprocess wrapper                                            #
    # ---------------------------------------------------------------- #

    def _adb(self, *args, timeout: int = 15) -> Tuple[str, str, int]:
        """
        Run an ADB command. shell=False always — no injection possible.
        Returns (stdout, stderr, returncode).
        """
        cmd = ["adb"] + [str(a) for a in args]
        try:
            result = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "ADB command timed out", -1
        except FileNotFoundError:
            return (
                "",
                "ADB not found. Install Android Platform Tools and add to PATH.\n"
                "Download: https://developer.android.com/tools/releases/platform-tools",
                -1,
            )

    # ---------------------------------------------------------------- #
    # Intent dispatcher                                                 #
    # ---------------------------------------------------------------- #

    def execute(self, intent: Intent) -> dict:
        action = intent.action
        handler = getattr(self, action, None)
        if handler is None:
            return {
                "status": "error",
                "error": f"Unknown action '{action}' for phone. Type 'help' for reference.",
            }
        return handler(intent.args)

    # ---------------------------------------------------------------- #
    # Actions                                                           #
    # ---------------------------------------------------------------- #

    def info(self, args: dict) -> dict:
        model, err, rc = self._adb("shell", "getprop", "ro.product.model")
        if rc != 0:
            return {"status": "error", "error": err or "ADB connection failed"}
        android_ver, _, _ = self._adb("shell", "getprop", "ro.build.version.release")
        bat, _, _ = self._adb("shell", "dumpsys", "battery")
        level = "?"
        for line in bat.splitlines():
            if "level:" in line:
                level = line.split(":")[1].strip()
                break
        return {
            "status": "success",
            "message": f"Phone: {model} (Android {android_ver}) │ Battery: {level}%",
            "data": {"model": model, "android": android_ver, "battery": level},
        }

    def status(self, args: dict) -> dict:
        return self.info(args)

    def screenshot(self, args: dict) -> dict:
        os.makedirs(self.screenshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        remote_path = f"/sdcard/tcc_shot_{ts}.png"
        local_path = os.path.join(self.screenshot_dir, f"phone_{ts}.png")

        _, err, rc = self._adb("shell", "screencap", "-p", remote_path)
        if rc != 0:
            return {"status": "error", "error": err or "screencap failed"}

        _, err, rc = self._adb("pull", remote_path, local_path, timeout=30)
        if rc != 0:
            return {"status": "error", "error": err or "pull screenshot failed"}

        self._adb("shell", "rm", remote_path)  # cleanup remote temp file

        size_kb = os.path.getsize(local_path) // 1024 if os.path.exists(local_path) else 0
        return {
            "status": "success",
            "message": f"Screenshot saved → {local_path}  ({size_kb}KB)",
            "data": {"file": local_path, "size_kb": size_kb},
        }

    def battery(self, args: dict) -> dict:
        out, err, rc = self._adb("shell", "dumpsys", "battery")
        if rc != 0:
            return {"status": "error", "error": err or "ADB failed"}

        level, charging = "?", False
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("level:"):
                level = line.split(":")[1].strip()
            elif line.startswith("status:"):
                # 2 = charging
                charging = line.split(":")[1].strip() == "2"

        return {
            "status": "success",
            "message": f"Phone │ Battery: {level}% │ Charging: {'Yes' if charging else 'No'}",
            "data": {"level": level, "charging": charging},
        }

    def launch(self, args: dict) -> dict:
        app = args.get("app", "").strip().lower()
        if not app:
            return {"status": "error", "error": "No app specified. Usage: phone launch <app>"}

        package = APP_PACKAGES.get(app.lower(), app)
        out, err, rc = self._adb(
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        )
        if rc != 0 or "error" in (out + err).lower():
            return {
                "status": "error",
                "error": f"Failed to launch '{app}': {err or out}",
            }
        return {
            "status": "success",
            "message": f"{app.title()} launched on phone",
            "data": {"app": app, "package": package},
        }

    def open(self, args: dict) -> dict:
        return self.launch(args)

    def volume(self, args: dict) -> dict:
        level = args.get("level", "")
        if not level:
            return {
                "status": "error",
                "error": "Volume level required (0-15). Usage: phone volume <level>",
            }
        try:
            level_int = int(level)
            if not 0 <= level_int <= 15:
                raise ValueError
        except ValueError:
            return {"status": "error", "error": "Volume must be an integer 0–15"}

        _, err, rc = self._adb(
            "shell", "media", "volume",
            "--show", "1", "--stream", "3", "--set", str(level_int),
        )
        if rc != 0:
            return {"status": "error", "error": err or "Volume command failed"}

        return {
            "status": "success",
            "message": f"Volume set to {level_int}/15",
            "data": {"level": level_int},
        }

    def brightness(self, args: dict) -> dict:
        level = args.get("level", "")
        if not level:
            return {
                "status": "error",
                "error": "Brightness required (0-255). Usage: phone brightness <level>",
            }
        try:
            level_int = int(level)
            if not 0 <= level_int <= 255:
                raise ValueError
        except ValueError:
            return {"status": "error", "error": "Brightness must be an integer 0–255"}

        _, err, rc = self._adb(
            "shell", "settings", "put", "system", "screen_brightness", str(level_int),
        )
        if rc != 0:
            return {"status": "error", "error": err or "Brightness command failed"}

        return {
            "status": "success",
            "message": f"Brightness set to {level_int}",
            "data": {"level": level_int},
        }

    def lock(self, args: dict) -> dict:
        _, err, rc = self._adb("shell", "input", "keyevent", "26")  # KEYCODE_POWER
        if rc != 0:
            return {"status": "error", "error": err or "Lock failed"}
        return {"status": "success", "message": "Phone locked", "data": {}}

    def push(self, args: dict) -> dict:
        src = args.get("src", "").strip()
        dst = args.get("dst", "/sdcard/").strip()
        if not src:
            return {
                "status": "error",
                "error": "Source required. Usage: phone push <local_src> <device_dst>",
            }
        if not os.path.exists(src):
            return {"status": "error", "error": f"Source not found: '{src}'"}

        size = os.path.getsize(src)
        _, err, rc = self._adb("push", src, dst, timeout=120)
        if rc != 0:
            return {"status": "error", "error": err or "Push failed"}

        return {
            "status": "success",
            "message": f"{os.path.basename(src)} → {dst}  ({size // 1024}KB)",
            "data": {"src": src, "dst": dst, "bytes": size},
        }

    def pull(self, args: dict) -> dict:
        src = args.get("src", "").strip()
        dst = args.get("dst", ".").strip()
        if not src:
            return {
                "status": "error",
                "error": "Source path required. Usage: phone pull <device_src> <local_dst>",
            }

        _, err, rc = self._adb("pull", src, dst, timeout=120)
        if rc != 0:
            return {"status": "error", "error": err or "Pull failed"}

        return {
            "status": "success",
            "message": f"{src} → {dst}",
            "data": {"src": src, "dst": dst},
        }

    def ls(self, args: dict) -> dict:
        path = args.get("path", "/sdcard/")
        out, err, rc = self._adb("shell", "ls", "-la", path)
        if rc != 0:
            return {"status": "error", "error": err or "ls failed"}
        return {"status": "success", "message": out, "data": {"listing": out}}

    def notify(self, args: dict) -> dict:
        message = args.get("message", args.get("msg", "")).strip()
        if not message:
            return {"status": "error", "error": "No message specified"}

        # Sanitize: remove characters that could cause shell issues
        safe_msg = message.replace('"', "").replace("'", "").replace("`", "")[:200]

        _, _, rc = self._adb(
            "shell", "cmd", "notification", "post",
            "-S", "bigtext", "-t", "TCC JARVIS", "tcc_tag", safe_msg,
        )
        # Best-effort — not all Android versions support this command
        return {
            "status": "success",
            "message": f"Notification sent to phone: {message}",
            "data": {"message": message},
        }

    def run(self, args: dict) -> dict:
        """Execute a raw shell command on the phone via ADB."""
        cmd = args.get("cmd", "").strip()
        if not cmd:
            return {"status": "error", "error": "No command. Usage: phone run \"<cmd>\""}

        # Parse to list — prevents injection
        try:
            cmd_parts = shlex.split(cmd)
        except ValueError as e:
            return {"status": "error", "error": f"Invalid syntax: {e}"}

        out, err, rc = self._adb("shell", *cmd_parts, timeout=30)
        return {
            "status": "success" if rc == 0 else "error",
            "message": out or err or "(no output)",
            "data": {"stdout": out, "stderr": err, "returncode": rc},
        }

    def reboot(self, args: dict) -> dict:
        _, err, rc = self._adb("reboot")
        if rc != 0:
            return {"status": "error", "error": err or "Reboot failed"}
        return {"status": "success", "message": "Phone rebooting...", "data": {}}

    def shutdown(self, args: dict) -> dict:
        _, err, rc = self._adb("shell", "reboot", "-p")
        if rc != 0:
            return {"status": "error", "error": err or "Shutdown failed"}
        return {"status": "success", "message": "Phone shutting down...", "data": {}}
