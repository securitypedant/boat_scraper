"""Main entrypoint for the boat scraper."""
import argparse
import random
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from scraper.browser import BoatBrowser
from scraper.config import MAX_ATTEMPTS, MIN_DELAY, MAX_DELAY
from scraper.database import get_db, init_db
from scraper.detail_scraper import scrape_listing
from scraper.sitemap import discover_urls


_running = True


def _signal_handler(signum, frame):
    global _running
    print("\n[run] Interrupt received, finishing current URL and shutting down...")
    _running = False


def _save_result(db, data: dict):
    """Insert or update a boat record."""
    # Ensure source is set
    if not data.get("source"):
        data["source"] = "BoatTrader"

    cursor = db.execute(
        """
        INSERT INTO boats (url, year, name, make, length, class, engine, total_power, engine_hours, model, capacity, hin, source)
        VALUES (:url, :year, :name, :make, :length, :class, :engine, :total_power, :engine_hours, :model, :capacity, :hin, :source)
        ON CONFLICT(url) DO UPDATE SET
            year=excluded.year,
            name=excluded.name,
            make=excluded.make,
            length=excluded.length,
            class=excluded.class,
            engine=excluded.engine,
            total_power=excluded.total_power,
            engine_hours=excluded.engine_hours,
            model=excluded.model,
            capacity=excluded.capacity,
            hin=excluded.hin,
            source=excluded.source,
            scraped_at=CURRENT_TIMESTAMP
        """,
        data,
    )
    db.commit()
    return cursor.rowcount


def _update_progress(db, url: str, status: str, error_msg: str | None = None):
    """Update URL status in progress table."""
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
    """Get current scraping statistics."""
    cursor = db.execute(
        "SELECT status, COUNT(*) FROM progress GROUP BY status"
    )
    stats = dict(cursor.fetchall())
    total = sum(stats.values())
    return {
        "total": total,
        "pending": stats.get("pending", 0),
        "done": stats.get("done", 0),
        "failed": stats.get("failed", 0),
    }


def scrape(limit: int | None = None, retry_failed: bool = False, stop_event: threading.Event | None = None, source: str | None = None):
    """Main scraping loop.

    Args:
        limit: Maximum number of URLs to scrape (None for all).
        retry_failed: If True, retry URLs previously marked as failed.
        stop_event: If provided, the scraper will check this event instead of
                    global signal handling. Used for programmatic control.
        source: If provided, only scrape URLs matching this source domain.
    """
    # Initialize database
    init_db()
    db = get_db()

    # Setup signal handling only in standalone mode
    if stop_event is None:
        signal.signal(signal.SIGINT, _signal_handler)
        is_stopped = lambda: not _running
    else:
        is_stopped = lambda: stop_event.is_set()

    with BoatBrowser() as browser:
        page = browser.page

        # Phase 1: Discover URLs using the authenticated browser
        if not retry_failed:
            discover_urls(page)
        else:
            # Reset failed to pending
            db.execute("UPDATE progress SET status = 'pending' WHERE status = 'failed'")
            db.commit()
            print("[run] Retrying failed URLs...")

        # Build pending queue
        cursor = db.execute(
            "SELECT url FROM progress WHERE status = 'pending' ORDER BY RANDOM()"
        )
        urls = [row[0] for row in cursor.fetchall()]

        if not urls:
            print("[run] No pending URLs to scrape.")
            return

        # Filter by source if specified
        if source:
            before = len(urls)
            source_domain = {
                "BoatTrader": "boattrader.com",
                "YachtWorld": "yachtworld.com",
                "BoatsDotCom": "boats.com",
            }.get(source)
            if source_domain:
                urls = [url for url in urls if source_domain in url.lower()]
                print(f"[run] Filtered {before} URLs by source='{source}' → {len(urls)} matching")
            if not urls:
                print(f"[run] No pending URLs match source='{source}'.")
                return

        if limit is not None:
            urls = urls[:limit]
            print(f"[run] Limited to {limit} URLs.")

        print(f"[run] Starting scrape of {len(urls)} URLs...")
        stats_interval = max(1, len(urls) // 10)

        for i, url in enumerate(urls, 1):
            if is_stopped():
                print("[run] Stopping gracefully...")
                break

            try:
                data = scrape_listing(page, url)

                if data is None:
                    _update_progress(db, url, "failed", "Challenge page or timeout")
                elif not data.get("name") and not data.get("year"):
                    _update_progress(db, url, "failed", "No title/year extracted")
                else:
                    _save_result(db, data)
                    _update_progress(db, url, "done")

            except Exception as e:
                error_str = str(e)
                print(f"[run] Error scraping {url}: {error_str}")

                # Check attempts
                cursor = db.execute(
                    "SELECT attempts FROM progress WHERE url = ?", (url,)
                )
                row = cursor.fetchone()
                attempts = row[0] if row else 0

                if attempts + 1 >= MAX_ATTEMPTS:
                    _update_progress(db, url, "failed", error_str)
                else:
                    _update_progress(db, url, "failed", error_str)

            # Progress stats
            if i % stats_interval == 0 or i == len(urls):
                stats = _get_stats(db)
                print(
                    f"[run] Progress: {i}/{len(urls)} this run | "
                    f"Total queue: done={stats['done']}, pending={stats['pending']}, failed={stats['failed']}"
                )

            # Rate limiting
            if i < len(urls) and not is_stopped():
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                time.sleep(delay)

    stats = _get_stats(db)
    print("\n[run] Scraping complete.")
    print(f"[run] Final stats: {stats}")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="BoatTrader boat scraper")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit scraping to N URLs (for testing)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed URLs",
    )
    args = parser.parse_args()

    scrape(limit=args.limit, retry_failed=args.retry_failed)


if __name__ == "__main__":
    main()
