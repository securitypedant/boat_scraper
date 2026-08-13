"""Carvana car listing discovery and detail scraping."""
import base64
import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from scraper.browser import wait_for_site_access
from scraper.database import get_db
from scraper.logger import LogLevel, log


CARVANA_SITEMAP_INDEX = "https://www.carvana.com/sitemap.xml"
CARVANA_CATEGORY_RE = re.compile(r"^https://www\.carvana\.com/cars/.+")


def _fetch_text(page: Page, url: str) -> str:
    """Fetch a URL via the browser's fetch API and return decoded text.

    Handles gzip-compressed resources (.gz sitemaps) by returning the raw
    response as a base64 data URL and decompressing locally.
    """
    data_url: str = page.evaluate(
        """async (url) => {
            const resp = await fetch(url, { credentials: 'include' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const blob = await resp.blob();
            return await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(new Error('FileReader failed'));
                reader.readAsDataURL(blob);
            });
        }""",
        url,
    )
    comma = data_url.find(",")
    if comma < 0:
        raise ValueError("Unexpected fetch response format")
    meta = data_url[:comma].lower()
    payload = base64.b64decode(data_url[comma + 1 :])
    if url.endswith(".gz") or "gzip" in meta or "application/gzip" in meta or "application/x-gzip" in meta:
        payload = gzip.decompress(payload)
    return payload.decode("utf-8", errors="replace")


def _discover_category_urls(page: Page, refresh: bool = False) -> list[str]:
    """Fetch the Carvana sitemap index and return category page URLs."""
    index_text = _fetch_text(page, CARVANA_SITEMAP_INDEX)
    root = ET.fromstring(index_text)
    ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    sitemap_urls = []
    for sm in root.findall("ns:sitemap", ns):
        loc = sm.find("ns:loc", ns)
        if loc is not None and loc.text:
            sitemap_urls.append(loc.text.strip())

    category_urls = set()
    for sm_url in sitemap_urls[:5]:
        try:
            text = _fetch_text(page, sm_url)
            sm_root = ET.fromstring(text)
            for url_elem in sm_root.findall("ns:url", ns):
                loc = url_elem.find("ns:loc", ns)
                if loc is not None and loc.text:
                    url = loc.text.strip()
                    if CARVANA_CATEGORY_RE.match(url) and "/vehicle/" not in url:
                        category_urls.add(url)
        except Exception as e:
            log(LogLevel.QUIET, f"[carvana] Failed to parse {sm_url}: {e}")

    return sorted(category_urls)


def _parse_ymmt(name: str) -> dict[str, Any]:
    """Parse '2025 Toyota Camry LE' into year/make/model/trim."""
    result = {"year": None, "make": None, "model": None, "trim": None}
    parts = name.strip().split()
    if len(parts) < 3:
        return result
    try:
        result["year"] = int(parts[0])
    except ValueError:
        return result
    # Two-word makes
    if parts[1].lower() == "land" and len(parts) > 3:
        result["make"] = f"{parts[1]} {parts[2]}"
        result["model"] = parts[3]
        result["trim"] = " ".join(parts[4:]) if len(parts) > 4 else None
    elif "mercedes-benz" in name.lower():
        result["make"] = "Mercedes-Benz"
        result["model"] = parts[2]
        result["trim"] = " ".join(parts[3:]) if len(parts) > 3 else None
    else:
        result["make"] = parts[1]
        result["model"] = parts[2]
        result["trim"] = " ".join(parts[3:]) if len(parts) > 3 else None
    return result


def _extract_listings_from_html(html: str) -> list[dict[str, Any]]:
    """Parse Carvana category page HTML for JSON-LD Vehicle listings."""
    listings = []
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("@type") == "Vehicle":
            listings.append(data)
        # Some pages wrap listings in an ItemList
        if data.get("@type") == "ItemList" and "itemListElement" in data:
            for elem in data["itemListElement"]:
                item = elem.get("item") if isinstance(elem, dict) else None
                if item and item.get("@type") == "Vehicle":
                    listings.append(item)
    return listings


def _extract_detail_urls_from_category_page(page: Page, url: str) -> set[str]:
    """Render a Carvana category page and extract detail URLs from JSON-LD.

    Carvana category pages are SSR for the first batch, so we can fetch the
    HTML once and parse JSON-LD. This avoids scrolling/API interception.
    """
    try:
        html = _fetch_text(page, url)
    except Exception as e:
        log(LogLevel.QUIET, f"[carvana] Failed to fetch category {url}: {e}")
        return set()

    detail_urls = set()
    for data in _extract_listings_from_html(html):
        offer = data.get("offers") or {}
        detail_url = offer.get("url") or data.get("url")
        if detail_url and "/vehicle/" in detail_url:
            detail_urls.add(detail_url.split("?")[0])

    return detail_urls


def discover_urls(
    page: Page,
    db=None,
    refresh: bool = False,
    stop_event=None,
) -> list[str]:
    """Discover Carvana detail URLs from sitemap category pages."""
    if db is None:
        db = get_db("Carvana")

    category_urls = _discover_category_urls(page, refresh=refresh)
    log(LogLevel.STANDARD, f"[carvana] Found {len(category_urls)} category pages")

    if stop_event and stop_event.is_set():
        log(LogLevel.STANDARD, "[carvana] Stop requested before category scan.")
        return []

    if not wait_for_site_access(page, "https://www.carvana.com", timeout=30):
        log(LogLevel.STANDARD, "[carvana] Could not access www.carvana.com; aborting discovery.")
        return []

    all_detail_urls = set()
    for i, cat_url in enumerate(category_urls[:30], 1):
        if stop_event and stop_event.is_set():
            log(LogLevel.STANDARD, "[carvana] Stop requested during category scan.")
            break
        urls = _extract_detail_urls_from_category_page(page, cat_url)
        all_detail_urls.update(urls)
        log(LogLevel.STANDARD, f"[carvana] {i}/30 {cat_url} -> {len(urls)} detail links (total {len(all_detail_urls)})")
        time.sleep(1.0)

    cursor = db.cursor()
    new_urls = 0
    for url in sorted(all_detail_urls):
        cursor.execute(
            "INSERT OR IGNORE INTO progress (url, status) VALUES (?, 'pending')",
            (url,),
        )
        if cursor.rowcount > 0:
            new_urls += 1
    db.commit()

    log(LogLevel.STANDARD, f"[carvana] {new_urls} new detail URLs queued")
    return list(all_detail_urls)


def scrape_listing(page: Page, url: str) -> dict[str, Any] | None:
    """Scrape a single Carvana vehicle page.

    Carvana search/category JSON-LD already gives us most fields, so we try to
    re-fetch the page's JSON-LD first. Fall back to the hydrated DOM if needed.
    """
    try:
        html = _fetch_text(page, url)
    except Exception as e:
        log(LogLevel.QUIET, f"[carvana] Failed to fetch {url}: {e}")
        return None

    listings = _extract_listings_from_html(html)
    data = listings[0] if listings else {}

    if data:
        name = data.get("name", "")
        ymmt = _parse_ymmt(name)
        offer = data.get("offers") or {}
        return {
            "url": url,
            "vin": (data.get("vehicleIdentificationNumber") or "").upper() or None,
            "year": ymmt.get("year"),
            "make": ymmt.get("make"),
            "model": ymmt.get("model"),
            "trim": ymmt.get("trim"),
            "exterior_color": data.get("color") or None,
            "engine": None,
            "transmission": None,
            "fuel_type": None,
            "length": None,
            "width": None,
            "height": None,
            "wheel_size": None,
            "tire_size": None,
            "interior_color": None,
            "interior_trim": None,
            "source_site": "Carvana",
            "price": offer.get("price") if isinstance(offer, dict) else None,
            "mileage": data.get("mileageFromOdometer"),
        }

    # Fallback: render the VDP and parse the visible title
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception:
        return None

    soup = BeautifulSoup(page.content(), "lxml")
    title_text = ""
    h1 = soup.find("h1")
    if h1:
        title_text = h1.get_text(strip=True)
    elif soup.title:
        title_text = soup.title.get_text(strip=True)

    ymmt = _parse_ymmt(title_text)

    # Try to find exterior color in page text
    text = soup.get_text(" ", strip=True)
    color = data.get("color") if data else None
    if not color:
        m = re.search(r"Exterior[\s:]+([^\n,]+)", text, re.IGNORECASE)
        if m:
            color = m.group(1).strip()

    return {
        "url": url,
        "vin": None,
        "year": ymmt.get("year"),
        "make": ymmt.get("make"),
        "model": ymmt.get("model"),
        "trim": ymmt.get("trim"),
        "exterior_color": color,
        "engine": None,
        "transmission": None,
        "fuel_type": None,
        "length": None,
        "width": None,
        "height": None,
        "wheel_size": None,
        "tire_size": None,
        "interior_color": None,
        "interior_trim": None,
        "source_site": "Carvana",
        "price": None,
        "mileage": None,
    }


def save_car(db, data: dict) -> int:
    """Insert a Carvana car record. Duplicates are allowed."""
    cursor = db.execute(
        """
        INSERT INTO cars (
            vin, year, make, model, trim, exterior_color, engine, transmission,
            fuel_type, length, width, height, wheel_size, tire_size,
            interior_color, interior_trim, source_site, url
        ) VALUES (
            :vin, :year, :make, :model, :trim, :exterior_color, :engine, :transmission,
            :fuel_type, :length, :width, :height, :wheel_size, :tire_size,
            :interior_color, :interior_trim, :source_site, :url
        )
        """,
        data,
    )
    db.commit()
    return cursor.lastrowid
