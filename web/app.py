"""Flask web dashboard for boat scraper."""
import json
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request, send_file

from query.database import build_delete_query, build_query, get_db
from scraper.config import DB_PATH
from scraper.sitemap import SITE_MAPS
from web.log_buffer import LogBuffer, setup_logging
from web.scraper_manager import ScraperManager

app = Flask(__name__)

log_buffer = LogBuffer()
logger = setup_logging(log_buffer)
manager = ScraperManager(log_buffer)

# In-memory rate limiter for /api/sitemap-urls
_sitemap_url_last = {}
_sitemap_url_cache = {}


@app.route("/")
def dashboard():
    version = "unknown"
    # Try Docker image version first
    vfile = Path("/app/.version")
    if vfile.exists():
        version = vfile.read_text().strip()
    else:
        # Local dev: try git hash
        try:
            version = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(Path(__file__).resolve().parent.parent),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            pass
    return render_template("index.html", version=version)


@app.route("/api/start", methods=["POST"])
def start_scraper():
    log_buffer.write("[dashboard] POST /api/start received")
    data = request.get_json(silent=True) or {}
    limit = data.get("limit")
    retry_failed = data.get("retry_failed", False)
    source = data.get("source")  # e.g. "BoatTrader", "YachtWorld", "BoatsDotCom"

    if limit is not None:
        limit = int(limit)

    try:
        ok = manager.start(limit=limit, retry_failed=retry_failed, source=source)
        log_buffer.write(f"[dashboard] manager.start() returned {ok}, source={source}")
    except Exception as exc:
        log_buffer.write(f"[dashboard] manager.start() ERROR: {exc}")
        ok = False

    return jsonify({"success": ok, "running": manager.is_running})


@app.route("/api/stop", methods=["POST"])
def stop_scraper():
    log_buffer.write("[dashboard] POST /api/stop received")
    ok = manager.stop()
    log_buffer.write(f"[dashboard] manager.stop() returned {ok}")
    return jsonify({"success": ok, "running": manager.is_running})


@app.route("/api/discover", methods=["POST"])
def discover_scraper():
    log_buffer.write("[dashboard] POST /api/discover received")
    data = request.get_json(silent=True) or {}
    source = data.get("source")  # e.g. "BoatTrader", "YachtWorld", "BoatsDotCom"
    refresh = data.get("refresh", False)
    try:
        ok = manager.discover(source=source, refresh=refresh)
        log_buffer.write(f"[dashboard] manager.discover() returned {ok}, source={source} refresh={refresh}")
    except Exception as exc:
        log_buffer.write(f"[dashboard] manager.discover() ERROR: {exc}")
        ok = False
    return jsonify({"success": ok, "running": manager.discover_running})


@app.route("/api/sitemap-urls")
def sitemap_urls():
    """Return sitemap file URLs for a source (cached for 60s, rate-limited)."""
    source = request.args.get("source", "YachtWorld")
    cfg = SITE_MAPS.get(source)
    if not cfg:
        return jsonify({"error": "Unknown source", "urls": []}), 400

    # Rate limit: max 1 request per 5 seconds per source
    now = time.time()
    last = _sitemap_url_last.get(source, 0)
    if now - last < 5:
        # Return cached result if available
        cached = _sitemap_url_cache.get(source)
        if cached:
            return jsonify(cached)
        return jsonify({"error": "Rate limited. Please wait a few seconds.", "urls": []}), 429
    _sitemap_url_last[source] = now

    log_buffer.write(f"[dashboard] GET /api/sitemap-urls source={source}")
    index_url = cfg["index_url"]

    try:
        from scraper.browser import BoatBrowser
        import xml.etree.ElementTree as ET

        with BoatBrowser() as browser:
            page = browser.page
            domain = urlparse(index_url).netloc
            try:
                page.goto(f"https://{domain}/", wait_until="networkidle", timeout=20000)
            except Exception:
                pass

            text = page.evaluate(
                """async (url) => {
                    const resp = await fetch(url, { credentials: 'include' });
                    return await resp.text();
                }""",
                index_url,
            )

            root = ET.fromstring(text)
            ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            sitemap_filter = cfg.get("sitemap_filter")
            urls = []
            for sm in root.findall("ns:sitemap", ns):
                loc = sm.find("ns:loc", ns)
                if loc is not None and loc.text:
                    loc_text = loc.text.strip()
                    if sitemap_filter is None or sitemap_filter in loc_text:
                        urls.append(loc_text)

        result = {"source": source, "count": len(urls), "urls": urls}
        _sitemap_url_cache[source] = result
        return jsonify(result)
    except Exception as exc:
        log_buffer.write(f"[dashboard] sitemap-urls ERROR: {exc}")
        return jsonify({"error": str(exc), "urls": []}), 500


@app.route("/api/prescrape", methods=["POST"])
def start_prescraper():
    log_buffer.write("[dashboard] POST /api/prescrape received")
    try:
        ok = manager.prescrape()
        log_buffer.write(f"[dashboard] manager.prescrape() returned {ok}")
    except Exception as exc:
        log_buffer.write(f"[dashboard] manager.prescrape() ERROR: {exc}")
        ok = False
    return jsonify({"success": ok, "running": manager.prescraper_running})


@app.route("/api/status")
def status():
    return jsonify(manager.get_status())


@app.route("/api/logs")
def logs():
    """Server-Sent Events stream of log lines."""
    def event_stream():
        cond = log_buffer.subscribe()
        try:
            # Send existing lines first
            for line in log_buffer.tail(100):
                yield f"data: {json.dumps({'type': 'log', 'line': line})}\n\n"

            # Wait for new lines
            while True:
                with cond:
                    cond.wait(timeout=5)
                for line in log_buffer.tail(100):
                    yield f"data: {json.dumps({'type': 'log', 'line': line})}\n\n"
        except GeneratorExit:
            pass
        finally:
            log_buffer.unsubscribe(cond)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/query")
def query_boats():
    """Query boats with filters."""
    try:
        limit = request.args.get("limit", 20, type=int)
        offset = request.args.get("offset", 0, type=int)
        year = request.args.get("year", type=int)
        make = request.args.get("make")
        boat_class = request.args.get("class")
        engine = request.args.get("engine")
        hin = request.args.get("hin")
        source = request.args.get("source")
        min_length = request.args.get("min_length", type=int)
        max_length = request.args.get("max_length", type=int)
        has_field = request.args.get("has_field")
        missing_field = request.args.get("missing_field")
        order_by = request.args.get("order_by", "scraped_at DESC")

        sql, params = build_query(
            year=year,
            make=make,
            boat_class=boat_class,
            engine=engine,
            hin=hin,
            source=source,
            min_length=min_length,
            max_length=max_length,
            has_field=has_field,
            missing_field=missing_field,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]

        # Total count
        count_sql = "SELECT COUNT(*) FROM boats WHERE 1=1"
        count_params = []
        if year is not None:
            count_sql += " AND year = ?"
            count_params.append(year)
        if make is not None:
            count_sql += " AND make LIKE ?"
            count_params.append(f"%{make}%")
        if boat_class is not None:
            count_sql += " AND class LIKE ?"
            count_params.append(f"%{boat_class}%")
        if engine is not None:
            count_sql += " AND engine LIKE ?"
            count_params.append(f"%{engine}%")
        if hin is not None:
            count_sql += " AND hin LIKE ?"
            count_params.append(f"%{hin}%")
        if source is not None:
            count_sql += " AND source = ?"
            count_params.append(source)
        if has_field is not None:
            count_sql += f" AND {has_field} IS NOT NULL"
        if missing_field is not None:
            count_sql += f" AND {missing_field} IS NULL"

        cursor = db.execute(count_sql, count_params)
        total = cursor.fetchone()[0]
        db.close()

        return jsonify({
            "success": True,
            "total": total,
            "offset": offset,
            "limit": limit,
            "rows": rows,
        })

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/upload-sitemaps", methods=["POST"])
def upload_sitemaps():
    """Accept .gz sitemap file uploads and save to /app/data/sitemaps/."""
    import os

    upload_dir = Path("/app/data/sitemaps")
    upload_dir.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"success": False, "error": "No files provided"}), 400

    saved = []
    for f in files:
        if f.filename and (f.filename.endswith(".gz") or f.filename.endswith(".xml")):
            dest = upload_dir / f.filename
            f.save(str(dest))
            saved.append(f.filename)
        else:
            log_buffer.write(f"[dashboard] Skipping upload: {f.filename}")

    log_buffer.write(f"[dashboard] Uploaded {len(saved)} sitemap files")
    return jsonify({"success": True, "saved": saved, "count": len(saved)})


@app.route("/api/delete-all", methods=["POST"])
def delete_all():
    """Delete all boats matching the query filters (ignoring limit/offset)."""
    data = request.get_json(silent=True) or {}

    # Build DELETE query using same filters as query endpoint
    sql, params = build_delete_query(
        year=data.get("year"),
        make=data.get("make"),
        boat_class=data.get("boat_class"),
        engine=data.get("engine"),
        hin=data.get("hin"),
        source=data.get("source"),
        min_length=data.get("min_length"),
        max_length=data.get("max_length"),
        has_field=data.get("has_field"),
        missing_field=data.get("missing_field"),
    )

    db = get_db()
    # First count how many will be deleted
    count_sql = sql.replace("DELETE FROM boats", "SELECT COUNT(*) FROM boats")
    cursor = db.execute(count_sql, params)
    to_delete = cursor.fetchone()[0]

    if to_delete == 0:
        db.close()
        return jsonify({"success": True, "deleted": 0, "message": "No matching records found."})

    cursor = db.execute(sql, params)
    deleted = cursor.rowcount
    db.commit()
    db.close()

    log_buffer.write(f"[dashboard] Deleted {deleted} boat records matching filters.")
    return jsonify({"success": True, "deleted": deleted, "message": f"Deleted {deleted} records."})


@app.route("/api/boat/<int:boat_id>")
def get_boat(boat_id: int):
    """Get a single boat by ID."""
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.execute(
        "SELECT * FROM boats WHERE id = ?", (boat_id,)
    )
    row = cursor.fetchone()
    db.close()
    if row is None:
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True, "boat": dict(row)})


@app.route("/api/boat/<int:boat_id>", methods=["POST"])
def update_boat(boat_id: int):
    """Update a boat record."""
    data = request.get_json(silent=True) or {}
    allowed = {
        "year", "name", "make", "length", "class", "engine",
        "total_power", "engine_hours", "model", "capacity", "hin",
    }
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"success": False, "error": "No valid fields provided"}), 400

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [boat_id]

    db = get_db()
    cursor = db.execute(f"UPDATE boats SET {set_clause} WHERE id = ?", values)
    db.commit()
    updated = cursor.rowcount
    db.close()
    return jsonify({"success": updated > 0, "updated": updated})


@app.route("/api/boat/<int:boat_id>", methods=["DELETE"])
def delete_boat(boat_id: int):
    """Delete a boat record."""
    db = get_db()
    cursor = db.execute("DELETE FROM boats WHERE id = ?", (boat_id,))
    db.commit()
    deleted = cursor.rowcount
    db.close()
    return jsonify({"success": deleted > 0, "deleted": deleted})


@app.route("/api/wipe", methods=["POST"])
def wipe_database():
    """Wipe boats + progress tables. Optionally keep manufacturers."""
    data = request.get_json(silent=True) or {}
    keep_mfrs = data.get("keep_manufacturers", True)

    db = get_db()
    db.execute("DELETE FROM boats")
    db.execute("DELETE FROM progress")
    if not keep_mfrs:
        db.execute("DELETE FROM manufacturers")
    db.commit()

    # Reset totals
    cursor = db.execute("SELECT COUNT(*) FROM boats")
    remaining_boats = cursor.fetchone()[0]
    cursor = db.execute("SELECT COUNT(*) FROM progress")
    remaining_progress = cursor.fetchone()[0]
    cursor = db.execute("SELECT COUNT(*) FROM manufacturers")
    remaining_mfrs = cursor.fetchone()[0]
    db.close()

    return jsonify({
        "success": True,
        "remaining_boats": remaining_boats,
        "remaining_progress": remaining_progress,
        "remaining_manufacturers": remaining_mfrs,
    })


@app.route("/api/wipe-manufacturers", methods=["POST"])
def wipe_manufacturers():
    """Delete all manufacturers from the database."""
    db = get_db()
    cursor = db.execute("DELETE FROM manufacturers")
    db.commit()
    deleted = cursor.rowcount
    cursor = db.execute("SELECT COUNT(*) FROM manufacturers")
    remaining = cursor.fetchone()[0]
    db.close()
    log_buffer.write(f"[dashboard] Wiped {deleted} manufacturers. Remaining: {remaining}")
    return jsonify({"success": True, "deleted": deleted, "remaining": remaining})


@app.route("/api/retry-failed", methods=["POST"])
def retry_failed():
    """Reset all failed URLs to pending for another attempt."""
    db = get_db()
    cursor = db.execute("""
        UPDATE progress SET status = 'pending', attempts = 0, error_msg = NULL
        WHERE status = 'failed'
    """)
    db.commit()
    reset_count = cursor.rowcount
    db.close()
    log_buffer.write(f"[dashboard] Reset {reset_count} failed URLs to pending for retry.")
    return jsonify({"success": True, "reset_count": reset_count})


@app.route("/api/download")
def download_database():
    """Download the SQLite database file."""
    if not DB_PATH.exists():
        return jsonify({"success": False, "error": "Database file not found"}), 404
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"boats_{timestamp}.db"
    return send_file(
        str(DB_PATH),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/version")
def get_version():
    """Return the git commit hash baked into the image."""
    version_path = Path("/app/.version")
    version = "unknown"
    if version_path.exists():
        version = version_path.read_text().strip()
    return jsonify({"version": version})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
