"""APScheduler integration for periodic scraping and discovery jobs."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from scraper.config import DATA_DIR, SOURCE_DB_NAMES
from scraper.logger import LogLevel, log
from web.log_buffer import LogBuffer
from web.scraper_manager import ScraperManager


SCHEDULES_FILE = DATA_DIR / "schedules.json"
ALL_SOURCES = ["AllBoatSites", "BoatTrader", "YachtWorld", "BoatsDotCom"] + list(SOURCE_DB_NAMES.keys())


def load_schedules() -> list[dict[str, Any]]:
    """Load persisted schedule definitions."""
    if not SCHEDULES_FILE.exists():
        return []
    try:
        data = json.loads(SCHEDULES_FILE.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_schedules(schedules: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2))


def _parse_interval(minutes: int) -> IntervalTrigger:
    """Build an interval trigger from minutes."""
    return IntervalTrigger(minutes=max(1, int(minutes)))


class ScheduleManager:
    """Wraps APScheduler and wires it into the web manager."""

    def __init__(self, manager: ScraperManager, log_buffer: LogBuffer):
        self.manager = manager
        self.log_buffer = log_buffer
        self.scheduler = BackgroundScheduler(timezone=os.environ.get("TZ", "UTC"))
        self._lock = threading.Lock()
        # Keep the scheduler from auto-starting until init() is called.

    def _log(self, msg: str) -> None:
        line = f"[scheduler] {msg}"
        self.log_buffer.write(line)
        log(LogLevel.STANDARD, line)

    def _run_job(self, source: str, action: str) -> None:
        """Background-safe wrapper that avoids overlapping runs per source."""
        try:
            if action in ("scrape", "discover_and_scrape"):
                if self.manager.scraper_running(source):
                    self._log(f"Skipping {action} for {source}: scraper already running")
                    return
            if action == "discover":
                if self.manager.discover_running(source):
                    self._log(f"Skipping discover for {source}: discovery already running")
                    return

            self._log(f"Scheduled {action} starting for {source}")
            if action == "discover":
                self.manager.discover(source=source)
            elif action == "scrape":
                self.manager.start(source=source)
            elif action == "discover_and_scrape":
                # For car sources start() already discovers before scraping.
                if source in SOURCE_DB_NAMES:
                    self.manager.start(source=source)
                else:
                    ok = self.manager.discover(source=source)
                    if ok:
                        # Wait until discovery finishes, then start scraping.
                        for _ in range(360):  # up to 30 minutes
                            if not self.manager.discover_running(source):
                                break
                            time.sleep(5)
                        self.manager.start(source=source)
            else:
                self._log(f"Unknown scheduled action: {action}")
        except Exception:
            self._log("Scheduled job failed:")
            self._log(traceback.format_exc())

    def _schedule(self, job_id: str, source: str, action: str, minutes: int) -> None:
        """Add or replace a single scheduled job."""
        trigger = _parse_interval(minutes)
        self.scheduler.add_job(
            func=self._run_job,
            trigger=trigger,
            id=job_id,
            args=(source, action),
            replace_existing=True,
        )

    def init(self) -> None:
        """Load persisted schedules and start the scheduler."""
        schedules = load_schedules()
        for sched in schedules:
            if not sched.get("enabled", True):
                continue
            source = sched.get("source")
            action = sched.get("action", "discover_and_scrape")
            minutes = sched.get("minutes", 60)
            if source not in ALL_SOURCES:
                self._log(f"Ignoring schedule for unknown source: {source}")
                continue
            job_id = f"{source}_{action}"
            self._schedule(job_id, source, action, minutes)
            self._log(f"Loaded schedule: {source}/{action} every {minutes} min")
        self.scheduler.start()
        self._log("Scheduler started")

    def add_or_update(
        self,
        source: str,
        action: str = "discover_and_scrape",
        minutes: int = 60,
        enabled: bool = True,
    ) -> bool:
        """Persist a schedule and (re)schedule it."""
        if source not in ALL_SOURCES:
            return False
        if action not in ("discover", "scrape", "discover_and_scrape"):
            return False

        schedules = load_schedules()
        updated = False
        for sched in schedules:
            if sched.get("source") == source and sched.get("action") == action:
                sched["minutes"] = minutes
                sched["enabled"] = enabled
                updated = True
                break
        if not updated:
            schedules.append({
                "source": source,
                "action": action,
                "minutes": minutes,
                "enabled": enabled,
                "created_at": datetime.utcnow().isoformat(),
            })
        save_schedules(schedules)

        job_id = f"{source}_{action}"
        if enabled:
            self._schedule(job_id, source, action, minutes)
            self._log(f"Scheduled {source}/{action} every {minutes} min")
        else:
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass
            self._log(f"Removed schedule {source}/{action}")
        return True

    def remove(self, source: str, action: str) -> bool:
        """Remove a persisted schedule and its APScheduler job."""
        schedules = [s for s in load_schedules() if not (s.get("source") == source and s.get("action") == action)]
        save_schedules(schedules)
        job_id = f"{source}_{action}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass
        return True

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return current persisted schedules plus next run time for active jobs."""
        schedules = load_schedules()
        result = []
        for sched in schedules:
            job_id = f"{sched.get('source')}_{sched.get('action')}"
            next_run = None
            try:
                job = self.scheduler.get_job(job_id)
                if job and job.next_run_time:
                    next_run = job.next_run_time.isoformat()
            except Exception:
                pass
            item = dict(sched)
            item["next_run"] = next_run
            result.append(item)
        return result
