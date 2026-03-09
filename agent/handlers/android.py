"""
agent/handlers/android.py — Android execution backend for the TCC agent.

This handler runs ON the Android device inside Termux.
It calls Android system commands directly (no ADB).

Commands used:
  am, monkey, settings, media, input, screencap, dumpsys, getprop, cmd
"""

import os
import subprocess
import shlex
from datetime import datetime


class AndroidHandler:
    def __init__(self):
        self.screenshot_dir = "/sdcard/tcc_screenshots"
        os.makedirs(self.screenshot_dir, exist_ok=True)

    # ---------------------------------------------------------------- #
    # Shell helper                                                      #
    # ---------------------------------------------------------------- #

    def _sh(self, *args, timeout: int = 10):
        """Run a command safely. Returns (stdout, stderr, returncode)."""
        try:
            result = subprocess.run(
                list(args),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command timed out", -1
        except Exception as e:
            return "", str(e), -1

    # ---------------------------------------------------------------- #
    # Actions                                                           #
    # ---------------------------------------------------------------- #

    def info(self, args: dict) -> dict:
        model, _, _ = self._sh("getprop", "ro.product.model")
        android_ver, _, _ = self._sh("getprop", "ro.build.version.release")
        bat_cap, _, _ = self._sh("cat", "/sys/class/power_supply/battery/capacity")
        return {
            "status": "success",
            "message": f"{model} (Android {android_ver}) │ Battery: {bat_cap}%",
            "data": {"model": model, "android": android_ver, "battery": bat_cap},
        }

    def status(self, args: dict) -> dict:
        return self.info(args)

    def screenshot(self, args: dict) -> dict:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"{self.screenshot_dir}/shot_{ts}.png"
        _, err, rc = self._sh("screencap", "-p", filepath)
        if rc != 0:
            return {"status": "error", "error": err or "screencap failed"}
        size_kb = os.path.getsize(filepath) // 1024 if os.path.exists(filepath) else 0
        return {
            "status": "success",
            "message": f"Screenshot: {filepath} ({size_kb}KB)",
            "data": {"file": filepath, "size_kb": size_kb},
        }

    def battery(self, args: dict) -> dict:
        out, _, _ = self._sh("dumpsys", "battery")
        level, charging = "?", False
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("level:"):
                level = line.split(":")[1].strip()
            elif line.startswith("status:"):
                charging = line.split(":")[1].strip() == "2"
        return {
            "status": "success",
            "message": f"Battery: {level}% │ Charging: {'Yes' if charging else 'No'}",
            "data": {"level": level, "charging": charging},
        }

    def launch(self, args: dict) -> dict:
        app = args.get("app", "").strip()
        if not app:
            return {"status": "error", "error": "No app specified"}

        APP_PACKAGES = {
            "chrome":    "com.android.chrome",
            "youtube":   "com.google.android.youtube",
            "spotify":   "com.spotify.music",
            "whatsapp":  "com.whatsapp",
            "instagram": "com.instagram.android",
            "camera":    "com.android.camera2",
            "settings":  "com.android.settings",
            "maps":      "com.google.android.apps.maps",
            "gmail":     "com.google.android.gm",
            "telegram":  "org.telegram.messenger",
            "discord":   "com.discord",
        }
        package = APP_PACKAGES.get(app.lower(), app)
        _, err, rc = self._sh(
            "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        )
        if rc != 0:
            return {"status": "error", "error": f"Could not launch '{app}': {err}"}
        return {
            "status": "success",
            "message": f"{app} launched",
            "data": {"app": app, "package": package},
        }

    def open(self, args: dict) -> dict:
        return self.launch(args)

    def volume(self, args: dict) -> dict:
        level = args.get("level", "5")
        try:
            level_int = max(0, min(15, int(level)))
        except ValueError:
            return {"status": "error", "error": "Volume must be 0-15"}
        _, err, rc = self._sh(
            "media", "volume", "--show", "1", "--stream", "3", "--set", str(level_int)
        )
        if rc != 0:
            return {"status": "error", "error": err}
        return {"status": "success", "message": f"Volume: {level_int}/15", "data": {"level": level_int}}

    def brightness(self, args: dict) -> dict:
        level = args.get("level", "128")
        try:
            level_int = max(0, min(255, int(level)))
        except ValueError:
            return {"status": "error", "error": "Brightness must be 0-255"}
        _, err, rc = self._sh(
            "settings", "put", "system", "screen_brightness", str(level_int)
        )
        if rc != 0:
            return {"status": "error", "error": err}
        return {"status": "success", "message": f"Brightness: {level_int}", "data": {"level": level_int}}

    def lock(self, args: dict) -> dict:
        _, err, rc = self._sh("input", "keyevent", "26")  # KEYCODE_POWER
        if rc != 0:
            return {"status": "error", "error": err or "lock failed"}
        return {"status": "success", "message": "Phone locked", "data": {}}

    def notify(self, args: dict) -> dict:
        message = args.get("message", "").strip()
        # Sanitize to prevent any injection
        safe_msg = message.replace('"', "").replace("'", "").replace("`", "")[:200]
        _, _, rc = self._sh(
            "cmd", "notification", "post", "-S", "bigtext",
            "-t", "TCC JARVIS", "tcc", safe_msg,
        )
        return {"status": "success", "message": f"Notification: {message}", "data": {}}

    def run(self, args: dict) -> dict:
        cmd = args.get("cmd", "").strip()
        if not cmd:
            return {"status": "error", "error": "No command"}
        try:
            cmd_parts = shlex.split(cmd)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        out, err, rc = self._sh(*cmd_parts, timeout=30)
        return {
            "status": "success" if rc == 0 else "error",
            "message": out or err or "(no output)",
            "data": {"stdout": out, "stderr": err},
        }

    def ls(self, args: dict) -> dict:
        path = args.get("path", "/sdcard/")
        out, err, rc = self._sh("ls", "-la", path)
        if rc != 0:
            return {"status": "error", "error": err}
        return {"status": "success", "message": out, "data": {"listing": out}}

    def reboot(self, args: dict) -> dict:
        self._sh("reboot")
        return {"status": "success", "message": "Rebooting...", "data": {}}

    def shutdown(self, args: dict) -> dict:
        self._sh("reboot", "-p")
        return {"status": "success", "message": "Shutting down...", "data": {}}
