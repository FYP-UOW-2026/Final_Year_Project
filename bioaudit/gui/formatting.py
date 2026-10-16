"""Small text formatters shared by the desktop app's tabs.

Kept apart from app.py because team.py needs them too, and team.py cannot import from
app.py (app.py imports team).
"""

from __future__ import annotations

import datetime


def format_when(value) -> str:
    """A server timestamp as '13 Oct 2026, 18:22', in local time.

    The server returns Firestore timestamps exactly as stored, which arrive as
    {"_seconds": ..., "_nanoseconds": ...}. Slicing that like a date string is what
    produced labels like "{'_seconds': 179188". An ISO string is accepted too, so this
    keeps working if the server is later changed to send one.
    """
    if isinstance(value, dict):
        seconds = value.get("_seconds", value.get("seconds"))
        if seconds is None:
            return "unknown time"
        moment = datetime.datetime.fromtimestamp(int(seconds))
    elif isinstance(value, str) and value:
        try:
            moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:16]
        if moment.tzinfo is not None:
            moment = moment.astimezone()
    else:
        return "unknown time"
    return moment.strftime("%d %b %Y, %H:%M")


def severity_summary(counts: dict) -> str:
    """Finding counts in words, e.g. '2 High, 1 Low', rather than 'H2 L1'."""
    parts = [f"{counts[name]} {name.capitalize()}"
             for name in ("critical", "high", "medium", "low", "info")
             if counts.get(name)]
    return ", ".join(parts) or "no findings"
