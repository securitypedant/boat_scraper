"""Prescraper for USCG Manufacturer Identification Codes (MICs).

Scrapes all boat manufacturers listed at:
https://uscgboating.org/content/manufacturers-identification.php

Stores: mic, company_name, city, state in SQLite.
"""
import re
import sqlite3
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.database import get_db

BASE_URL = "https://uscgboating.org/content/manufacturers-identification.php"
PAGESIZE = 25


def _fetch_page(page_num: int) -> BeautifulSoup:
    """Fetch a single page and return parsed BeautifulSoup."""
    url = (
        f"{BASE_URL}?"
        f"pageNum_manufacturers={page_num}"
        f"&totalRows_manufacturers=16263"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def _extract_manufacturers(soup: BeautifulSoup) -> list[dict]:
    """Parse manufacturers from a single page's HTML."""
    results = []
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        # Skip if too few rows or no header
        if len(rows) < 2:
            continue

        # Check header
        header = rows[0]
        header_tds = header.find_all(["th", "td"])
        header_texts = [h.get_text(strip=True) for h in header_tds]
        if header_texts[:5] != ["MIC", "Company", "Address", "City", "State"]:
            continue

        # Parse data rows
        for row in rows[1:]:
            tds = row.find_all("td")
            if len(tds) < 5:
                continue
            texts = [t.get_text(strip=True) for t in tds]
            mic, company, address, city, state = texts[0], texts[1], texts[2], texts[3], texts[4]
            # Skip empty/placeholder rows
            if mic in ("", "-") or not company:
                continue
            results.append({
                "mic": mic,
                "company": company,
                "city": city or None,
                "state": state or None,
            })

    return results


def _clean_company(company: str) -> str:
    """Remove legal suffixes and marine-industry words to get a clean brand name."""
    suffixes = [
        r",?\s*INC\.?\s*$",
        r",?\s*LLC\.?\s*$",
        r",?\s*CORP\.?\s*$",
        r",?\s*CORPORATION\.?\s*$",
        r",?\s*LTD\.?\s*$",
        r",?\s*LIMITED\s*$",
        r",?\s*CO\.?\s*$",
        r",?\s*COMPANY\s*$",
        r",?\s*LP\s*$",
        r",?\s*L\.?P\.?\s*$",
        r"\s+U\.S\.A\.?\s*$",
        r"\s+USA\s*$",
        r"\s+BOATS?\s*$",
        r"\s+YACHTS?\s*$",
        r"\s+MOTOR\s*$",
        r"\s+MARINE\s*$",
        r"\s+HOLDINGS?\s*$",
        r"\s*\(OOB\)\s*$",
        r"\s*\(OMC\)\s*$",
    ]
    cleaned = company.strip()
    for pat in suffixes:
        cleaned = re.sub(pat, "", cleaned, flags=re.I).strip()
    return cleaned


def run_prescrape(on_progress=None) -> int:
    """Fetch all USCG manufacturers and store them in SQLite.

    Args:
        on_progress: Optional callback(page_num, total_pages, current_total)

    Returns number of manufacturers inserted.
    """
    conn = get_db()

    # Get existing count + page offset
    cursor = conn.execute("SELECT COUNT(*) FROM manufacturers")
    existing = cursor.fetchone()[0]

    # Calculate starting page
    start_page = existing // PAGESIZE

    print(f"[uscg] Existing: {existing} manufacturers. Starting from page {start_page}...")

    # Fetch page 0 to get total count (or use cached value)
    soup = _fetch_page(start_page) if start_page == 0 else _fetch_page(start_page)
    total_match = re.search(r'Records Found:\s*(\d+)', soup.get_text())
    total_records = int(total_match.group(1)) if total_match else 16263
    total_pages = (total_records + PAGESIZE - 1) // PAGESIZE
    print(f"[uscg] Total records: {total_records}, total pages: {total_pages}")

    if existing >= total_records:
        print(f"[uscg] Already complete ({existing}/{total_records}). Skipping.")
        if on_progress:
            on_progress(total_pages, total_pages, existing)
        return existing

    inserted = 0
    for page_num in range(start_page, total_pages):
        try:
            if page_num > start_page:
                soup = _fetch_page(page_num)
            manufacturers = _extract_manufacturers(soup)
            for m in manufacturers:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO manufacturers (mic, company, city, state)
                    VALUES (:mic, :company, :city, :state)
                    """,
                    m,
                )
            batch = conn.total_changes
            conn.commit()
            # Track newly inserted
            cursor = conn.execute("SELECT COUNT(*) FROM manufacturers")
            current_total = cursor.fetchone()[0]
            inserted += len(manufacturers)

            if on_progress:
                on_progress(page_num + 1, total_pages, current_total)

            if (page_num + 1) % 20 == 0:
                print(f"[uscg] Page {page_num + 1}/{total_pages} ({current_total}/{total_records} total)")

            time.sleep(0.2)  # Be polite
        except Exception as e:
            print(f"[uscg] Error on page {page_num}: {e}")
            continue

    conn.close()
    print(f"[uscg] Done. Total manufacturers: {existing + inserted}")
    return existing + inserted


def list_manufacturers() -> list[dict]:
    """Return all manufacturers as a list of dicts."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT mic, company, city, state FROM manufacturers ORDER BY company")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def lookup_make(name: str) -> str | None:
    """Given a boat name, try to extract the make by fuzzy matching against manufacturers."""
    if not name:
        return None

    # Strip common prefixes like "New 2026", "Used 2023", etc.
    name = re.sub(r"^(New|Used)\s+(19|20)\d{2}\s*", "", name, flags=re.I).strip()
    name = re.sub(r"^(19|20)\d{2}\s*", "", name).strip()

    words = name.strip().split()
    if not words:
        return None

    conn = get_db()
    cursor = conn.execute("SELECT company FROM manufacturers")
    companies = [row[0] for row in cursor.fetchall()]
    conn.close()

    # Build clean lookup: map clean lowercase name → original company
    clean_map: dict[str, str] = {}
    for company in companies:
        clean = _clean_company(company).lower()
        if clean and len(clean) >= 2:
            clean_map[clean] = company

    best_match = None
    best_score = 0

    # Try prefixes from longest to shortest
    for i in range(len(words), 0, -1):
        prefix = " ".join(words[:i]).lower()
        if len(prefix) < 3:
            continue
        for clean_name, original in clean_map.items():
            # Exact match
            if clean_name == prefix:
                score = len(clean_name) * 2  # Exact gets higher score
                if score > best_score:
                    best_score = score
                    best_match = original
                continue
            # Company name starts with boat prefix (boat name is shorter)
            if clean_name.startswith(prefix):
                # Require prefix to cover a significant chunk of company name
                ratio = len(prefix) / len(clean_name)
                if ratio >= 0.6 and len(prefix) >= 4:
                    score = len(prefix)
                    if score > best_score:
                        best_score = score
                        best_match = original

    if best_match:
        return _clean_company(best_match).title()

    # Fallback: return first word if it looks like a brand
    first = words[0]
    if len(first) >= 3 and not re.match(r"\d+$", first):
        return first.title()
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="USCG Manufacturer prescraper")
    parser.add_argument("--list", action="store_true", help="List all manufacturers")
    parser.add_argument("--test", type=str, help="Test make extraction on a name")
    args = parser.parse_args()

    if args.list:
        for m in list_manufacturers():
            print(f"{m['mic']:5} {m['company'][:50]}")
    elif args.test:
        make = lookup_make(args.test)
        print(f"Input: '{args.test}'")
        print(f"Make:  '{make}'")
    else:
        run_prescrape()


if __name__ == "__main__":
    main()
