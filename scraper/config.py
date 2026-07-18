"""Configuration and constants for the boat scraper."""
import os
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "boats.db"
BROWSER_CONTEXT_DIR = DATA_DIR / "browser_context"

# Browser settings
HEADLESS = os.environ.get("BOAT_SCRAPER_HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT = int(os.environ.get("BOAT_SCRAPER_TIMEOUT", "30000"))
NAVIGATION_TIMEOUT = int(os.environ.get("BOAT_SCRAPER_NAV_TIMEOUT", "60000"))

# Rate limiting
MIN_DELAY = float(os.environ.get("BOAT_SCRAPER_MIN_DELAY", "2.0"))
MAX_DELAY = float(os.environ.get("BOAT_SCRAPER_MAX_DELAY", "8.0"))

# Retry settings
MAX_ATTEMPTS = int(os.environ.get("BOAT_SCRAPER_MAX_ATTEMPTS", "3"))

# URLs
SITEMAP_INDEX_URL = "https://www.boattrader.com/sitemap-index-en.xml"
BASE_DOMAIN = "https://www.boattrader.com"

# CSS Selectors for detail pages (will try multiple strategies)
SELECTORS = {
    # Title/heading area
    "title_h1": [
        "h1.listing-title",
        "h1.boat-title",
        "h1[data-testid='listing-title']",
        "h1",
    ],
    # Specs tables/panels
    "specs_table": [
        "table.specs-table",
        "div.specifications",
        "div[data-testid='specifications']",
        "dl.spec-list",
        ".listing-specs",
        "#specifications",
    ],
    "specs_rows": [
        "tr",
        "div.spec-row",
        "dl > div",
        ".spec-item",
    ],
    # Individual fields (label-based search)
    "field_labels": {
        "year": ["year", "model year"],
        "length": ["length", "loa", "overall length"],
        "class": ["class", "boat class", "category"],
        "engine": ["engine", "engine make", "engine type"],
        "total_power": ["total power", "hp", "horsepower", "power"],
        "engine_hours": ["engine hours", "hours", "engine hour"],
        "model": ["model", "boat model"],
        "capacity": ["capacity", "max passengers", "persons", "seating capacity"],
    },
}

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
