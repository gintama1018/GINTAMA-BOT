"""
src/executor.py — Local command executor for the 'system' target.

Routes system.* actions to the SystemModule which executes them
on the local machine TCC is running on.
"""

from src.parser import Intent


class LocalExecutor:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        self._system = None  # lazy load

    def _get_system(self):
        if self._system is None:
            from modules.system import SystemModule
            self._system = SystemModule(self.config, self.logger)
        return self._system

    def execute(self, intent: Intent) -> dict:
        """Execute a system-target intent locally."""
        system = self._get_system()
        action = intent.action

        handler = getattr(system, action, None)
        if handler is None:
            return {
                "status": "error",
                "error": (
                    f"Unknown action '{action}' for 'system'. "
                    "Type 'help' to see available actions."
                ),
                "device_ip": "local",
                "transport": "local",
            }

        result = handler(intent.args)
        result.setdefault("device_ip", "local")
        result.setdefault("transport", "local")
        return result
