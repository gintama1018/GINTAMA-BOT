"""
modules/screen_record.py — JARVIS Screen Recording

Records screen to MP4 using ffmpeg (Windows/Linux) or ADB screenrecord (Android).

Usage:
    rec = ScreenRecorder()
    path = rec.start_recording(duration=30)  # returns path when done
    rec.stop_recording()                      # stop early
"""

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RECORDINGS_DIR = Path(__file__).parent.parent / "screenshots" / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


class ScreenRecorder:
    """Record screen or Android device screen."""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._output_path: Optional[Path] = None
        self._recording = False
        self._thread: Optional[threading.Thread] = None

    def record_pc(self, duration: int = 30, fps: int = 15) -> dict:
        """
        Record PC screen using ffmpeg.
        Returns when recording completes.
        duration: max seconds to record (0 = until stop_recording() is called)
        """
        ts = int(time.time())
        out_path = RECORDINGS_DIR / f"screen_{ts}.mp4"

        # ffmpeg command — Windows (gdigrab) or Linux (x11grab)
        import platform
        sys = platform.system()
        if sys == "Windows":
            cmd = [
                "ffmpeg", "-y",
                "-f", "gdigrab",
                "-framerate", str(fps),
                "-i", "desktop",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-t", str(duration) if duration > 0 else "999999",
                str(out_path)
            ]
        elif sys == "Linux":
            cmd = [
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-framerate", str(fps),
                "-i", ":0.0",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-t", str(duration) if duration > 0 else "999999",
                str(out_path)
            ]
        else:
            return {"ok": False, "error": f"Unsupported OS: {sys}"}

        try:
            self._output_path = out_path
            self._recording = True
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if duration > 0:
                self._proc.wait(timeout=duration + 5)
            return {"ok": True, "path": str(out_path), "source": "pc_screen"}
        except FileNotFoundError:
            return {"ok": False, "error": "ffmpeg not installed. Install ffmpeg and add to PATH."}
        except subprocess.TimeoutExpired:
            self._proc.kill()
            return {"ok": True, "path": str(out_path), "source": "pc_screen"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._recording = False

    def record_android(self, duration: int = 30) -> dict:
        """
        Record Android screen via ADB screenrecord.
        Max 180 seconds (Android limit).
        """
        duration = min(duration, 180)
        ts = int(time.time())
        remote_path = f"/sdcard/jarvis_rec_{ts}.mp4"
        local_path = RECORDINGS_DIR / f"android_{ts}.mp4"

        try:
            self._recording = True
            proc = subprocess.Popen(
                ["adb", "shell", "screenrecord", "--time-limit", str(duration), remote_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            proc.wait(timeout=duration + 10)

            # Pull to local
            subprocess.run(
                ["adb", "pull", remote_path, str(local_path)],
                capture_output=True, timeout=60
            )
            subprocess.run(["adb", "shell", "rm", remote_path], capture_output=True)

            if local_path.exists():
                return {"ok": True, "path": str(local_path), "source": "android_screen"}
            return {"ok": False, "error": "Recording file not found"}

        except FileNotFoundError:
            return {"ok": False, "error": "ADB not found"}
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            return {"ok": False, "error": "Recording timed out"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._recording = False

    def stop_recording(self) -> Optional[str]:
        """Stop an in-progress recording early. Returns output path."""
        if self._proc and self._recording:
            self._proc.terminate()
            time.sleep(0.5)
            self._recording = False
            return str(self._output_path) if self._output_path else None
        return None

    @property
    def is_recording(self) -> bool:
        return self._recording
