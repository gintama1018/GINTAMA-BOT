"""
modules/files.py — Local file operations module.

Provides file management for the TCC system target:
  ls, copy, move, delete, exists, mkdir
"""

import os
import shutil


class FilesModule:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

    def ls(self, args: dict) -> dict:
        path = args.get("path", ".").strip()
        try:
            entries = sorted(os.listdir(path))
            lines = []
            for e in entries:
                full = os.path.join(path, e)
                tag = "/" if os.path.isdir(full) else ""
                size = f"  {os.path.getsize(full):>10} B" if os.path.isfile(full) else ""
                lines.append(f"{e}{tag}{size}")
            return {
                "status": "success",
                "message": "\n".join(lines) or "(empty directory)",
                "data": {"entries": entries, "path": path},
            }
        except FileNotFoundError:
            return {"status": "error", "error": f"Path not found: '{path}'"}
        except PermissionError:
            return {"status": "error", "error": f"Permission denied: '{path}'"}

    def copy(self, args: dict) -> dict:
        src = args.get("src", "")
        dst = args.get("dst", "")
        if not src or not dst:
            return {"status": "error", "error": "src and dst required"}
        try:
            shutil.copy2(src, dst)
            return {"status": "success", "message": f"Copied {src} → {dst}", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def move(self, args: dict) -> dict:
        src = args.get("src", "")
        dst = args.get("dst", "")
        if not src or not dst:
            return {"status": "error", "error": "src and dst required"}
        try:
            shutil.move(src, dst)
            return {"status": "success", "message": f"Moved {src} → {dst}", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def delete(self, args: dict) -> dict:
        path = args.get("path", "")
        if not path:
            return {"status": "error", "error": "path required"}
        if not os.path.exists(path):
            return {"status": "error", "error": f"Not found: '{path}'"}
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return {"status": "success", "message": f"Deleted: {path}", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def mkdir(self, args: dict) -> dict:
        path = args.get("path", "")
        if not path:
            return {"status": "error", "error": "path required"}
        try:
            os.makedirs(path, exist_ok=True)
            return {"status": "success", "message": f"Created: {path}", "data": {}}
        except Exception as e:
            return {"status": "error", "error": str(e)}
