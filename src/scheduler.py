"""
src/scheduler.py — Cron-style background skill scheduler.

Reads [schedule] from config.toml and triggers skills at the right time.

Config format:
    [schedule]
    morning = "07:00 daily"
    backup  = "23:00 daily"
    focus   = "09:00 weekdays"

Frequency options: daily | weekdays | weekends
"""

import threading
import time
from datetime import datetime
from typing import Callable, Optional


class Scheduler:
    def __init__(self, config: dict, logger, skill_executor: Callable[[str], None]):
        """
        skill_executor: callable that takes a skill name string and runs it.
        """
        self.config = config
        self.logger = logger
        self.run_skill = skill_executor
        self.tasks: dict = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def load_schedule(self) -> None:
        """Parse [schedule] config and register tasks."""
        schedule_cfg = self.config.get("schedule", {})
        for skill_name, schedule_str in schedule_cfg.items():
            parsed = self._parse_schedule(schedule_str)
            if parsed:
                self.tasks[skill_name] = {**parsed, "last_run": None}
                self.logger.info(
                    f"Scheduler: '{skill_name}' scheduled at {schedule_str}"
                )

    def start(self) -> None:
        """Start the background scheduler thread."""
        self.load_schedule()
        if not self.tasks:
            return  # nothing to schedule
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.logger.info(f"Scheduler started ({len(self.tasks)} task(s))")

    def stop(self) -> None:
        self._running = False

    # ---------------------------------------------------------------- #
    # Internal                                                          #
    # ---------------------------------------------------------------- #

    def _parse_schedule(self, schedule_str: str) -> Optional[dict]:
        """Parse '07:00 daily' → {hour, minute, frequency}"""
        parts = schedule_str.strip().split()
        if len(parts) < 2:
            return None
        try:
            hour, minute = map(int, parts[0].split(":"))
        except ValueError:
            return None
        return {"hour": hour, "minute": minute, "frequency": parts[1].lower()}

    def _should_run(self, task: dict, now: datetime) -> bool:
        if now.hour != task["hour"] or now.minute != task["minute"]:
            return False
        last = task.get("last_run")
        if last and (now - last).total_seconds() < 59:
            return False  # already ran this minute
        freq = task.get("frequency", "daily")
        if freq == "daily":
            return True
        elif freq == "weekdays":
            return now.weekday() < 5
        elif freq == "weekends":
            return now.weekday() >= 5
        return False

    def _loop(self) -> None:
        while self._running:
            now = datetime.now()
            for skill_name, task in list(self.tasks.items()):
                if self._should_run(task, now):
                    task["last_run"] = now
                    self.logger.info(f"Scheduler: triggering '{skill_name}'")
                    try:
                        self.run_skill(skill_name)
                    except Exception as e:
                        self.logger.error(
                            f"Scheduler: skill '{skill_name}' failed: {e}"
                        )
            time.sleep(30)  # check every 30 seconds
