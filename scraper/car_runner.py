"""Car scraping runner for CarMax and Carvana."""
from __future__ import annotations

import argparse
import random
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from scraper.browser import BoatBrowser
from scraper.config import MAX_ATTEMPTS, MAX_DELAY, MIN_DELAY, SOURCE_DB_NAMES
from scraper.database import get_db
from scraper.logger import LogLevel, log

# Import car site modules
from scraper.cars import carmax, carvana


_SOURCE_MODULES = {
    "CarMax": carmax,
    "Carvana": carvana,
}

_SOURCE_DOMAINS = {
    "CarMax": "https://www.carmax.com/",
    "Carvana": "https://www.carvana.com/",
}

_running = True


def _signal_handler(signum, frame):
    global _running
    log(LogLevel.STANDARD, "\n[car_runner] Interrupt received, finishing current URL...")
    _running = False


def _update_progress(db, url: str, status: str, error_msg: str | None = None):
    db.execute(
        """
        UPDATE progress
        SET status = ?,
            error_msg = ?,
            attempts = attempts + 1,
            last_attempt_at = ?
        WHERE url = ?
        """,
        (status, error_msg, datetime.now(timezone.utc).isoformat(), url),
    )
    db.commit()


def _get_stats(db) -> dict:
    cursor = db.execute("SELECT status, COUNT(*) FROM progress GROUP BY status")
    stats = dict(cursor.fetchall())
    return {
        "total": sum(stats.values()),
        "pending": stats.get("pending", 0),
        "done": stats.get("done", 0),
        "failed": stats.get("failed", 0),
    }


def _warm_source(page, source: str, warmed: set[str]):
    if source in warmed or source not in _SOURCE_DOMAINS:
        return
    homepage = _SOURCE_DOMAINS[source]
    log(LogLevel.STANDARD, f"[car_runner] Warming {source} domain ({homepage})...")
    try:
        page.goto(homepage, wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
    except Exception as e:
        log(LogLevel.QUIET, f"[car_runner] {source} domain warm failed: {e}")
    warmed.add(source)


def scrape(
    source: str,
    limit: int | None = None,
    retry_failed: bool = False,
    stop_event: threading.Event | None = None,
    discover: bool = True,
):
    """Main car scraping loop for CarMax or Carvana."""
    if source not in SOURCE_DB_NAMES:
        raise ValueError(f"Unknown car source: {source}")

    module = _SOURCE_MODULES[source]

    if stop_event is None:
        signal.signal(signal.SIGINT, _signal_handler)
        is_stopped: Callable[[], bool] = lambda: not _running
    else:
        is_stopped = lambda: stop_event.is_set()

    db = get_db(source)

    if retry_failed:
        db.execute("UPDATE progress SET status = 'pending' WHERE status = 'failed'")
        db.commit()
        log(LogLevel.STANDARD, "[car_runner] Retrying failed URLs...")

    with BoatBrowser() as browser:
        page = browser.page

        if discover and not retry_failed:
            log(LogLevel.STANDARD, f"[car_runner] Discovering {source} URLs...")
            module.discover_urls(page, db=db)

        cursor = db.execute(
            "SELECT url FROM progress WHERE status = 'pending' ORDER BY RANDOM()"
        )
        urls = [row[0] for row in cursor.fetchall()]

        if not urls:
            log(LogLevel.STANDARD, "[car_runner] No pending URLs to scrape.")
            db.close()
            return

        if limit is not None:
            urls = urls[:limit]
            log(LogLevel.STANDARD, f"[car_runner] Limited to {limit} URLs.")

        log(LogLevel.STANDARD, f"[car_runner] Starting scrape of {len(urls)} {source} URLs...")
        stats_interval = max(1, len(urls) // 10)
        _page_recycle_every = 200
        _browser_recycle_every = 1500
        _since_last_recycle = 0
        _consecutive_failed = 0
        warmed_sources: set[str] = set()

        for i, url in enumerate(urls, 1):
            if is_stopped():
                log(LogLevel.STANDARD, "[car_runner] Stopping gracefully...")
                break

            if _since_last_recycle >= _page_recycle_every:
                log(LogLevel.STANDARD, f"[browser] Recycling page after {_since_last_recycle} scrapes...")
                page = browser.recycle_page()
                _since_last_recycle = 0
                warmed_sources = set()

            if i % _browser_recycle_every == 0:
                log(LogLevel.STANDARD, f"[browser] Full restart after {i} scrapes...")
                browser.shutdown()
                page = browser.start()
                warmed_sources = set()

            try:
                _warm_source(page, source, warmed_sources)
                data = module.scrape_listing(page, url)

                if data is None:
                    _consecutive_failed += 1
                    _update_progress(db, url, "failed", "Challenge page or timeout")
                    log(
                        LogLevel.STANDARD,
                        f"[car_runner] No data for {source} URL "
                        f"(consecutive failures={_consecutive_failed}): {url}",
                    )
                    if _consecutive_failed >= 5:
                        log(
                            LogLevel.QUIET,
                            "[car_runner] WARNING: 5+ consecutive failures. "
                            "Likely IP/ASN flagged.",
                        )
                elif not data.get("vin") and not data.get("year"):
                    _consecutive_failed += 1
                    _update_progress(db, url, "failed", "No VIN/year extracted")
                else:
                    _consecutive_failed = 0
                    module.save_car(db, data)
                    _update_progress(db, url, "done")
                    vin = data.get("vin", "N/A")
                    year = data.get("year", "?")
                    make = data.get("make", "Unknown")
                    model = data.get("model", "")
                    log(
                        LogLevel.STANDARD,
                        f"[scrape] {source} | {year} {make} {model} | VIN: {vin}",
                    )

            except Exception as e:
                _consecutive_failed += 1
                error_str = str(e)
                log(
                    LogLevel.QUIET,
                    f"[car_runner] Error scraping {url}: {error_str} "
                    f"(consecutive failures={_consecutive_failed})",
                )
                cursor = db.execute(
                    "SELECT attempts FROM progress WHERE url = ?", (url,)
                )
                row = cursor.fetchone()
                attempts = row[0] if row else 0
                if attempts + 1 >= MAX_ATTEMPTS:
                    _update_progress(db, url, "failed", error_str)
                else:
                    _update_progress(db, url, "pending", error_str)
                    log(
                        LogLevel.STANDARD,
                        f"[car_runner] Will retry {url} (attempt {attempts + 1}/{MAX_ATTEMPTS})",
                    )

            _since_last_recycle += 1

            if i % stats_interval == 0 or i == len(urls):
                stats = _get_stats(db)
                log(
                    LogLevel.STANDARD,
                    f"[car_runner] Progress: {i}/{len(urls)} this run | "
                    f"done={stats['done']}, pending={stats['pending']}, failed={stats['failed']}",
                )

            if i < len(urls) and not is_stopped():
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                slept = 0.0
                chunk = 0.5
                while slept < delay and not is_stopped():
                    time.sleep(min(chunk, delay - slept))
                    slept += chunk

    stats = _get_stats(db)
    log(LogLevel.STANDARD, "\n[car_runner] Scraping complete.")
    log(LogLevel.STANDARD, f"[car_runner] Final stats: {stats}")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Vehicle Data Scraper - cars")
    parser.add_argument("source", choices=["CarMax", "Carvana"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    scrape(
        source=args.source,
        limit=args.limit,
        retry_failed=args.retry_failed,
    )


if __name__ == "__main__":
    main()
