"""Database access helpers for querying boat data."""
import sqlite3
from query.config import DB_PATH


def get_db() -> sqlite3.Connection:
    """Open a read-only-ish connection to the boats database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def build_query(
    year: int | None = None,
    make: str | None = None,
    boat_class: str | None = None,
    engine: str | None = None,
    hin: str | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    has_field: str | None = None,
    missing_field: str | None = None,
    order_by: str = "scraped_at DESC",
    limit: int | None = None,
    offset: int = 0,
) -> tuple[str, list]:
    """Build a parameterized SELECT query."""
    sql = """
        SELECT
            id, url, year, name, make, length, class, engine,
            total_power, engine_hours, model, capacity, hin, scraped_at
        FROM boats
        WHERE 1=1
    """
    params: list = []

    if year is not None:
        sql += " AND year = ?"
        params.append(year)

    if make is not None:
        sql += " AND make LIKE ?"
        params.append(f"%{make}%")

    if boat_class is not None:
        sql += " AND class LIKE ?"
        params.append(f"%{boat_class}%")

    if engine is not None:
        sql += " AND engine LIKE ?"
        params.append(f"%{engine}%")

    if hin is not None:
        sql += " AND hin LIKE ?"
        params.append(f"%{hin}%")

    if min_length is not None:
        sql += " AND CAST(REPLACE(REPLACE(length, 'ft', ''), \"'\", '') AS REAL) >= ?"
        params.append(min_length)

    if max_length is not None:
        sql += " AND CAST(REPLACE(REPLACE(length, 'ft', ''), \"'\", '') AS REAL) <= ?"
        params.append(max_length)

    if has_field is not None:
        sql += f" AND {has_field} IS NOT NULL"

    if missing_field is not None:
        sql += f" AND {missing_field} IS NULL"

    if order_by:
        # Basic sanitization: only allow known columns
        allowed_cols = {
            "id", "url", "year", "name", "make", "length", "class", "engine",
            "total_power", "engine_hours", "model", "capacity", "hin", "scraped_at",
        }
        parts = order_by.split()
        col = parts[0]
        if col in allowed_cols:
            direction = "DESC" if len(parts) > 1 and parts[1].upper() == "DESC" else "ASC"
            sql += f" ORDER BY {col} {direction}"

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    if offset:
        sql += " OFFSET ?"
        params.append(offset)

    return sql, params
