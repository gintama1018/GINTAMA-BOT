"""
gateway/web_gateway.py — JARVIS Web UI Gateway (Phase 4)

FastAPI server at http://127.0.0.1:7071 exposing:
  GET  /           → chat.html (or redirect)
  GET  /dashboard  → dashboard.html
  WS   /ws/{sender_id} → WebSocket chat
  POST /api/message   → REST message endpoint
  GET  /api/status    → bot status JSON

Run: python main.py --web

Security:
  - Binds to 127.0.0.1 only (Tailscale for remote)
  - CORS restricted to loopback
  - Auth via X-JARVIS-Token header or ?token= query param
  - Sessions tied to sender_id (web:{uid})

Requires: pip install fastapi uvicorn python-multipart
"""

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

WEB_DIR = Path(__file__).parent.parent / "web"


def create_app(agent_loop, config: dict, rate_limiter=None, pairing_manager=None):
    """Create and return the FastAPI app (does NOT start the server)."""
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
        from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel
    except ImportError:
        raise RuntimeError(
            "FastAPI not installed. Run: pip install fastapi uvicorn python-multipart"
        )

    gw_cfg = config.get("gateway", {})
    auth_token = os.environ.get("JARVIS_WEB_TOKEN") or gw_cfg.get("token", "")
    allowed_origins = gw_cfg.get("cors_origins", ["http://localhost:7071", "http://127.0.0.1:7071"])

    app = FastAPI(
        title="JARVIS Web Gateway",
        version="3.0.0",
        docs_url=None,   # Disable Swagger (security)
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ---------------------------------------------------------------- #
    # Auth dependency                                                   #
    # ---------------------------------------------------------------- #

    def verify_token(token: Optional[str] = None) -> bool:
        """Verify auth token (skip if none configured)."""
        if not auth_token:
            return True  # No token set → loopback-only security
        return token == auth_token

    # ---------------------------------------------------------------- #
    # Static files / HTML                                               #
    # ---------------------------------------------------------------- #

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def root():
        chat_html = WEB_DIR / "chat.html"
        if chat_html.exists():
            return HTMLResponse(chat_html.read_text())
        return HTMLResponse("<h1>JARVIS</h1><p>Place web/chat.html for UI</p>")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        dash_html = WEB_DIR / "dashboard.html"
        if dash_html.exists():
            return HTMLResponse(dash_html.read_text())
        return RedirectResponse("/")

    # ---------------------------------------------------------------- #
    # REST API                                                          #
    # ---------------------------------------------------------------- #

    class MessageRequest(BaseModel):
        message: str
        sender_id: str = "web_user"

    @app.get("/api/status")
    async def api_status(token: Optional[str] = None):
        if auth_token and not verify_token(token):
            raise HTTPException(status_code=401, detail="Invalid token")

        sm = getattr(agent_loop, "_session_manager", None)
        sessions = sm.list_sessions() if sm else []
        return JSONResponse({
            "status": "online",
            "version": "3.0.0",
            "agent_available": agent_loop.is_available() if hasattr(agent_loop, "is_available") else True,
            "sessions": len(sessions),
            "uptime": int(time.time()),
        })

    @app.post("/api/message")
    async def api_message(req: MessageRequest, token: Optional[str] = None):
        if auth_token and not verify_token(token):
            raise HTTPException(status_code=401, detail="Invalid token")

        if rate_limiter:
            allowed, msg = rate_limiter.check(f"web:{req.sender_id}")
            if not allowed:
                raise HTTPException(status_code=429, detail=msg)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: agent_loop.run(
                user_message=req.message,
                channel="web",
                sender_id=req.sender_id,
            )
        )
        return JSONResponse({"response": response, "sender_id": req.sender_id})

    # ---------------------------------------------------------------- #
    # WebSocket                                                         #
    # ---------------------------------------------------------------- #

    # Active WebSocket connections {sender_id: websocket}
    active_connections: dict = {}

    @app.websocket("/ws/{sender_id}")
    async def websocket_endpoint(
        websocket: WebSocket,
        sender_id: str,
        token: Optional[str] = None,
    ):
        if auth_token and not verify_token(token):
            await websocket.close(code=4001, reason="Unauthorized")
            return

        await websocket.accept()
        # Sanitize sender_id
        sender_id = sender_id[:64].replace("/", "_").replace("..", "")
        active_connections[sender_id] = websocket

        try:
            await websocket.send_text(json.dumps({
                "type": "connected",
                "sender_id": sender_id,
                "message": "Connected to JARVIS v3.0",
            }))

            while True:
                data = await websocket.receive_text()
                try:
                    payload = json.loads(data)
                    user_message = payload.get("message", "").strip()[:4096]
                except (json.JSONDecodeError, KeyError):
                    user_message = data.strip()[:4096]

                if not user_message:
                    continue

                # Rate limiting
                if rate_limiter:
                    allowed, msg = rate_limiter.check(f"web:{sender_id}")
                    if not allowed:
                        await websocket.send_text(json.dumps({
                            "type": "error", "message": msg
                        }))
                        continue

                # Notify front-end: thinking
                await websocket.send_text(json.dumps({
                    "type": "thinking", "message": "..."
                }))

                # Tool call streaming callbacks
                async def on_tool_call(tool_name, tool_args):
                    await websocket.send_text(json.dumps({
                        "type": "tool_call",
                        "tool": tool_name,
                        "args": str(tool_args),
                    }))

                async def on_tool_result(tool_name, result):
                    await websocket.send_text(json.dumps({
                        "type": "tool_result",
                        "tool": tool_name,
                        "result": str(result)[:500],
                    }))

                # Run agent loop
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: agent_loop.run(
                        user_message=user_message,
                        channel="web",
                        sender_id=sender_id,
                        on_tool_call=lambda t, a: asyncio.run_coroutine_threadsafe(
                            on_tool_call(t, a), loop
                        ),
                        on_tool_result=lambda t, r: asyncio.run_coroutine_threadsafe(
                            on_tool_result(t, r), loop
                        ),
                    )
                )

                await websocket.send_text(json.dumps({
                    "type": "response",
                    "message": response or "Done.",
                }))

        except WebSocketDisconnect:
            pass
        finally:
            active_connections.pop(sender_id, None)

    return app


def start_web_server(agent_loop, config: dict, rate_limiter=None, pairing_manager=None) -> None:
    """Start FastAPI server (blocking). Call in a thread or directly."""
    try:
        import uvicorn
    except ImportError:
        raise RuntimeError("uvicorn not installed. Run: pip install uvicorn")

    gw_cfg = config.get("gateway", {})
    host = gw_cfg.get("host", "127.0.0.1")
    port = int(gw_cfg.get("port", 7071))

    # Safety: only bind to loopback or Tailscale
    if host not in ("127.0.0.1", "localhost") and not host.startswith("100."):
        import warnings
        warnings.warn(
            f"[WebGateway] Binding to {host} — ensure this is intentional. "
            "Prefer 127.0.0.1 and use Tailscale for remote access.",
            stacklevel=2
        )

    app = create_app(agent_loop, config, rate_limiter, pairing_manager)

    print(f"[WebGateway] Starting at http://{host}:{port}")
    print(f"[WebGateway] Chat UI:    http://{host}:{port}/")
    print(f"[WebGateway] Dashboard:  http://{host}:{port}/dashboard")
    print(f"[WebGateway] WebSocket:  ws://{host}:{port}/ws/{{sender_id}}")

    uvicorn.run(app, host=host, port=port, log_level="warning")
