"""Sitemap discovery and URL extraction via the browser's fetch API.

Since Cloudflare blocks direct HTTP requests for sitemap files, we use the
already-authenticated Playwright browser page to fetch XML and .gz files.
"""
import gzip
import io
import xml.etree.ElementTree as ET

from playwright.sync_api import Page

from scraper.config import SITEMAP_INDEX_URL
from scraper.database import get_db


def _fetch_text(page: Page, url: str) -> str:
    """Fetch a text/XML URL via the browser's fetch API."""
    return page.evaluate(
        """async (url) => {
            const resp = await fetch(url, { credentials: 'include' });
            return await resp.text();
        }""",
        url,
    )


def _fetch_gz(page: Page, url: str) -> str:
    """Fetch a .gz URL via the browser's fetch API and decompress in Python."""
    raw: list[int] = page.evaluate(
        """async (url) => {
            const resp = await fetch(url, { credentials: 'include' });
            const buf = await resp.arrayBuffer();
            return Array.from(new Uint8Array(buf));
        }""",
        url,
    )
    data = bytes(raw)
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            return f.read().decode("utf-8")
    return data.decode("utf-8")


def _fetch_sitemap_text(page: Page, url: str) -> str:
    """Fetch a sitemap URL, handling gzip decompression if needed."""
    if url.endswith(".gz"):
        return _fetch_gz(page, url)
    return _fetch_text(page, url)


def discover_urls(page: Page) -> list[str]:
    """Discover boat detail URLs from BoatTrader sitemaps and store them in the DB.

    Args:
        page: An authenticated Playwright page (Cloudflare already passed).

    Returns a list of unique listing URLs (may be empty if already populated).
    """
    db = get_db()

    # Check if we already have pending URLs
    cursor = db.execute("SELECT COUNT(*) FROM progress WHERE status = 'pending'")
    pending_count = cursor.fetchone()[0]

    if pending_count > 0:
        print(f"[sitemap] Found {pending_count} pending URLs already in database. Skipping discovery.")
        db.close()
        return []

    print("[sitemap] Fetching sitemap index...")
    index_text = _fetch_sitemap_text(page, SITEMAP_INDEX_URL)

    # Parse index XML
    root = ET.fromstring(index_text)
    ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    sitemap_urls = []
    for sm in root.findall("ns:sitemap", ns):
        loc = sm.find("ns:loc", ns)
        if loc is not None and "boatdetail" in loc.text:
            sitemap_urls.append(loc.text)

    print(f"[sitemap] Found {len(sitemap_urls)} boat detail sitemap files.")

    all_urls = set()
    for sm_url in sitemap_urls:
        print(f"[sitemap] Fetching {sm_url}...")
        try:
            content = _fetch_sitemap_text(page, sm_url)
            sm_root = ET.fromstring(content)
            for url_elem in sm_root.findall("ns:url", ns):
                loc = url_elem.find("ns:loc", ns)
                if loc is not None:
                    all_urls.add(loc.text.strip())
        except Exception as e:
            print(f"[sitemap] Warning: Failed to fetch {sm_url}: {e}")
            continue

    print(f"[sitemap] Total unique boat URLs found: {len(all_urls)}")

    # Insert into database
    cursor = db.cursor()
    inserted = 0
    for url in sorted(all_urls):
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO progress (url, status) VALUES (?, 'pending')",
                (url,),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except Exception:
            pass

    db.commit()
    db.close()

    print(f"[sitemap] Inserted {inserted} new URLs into progress queue.")
    return list(all_urls)
