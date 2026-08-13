"""Database access helpers for querying both boats and car databases."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from query.config import DB_PATH
from scraper.config import DATA_DIR, SOURCE_DB_NAMES


# Map logical database names to file paths and table metadata.
DATABASES: dict[str, Any] = {
    "boats": {
        "path": DB_PATH,
        "table": "boats",
        "source_col": "source",
        "display": "Boats",
        "order_by": "scraped_at DESC",
        "allowed_cols": {
            "id", "url", "year", "name", "make", "length", "class", "engine",
            "total_power", "engine_hours", "model", "capacity", "hin", "source", "scraped_at",
        },
    },
}

for source in SOURCE_DB_NAMES:
    key = source.lower()
    DATABASES[key] = {
        "path": DATA_DIR / SOURCE_DB_NAMES[source],
        "table": "cars",
        "source_col": "source_site",
        "display": source,
        "order_by": "scraped_at DESC",
        "allowed_cols": {
            "id", "vin", "year", "make", "model", "trim", "exterior_color", "engine",
            "transmission", "fuel_type", "length", "width", "height", "wheel_size", "tire_size",
            "interior_color", "interior_trim", "source_site", "url", "scraped_at",
        },
    }


def list_databases() -> list[dict[str, str]]:
    """Return the set of queryable databases."""
    return [{"key": k, "display": v["display"]} for k, v in DATABASES.items()]


def get_db(db_name: str = "boats") -> sqlite3.Connection:
    """Open a connection to the requested database."""
    if db_name not in DATABASES:
        raise ValueError(f"Unknown database: {db_name}")
    path = str(DATABASES[db_name]["path"])
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _build_where(
    sql: str,
    params: list,
    table_info: dict,
    *,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    hin: str | None = None,
    boat_class: str | None = None,
    source: str | None = None,
    search: str | None = None,
    has_field: str | None = None,
    missing_field: str | None = None,
) -> tuple[str, list]:
    """Append WHERE conditions to sql and extend params for the given table."""
    is_boats = table_info["table"] == "boats"

    if year is not None:
        sql += " AND year = ?"
        params.append(year)

    if make is not None:
        sql += " AND make LIKE ?"
        params.append(f"%{make}%")

    if model is not None:
        sql += " AND model LIKE ?"
        params.append(f"%{model}%")

    if engine is not None:
        sql += " AND engine LIKE ?"
        params.append(f"%{engine}%")

    if source is not None:
        sql += f" AND {table_info['source_col']} = ?"
        params.append(source)

    if is_boats:
        if hin is not None:
            sql += " AND hin LIKE ?"
            params.append(f"%{hin}%")
        if boat_class is not None:
            sql += " AND class LIKE ?"
            params.append(f"%{boat_class}%")
        if search is not None:
            sql += " AND (name LIKE ? OR make LIKE ? OR model LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like, like])
        if has_field is not None and has_field in table_info["allowed_cols"]:
            sql += f" AND {has_field} IS NOT NULL AND {has_field} != ''"
        if missing_field is not None and missing_field in table_info["allowed_cols"]:
            sql += f" AND ({missing_field} IS NULL OR {missing_field} = '')"
    else:
        # Car databases
        if hin is not None:
            sql += " AND vin LIKE ?"
            params.append(f"%{hin}%")
        if boat_class is not None:
            sql += " AND (trim LIKE ? OR exterior_color LIKE ?)"
            like = f"%{boat_class}%"
            params.extend([like, like])
        if search is not None:
            sql += " AND (vin LIKE ? OR make LIKE ? OR model LIKE ? OR trim LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if has_field is not None and has_field in table_info["allowed_cols"]:
            sql += f" AND {has_field} IS NOT NULL AND {has_field} != ''"
        if missing_field is not None and missing_field in table_info["allowed_cols"]:
            sql += f" AND ({missing_field} IS NULL OR {missing_field} = '')"

    return sql, params


def build_query(
    db_name: str = "boats",
    *,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    hin: str | None = None,
    boat_class: str | None = None,
    source: str | None = None,
    search: str | None = None,
    has_field: str | None = None,
    missing_field: str | None = None,
    order_by: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[str, list]:
    """Build a parameterized SELECT query for the requested database."""
    info = DATABASES.get(db_name)
    if info is None:
        raise ValueError(f"Unknown database: {db_name}")

    allowed_cols = info["allowed_cols"]
    cols = ", ".join(allowed_cols)
    sql = f"SELECT {cols} FROM {info['table']} WHERE 1=1"
    params: list = []

    sql, params = _build_where(
        sql, params, info,
        year=year, make=make, model=model, engine=engine,
        hin=hin, boat_class=boat_class, source=source, search=search,
        has_field=has_field, missing_field=missing_field,
    )

    effective_order = order_by or info["order_by"]
    parts = effective_order.split()
    col = parts[0]
    if col in allowed_cols:
        direction = "DESC" if len(parts) > 1 and parts[1].upper() == "DESC" else "ASC"
        sql += f" ORDER BY {col} {direction}"
    else:
        # Fallback to default ordering if an invalid column is supplied.
        fallback = info["order_by"].split()
        direction = "DESC" if len(fallback) > 1 and fallback[1].upper() == "DESC" else "ASC"
        sql += f" ORDER BY {fallback[0]} {direction}"

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    if offset:
        sql += " OFFSET ?"
        params.append(offset)

    return sql, params


def build_count_query(
    db_name: str = "boats",
    *,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    hin: str | None = None,
    boat_class: str | None = None,
    source: str | None = None,
    search: str | None = None,
    has_field: str | None = None,
    missing_field: str | None = None,
) -> tuple[str, list]:
    """Build a COUNT query matching the same filters."""
    info = DATABASES.get(db_name)
    if info is None:
        raise ValueError(f"Unknown database: {db_name}")

    sql = f"SELECT COUNT(*) FROM {info['table']} WHERE 1=1"
    params: list = []

    sql, params = _build_where(
        sql, params, info,
        year=year, make=make, model=model, engine=engine,
        hin=hin, boat_class=boat_class, source=source, search=search,
        has_field=has_field, missing_field=missing_field,
    )

    return sql, params


def build_delete_query(
    db_name: str = "boats",
    *,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    hin: str | None = None,
    boat_class: str | None = None,
    source: str | None = None,
    search: str | None = None,
    has_field: str | None = None,
    missing_field: str | None = None,
) -> tuple[str, list]:
    """Build a parameterized DELETE query for the requested database."""
    info = DATABASES.get(db_name)
    if info is None:
        raise ValueError(f"Unknown database: {db_name}")

    sql = f"DELETE FROM {info['table']} WHERE 1=1"
    params: list = []

    sql, params = _build_where(
        sql, params, info,
        year=year, make=make, model=model, engine=engine,
        hin=hin, boat_class=boat_class, source=source, search=search,
        has_field=has_field, missing_field=missing_field,
    )

    return sql, params
