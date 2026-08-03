"""CarMax car listing discovery and detail scraping."""
import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from scraper.config import MAX_ATTEMPTS
from scraper.database import get_db
from scraper.logger import LogLevel, log


CARMAX_SITEMAP_INDEX = "https://www.carmax.com/shared/sitemaps/index.xml"
CARMAX_RESEARCH_URL = "https://www.carmax.com/research/{make}/{model}/{year}"
CARMAX_DETAIL_RE = re.compile(r"/car/(\d+)")
CARMAX_CATEGORY_RE = re.compile(r"^https://www\.carmax\.com/cars/[^/]+/[^/]+/?$")


def _fetch_text(page: Page, url: str) -> str:
    """Fetch a URL via the browser's fetch API."""
    return page.evaluate(
        """async (url) => {
            const resp = await fetch(url, { credentials: 'include' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return await resp.text();
        }""",
        url,
    )


def _discover_category_urls(page: Page, refresh: bool = False) -> list[str]:
    """Fetch the CarMax sitemap index, parse the make-model sitemap, and return
    category page URLs like /cars/toyota/camry."""
    index_text = _fetch_text(page, CARMAX_SITEMAP_INDEX)
    root = ET.fromstring(index_text)
    ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    sitemap_urls = []
    for sm in root.findall("ns:sitemap", ns):
        loc = sm.find("ns:loc", ns)
        if loc is not None and loc.text and "sitemap-cars-make-model" in loc.text:
            sitemap_urls.append(loc.text.strip())

    category_urls = set()
    for sm_url in sitemap_urls:
        try:
            text = _fetch_text(page, sm_url)
            sm_root = ET.fromstring(text)
            for url_elem in sm_root.findall("ns:url", ns):
                loc = url_elem.find("ns:loc", ns)
                if loc is not None and loc.text:
                    url = loc.text.strip()
                    if CARMAX_CATEGORY_RE.match(url):
                        category_urls.add(url)
        except Exception as e:
            log(LogLevel.QUIET, f"[carmax] Failed to parse {sm_url}: {e}")

    return sorted(category_urls)


def _extract_stock_links(page: Page, category_url: str) -> set[str]:
    """Visit a category page and collect /car/{stockNumber} detail links."""
    detail_ids = set()
    try:
        page.goto(category_url, wait_until="domcontentloaded", timeout=30000)
        # Give React a moment to render
        page.wait_for_timeout(2500)
    except Exception as e:
        log(LogLevel.QUIET, f"[carmax] Failed to load category {category_url}: {e}")
        return detail_ids

    previous_count = -1
    stagnant = 0
    max_scrolls = 10
    for _ in range(max_scrolls):
        # Pull all /car/ links currently in the DOM
        hrefs = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href^="/car/"]'))
                .map(a => a.getAttribute('href'))"""
        )
        if hrefs:
            for href in hrefs:
                m = CARMAX_DETAIL_RE.search(href)
                if m:
                    detail_ids.add(f"https://www.carmax.com/car/{m.group(1)}")

        if len(detail_ids) == previous_count:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            stagnant = 0
        previous_count = len(detail_ids)

        # Scroll to load more
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)

    return detail_ids


def discover_urls(page: Page, db=None, refresh: bool = False) -> list[str]:
    """Discover CarMax detail URLs from sitemap category pages."""
    if db is None:
        db = get_db("CarMax")

    category_urls = _discover_category_urls(page, refresh=refresh)
    log(LogLevel.STANDARD, f"[carmax] Found {len(category_urls)} make/model category pages")

    all_detail_urls = set()
    for cat_url in category_urls[:50]:  # cap initial exploration to avoid marathon runs
        ids = _extract_stock_links(page, cat_url)
        all_detail_urls.update(ids)
        log(LogLevel.DEBUG, f"[carmax] {cat_url} -> {len(ids)} detail links (total {len(all_detail_urls)})")
        time.sleep(1.5)

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

    log(LogLevel.STANDARD, f"[carmax] {new_urls} new detail URLs queued")
    return list(all_detail_urls)


def _parse_ymmtt(title_text: str) -> dict[str, str | None]:
    """Best-effort parse of '2024 Toyota Camry SE' into year/make/model/trim."""
    result = {"year": None, "make": None, "model": None, "trim": None}
    m = re.match(r"^(19|20)(\d{2})\s+(.+)", title_text)
    if not m:
        return result
    result["year"] = int(m.group(0)[:4])
    remainder = m.group(3).strip()
    parts = remainder.split()
    if len(parts) >= 2:
        result["make"] = parts[0]
        # Try common two-word makes
        if parts[0].lower() in ("land", "mercedes-benz", "alfa") and len(parts) > 2:
            result["make"] = f"{parts[0]} {parts[1]}"
            parts = parts[1:]
        result["model"] = parts[1]
        if len(parts) >= 3:
            result["trim"] = " ".join(parts[2:])
    return result


def _spec_value(soup: BeautifulSoup, labels: list[str]) -> str | None:
    """Find a value in a CarMax research specs table by label."""
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if any(l in label for l in labels):
                return cells[1].get_text(strip=True)
    # Fallback: search any element whose text starts with the label
    for label_text in labels:
        for el in soup.find_all(text=re.compile(label_text, re.I)):
            parent = el.parent
            if parent:
                # Look at next sibling or parent's next sibling
                nxt = parent.find_next_sibling()
                if nxt:
                    val = nxt.get_text(strip=True)
                    if val:
                        return val
    return None


def fetch_specs(page: Page, year: int, make: str, model: str, trim: str | None) -> dict[str, str | None]:
    """Fetch CarMax research page specs for a YMMT."""
    url = CARMAX_RESEARCH_URL.format(make=make.lower(), model=model.lower(), year=year)
    try:
        html = _fetch_text(page, url)
    except Exception as e:
        log(LogLevel.DEBUG, f"[carmax] Spec fetch failed for {url}: {e}")
        return {}

    soup = BeautifulSoup(html, "lxml")
    return {
        "length": _spec_value(soup, ["overall length", "length"]),
        "width": _spec_value(soup, ["width"]),
        "height": _spec_value(soup, ["height"]),
        "wheel_size": _spec_value(soup, ["wheel size"]),
        "tire_size": _spec_value(soup, ["front tires", "tire size", "tires"]),
    }


def scrape_listing(page: Page, url: str) -> dict[str, Any] | None:
    """Scrape a single CarMax detail page."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        log(LogLevel.QUIET, f"[carmax] Timeout loading {url}")
        return None
    except Exception as exc:
        log(LogLevel.QUIET, f"[carmax] Error navigating to {url}: {exc}")
        return None

    soup = BeautifulSoup(page.content(), "lxml")
    text = soup.get_text(" ", strip=True)

    # Title from first H1 or page title
    title_text = ""
    h1 = soup.find("h1")
    if h1:
        title_text = h1.get_text(strip=True)
    if not title_text and soup.title:
        title_text = soup.title.get_text(strip=True)

    parsed = _parse_ymmtt(title_text)

    # VIN regex
    vin_match = re.search(r"VIN\s+([A-HJ-NPR-Z0-9]{17})", text, re.IGNORECASE)
    vin = vin_match.group(1).upper() if vin_match else None

    # Price
    price_match = re.search(r"\$[\d,]+", text)
    price = price_match.group(0) if price_match else None

    # Mileage
    mileage_match = re.search(r"([\d,]+)\s*(mi|miles|mile)", text, re.IGNORECASE)
    mileage = mileage_match.group(1) if mileage_match else None

    # Exterior color
    exterior_color = None
    for phrase in ["Exterior color:", "Exterior:", "Color:", "exterior color"]:
        m = re.search(rf"{re.escape(phrase)}\s*([^\n,]+)", text, re.IGNORECASE)
        if m:
            exterior_color = m.group(1).strip()
            break
    if not exterior_color:
        # Try generic color near title ( heuristic )
        color_words = re.findall(r"\b(Black|White|Silver|Gray|Red|Blue|Green|Yellow|Orange|Brown|Beige|Gold|White|Grey)\b", title_text, re.I)
        if color_words:
            exterior_color = color_words[0]

    # Engine / transmission / fuel
    engine = _spec_value(soup, ["engine", "engine type"]) or None
    transmission = _spec_value(soup, ["transmission", "drivetrain"]) or None
    fuel = _spec_value(soup, ["fuel type", "fuel"]) or None

    # Interior color/trim (best effort)
    interior_color = None
    interior_trim = None
    for phrase in ["Interior color:", "Interior:", "interior color"]:
        m = re.search(rf"{re.escape(phrase)}\s*([^\n,]+)", text, re.IGNORECASE)
        if m:
            interior_color = m.group(1).strip()
            break

    result = {
        "url": url,
        "vin": vin,
        "year": parsed.get("year"),
        "make": parsed.get("make"),
        "model": parsed.get("model"),
        "trim": parsed.get("trim"),
        "exterior_color": exterior_color,
        "engine": engine,
        "transmission": transmission,
        "fuel_type": fuel,
        "length": None,
        "width": None,
        "height": None,
        "wheel_size": None,
        "tire_size": None,
        "interior_color": interior_color,
        "interior_trim": interior_trim,
        "source_site": "CarMax",
        "price": price,
        "mileage": mileage,
    }

    # Fetch YMMT specs if we have enough info
    if result["year"] and result["make"] and result["model"]:
        specs = fetch_specs(page, result["year"], result["make"], result["model"], result["trim"])
        result.update(specs)

    return result


def save_car(db, data: dict) -> int:
    """Insert a CarMax car record. Duplicates are allowed."""
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


def save_spec(db, data: dict) -> None:
    """Upsert a YMMT spec record."""
    db.execute(
        """
        INSERT INTO specs (
            year, make, model, trim, length, width, height, wheel_size, tire_size, source_site
        ) VALUES (
            :year, :make, :model, :trim, :length, :width, :height, :wheel_size, :tire_size, :source_site
        )
        ON CONFLICT(year, make, model, trim) DO UPDATE SET
            length=excluded.length,
            width=excluded.width,
            height=excluded.height,
            wheel_size=excluded.wheel_size,
            tire_size=excluded.tire_size,
            scraped_at=CURRENT_TIMESTAMP
        """,
        data,
    )
    db.commit()
