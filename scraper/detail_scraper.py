"""Boat listing detail page scraper with BoatTrader-specific extraction."""
import json
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from prescraper.uscg_prescraper import _clean_company, lookup_make

def _parse_title(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract year, name, model from page title / headings."""
    result = {"year": None, "name": None, "model": None}

    # Try JSON-LD structured data first
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0] if data else {}
            if data.get("@type") in ["Product", "Vehicle", "Boat", "IndividualProduct"]:
                result["name"] = data.get("name", data.get("model", "")).strip() or None
                result["model"] = data.get("mpn", data.get("model", "")).strip() or None
                year_str = data.get("vehicleModelDate", data.get("productionDate", ""))
                if year_str:
                    m = re.search(r"(19|20)\d{2}", str(year_str))
                    if m:
                        result["year"] = int(m.group())
            break
        except Exception:
            continue

    # Fallback: parse from H1 title
    if not result["name"]:
        h1 = soup.find("h1")
        if h1:
            title_text = h1.get_text(strip=True)
            year_match = re.match(r"^(19|20)(\d{2})\s+(.+)", title_text)
            if year_match:
                result["year"] = int(year_match.group(0)[:4])
                result["name"] = year_match.group(3).strip()
            else:
                result["name"] = title_text

    return result


def _extract_specs_boattrader(soup: BeautifulSoup) -> dict[str, str]:
    """Extract specs using BoatTrader's specific DOM structure.

    Each spec lives in a child div of the boatDetails container:
        <div class="style-module_boatDetail__...">
            <span>Label</span>
            <p>Value</p>
        </div>
    """
    specs: dict[str, str] = {}
    container = soup.find(attrs={"class": re.compile(r"boatDetails")})
    if not container:
        return specs

    for child in container.find_all("div", recursive=False):
        span = child.find("span")
        p = child.find("p")
        if span and p:
            key = span.get_text(strip=True).lower()
            val = p.get_text(strip=True)
            specs[key] = val

    return specs


def _clean_numeric(val: str | None) -> str | None:
    """Clean and normalize numeric-ish values."""
    if not val:
        return None
    val = val.strip()
    if val.lower() in ("n/a", "none", "not specified", "-", ""):
        return None
    return val


def _extract_make_from_breadcrumbs(soup: BeautifulSoup) -> str | None:
    """Try to extract make from breadcrumb navigation."""
    breadcrumb = soup.find(attrs={"class": re.compile(r"breadcrumb", re.I)})
    if not breadcrumb:
        return None
    # Get all list items or links in the breadcrumb
    items = breadcrumb.find_all(["a", "li", "span"])
    texts = [it.get_text(strip=True) for it in items if it.get_text(strip=True)]
    if not texts:
        return None
    # Usually last meaningful item is the brand/make
    for candidate in reversed(texts):
        cand = candidate.strip()
        # Skip generic crumbs
        if cand in ("Home", "Boats For Sale", "Power", "Sail", ""):
            continue
        # Return if it's a short-ish brand name
        if len(cand) >= 2 and not re.search(r"\d{4}", cand):
            return cand
    return None


def _extract_hin_from_page(page: Page) -> str | None:
    """Extract HIN from window.__REDUX_STATE__ using Playwright JS evaluation.

    The Redux state path is: app.data.hull.hin
    """
    try:
        hin = page.evaluate("""() => {
            try {
                const state = window.__REDUX_STATE__;
                if (!state) return null;
                // BoatTrader path: app.data.hull.hin
                const app = state.app || state;
                const data = app.data || app;
                const hull = data.hull || {};
                return hull.hin || null;
            } catch (e) {
                return null;
            }
        }""")
        if isinstance(hin, str) and hin.strip() and hin.strip() not in ("None", "null"):
            return hin.strip()
    except Exception:
        pass
    return None


def scrape_listing(page: Page, url: str) -> dict[str, Any] | None:
    """Scrape a single boat listing page.

    Returns a dict with all requested fields, or None if the page is invalid.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeout:
        print(f"[scraper] Timeout loading {url}")
        return None
    except Exception as exc:
        print(f"[scraper] Error navigating to {url}: {exc}")
        return None

    # Check for challenge page
    content_text = page.content()
    if "performing security verification" in content_text.lower():
        print(f"[scraper] Challenge page detected at {url}")
        return None

    soup = BeautifulSoup(content_text, "lxml")

    # Initialize all fields
    result = {
        "url": url,
        "year": None,
        "name": None,
        "make": None,
        "length": None,
        "class": None,
        "engine": None,
        "total_power": None,
        "engine_hours": None,
        "model": None,
        "capacity": None,
        "hin": None,
        "source": "BoatTrader",
    }

    # 1. Extract from title / headings
    title_data = _parse_title(soup)
    result["year"] = title_data.get("year")
    result["name"] = title_data.get("name")
    result["model"] = title_data.get("model")

    # 1b. Extract make (breadcrumbs first, then USCG lookup)
    make = _extract_make_from_breadcrumbs(soup)
    if not make:
        make = lookup_make(result["name"])
    if make:
        result["make"] = _clean_company(make)

    # 1c. Extract HIN from Redux state JSON blob
    result["hin"] = _extract_hin_from_page(page)

    # 2. Extract specs using BoatTrader DOM
    specs = _extract_specs_boattrader(soup)

    # 3. Map specs to fields
    if not result["year"]:
        year_val = specs.get("year")
        if year_val:
            m = re.search(r"(19|20)\d{2}", str(year_val))
            if m:
                result["year"] = int(m.group())

    if not result["name"]:
        result["name"] = specs.get("name") or (soup.title.get_text(strip=True) if soup.title else None)

    result["length"] = _clean_numeric(specs.get("length"))
    result["class"] = _clean_numeric(specs.get("class"))
    result["engine"] = _clean_numeric(specs.get("engine"))
    result["total_power"] = _clean_numeric(specs.get("total power"))
    result["engine_hours"] = _clean_numeric(specs.get("engine(s) hours") or specs.get("engine hours"))

    if not result["model"]:
        result["model"] = _clean_numeric(specs.get("model"))

    result["capacity"] = _clean_numeric(specs.get("capacity"))

    # 4. Parse model from name if still missing
    if result["name"] and not result["model"]:
        name = result["name"]
        parts = name.split()
        if len(parts) >= 3:
            for i, part in enumerate(parts):
                if re.match(r"\d+", part):
                    result["model"] = " ".join(parts[i:])
                    break

    return result
