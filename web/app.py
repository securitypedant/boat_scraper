"""Flask web dashboard for boat scraper."""
import json
import sqlite3
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, send_file

from query.database import build_query, get_db
from scraper.config import DB_PATH
from web.log_buffer import LogBuffer
from web.scraper_manager import ScraperManager

app = Flask(__name__)

log_buffer = LogBuffer()
manager = ScraperManager(log_buffer)


@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_scraper():
    log_buffer.write("[dashboard] POST /api/start received")
    data = request.get_json(silent=True) or {}
    limit = data.get("limit")
    retry_failed = data.get("retry_failed", False)

    if limit is not None:
        limit = int(limit)

    try:
        ok = manager.start(limit=limit, retry_failed=retry_failed)
        log_buffer.write(f"[dashboard] manager.start() returned {ok}")
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
