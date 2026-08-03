"""Database initialization, migration, and management."""
import sqlite3
from pathlib import Path

from scraper.config import DB_PATH, DATA_DIR, SOURCE_DB_NAMES

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
    # Migration 5: Create sources table to track all URLs ever found
    """
    CREATE TABLE IF NOT EXISTS sources (
        url TEXT PRIMARY KEY,
        source_site TEXT,
        first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sources_site ON sources(source_site);
    CREATE INDEX IF NOT EXISTS idx_sources_first_seen ON sources(first_seen);

    -- Add UNIQUE constraint on hin separately (SQLite doesn't support ALTER TABLE ADD CONSTRAINT)
    -- Instead we'll use it in application logic
    """,
]

CAR_MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS cars (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vin TEXT,
        year INTEGER,
        make TEXT,
        model TEXT,
        trim TEXT,
        exterior_color TEXT,
        engine TEXT,
        transmission TEXT,
        fuel_type TEXT,
        length TEXT,
        width TEXT,
        height TEXT,
        wheel_size TEXT,
        tire_size TEXT,
        interior_color TEXT,
        interior_trim TEXT,
        source_site TEXT,
        url TEXT,
        scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_cars_vin ON cars(vin);
    CREATE INDEX IF NOT EXISTS idx_cars_ymm ON cars(year, make, model);

    CREATE TABLE IF NOT EXISTS progress (
        url TEXT PRIMARY KEY,
        status TEXT CHECK(status IN ('pending','done','failed')) DEFAULT 'pending',
        error_msg TEXT,
        attempts INTEGER DEFAULT 0,
        last_attempt_at DATETIME
    );
    CREATE INDEX IF NOT EXISTS idx_progress_status ON progress(status);

    CREATE TABLE IF NOT EXISTS specs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER,
        make TEXT,
        model TEXT,
        trim TEXT,
        length TEXT,
        width TEXT,
        height TEXT,
        wheel_size TEXT,
        tire_size TEXT,
        source_site TEXT,
        scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(year, make, model, trim)
    );
    CREATE INDEX IF NOT EXISTS idx_specs_ymm ON specs(year, make, model);
    """,
]


def _db_path_for_source(source: str | None = None) -> Path:
    """Return the DB path for a source, defaulting to the legacy boats DB."""
    if source and source in SOURCE_DB_NAMES:
        return DATA_DIR / SOURCE_DB_NAMES[source]
    return DB_PATH


def init_db(source: str | None = None) -> sqlite3.Connection:
    """Initialize the SQLite database with schema and migrations."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _db_path_for_source(source)
    conn = sqlite3.connect(str(db_path))
    _run_migrations(conn, source=source)
    return conn


def _run_migrations(conn: sqlite3.Connection, source: str | None = None) -> None:
    """Run migrations that haven't been applied yet."""
    is_car = source in SOURCE_DB_NAMES
    migrations = CAR_MIGRATIONS if is_car else MIGRATIONS

    # Create migration tracking table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS __migrations (
            version INTEGER PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for i, sql in enumerate(migrations, start=1):
        cursor = conn.execute(
            "SELECT 1 FROM __migrations WHERE version = ?", (i,)
        )
        if cursor.fetchone() is None:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO __migrations (version) VALUES (?)", (i,)
            )
            conn.commit()
            print(f"[db] Applied migration {i} for {source or 'boats'}")


def get_db(source: str | None = None) -> sqlite3.Connection:
    """Get a database connection (initializing if needed)."""
    db_path = _db_path_for_source(source)
    if not db_path.exists():
        return init_db(source)
    conn = sqlite3.connect(str(db_path))
    _run_migrations(conn, source=source)
    return conn
