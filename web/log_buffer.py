"""Thread-safe ring buffer for capturing scraper logs + rotating file handler."""
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from scraper.config import DATA_DIR

LOG_PATH = DATA_DIR / "boats_scraper.log"


class LogBuffer:
    """A thread-safe ring buffer that stores the last N log lines."""

    def __init__(self, max_lines: int = 1000):
        self._max_lines = max_lines
        self._buffer: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._subscribers: list[threading.Condition] = []
        self._file_logger: logging.Logger | None = None

    def set_file_logger(self, logger: logging.Logger) -> None:
        """Attach a file logger so writes also hit disk."""
        self._file_logger = logger

    def write(self, line: str) -> None:
        """Append a log line, notify subscribers, and write to disk."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        with self._lock:
            self._buffer.append(entry)
            for cond in self._subscribers:
                cond.notify_all()
        # Write to file outside the lock so slow I/O doesn't block SSE
        if self._file_logger:
            self._file_logger.info(line)

    def tail(self, n: int = 100) -> list[str]:
        """Return the last N log lines."""
        with self._lock:
            return list(self._buffer)[-n:]

    def subscribe(self) -> threading.Condition:
        """Create a condition variable for waiting on new log lines."""
        cond = threading.Condition(self._lock)
        with self._lock:
            self._subscribers.append(cond)
        return cond

    def unsubscribe(self, cond: threading.Condition) -> None:
        """Remove a subscriber condition variable."""
        with self._lock:
            if cond in self._subscribers:
                self._subscribers.remove(cond)


def setup_logging(buffer: LogBuffer) -> logging.Logger:
    """Configure rotating file logger and bridge it to the in-memory buffer.

    Returns the file logger so it can be attached to the buffer.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("boat_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers = []  # avoid duplicate handlers on reload

    handler = RotatingFileHandler(
        str(LOG_PATH),
        maxBytes=50 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    buffer.set_file_logger(logger)
    logger.info("Logging initialized — rotating at 50MB")
    return logger
