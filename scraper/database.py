"""Database initialization, migration, and management."""
import sqlite3
from pathlib import Path

from scraper.config import DB_PATH, DATA_DIR

MIGRATIONS = [
    # Migration 1: Initial schema
    """
    CREATE TABLE IF NOT EXISTS boats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        year INTEGER,
        name TEXT,
        length TEXT,
        class TEXT,
        engine TEXT,
        total_power TEXT,
        engine_hours TEXT,
        model TEXT,
        capacity TEXT,
        scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_year_class ON boats(year, class);
    CREATE INDEX IF NOT EXISTS idx_name ON boats(name);

    CREATE TABLE IF NOT EXISTS progress (
        url TEXT PRIMARY KEY,
        status TEXT CHECK(status IN ('pending','done','failed')) DEFAULT 'pending',
        error_msg TEXT,
        attempts INTEGER DEFAULT 0,
        last_attempt_at DATETIME
    );
    CREATE INDEX IF NOT EXISTS idx_status ON progress(status);
    """,
    # Migration 2: Add manufacturers table and make column
    """
    CREATE TABLE IF NOT EXISTS manufacturers (
        mic TEXT PRIMARY KEY,
        company TEXT NOT NULL,
        city TEXT,
        state TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_manufacturer_company ON manufacturers(company);

    ALTER TABLE boats ADD COLUMN make TEXT;
    CREATE INDEX IF NOT EXISTS idx_make ON boats(make);
    """,
    # Migration 3: Add HIN column
    """
    ALTER TABLE boats ADD COLUMN hin TEXT;
    CREATE INDEX IF NOT EXISTS idx_hin ON boats(hin);
    """,
    # Migration 4: Add source column
    """
    ALTER TABLE boats ADD COLUMN source TEXT DEFAULT 'BoatTrader';
    CREATE INDEX IF NOT EXISTS idx_source ON boats(source);
    """,
]


def init_db() -> sqlite3.Connection:
    """Initialize the SQLite database with schema and migrations."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    _run_migrations(conn)
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run migrations that haven't been applied yet."""
    # Create migration tracking table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS __migrations (
            version INTEGER PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for i, sql in enumerate(MIGRATIONS, start=1):
        cursor = conn.execute(
            "SELECT 1 FROM __migrations WHERE version = ?", (i,)
        )
        if cursor.fetchone() is None:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO __migrations (version) VALUES (?)", (i,)
            )
            conn.commit()
            print(f"[db] Applied migration {i}")


def get_db() -> sqlite3.Connection:
    """Get a database connection (initializing if needed)."""
    if not DB_PATH.exists():
        return init_db()
    conn = sqlite3.connect(str(DB_PATH))
    _run_migrations(conn)
    return conn
