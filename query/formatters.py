"""Formatting utilities for query results."""
import csv
import json
import sys
from typing import Any


def format_table(rows: list[dict[str, Any]]) -> str:
    """Render rows as an ASCII table."""
    if not rows:
        return "No results."

    cols = list(rows[0].keys())
    # Calculate widths
    widths = {c: len(c) for c in cols}
    for row in rows:
        for c in cols:
            val = str(row.get(c) or "")
            widths[c] = max(widths[c], len(val))

    # Build separator
    sep = "+-" + "-+".join("-" * widths[c] for c in cols) + "-+"

    # Build header
    header = "| " + " | ".join(c.ljust(widths[c]) for c in cols) + " |"

    lines = [sep, header, sep]
    for row in rows:
        line = "| " + " | ".join(str(row.get(c) or "").ljust(widths[c]) for c in cols) + " |"
        lines.append(line)
    lines.append(sep)

    return "\n".join(lines)


def format_json(rows: list[dict[str, Any]]) -> str:
    """Render rows as pretty-printed JSON."""
    return json.dumps(rows, indent=2, ensure_ascii=False, default=str)


def format_csv(rows: list[dict[str, Any]]) -> str:
    """Render rows as CSV."""
    if not rows:
        return ""
    cols = list(rows[0].keys())
    out = sys.stdout
    writer = csv.DictWriter(out, fieldnames=cols)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return ""
