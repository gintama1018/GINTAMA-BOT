"""
agent/agent.py — TCC Device Agent

Lightweight HTTP server that runs on each device.
TCC client connects to this agent to execute commands remotely.

Deployment:
  python agent.py               (foreground)
  python agent.py --daemon      (background, Linux/Mac: use systemd/launchd)

Security:
  - Auth token required in every request header (env: TCC_AUTH_TOKEN)
  - Actions checked against allowed/denied lists before execution
  - Rate limiting: 5 auth failures per 60s → 5-minute IP block
  - Defaults to 127.0.0.1; change to Tailscale IP for remote access
  - shell=False enforced in all OS subprocess calls
  - Production WSGI via waitress (falls back to Flask dev server)
"""

import os
import sys
import time
import platform
import tomllib
from collections import defaultdict
from functools import wraps
from datetime import datetime, timezone

# Add agent directory to sys.path so handlers can be found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Add project root so agent can share modules if co-located
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from flask import Flask, request, jsonify, abort
except ImportError:
    print("ERROR: Flask not installed. Run: pip install flask")
    sys.exit(1)

# S3/S8: Use production WSGI server (waitress) if available
try:
    from waitress import serve as _waitress_serve
    HAS_WAITRESS = True
except ImportError:
    HAS_WAITRESS = False


# -------------------------------------------------------------------- #
# Config loading                                                        #
# -------------------------------------------------------------------- #

def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.toml")
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        print(f"Warning: agent.toml not found at {config_path}. Using defaults.")
        return {}
    except Exception as e:
        print(f"Warning: Could not load agent.toml: {e}")
        return {}


_cfg = _load_config()
_agent = _cfg.get("agent", {})
_perms = _cfg.get("permissions", {})

# S9: Support TCC_AUTH_TOKEN environment variable (takes priority over config file)
AUTH_TOKEN      = os.environ.get("TCC_AUTH_TOKEN") or _agent.get("auth_token", "")
LISTEN_PORT     = int(_agent.get("listen_port", 7070))
LISTEN_HOST     = _agent.get("listen_host", "127.0.0.1")
DEVICE_NAME     = _agent.get("device_name", platform.node())
DEVICE_TYPE     = _agent.get("device_type", platform.system().lower())
ALLOWED_ACTIONS = set(_perms.get("allowed_actions", []))
DENIED_ACTIONS  = set(_perms.get("denied_actions", ["reboot", "shutdown", "run"]))

_PLACEHOLDER_TOKEN = "CHANGE_ME_strong_secret_token_here"

# -------------------------------------------------------------------- #
# S4: Rate limiting — simple in-memory IP-based auth failure tracker   #
# -------------------------------------------------------------------- #

_auth_failures: dict = defaultdict(list)
_blocked_ips:   dict = {}   # ip → unblock timestamp
_RATE_WINDOW    = 60         # seconds to track failures
_RATE_LIMIT     = 5          # max failures before block
_BLOCK_DURATION = 300        # seconds to block an IP


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    if ip in _blocked_ips:
        if now < _blocked_ips[ip]:
            return True
        del _blocked_ips[ip]
        _auth_failures[ip] = []
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < _RATE_WINDOW]
    return False


def _record_auth_failure(ip: str) -> None:
    now = time.time()
    _auth_failures[ip].append(now)
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < _RATE_WINDOW]
    if len(_auth_failures[ip]) >= _RATE_LIMIT:
        _blocked_ips[ip] = now + _BLOCK_DURATION
        _log("WARN", f"RATE LIMIT: {ip} blocked {_BLOCK_DURATION}s after {_RATE_LIMIT} auth failures")

# -------------------------------------------------------------------- #
# Handler lazy-loader                                                   #
# -------------------------------------------------------------------- #

_handler = None

def _get_handler():
    global _handler
    if _handler is not None:
        return _handler

    dt = DEVICE_TYPE.lower()
    if dt == "android":
        from handlers.android import AndroidHandler
        _handler = AndroidHandler()
    elif dt in ("linux", "darwin"):
        from handlers.linux import LinuxHandler
        _handler = LinuxHandler()
    else:
        from handlers.windows import WindowsHandler
        _handler = WindowsHandler()
    return _handler


# -------------------------------------------------------------------- #
# Flask app                                                             #
# -------------------------------------------------------------------- #

app = Flask(__name__)


def _log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {level:<5} {msg}", flush=True)


def require_auth(f):
    """Decorator: validates Bearer token with rate limiting. Rejects with 401/429."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        # S4: Check rate limit before auth
        if _is_rate_limited(ip):
            _log("WARN", f"RATE LIMITED request from {ip}")
            abort(429)
        if not AUTH_TOKEN:
            # S2: No token = reject all requests; logged at startup
            _log("WARN", f"Request from {ip} rejected: no auth token configured")
            abort(503)
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {AUTH_TOKEN}":
            _record_auth_failure(ip)
            # Log auth failure with source IP — never log the token itself
            _log("WARN", f"AUTH FAIL from {ip}")
            abort(401)
        return f(*args, **kwargs)
    return decorated


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint — no auth required."""
    return jsonify({
        "status": "ok",
        "device": DEVICE_NAME,
        "type": DEVICE_TYPE,
        "version": "2.0",
    })


@app.route("/command", methods=["POST"])
@require_auth
def handle_command():
    """Execute a command on this device and return structured JSON."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "error": "No JSON payload"}), 400

    action = data.get("action", "").strip().lower()
    args   = data.get("args", {})

    if not action:
        return jsonify({"status": "error", "error": "No action specified"}), 400

    # Permission enforcement — before any execution
    if action in DENIED_ACTIONS:
        _log("WARN", f"DENIED action='{action}' from {request.remote_addr}")
        return jsonify({
            "device": DEVICE_NAME,
            "action": action,
            "status": "denied",
            "error": f"Action '{action}' is denied on this device",
            "data": None,
        }), 403

    if ALLOWED_ACTIONS and action not in ALLOWED_ACTIONS:
        _log("WARN", f"NOT ALLOWED action='{action}' from {request.remote_addr}")
        return jsonify({
            "device": DEVICE_NAME,
            "action": action,
            "status": "denied",
            "error": f"Action '{action}' is not in the allowed list for this device",
            "data": None,
        }), 403

    start = time.perf_counter()

    try:
        handler = _get_handler()
        method = getattr(handler, action, None)
        if method is None:
            result = {
                "status": "error",
                "error": f"Action '{action}' is not implemented on {DEVICE_TYPE}",
            }
        else:
            result = method(args)
    except Exception as e:
        _log("ERROR", f"action='{action}' raised: {e}")
        result = {"status": "error", "error": str(e)}

    latency_ms = int((time.perf_counter() - start) * 1000)
    _log("INFO", f"action='{action}' status={result.get('status','?')} {latency_ms}ms")

    return jsonify({
        "device":     DEVICE_NAME,
        "action":     action,
        "status":     result.get("status", "error"),
        "data":       result.get("data"),
        "message":    result.get("message", ""),
        "latency_ms": latency_ms,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "error":      result.get("error"),
    })


# -------------------------------------------------------------------- #
# Entry point                                                           #
# -------------------------------------------------------------------- #

def start_agent():
    global LISTEN_HOST

    print(f"\n{'='*55}")
    print(f"  TCC Device Agent v2.0")
    print(f"  Device : {DEVICE_NAME} ({DEVICE_TYPE})")
    print(f"  Port   : {LISTEN_PORT}")
    print(f"  WSGI   : {'waitress' if HAS_WAITRESS else 'flask-dev (install waitress for production)'}")

    # S2: Loud warnings if auth is misconfigured
    if not AUTH_TOKEN:
        print(f"\n{'!'*55}")
        print(f"  !! SECURITY WARNING: No auth token configured.   !!")
        print(f"  !! Set auth_token in agent.toml                  !!")
        print(f"  !! or export TCC_AUTH_TOKEN=<secret>             !!")
        print(f"  !! All requests will be REJECTED until this       !!")
        print(f"  !! is fixed. Forcing listen_host=127.0.0.1.      !!")
        print(f"{'!'*55}\n")
        LISTEN_HOST = "127.0.0.1"
    elif AUTH_TOKEN == _PLACEHOLDER_TOKEN:
        print(f"\n{'!'*55}")
        print(f"  !! SECURITY WARNING: Placeholder token in use.   !!")
        print(f"  !! Change auth_token in agent.toml to a strong   !!")
        print(f"  !! random secret before exposing to a network.   !!")
        print(f"{'!'*55}\n")

    token_display = "env:TCC_AUTH_TOKEN" if os.environ.get("TCC_AUTH_TOKEN") else ("set" if AUTH_TOKEN else "MISSING")
    print(f"  Auth   : {token_display}")
    print(f"  Host   : {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  Allowed: {sorted(ALLOWED_ACTIONS) or 'all'}")
    print(f"  Denied : {sorted(DENIED_ACTIONS)}")
    print(f"{'='*55}\n")

    try:
        if HAS_WAITRESS:
            # S3/S8: Production WSGI server with thread pool
            _waitress_serve(app, host=LISTEN_HOST, port=LISTEN_PORT, threads=4)
        else:
            app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        # Q4: Graceful shutdown log
        _log("INFO", "Agent stopped by user (Ctrl+C)")
        print("\nAgent stopped.")


if __name__ == "__main__":
    start_agent()
