"""
src/router.py — Command router.

Maps target device name → IP address → sends HTTP request to agent.
Falls back to ADB for Android devices without an HTTP agent configured.
"""

import requests
from src.parser import Intent
from src.logger import StructuredLogger


class CommandRouter:
    def __init__(self, config: dict, logger: StructuredLogger):
        self.config = config
        self.logger = logger
        self.devices = config.get("devices", {})

    def route(self, intent: Intent) -> dict:
        target = intent.target

        # Broadcast to all devices
        if target == "all":
            return self._broadcast(intent)

        device = self.devices.get(target)
        if not device:
            return {
                "status": "error",
                "error": (
                    f"Unknown device '{target}'. "
                    f"Register it in config.toml. "
                    f"Known devices: {list(self.devices.keys()) or 'none'}"
                ),
                "device_ip": "",
                "transport": "",
            }

        ip = device.get("ip", "").strip()
        port = int(device.get("port", 7070))
        auth_token = device.get("auth_token", "")
        transport = device.get("transport", "lan")
        device_type = device.get("type", "unknown")

        # No IP configured → fall back to ADB for Android
        if not ip:
            if device_type == "android":
                return self._route_adb(intent)
            return {
                "status": "error",
                "error": f"No IP configured for '{target}'. Set it in config.toml.",
                "device_ip": "",
                "transport": "",
            }

        return self._send_http(intent, ip, port, auth_token, target, transport)

    # ---------------------------------------------------------------- #
    # HTTP transport                                                    #
    # ---------------------------------------------------------------- #

    def _send_http(
        self,
        intent: Intent,
        ip: str,
        port: int,
        auth_token: str,
        device_name: str,
        transport: str,
    ) -> dict:
        url = f"http://{ip}:{port}/command"
        payload = {
            "target": intent.target,
            "action": intent.action,
            "args": intent.args,
            "flags": intent.flags,
        }
        # Auth token in header only — never logged
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            data["device_ip"] = ip
            data["transport"] = transport
            if data.get("status") == "success" and not data.get("message"):
                data["message"] = _format_success_msg(intent, data)
            return data

        except requests.exceptions.ConnectionError:
            return {
                "status": "error",
                "error": f"Cannot reach '{device_name}' at {ip}:{port}. Is the agent running?",
                "device_ip": ip,
                "transport": transport,
            }
        except requests.exceptions.Timeout:
            return {
                "status": "error",
                "error": f"Timeout reaching '{device_name}' ({ip}:{port})",
                "device_ip": ip,
                "transport": transport,
            }
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "?"
            msg = "Unauthorized — check auth_token in config.toml" if status_code == 401 else str(e)
            return {
                "status": "error",
                "error": f"Agent error [{status_code}]: {msg}",
                "device_ip": ip,
                "transport": transport,
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "device_ip": ip,
                "transport": transport,
            }

    # ---------------------------------------------------------------- #
    # ADB transport (Android without HTTP agent)                        #
    # ---------------------------------------------------------------- #

    def _route_adb(self, intent: Intent) -> dict:
        from modules.phone import PhoneModule
        phone = PhoneModule(self.config, self.logger)
        result = phone.execute(intent)
        result.setdefault("device_ip", "adb")
        result.setdefault("transport", "adb")
        return result

    # ---------------------------------------------------------------- #
    # Broadcast                                                         #
    # ---------------------------------------------------------------- #

    def _broadcast(self, intent: Intent) -> dict:
        if not self.devices:
            return {
                "status": "error",
                "error": "No devices registered. Add devices to config.toml.",
                "device_ip": "broadcast",
                "transport": "broadcast",
            }

        results = []
        for name in self.devices:
            sub = Intent(
                raw=intent.raw,
                target=name,
                action=intent.action,
                args=dict(intent.args),
                flags=dict(intent.flags),
            )
            r = self.route(sub)
            results.append({"device": name, "result": r})

        all_ok = all(r["result"].get("status") == "success" for r in results)
        return {
            "status": "success" if all_ok else "partial",
            "message": f"Broadcast to {len(results)} device(s) — {'all OK' if all_ok else 'some failed'}",
            "data": results,
            "device_ip": "broadcast",
            "transport": "mixed",
        }


def _format_success_msg(intent: Intent, data: dict) -> str:
    """Build a descriptive success message for display."""
    action = intent.action
    target = intent.target
    d = data.get("data") or {}

    if action == "screenshot":
        return f"Screenshot saved → {d.get('file', '?')}  ({d.get('size_kb', '?')}KB)"
    elif action == "battery":
        chg = "Yes" if d.get("charging") else "No"
        return f"{target.title()} │ Battery: {d.get('level', '?')}% │ Charging: {chg}"
    elif action in ("launch", "open"):
        return f"{intent.args.get('app', '?').title()} launched on {target}"
    elif action == "volume":
        return f"Volume set to {intent.args.get('level', '?')}/15"
    elif action == "brightness":
        return f"Brightness set to {intent.args.get('level', '?')}"
    elif action == "notify":
        return f"Notification sent to {target}"
    elif action == "push":
        return f"Push complete → {intent.args.get('dst', '?')}"
    elif action == "pull":
        return f"Pull complete → {intent.args.get('dst', '.')}"
    elif action == "lock":
        return f"{target.title()} locked"
    elif action in ("reboot", "shutdown"):
        return f"{target.title()} {action}ing..."
    elif action in ("run", "ls"):
        return data.get("message", "Done")
    return data.get("message", "Command executed successfully")
