"""
tools/browser_tool.py — JARVIS Browser Control (Phase 6)

Playwright-based headless browser for:
  - take_screenshot(url) → saves PNG, returns path
  - fill_form(url, fields)
  - click_element(selector)
  - get_page_text(url)
  - run_js(code)

Security (SSRF protection):
  - Blocks requests to localhost, 127.x.x.x, 10.x, 192.168.x, 169.254.x
  - Blocks requests to internal Tailscale IPs (100.x)
  - Allowlist: only URLs starting with https://
  - All URLs validated before passing to browser

Requires: pip install playwright ; python -m playwright install chromium
"""

import logging
import re
import socket
import ipaddress
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Output directory for browser screenshots
BROWSER_SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots" / "browser"
BROWSER_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# SSRF-blocklisted address ranges
_PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),     # Tailscale / CGNAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_ssrf_safe(url: str) -> tuple[bool, str]:
    """
    Returns (True, "") if the URL is safe to fetch,
    or (False, reason) if it would expose internal resources.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' not allowed (use https)"
    if parsed.scheme == "http":
        return False, "HTTP not allowed, use HTTPS"

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "Missing hostname"

    # Block internal hostnames
    internal_hosts = {"localhost", "metadata.google.internal", "169.254.169.254"}
    if hostname.lower() in internal_hosts:
        return False, f"Blocked hostname: {hostname}"

    # Resolve and check IPs
    try:
        addrs = socket.getaddrinfo(hostname, None)
        for addr_info in addrs:
            ip_str = addr_info[4][0]
            ip = ipaddress.ip_address(ip_str)
            for net in _PRIVATE_NETS:
                if ip in net:
                    return False, f"Blocked private/internal IP: {ip_str}"
    except socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"

    return True, ""


class BrowserTool:
    """
    Singleton wrapper around Playwright browser.
    Call BrowserTool.get_instance() to reuse a single browser process.
    """
    _instance: Optional["BrowserTool"] = None

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None

    @classmethod
    def get_instance(cls) -> "BrowserTool":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_started(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed.\n"
                "Run: pip install playwright && python -m playwright install chromium"
            )
        self._playwright = sync_playwright().__enter__()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

    def close(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.__exit__(None, None, None)
            self._playwright = None

    # ---------------------------------------------------------------- #
    # Public tool methods                                               #
    # ---------------------------------------------------------------- #

    def take_screenshot(self, url: str) -> dict:
        """
        Navigate to URL and save a screenshot.
        Returns {"ok": True, "path": "/path/to/shot.png"} or {"ok": False, "error": "..."}
        """
        safe, reason = _is_ssrf_safe(url)
        if not safe:
            return {"ok": False, "error": f"Blocked: {reason}"}

        self._ensure_started()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            filename = _url_to_filename(url) + ".png"
            out_path = BROWSER_SCREENSHOTS_DIR / filename
            page.screenshot(path=str(out_path), full_page=False)
            return {"ok": True, "path": str(out_path), "url": url}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            page.close()

    def get_page_text(self, url: str, max_chars: int = 8000) -> dict:
        """
        Fetch page and return its text content.
        """
        safe, reason = _is_ssrf_safe(url)
        if not safe:
            return {"ok": False, "error": f"Blocked: {reason}"}

        self._ensure_started()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            text = page.inner_text("body")
            # Clean up whitespace
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... [truncated]"
            return {"ok": True, "text": text, "url": url}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            page.close()

    def fill_and_submit(self, url: str, fields: dict, submit_selector: str = "") -> dict:
        """
        Navigate to URL, fill form fields, optionally click submit.
        fields: {"#email": "user@example.com", "#password": "...", ...}
        """
        safe, reason = _is_ssrf_safe(url)
        if not safe:
            return {"ok": False, "error": f"Blocked: {reason}"}

        self._ensure_started()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for selector, value in fields.items():
                page.fill(selector, str(value))
            if submit_selector:
                page.click(submit_selector)
                page.wait_for_load_state("networkidle", timeout=15000)
            return {"ok": True, "url": page.url}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            page.close()

    def run_js(self, url: str, js_code: str) -> dict:
        """
        Navigate to URL and execute JavaScript, returning the result.
        js_code must not exceed 10000 characters.
        """
        safe, reason = _is_ssrf_safe(url)
        if not safe:
            return {"ok": False, "error": f"Blocked: {reason}"}
        if len(js_code) > 10000:
            return {"ok": False, "error": "JS code too long (max 10000 chars)"}

        self._ensure_started()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            result = page.evaluate(js_code)
            return {"ok": True, "result": str(result)[:4000]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            page.close()


def _url_to_filename(url: str) -> str:
    """Convert URL to safe filename."""
    import hashlib
    hostname = urlparse(url).hostname or "page"
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{hostname}_{h}"
