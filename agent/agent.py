"""
agent/agent.py — TCC Device Agent

Lightweight HTTP server that runs on each device.
TCC client connects to this agent to execute commands remotely.

Deployment:
  python agent.py               (foreground)
  python agent.py --daemon      (background, Linux/Mac: use systemd/launchd)

Security:
  - Auth token required in every request header
  - Actions checked against allowed/denied lists before execution
  - Binds to 0.0.0.0 by default; restrict to Tailscale IP in production
  - shell=False enforced in all OS subprocess calls
"""

import os
import sys
import time
import platform
import tomllib
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

AUTH_TOKEN     = _agent.get("auth_token", "")
LISTEN_PORT    = int(_agent.get("listen_port", 7070))
LISTEN_HOST    = _agent.get("listen_host", "0.0.0.0")
DEVICE_NAME    = _agent.get("device_name", platform.node())
DEVICE_TYPE    = _agent.get("device_type", platform.system().lower())
ALLOWED_ACTIONS = set(_perms.get("allowed_actions", []))
DENIED_ACTIONS  = set(_perms.get("denied_actions", ["reboot", "shutdown", "run"]))

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
    """Decorator: validates Bearer token. Rejects with 401 if wrong."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_TOKEN:
            return f(*args, **kwargs)  # No token configured — dev mode
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {AUTH_TOKEN}":
            # Log auth failure with source IP — never log the token itself
            _log("WARN", f"AUTH FAIL from {request.remote_addr}")
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
    print(f"\n{'='*50}")
    print(f"  TCC Device Agent v2.0")
    print(f"  Device : {DEVICE_NAME} ({DEVICE_TYPE})")
    print(f"  Port   : {LISTEN_PORT}")
    print(f"  Auth   : {'configured' if AUTH_TOKEN else 'NONE (dev mode)'}")
    print(f"  Allowed: {sorted(ALLOWED_ACTIONS) or 'all'}")
    print(f"  Denied : {sorted(DENIED_ACTIONS)}")
    print(f"{'='*50}\n")

    app.run(
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    start_agent()
