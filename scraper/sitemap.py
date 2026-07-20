"""Per-site sitemap discovery and URL extraction."""
import gzip
import io
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page

from scraper.database import get_db

# Site-specific sitemap configurations
# Each entry defines how to discover URLs for a given source.
# sitemap_filter: substring to look for in sitemap <loc> entries (or None for all)
SITE_MAPS = {
    "BoatTrader": {
        "index_url": "https://www.boattrader.com/sitemap-index-en.xml",
        "sitemap_filter": "boatdetail",
        "url_filter": None,  # accept all URLs (boatdetail sitemaps are already filtered by sitemap_filter)
    },
    "YachtWorld": {
        "index_url": "https://www.yachtworld.com/sitemap-index-us.xml",
        "sitemap_filter": None,
        "url_filter": "/yacht/",
    },
    "BoatsDotCom": {
        "index_url": "https://www.boats.com/sitemap.xml",
        "sitemap_filter": None,
        "url_filter": None,  # boats.com sitemaps only contain search/filter pages, not individual listings
        "no_detail_sitemaps": True,  # flag to skip URL extraction after parsing index
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


def _fetch_gz(page: Page, url: str, refresh: bool = False) -> str:
    """Fetch .gz sitemap. Checks local /app/data/sitemaps/ first."""
    # --- LOCAL OVERRIDE: check if user uploaded the .gz file ---
    if not refresh:
        local_dir = Path("/app/data/sitemaps")
        filename = Path(urlparse(url).path).name
        local_path = local_dir / filename
        if local_path.exists():
            print(f"[sitemap] Reading local {filename}")
            with open(local_path, "rb") as f:
                data = f.read()
            if data[:2] == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                    return f.read().decode("utf-8")
            return data.decode("utf-8")

    # --- REMOTE FETCH: chunked via browser to avoid wire crash ---
    # Fetch in page, cache as Uint8Array, read back in 1MB base64 chunks
    chunk_size = 1024 * 1024  # 1MB

    size_info = page.evaluate(
        """
        async (url) => {
            const resp = await fetch(url, { credentials: 'include' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const buf = await resp.arrayBuffer();
            window._gzBuf = new Uint8Array(buf);
            return { size: window._gzBuf.length };
        }
        """,
        url,
    )
    total = size_info["size"]

    try:
        chunks = []
        for offset in range(0, total, chunk_size):
            chunk_b64 = page.evaluate(
                """
                ({offset, size}) => {
                    const slice = window._gzBuf.slice(offset, offset + size);
                    let binary = '';
                    for (let i = 0; i < slice.length; i++) {
                        binary += String.fromCharCode(slice[i]);
                    }
                    return btoa(binary);
                }
                """,
                {"offset": offset, "size": min(chunk_size, total - offset)},
            )
            chunks.append(base64.b64decode(chunk_b64))

        data = b"".join(chunks)
    finally:
        page.evaluate("() => { delete window._gzBuf; }")

    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            return f.read().decode("utf-8")
    return data.decode("utf-8")


def _fetch_sitemap_text(page: Page, url: str, refresh: bool = False) -> str:
    """Fetch a sitemap URL, handling gzip decompression if needed."""
    if url.endswith(".gz"):
        return _fetch_gz(page, url, refresh=refresh)
    return _fetch_text(page, url)


def discover_urls(
    page: Page, source: str | None = None, refresh: bool = False
) -> list[str]:
    """Discover boat detail URLs from a site's sitemaps and store them in the DB.

    Args:
        page: An authenticated Playwright page.
        source: Which site's URLs to discover. If None, discovers BoatTrader URLs.
        refresh: If True, ignore local cached .gz files and refetch from the web.

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

    # Check if we already have pending URLs for THIS site (skip when refresh=True)
    if not refresh:
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
            index_text = _fetch_sitemap_text(page, index_url, refresh=refresh)
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

    if config.get("no_detail_sitemaps"):
        print(f"[sitemap] {source} does not expose individual boat listing URLs in sitemaps. Skipping URL extraction.")
        db.close()
        return []

    all_urls = set()
    for sm_url in sitemap_urls[:50]:  # cap at 50 sitemap files
        print(f"[sitemap] Fetching {sm_url}...")
        try:
            content = _fetch_sitemap_text(page, sm_url, refresh=refresh)
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
