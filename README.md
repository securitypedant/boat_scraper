# BoatTrader Scraper

A stealth Playwright-based scraper that extracts boat data from [BoatTrader](https://www.boattrader.com) and stores it in SQLite.

## Fields Extracted

- `year` - Model year
- `name` - Boat name / make
- `length` - Overall length
- `class` - Boat class/category
- `engine` - Engine make/type
- `total_power` - Total horsepower
- `engine_hours` - Engine hours
- `model` - Boat model
- `capacity` - Passenger/seating capacity

## Setup

```bash
cd boat_scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

### Full scrape (all boats)
```bash
python -m scraper.run
```

### Test run (limit to 20 URLs)
```bash
python -m scraper.run --limit 20
```

### Retry failed URLs
```bash
python -m scraper.run --retry-failed
```

### Environment variables
```bash
BOAT_SCRAPER_HEADLESS=false   # Force headed mode
BOAT_SCRAPER_MIN_DELAY=5      # Minimum delay between requests (seconds)
BOAT_SCRAPER_MAX_DELAY=15     # Maximum delay between requests (seconds)
BOAT_SCRAPER_MAX_ATTEMPTS=5   # Retry attempts per URL
```

## Data Access

```bash
sqlite3 data/boats.db
```

Example queries:
```sql
-- All boats
SELECT * FROM boats;

-- Counts by year and class
SELECT year, class, COUNT(*) FROM boats GROUP BY year, class;

-- Boats missing engine hours
SELECT url, name, year FROM boats WHERE engine_hours IS NULL;

-- Pending/failed URLs
SELECT url, status, attempts, error_msg FROM progress WHERE status != 'done';
```

## Architecture

- **Browser** (`browser.py`): Stealth Playwright with Cloudflare challenge detection and manual fallback
- **Sitemap** (`sitemap.py`): Discovers boat URLs from XML sitemaps via browser
- **Scraper** (`detail_scraper.py`): Extracts data using multiple strategies (JSON-LD, tables, definition lists)
- **Storage** (`database.py`): SQLite with resumable progress tracking
- **Runner** (`run.py`): CLI orchestrator with randomized delays and graceful shutdown
