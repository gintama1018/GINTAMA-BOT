"""
modules/network.py — Network utilities module.

Provides: ping, Tailscale status, local IP info, port scanning.
"""

import json
import platform
import socket
import subprocess


class NetworkModule:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

    def ping(self, args: dict) -> dict:
        host = args.get("host", "8.8.8.8").strip()
        count = str(args.get("count", "4"))

        # Build platform-appropriate ping command
        os_type = platform.system().lower()
        if os_type == "windows":
            cmd = ["ping", "-n", count, host]
        else:
            cmd = ["ping", "-c", count, host]

        try:
            result = subprocess.run(
                cmd, shell=False, capture_output=True, text=True, timeout=20
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "message": result.stdout.strip(),
                "data": {"host": host, "reachable": result.returncode == 0},
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"Ping timed out for {host}"}
        except FileNotFoundError:
            return {"status": "error", "error": "ping command not found"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def tailscale_status(self, args: dict) -> dict:
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                shell=False, capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return {"status": "error", "error": "Tailscale returned non-zero exit code"}
            data = json.loads(result.stdout)
            return {
                "status": "success",
                "message": "Tailscale is running",
                "data": data,
            }
        except FileNotFoundError:
            return {"status": "error", "error": "Tailscale not installed"}
        except json.JSONDecodeError:
            return {"status": "error", "error": "Could not parse Tailscale output"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def myip(self, args: dict) -> dict:
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            return {
                "status": "success",
                "message": f"{hostname} │ {local_ip}",
                "data": {"hostname": hostname, "local_ip": local_ip},
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def port_check(self, args: dict) -> dict:
        host = args.get("host", "127.0.0.1").strip()
        port = int(args.get("port", 7070))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            rc = sock.connect_ex((host, port))
            sock.close()
            open_flag = rc == 0
            return {
                "status": "success",
                "message": f"{host}:{port} is {'open' if open_flag else 'closed'}",
                "data": {"host": host, "port": port, "open": open_flag},
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
