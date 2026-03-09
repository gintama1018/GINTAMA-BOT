"""
modules/camera.py — JARVIS Camera Module

Unified camera access merging PC webcam (OpenCV) and Android ADB.
Also used by CameraModule in agent tool_registry.
"""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PHOTOS_DIR = Path(__file__).parent.parent / "screenshots" / "camera"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)


class CameraModule:
    """Unified camera: auto-detects best available source."""

    def take_photo(self, source: str = "auto", camera_index: int = 0) -> dict:
        """
        source: "auto" | "pc" | "android"
        auto → tries android first, falls back to pc
        """
        if source == "auto":
            result = self._android_photo()
            if not result.get("ok"):
                result = self._pc_photo(camera_index)
            return result
        if source == "android":
            return self._android_photo()
        return self._pc_photo(camera_index)

    def _pc_photo(self, camera_index: int = 0) -> dict:
        try:
            import cv2  # type: ignore
        except ImportError:
            return {"ok": False, "error": "opencv-python not installed. Run: pip install opencv-python"}

        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return {"ok": False, "error": f"Webcam index {camera_index} not available"}

        try:
            time.sleep(0.4)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            ret, frame = cap.read()
            if not ret:
                return {"ok": False, "error": "Failed to capture frame"}
            ts = int(time.time())
            path = PHOTOS_DIR / f"pc_{ts}.jpg"
            cv2.imwrite(str(path), frame)
            return {"ok": True, "path": str(path), "source": "pc_webcam"}
        finally:
            cap.release()

    def _android_photo(self) -> dict:
        """ADB camera via screenshot (front/back cam via intent)."""
        import subprocess

        try:
            # Check ADB is available
            result = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=5
            )
            if "device" not in result.stdout:
                return {"ok": False, "error": "No Android device connected via ADB"}

            # Use ADB screencap as fallback (always works)
            ts = int(time.time())
            remote_path = f"/sdcard/jarvis_photo_{ts}.png"
            local_path = PHOTOS_DIR / f"android_{ts}.png"

            subprocess.run(
                ["adb", "shell", "screencap", "-p", remote_path],
                capture_output=True, timeout=10
            )
            subprocess.run(
                ["adb", "pull", remote_path, str(local_path)],
                capture_output=True, timeout=15
            )
            # Clean up remote
            subprocess.run(["adb", "shell", "rm", remote_path], capture_output=True)

            if local_path.exists():
                return {"ok": True, "path": str(local_path), "source": "android_screencap"}
            return {"ok": False, "error": "ADB screenshot failed"}

        except FileNotFoundError:
            return {"ok": False, "error": "ADB not found"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ADB timeout"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
