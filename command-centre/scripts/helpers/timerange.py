"""?range=today|7d|30d → SQL predicate.

Use local-time bucketing everywhere (UTC breaks evening sessions —
see schema-recon.md). The predicate is applied to a timestamp column
whose name is injected; caller passes e.g. `started_at` or `ts`.
"""
from __future__ import annotations

from typing import Any


ALLOWED = ("today", "7d", "30d", "all")


def normalize(r: str | None) -> str:
    if not r:
        return "7d"
    r = r.lower()
    return r if r in ALLOWED else "7d"


def sql_predicate(range_: str | None, column: str = "started_at") -> tuple[str, list[Any]]:
    """Return ('WHERE fragment', params).

    Example:
        frag, params = sql_predicate("today", "started_at")
        cur.execute(f"SELECT ... FROM sessions WHERE {frag}", params)
    """
    r = normalize(range_)
    if r == "today":
        return f"DATE({column}, 'localtime') = DATE('now', 'localtime')", []
    if r == "7d":
        return f"{column} >= datetime('now', '-7 days')", []
    if r == "30d":
        return f"{column} >= datetime('now', '-30 days')", []
    return "1=1", []


def days_in_range(range_: str | None) -> int:
    r = normalize(range_)
    return {"today": 1, "7d": 7, "30d": 30, "all": 365}[r]
