"""
modules/notify.py — Cross-device notification module.

Sends desktop notifications using plyer (cross-platform).
Falls back gracefully if plyer is not installed.
"""


class NotifyModule:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

    def send(self, args: dict) -> dict:
        message = args.get("message", args.get("msg", "")).strip()
        if not message:
            return {"status": "error", "error": "No message. Usage: notify <message>"}

        try:
            import plyer
            plyer.notification.notify(
                title="TCC — JARVIS",
                message=message,
                timeout=5,
            )
        except Exception:
            pass  # Non-critical — message shown in terminal regardless

        return {
            "status": "success",
            "message": f"Notification: {message}",
            "data": {"message": message},
        }

    def notify(self, args: dict) -> dict:
        return self.send(args)
