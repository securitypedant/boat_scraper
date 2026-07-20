"""Manager for running scraper and prescraper in background threads."""
import io
import sys
import threading
import traceback
from datetime import datetime, timezone

from prescraper.uscg_prescraper import run_prescrape as uscg_prescrape
from scraper.database import get_db
from scraper.run import scrape

from web.log_buffer import LogBuffer


class ScraperManager:
    """Runs the boat scraper and USCG prescraper in background threads."""

    def __init__(self, log_buffer: LogBuffer):
        self.log_buffer = log_buffer
        self._scraper_thread: threading.Thread | None = None
        self._prescraper_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stop_requested = False
        self._start_time: datetime | None = None
        self._prescrape_current = 0
        self._prescrape_total = 0
        self._prescrape_records = 0

    @property
    def is_running(self) -> bool:
        return (
            self._scraper_thread is not None and self._scraper_thread.is_alive()
        ) or (
            self._prescraper_thread is not None and self._prescraper_thread.is_alive()
        )

    @property
    def scraper_running(self) -> bool:
        return self._scraper_thread is not None and self._scraper_thread.is_alive()

    @property
    def prescraper_running(self) -> bool:
        return self._prescraper_thread is not None and self._prescraper_thread.is_alive()

    @property
    def uptime_seconds(self) -> float | None:
        if self._start_time is None:
            return None
        return (datetime.now(timezone.utc) - self._start_time).total_seconds()

    def _set_prescrape_progress(self, current: int, total: int, records: int) -> None:
        self._prescrape_current = current
        self._prescrape_total = total
        self._prescrape_records = records

    def _log(self, msg: str) -> None:
        self.log_buffer.write(f"[manager] {msg}")

    def start(self, limit: int | None = None, retry_failed: bool = False, source: str | None = None) -> bool:
        """Start the boat scraper in a background thread."""
        self._log(f"start() called: limit={limit} retry_failed={retry_failed} source={source}")
        if self.scraper_running:
            self._log("start() rejected: scraper already running")
            return False

        self._stop_event.clear()
        self._stop_requested = False
        self._start_time = datetime.now(timezone.utc)
        self._scraper_thread = threading.Thread(
            target=self._run_scraper,
            args=(limit, retry_failed, source),
            daemon=True,
        )
        self._scraper_thread.start()
        self._log("start() accepted: scraper thread started")
        return True

    def stop(self) -> bool:
        """Signal the boat scraper to stop gracefully."""
        self._log("stop() called")
        if not self.scraper_running:
            self._log("stop() rejected: scraper not running")
            return False
        self._stop_requested = True
        self.log_buffer.write("[manager] Stop signal sent...")
        self._stop_event.set()
        return True

    def prescrape(self) -> bool:
        """Start the USCG manufacturer prescraper in a background thread."""
        self._log("prescrape() called")
        if self.prescraper_running:
            self._log("prescrape() rejected: prescraper already running")
            return False

        self._start_time = datetime.now(timezone.utc)
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

    def _run_scraper(self, limit: int | None, retry_failed: bool, source: str | None) -> None:
        self._log("--- scraper thread started ---")

        def run():
            self._log("Entering scrape()...")
            scrape(
                limit=limit,
                retry_failed=retry_failed,
                stop_event=self._stop_event,
                source=source,
            )
            self._log("scrape() returned normally")

        self._redirect_stdout(run)
        self._log("--- scraper thread finished ---")
        self._start_time = None

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
        self._start_time = None

    def get_status(self) -> dict:
        status = {
            "running": self.is_running,
            "scraper_running": self.scraper_running,
            "prescraper_running": self.prescraper_running,
            "stop_requested": self._stop_requested,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "uptime_seconds": self.uptime_seconds,
        }
        try:
            db = get_db()
            cursor = db.execute(
                "SELECT status, COUNT(*) FROM progress GROUP BY status"
            )
            progress = dict(cursor.fetchall())
            cursor = db.execute("SELECT COUNT(*) FROM boats")
            total_boats = cursor.fetchone()[0]
            cursor = db.execute("SELECT MAX(scraped_at) FROM boats")
            last_scraped = cursor.fetchone()[0]
            cursor = db.execute("SELECT COUNT(*) FROM manufacturers")
            total_mfrs = cursor.fetchone()[0]
            db.close()
            status.update({
                "pending": progress.get("pending", 0),
                "done": progress.get("done", 0),
                "failed": progress.get("failed", 0),
                "total_boats": total_boats,
                "last_scraped": last_scraped,
                "total_manufacturers": total_mfrs,
                "prescrape_current": self._prescrape_current,
                "prescrape_total": self._prescrape_total,
                "prescrape_records": self._prescrape_records,
            })
        except Exception:
            status.update({
                "pending": 0, "done": 0, "failed": 0,
                "total_boats": 0, "last_scraped": None,
                "total_manufacturers": 0,
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
