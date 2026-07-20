"""Per-site sitemap discovery and URL extraction."""
import gzip
import io
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from playwright.sync_api import Page

from scraper.database import get_db

# Site-specific sitemap configurations
# Each entry defines how to discover URLs for a given source.
# sitemap_filter: substring to look for in sitemap <loc> entries (or None for all)
SITE_MAPS = {
    "BoatTrader": {
        "index_url": "https://www.boattrader.com/sitemap-index-en.xml",
        "sitemap_filter": "boatdetail",
        "url_filter": None,  # accept all URLs
    },
    "YachtWorld": {
        "index_url": "https://www.yachtworld.com/sitemap-index-us.xml",
        "sitemap_filter": None,
        "url_filter": "/yacht/",
    },
    "BoatsDotCom": {
        "index_url": "https://www.boats.com/sitemap.xml",
        "sitemap_filter": None,
        "url_filter": "/boat",
    },
}


def _domain_for_source(source: str) -> str:
    """Return the canonical domain for a source."""
    return {
        "BoatTrader": "boattrader.com",
        "YachtWorld": "yachtworld.com",
        "BoatsDotCom": "boats.com",
    }.get(source, source.lower())


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
    """Fetch a .gz URL via direct HTTP (more robust than browser binary fetch)."""
    try:
        resp = requests.get(url, timeout=60, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/gzip,application/x-gzip,*/*",
        })
        resp.raise_for_status()
        data = resp.content
        if data[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                return f.read().decode("utf-8")
        return data.decode("utf-8")
    except Exception:
        # Fallback to browser if direct HTTP fails
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


def discover_urls(page: Page, source: str | None = None) -> list[str]:
    """Discover boat detail URLs from a site's sitemaps and store them in the DB.

    Args:
        page: An authenticated Playwright page.
        source: Which site's URLs to discover. If None, discovers BoatTrader URLs.

    Returns a list of unique listing URLs (may be empty if already populated).
    """
    if source is None:
        source = "BoatTrader"

    config = SITE_MAPS.get(source)
    if not config:
        print(f"[sitemap] Unknown source '{source}'. Skipping discovery.")
        return []

    domain = _domain_for_source(source)
    db = get_db()

    # Check if we already have pending URLs for THIS site
    cursor = db.execute(
        "SELECT COUNT(*) FROM progress WHERE status = 'pending' AND url LIKE ?",
        (f"%{domain}%",)
    )
    pending_count = cursor.fetchone()[0]

    if pending_count > 0:
        print(f"[sitemap] Found {pending_count} pending {source} URLs already in database. Skipping discovery.")
        db.close()
        return []

    index_url = config["index_url"]
    sitemap_filter = config["sitemap_filter"]
    url_filter = config["url_filter"]

    print(f"[sitemap] Fetching {source} sitemap index: {index_url}...")

    # For sites other than BoatTrader, navigate to the domain first to establish cookies/session
    domain = config["index_url"].split("/")[2]
    current = page.url.split("/")[2] if page.url else ""
    if domain not in current:
        try:
            print(f"[sitemap] Visiting {domain} first to establish session...")
            page.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=15000)
            import time
            time.sleep(2)
        except Exception:
            pass

    index_text = None
    for attempt in range(2):
        try:
            index_text = _fetch_sitemap_text(page, index_url)
            break
        except Exception as e:
            print(f"[sitemap] Failed to fetch {source} sitemap index (attempt {attempt + 1}): {e}")
            if attempt == 0:
                time.sleep(2)
            else:
                db.close()
                return []

    # Validate response is XML, not HTML
    stripped = index_text.strip().lower()
    if "<html" in stripped[:500] or "<!doctype" in stripped[:500]:
        print(f"[sitemap] {source} returned HTML instead of XML (blocked): {index_url}")
        db.close()
        return []
    if "<sitemapindex" not in stripped[:2000] and "<urlset" not in stripped[:2000]:
        print(f"[sitemap] {source} sitemap does not contain expected XML tags (blocked): {index_url}")
        db.close()
        return []

    # Parse index XML
    try:
        root = ET.fromstring(index_text)
    except ET.ParseError as e:
        print(f"[sitemap] Failed to parse {source} sitemap XML: {e}")
        db.close()
        return []
    ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    sitemap_urls = []
    for sm in root.findall("ns:sitemap", ns):
        loc = sm.find("ns:loc", ns)
        if loc is not None and loc.text:
            loc_text = loc.text.strip()
            if sitemap_filter is None or sitemap_filter in loc_text:
                sitemap_urls.append(loc_text)

    print(f"[sitemap] Found {len(sitemap_urls)} {source} sitemap files.")

    all_urls = set()
    for sm_url in sitemap_urls[:50]:  # cap at 50 sitemap files
        print(f"[sitemap] Fetching {sm_url}...")
        try:
            content = _fetch_sitemap_text(page, sm_url)
            # Skip if response is HTML
            stripped = content.strip().lower()
            if "<html" in stripped[:500] or "<!doctype" in stripped[:500]:
                print(f"[sitemap] {sm_url} returned HTML instead of XML (skipped)")
                continue
            sm_root = ET.fromstring(content)
            for url_elem in sm_root.findall("ns:url", ns):
                loc = url_elem.find("ns:loc", ns)
                if loc is not None and loc.text:
                    url = loc.text.strip()
                    if url_filter is None or url_filter in url:
                        all_urls.add(url)
        except ET.ParseError as e:
            print(f"[sitemap] XML parse error for {sm_url}: {e} (skipped)")
            continue
        except Exception as e:
            print(f"[sitemap] Warning: Failed to fetch {sm_url}: {e}")
            continue

    print(f"[sitemap] Total unique {source} URLs found: {len(all_urls)}")

    # Track in sources table (all URLs ever found) and progress (only new ones)
    cursor = db.cursor()
    new_urls = 0
    updated_sources = 0
    progress_inserted = 0

    for url in sorted(all_urls):
        try:
            # Upsert into sources (track every URL ever seen)
            cursor.execute(
                """
                INSERT INTO sources (url, source_site, first_seen, last_seen)
                VALUES (:url, :site, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(url) DO UPDATE SET
                    last_seen = CURRENT_TIMESTAMP,
                    source_site = excluded.source_site
                """,
                {"url": url, "site": source},
            )
            if cursor.rowcount > 0:
                # Row was newly inserted (not updated)
                new_urls += 1
                # Also add to progress queue since it's new
                cursor.execute(
                    "INSERT OR IGNORE INTO progress (url, status) VALUES (?, 'pending')",
                    (url,),
                )
                if cursor.rowcount > 0:
                    progress_inserted += 1
            else:
                updated_sources += 1
        except Exception:
            pass

    db.commit()
    db.close()

    print(f"[sitemap] {new_urls} brand-new URLs added to queue, {updated_sources} previously known.")
    print(f"[sitemap] {progress_inserted} new URLs inserted into progress queue.")
    return list(all_urls)
