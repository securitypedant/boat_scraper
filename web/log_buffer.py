"""Thread-safe ring buffer for capturing scraper logs."""
import threading
from collections import deque
from datetime import datetime, timezone


class LogBuffer:
    """A thread-safe ring buffer that stores the last N log lines."""

    def __init__(self, max_lines: int = 1000):
        self._max_lines = max_lines
        self._buffer: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._subscribers: list[threading.Condition] = []

    def write(self, line: str) -> None:
        """Append a log line and notify all waiting subscribers."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        with self._lock:
            self._buffer.append(entry)
            for cond in self._subscribers:
                cond.notify_all()

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
