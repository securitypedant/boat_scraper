"""CLI entrypoint for querying boat data."""
import argparse
import sqlite3
import sys

from query.config import DB_PATH
from query.database import build_query, get_db
from query.formatters import format_csv, format_json, format_table


def run_query(
    year=None,
    make=None,
    boat_class=None,
    engine=None,
    min_length=None,
    max_length=None,
    has_field=None,
    missing_field=None,
    order_by="scraped_at DESC",
    limit=20,
    offset=0,
    fmt="table",
    count_only=False,
):
    if not DB_PATH.exists():
        print(f"[query] Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    db = get_db()

    if count_only:
        sql = "SELECT COUNT(*) FROM boats WHERE 1=1"
        params = []
        if year is not None:
            sql += " AND year = ?"; params.append(year)
        if make is not None:
            sql += " AND make LIKE ?"; params.append(f"%{make}%")
        if boat_class is not None:
            sql += " AND class LIKE ?"; params.append(f"%{boat_class}%")
        if engine is not None:
            sql += " AND engine LIKE ?"; params.append(f"%{engine}%")
        if has_field is not None:
            sql += f" AND {has_field} IS NOT NULL"
        if missing_field is not None:
            sql += f" AND {missing_field} IS NULL"
        cursor = db.execute(sql, params)
        total = cursor.fetchone()[0]
        print(total)
        db.close()
        return

    sql, params = build_query(
        year=year,
        make=make,
        boat_class=boat_class,
        engine=engine,
        min_length=min_length,
        max_length=max_length,
        has_field=has_field,
        missing_field=missing_field,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )

    cursor = db.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    db.close()

    if fmt == "json":
        print(format_json(rows))
    elif fmt == "csv":
        format_csv(rows)
    else:
        print(format_table(rows))


def main():
    parser = argparse.ArgumentParser(description="Query scraped boat data from SQLite")
    parser.add_argument("--limit", type=int, default=20, help="Max rows to return (default: 20)")
    parser.add_argument("--offset", type=int, default=0, help="Row offset for pagination")
    parser.add_argument("--year", type=int, default=None, help="Filter by year")
    parser.add_argument("--make", default=None, help="Filter by make (partial match)")
    parser.add_argument("--class", dest="boat_class", default=None, help="Filter by class (partial match)")
    parser.add_argument("--engine", default=None, help="Filter by engine (partial match)")
    parser.add_argument("--min-length", type=int, default=None, help="Minimum length (numeric, ft)")
    parser.add_argument("--max-length", type=int, default=None, help="Maximum length (numeric, ft)")
    parser.add_argument("--has-field", default=None, help="Only rows where FIELD is not NULL (e.g. engine_hours)")
    parser.add_argument("--missing-field", default=None, help="Only rows where FIELD is NULL")
    parser.add_argument("--order-by", default="scraped_at DESC", help="Sort order (default: scraped_at DESC)")
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table", help="Output format")
    parser.add_argument("--count", action="store_true", help="Return count only")

    args = parser.parse_args()

    run_query(
        year=args.year,
        make=args.make,
        boat_class=args.boat_class,
        engine=args.engine,
        min_length=args.min_length,
        max_length=args.max_length,
        has_field=args.has_field,
        missing_field=args.missing_field,
        order_by=args.order_by,
        limit=args.limit,
        offset=args.offset,
        fmt=args.format,
        count_only=args.count,
    )


if __name__ == "__main__":
    main()
