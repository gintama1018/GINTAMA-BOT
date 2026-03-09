"""
tools/camera_tool.py — JARVIS Camera Control (Phase 6)

Supports:
  - PC webcam via OpenCV
  - Android phone camera via ADB

Usage:
    from tools.camera_tool import CameraTool
    cam = CameraTool()
    result = cam.take_photo(source="pc")   # or source="android"
    # result = {"ok": True, "path": "/path/to/photo.jpg"}
"""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PHOTOS_DIR = Path(__file__).parent.parent / "screenshots" / "camera"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)


class CameraTool:
    """Take photos from PC webcam or Android phone camera via ADB."""

    def take_photo(self, source: str = "pc", camera_index: int = 0) -> dict:
        """
        Take a photo.
        source: "pc" | "android"
        camera_index: webcam index (0 = default)
        Returns {"ok": True, "path": str} or {"ok": False, "error": str}
        """
        if source == "android":
            return self._android_photo()
        return self._pc_photo(camera_index)

    def _pc_photo(self, camera_index: int = 0) -> dict:
        """Capture from PC webcam using OpenCV."""
        try:
            import cv2  # type: ignore
        except ImportError:
            return {"ok": False, "error": "opencv-python not installed. Run: pip install opencv-python"}

        import platform
        if platform.system() == "Windows":
            cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return {"ok": False, "error": f"Could not open webcam (index {camera_index})"}
        try:
            # Allow camera to warm up
            time.sleep(0.5)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

            ret, frame = cap.read()
            if not ret or frame is None:
                return {"ok": False, "error": "Could not read frame from webcam"}

            ts = int(time.time())
            out_path = PHOTOS_DIR / f"pc_{ts}.jpg"
            cv2.imwrite(str(out_path), frame)
            return {"ok": True, "path": str(out_path), "source": "pc_webcam"}
        finally:
            cap.release()

    def _android_photo(self) -> dict:
        """Take selfie on Android phone via ADB."""
        import subprocess
        import os

        # ADB camera trigger: launch camera app, take shot, pull image
        try:
            # 1. Open Camera with Intent
            subprocess.run(
                ["adb", "shell", "am", "start", "-a", "android.media.action.IMAGE_CAPTURE"],
                capture_output=True, timeout=10
            )
            time.sleep(2)

            # 2. Tap the shutter button (center bottom area — may vary by device)
            # Get screen dimensions
            res = subprocess.run(
                ["adb", "shell", "wm", "size"],
                capture_output=True, text=True, timeout=5
            )
            width, height = 1080, 1920  # defaults
            for line in res.stdout.splitlines():
                if "Physical size" in line or "Override size" in line:
                    parts = line.split()[-1].split("x")
                    try:
                        width, height = int(parts[0]), int(parts[1])
                    except Exception:
                        pass

            cx, cy = width // 2, int(height * 0.85)
            subprocess.run(
                ["adb", "shell", "input", "tap", str(cx), str(cy)],
                capture_output=True, timeout=5
            )
            time.sleep(2)

            # 3. Find latest photo in DCIM
            res = subprocess.run(
                ["adb", "shell", "ls", "-t", "/sdcard/DCIM/Camera/"],
                capture_output=True, text=True, timeout=10
            )
            files = [f.strip() for f in res.stdout.splitlines() if f.strip().lower().endswith((".jpg", ".jpeg", ".png"))]
            if not files:
                return {"ok": False, "error": "No photo found in /sdcard/DCIM/Camera/"}

            latest = files[0]
            ts = int(time.time())
            local_path = PHOTOS_DIR / f"android_{ts}.jpg"
            subprocess.run(
                ["adb", "pull", f"/sdcard/DCIM/Camera/{latest}", str(local_path)],
                capture_output=True, timeout=30
            )
            if local_path.exists():
                return {"ok": True, "path": str(local_path), "source": "android"}
            return {"ok": False, "error": "Failed to pull photo from device"}

        except FileNotFoundError:
            return {"ok": False, "error": "ADB not found. Install Android Platform Tools."}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ADB command timed out"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list_webcams(self) -> list:
        """Detect available webcam indices (0-5)."""
        try:
            import cv2  # type: ignore
        except ImportError:
            return []
        available = []
        for i in range(6):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                available.append(i)
                cap.release()
        return available
