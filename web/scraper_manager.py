"""Manager for running scrapers, discovery, and prescraper in background threads.

Each source (or source group) gets its own thread and stop event, so multiple
scrapers can run concurrently.
"""
from __future__ import annotations

import io
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Any

from prescraper.uscg_prescraper import run_prescrape as uscg_prescrape
from scraper.config import SOURCE_DB_NAMES
from scraper.database import get_db
from scraper.run import discover_only, scrape
from scraper.car_runner import scrape as car_scrape
from scraper.cars import carmax, carvana
from scraper import logger
from scraper.browser import BoatBrowser

from web.log_buffer import LogBuffer


CAR_SOURCE_MODULES = {
    "CarMax": carmax,
    "Carvana": carvana,
}

# Pseudo-source used when no source is selected (legacy all-boat scrape).
_ALL_BOATS_KEY = "AllBoatSites"


def _source_key(source: str | None) -> str:
    """Convert a source value into a stable thread/state key."""
    if not source:
        return _ALL_BOATS_KEY
    return source


class ScraperManager:
    """Runs vehicle scrapers and the USCG prescraper in background threads."""

    def __init__(self, log_buffer: LogBuffer):
        self.log_buffer = log_buffer
        self._scraper_threads: dict[str, threading.Thread] = {}
        self._discover_threads: dict[str, threading.Thread] = {}
        self._prescraper_thread: threading.Thread | None = None
        self._stop_events: dict[str, threading.Event] = {}
        self._stop_requested: dict[str, bool] = {}
        self._start_times: dict[str, datetime] = {}
        self._prescrape_current = 0
        self._prescrape_total = 0
        self._prescrape_records = 0

    # --- aggregate status properties ---

    @property
    def is_running(self) -> bool:
        return (
            any(t.is_alive() for t in self._scraper_threads.values())
            or any(t.is_alive() for t in self._discover_threads.values())
            or (self._prescraper_thread is not None and self._prescraper_thread.is_alive())
        )

    def running_sources(self) -> list[str]:
        """Return source keys with an active scraper or discovery thread."""
        keys = set()
        for key, t in self._scraper_threads.items():
            if t.is_alive():
                keys.add(key)
        for key, t in self._discover_threads.items():
            if t.is_alive():
                keys.add(key)
        return sorted(keys)

    def scraper_running(self, source: str | None = None) -> bool:
        key = _source_key(source)
        t = self._scraper_threads.get(key)
        return t is not None and t.is_alive()

    def discover_running(self, source: str | None = None) -> bool:
        key = _source_key(source)
        t = self._discover_threads.get(key)
        return t is not None and t.is_alive()

    @property
    def prescraper_running(self) -> bool:
        return self._prescraper_thread is not None and self._prescraper_thread.is_alive()

    def uptime_seconds(self, source: str | None = None) -> float | None:
        key = _source_key(source)
        start = self._start_times.get(key)
        if start is None:
            return None
        return (datetime.now(timezone.utc) - start).total_seconds()

    def uptime_seconds_overall(self) -> float | None:
        if not self._start_times:
            return None
        oldest = min(self._start_times.values())
        return (datetime.now(timezone.utc) - oldest).total_seconds()

    # --- progress tracking ---

    def _set_prescrape_progress(self, current: int, total: int, records: int) -> None:
        self._prescrape_current = current
        self._prescrape_total = total
        self._prescrape_records = records

    def _log(self, msg: str) -> None:
        self.log_buffer.write(f"[manager] {msg}")

    def _cleanup_source(self, key: str) -> None:
        """Remove stale references once a thread has finished."""
        self._stop_requested.pop(key, None)
        self._start_times.pop(key, None)
        self._stop_events.pop(key, None)
        self._scraper_threads.pop(key, None)
        self._discover_threads.pop(key, None)

    # --- scraper control ---

    def start(self, source: str | None = None, limit: int | None = None, retry_failed: bool = False) -> bool:
        """Start a scraper for the given source in its own thread."""
        self._log(f"start() called: source={source} limit={limit} retry_failed={retry_failed}")
        key = _source_key(source)
        if self.scraper_running(source):
            self._log(f"start() rejected: scraper already running for {key}")
            return False

        self._stop_events[key] = threading.Event()
        self._stop_requested[key] = False
        self._start_times[key] = datetime.now(timezone.utc)
        self._scraper_threads[key] = threading.Thread(
            target=self._run_scraper,
            args=(key, source, limit, retry_failed),
            daemon=True,
        )
        self._scraper_threads[key].start()
        self._log(f"start() accepted: scraper thread started for {key}")
        return True

    def stop(self, source: str | None = None, all_sources: bool = False) -> bool:
        """Signal one or all scrapers/discovery jobs to stop gracefully."""
        self._log(f"stop() called: source={source} all_sources={all_sources}")
        if all_sources:
            any_running = False
            keys = set(self._scraper_threads.keys()) | set(self._discover_threads.keys())
            for key in keys:
                if self.scraper_running(key) or self.discover_running(key):
                    self._stop_requested[key] = True
                    self.log_buffer.write(f"[manager] Stop signal sent to {key}...")
                    evt = self._stop_events.get(key)
                    if evt is not None:
                        evt.set()
                    any_running = True
            return any_running

        key = _source_key(source)
        if not self.scraper_running(key) and not self.discover_running(key):
            self._log(f"stop() rejected: nothing running for {key}")
            return False
        self._stop_requested[key] = True
        self.log_buffer.write(f"[manager] Stop signal sent to {key}...")
        evt = self._stop_events.get(key)
        if evt is not None:
            evt.set()
        return True

    def discover(self, source: str | None = None, refresh: bool = False) -> bool:
        """Start sitemap/category discovery for one source."""
        self._log(f"discover() called: source={source} refresh={refresh}")
        key = _source_key(source)
        if self.discover_running(key):
            self._log(f"discover() rejected: discovery already running for {key}")
            return False

        self._stop_events[key] = threading.Event()
        self._stop_requested[key] = False
        self._start_times[key] = datetime.now(timezone.utc)
        self._discover_threads[key] = threading.Thread(
            target=self._run_discover,
            args=(key, source, refresh),
            daemon=True,
        )
        self._discover_threads[key].start()
        self._log(f"discover() accepted: discover thread started for {key}")
        return True

    def prescrape(self) -> bool:
        """Start the USCG manufacturer prescraper in a background thread."""
        self._log("prescrape() called")
        if self.prescraper_running:
            self._log("prescrape() rejected: prescraper already running")
            return False

        self._start_times["prescraper"] = datetime.now(timezone.utc)
        self._prescraper_thread = threading.Thread(
            target=self._run_prescraper,
            daemon=True,
        )
        self._prescraper_thread.start()
        self._log("prescrape() accepted: prescraper thread started")
        return True

    def _redirect_stdout(self, target):
        """Redirect stdout through target callable while running."""
        self._log("Redirecting stdout to log buffer...")
        old_stdout = sys.stdout
        sys.stdout = _LogStream(self.log_buffer)
        try:
            target()
        except Exception:
            self.log_buffer.write("[manager] UNCAUGHT EXCEPTION in background thread:\n")
            self.log_buffer.write(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            self._log("Stdout restored")

    # --- thread runnables ---

    def _run_scraper(self, key: str, source: str | None, limit: int | None, retry_failed: bool) -> None:
        self._log(f"--- scraper thread started ({key}) ---")

        def run():
            self._log(f"Entering scrape() for {key}...")
            if source in SOURCE_DB_NAMES:
                car_scrape(
                    source=source,
                    limit=limit,
                    retry_failed=retry_failed,
                    stop_event=self._stop_events[key],
                    discover=True,
                )
            else:
                scrape(
                    limit=limit,
                    retry_failed=retry_failed,
                    discover=False,
                    stop_event=self._stop_events[key],
                    source=source,
                )
            self._log(f"scrape() returned normally for {key}")

        self._redirect_stdout(run)
        self._log(f"--- scraper thread finished ({key}) ---")

    def _run_discover(self, key: str, source: str | None, refresh: bool) -> None:
        self._log(f"--- discover thread started ({key}) ---")
        stop_event = self._stop_events.get(key)

        def run():
            self._log(f"Entering discovery for {key}...")
            try:
                with BoatBrowser() as browser:
                    if source in SOURCE_DB_NAMES:
                        module = CAR_SOURCE_MODULES[source]
                        db = get_db(source)
                        module.discover_urls(browser.page, db=db, refresh=refresh, stop_event=stop_event)
                    else:
                        discover_only(browser.page, source=source, refresh=refresh, stop_event=stop_event)
            except Exception:
                traceback.print_exc()
            self._log(f"discovery returned for {key}")

        self._redirect_stdout(run)
        self._log(f"--- discover thread finished ({key}) ---")

    def _run_prescraper(self) -> None:
        self._log("--- prescraper thread started ---")
        self._set_prescrape_progress(0, 1, 0)

        def prog(current, total, records):
            self._set_prescrape_progress(current, total, records)

        def run():
            self._log("Entering uscg_prescrape()...")
            uscg_prescrape(on_progress=prog)
            self._log("uscg_prescrape() returned normally")

        self._redirect_stdout(run)
        self._set_prescrape_progress(self._prescrape_total, self._prescrape_total, self._prescrape_records)
        self._log("--- prescraper thread finished ---")
        self._start_times.pop("prescraper", None)

    # --- log level helpers ---

    def set_log_level(self, level: str) -> bool:
        """Set the global log level (DEBUG, STANDARD, QUIET)."""
        try:
            logger.set_log_level(level)
            self._log(f"Log level set to {level}")
            return True
        except Exception as exc:
            self._log(f"Failed to set log level: {exc}")
            return False

    def get_log_level(self) -> str:
        """Return the current log level name."""
        return logger.get_log_level().name

    # --- status ---

    def _progress_for_db(self, source_key: str | None) -> dict[str, int]:
        """Get progress stats for the database associated with a source."""
        if not source_key or source_key == _ALL_BOATS_KEY:
            db = get_db()
        elif source_key in SOURCE_DB_NAMES:
            db = get_db(source_key)
        else:
            db = get_db()
        try:
            cursor = db.execute("SELECT status, COUNT(*) FROM progress GROUP BY status")
            stats = dict(cursor.fetchall())
            return {
                "pending": stats.get("pending", 0),
                "done": stats.get("done", 0),
                "failed": stats.get("failed", 0),
                "total": sum(stats.values()),
            }
        finally:
            db.close()

    def _count_table(self, db, table: str) -> int:
        try:
            cursor = db.execute(f"SELECT COUNT(*) FROM {table}")
            return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_status(self) -> dict:
        status: dict[str, Any] = {
            "running": self.is_running,
            "running_sources": self.running_sources(),
            "stop_requested": dict(self._stop_requested),
            "uptime_seconds": self.uptime_seconds_overall(),
            "log_level": self.get_log_level(),
        }

        # Aggregate stats across relevant DBs
        boat_progress = self._progress_for_db(_ALL_BOATS_KEY)
        status.update({
            "pending": boat_progress["pending"],
            "done": boat_progress["done"],
            "failed": boat_progress["failed"],
            "total_boats": 0,
            "last_scraped": None,
            "total_manufacturers": 0,
        })

        # Per-source status cards
        per_source: dict[str, dict[str, Any]] = {}

        # Boat sources all share the same DB; report aggregate boat progress.
        per_source["AllBoatSites"] = {
            "running": self.scraper_running(_ALL_BOATS_KEY),
            "discover_running": self.discover_running(_ALL_BOATS_KEY),
            "uptime_seconds": self.uptime_seconds(_ALL_BOATS_KEY),
            **boat_progress,
        }

        for source in ["BoatTrader", "YachtWorld", "BoatsDotCom"]:
            progress = self._progress_for_db(source)
            per_source[source] = {
                "running": self.scraper_running(source),
                "discover_running": self.discover_running(source),
                "uptime_seconds": self.uptime_seconds(source),
                **progress,
            }

        for source in SOURCE_DB_NAMES:
            db = get_db(source)
            try:
                progress = self._progress_for_db(source)
                record_count = self._count_table(db, "cars")
                last_scraped = None
                try:
                    cur = db.execute("SELECT MAX(scraped_at) FROM cars")
                    last_scraped = cur.fetchone()[0]
                except Exception:
                    pass
            finally:
                db.close()
            per_source[source] = {
                "running": self.scraper_running(source),
                "discover_running": self.discover_running(source),
                "uptime_seconds": self.uptime_seconds(source),
                **progress,
                "records": record_count,
                "last_scraped": last_scraped,
            }

        status["sources"] = per_source

        try:
            db = get_db()
            status["total_boats"] = self._count_table(db, "boats")
            status["total_manufacturers"] = self._count_table(db, "manufacturers")
            cur = db.execute("SELECT MAX(scraped_at) FROM boats")
            status["last_scraped"] = cur.fetchone()[0]
            db.close()
        except Exception:
            pass

        status.update({
            "prescrape_current": self._prescrape_current,
            "prescrape_total": self._prescrape_total,
            "prescrape_records": self._prescrape_records,
        })

        return status


class _LogStream(io.TextIOBase):
    """A write-only text stream that forwards lines to a LogBuffer."""

    def __init__(self, buffer: LogBuffer):
        self.buffer = buffer
        self._line = ""

    def write(self, text: str) -> int:
        self._line += text
        while "\n" in self._line:
            line, self._line = self._line.split("\n", 1)
            if line.strip():
                self.buffer.write(line)
        return len(text)

    def flush(self) -> None:
        if self._line.strip():
            self.buffer.write(self._line)
            self._line = ""
