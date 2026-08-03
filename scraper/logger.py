"""Lightweight log-level helper that both CLI prints and web log buffer honor."""
import json
import os
from enum import IntEnum
from pathlib import Path

from scraper.config import DATA_DIR


class LogLevel(IntEnum):
    """Log levels, higher = quieter."""

    DEBUG = 0
    STANDARD = 1  # successes + failures + warnings
    QUIET = 2  # failures + warnings only


SETTINGS_FILE = DATA_DIR / "log_level.json"
_ENV_KEY = "VEHICLE_SCRAPER_LOG_LEVEL"


_LEVEL_NAMES = {
    "DEBUG": LogLevel.DEBUG,
    "STANDARD": LogLevel.STANDARD,
    "QUIET": LogLevel.QUIET,
}


def get_log_level() -> LogLevel:
    """Return the current log level from env, settings file, or default."""
    env = os.environ.get(_ENV_KEY, "").upper()
    if env in _LEVEL_NAMES:
        return _LEVEL_NAMES[env]

    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text())
            name = str(data.get("level", "STANDARD")).upper()
            if name in _LEVEL_NAMES:
                return _LEVEL_NAMES[name]
    except Exception:
        pass

    return LogLevel.STANDARD


def set_log_level(level: LogLevel | str) -> None:
    """Persist the log level to the settings file."""
    if isinstance(level, LogLevel):
        name = level.name
    else:
        name = str(level).upper()
        level = _LEVEL_NAMES.get(name, LogLevel.STANDARD)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"level": name}))
    os.environ[_ENV_KEY] = name


def log(level: LogLevel | str, message: str) -> None:
    """Print a message if its level is at or above the current threshold.

    Higher levels are more important and always pass.
    """
    if isinstance(level, str):
        level = _LEVEL_NAMES.get(level.upper(), LogLevel.STANDARD)
    if level.value >= get_log_level().value:
        print(message)
