"""Multi-site boat listing detail page scraper.

Supports: BoatTrader, YachtWorld, Boats.com
"""
import json
import re
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from prescraper.uscg_prescraper import _clean_company, lookup_make


def _detect_source(url: str) -> str:
    """Detect which site a URL belongs to."""
    lower = url.lower()
    if "yachtworld.com" in lower:
        return "YachtWorld"
    if "boats.com" in lower:
        return "BoatsDotCom"
    return "BoatTrader"


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
    """Extract specs using BoatTrader's specific DOM structure."""
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


def _extract_specs_yachtworld(soup: BeautifulSoup) -> dict[str, str]:
    """Extract specs from YachtWorld DOM.

    YachtWorld uses various class-based detail rows. We scan all elements
    looking for label/value pairs separated by pipes or in table-cell-like
    structures.
    """
    specs: dict[str, str] = {}

    # Pattern 1: elements with Detail in class name that contain text like "Label | Value"
    for el in soup.find_all(attrs={"class": re.compile(r"Detail", re.I)}):
        text = el.get_text(separator="|", strip=True)
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) == 2:
            key = parts[0].lower().replace(":", "").strip()
            val = parts[1]
            specs[key] = val

    # Pattern 2: table rows
    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) >= 2:
            key = tds[0].get_text(strip=True).lower().replace(":", "").strip()
            val = tds[1].get_text(strip=True)
            if key and val and key not in specs:
                specs[key] = val

    return specs


def _extract_specs_boatsdotcom(soup: BeautifulSoup) -> dict[str, str]:
    """Extract specs from Boats.com definition lists.

    Boats.com uses <dl><dt>Label</dt><dd>Value</dd>...</dl>
    """
    specs: dict[str, str] = {}

    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = dt.get_text(strip=True).lower().replace(":", "").strip()
            val = dd.get_text(separator=" ", strip=True)
            if key and val:
                specs[key] = val

    return specs


def _extract_hin_boattrader(page: Page) -> str | None:
    """Extract HIN from BoatTrader Redux state."""
    try:
        hin = page.evaluate("""() => {
            try {
                const state = window.__REDUX_STATE__;
                if (!state) return null;
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


def _extract_make_from_breadcrumbs(soup: BeautifulSoup) -> str | None:
    """Try to extract make from breadcrumb navigation."""
    breadcrumb = soup.find(attrs={"class": re.compile(r"breadcrumb", re.I)})
    if not breadcrumb:
        return None
    items = breadcrumb.find_all(["a", "li", "span"])
    texts = [it.get_text(strip=True) for it in items if it.get_text(strip=True)]
    if not texts:
        return None
    for candidate in reversed(texts):
        cand = candidate.strip()
        if cand in ("Home", "Boats For Sale", "Power", "Sail", ""):
            continue
        if len(cand) >= 2 and not re.search(r"\d{4}", cand):
            return cand
    return None


def _clean_numeric(val: str | None) -> str | None:
    """Clean and normalize numeric-ish values."""
    if not val:
        return None
    val = val.strip()
    if val.lower() in ("n/a", "none", "not specified", "-", ""):
        return None
    return val


def scrape_listing(page: Page, url: str) -> dict[str, Any] | None:
    """Scrape a single boat listing page from any supported site.

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
    source = _detect_source(url)

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
        "source": source,
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

    # 2. Site-specific spec extraction
    specs: dict[str, str] = {}
    if source == "BoatTrader":
        specs = _extract_specs_boattrader(soup)
        result["hin"] = _extract_hin_boattrader(page)
    elif source == "YachtWorld":
        specs = _extract_specs_yachtworld(soup)
    elif source == "BoatsDotCom":
        specs = _extract_specs_boatsdotcom(soup)

    # 3. Map specs to fields (normalize keys across sites)
    if not result["year"]:
        year_val = specs.get("year")
        if year_val:
            m = re.search(r"(19|20)\d{2}", str(year_val))
            if m:
                result["year"] = int(m.group())

    if not result["name"]:
        result["name"] = specs.get("name") or (soup.title.get_text(strip=True) if soup.title else None)

    # Length
    result["length"] = _clean_numeric(
        specs.get("length") or specs.get("loa") or specs.get("overall length")
    )

    # Class / Type
    result["class"] = _clean_numeric(
        specs.get("class") or specs.get("type") or specs.get("boat class") or specs.get("class:")
    )

    # Engine
    engine_val = specs.get("engine") or specs.get("engine make") or specs.get("engine make:")
    if engine_val:
        result["engine"] = _clean_numeric(engine_val)
    elif source == "BoatsDotCom":
        # Boats.com splits engine make/model
        engine_make = specs.get("engine make")
        engine_model = specs.get("engine model")
        if engine_make or engine_model:
            result["engine"] = _clean_numeric(f"{engine_make or ''} {engine_model or ''}".strip())

    # Total Power
    result["total_power"] = _clean_numeric(
        specs.get("total power") or specs.get("power") or specs.get("power:")
    )

    # Engine Hours
    result["engine_hours"] = _clean_numeric(
        specs.get("engine(s) hours") or specs.get("engine hours") or specs.get("engine hours:")
    )

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
