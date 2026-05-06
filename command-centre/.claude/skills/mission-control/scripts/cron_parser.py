"""parse_cron_simple — next-run computation for the subset of cron that
the ScheduleComposer UI can generate.

Supported fields (5-field cron `M H DOM MON DOW`):
  minute:      0-59, `*`, `*/N`, single comma-separated integer list
  hour:        0-23, `*`, `*/N`, single comma-separated integer list
  day-of-week: `*`, `N`, `A-B` range, `A,B,C` list   (Mon=1..Sun=0 cron, but
               the UI uses the Python convention Mon=0..Sun=6; we accept
               both via normalize_dow)
  day-of-month, month: only `*` supported

Returns the next `datetime` ≥ `from_dt` matching the expression, in local
time. Caller is responsible for converting to the DB's 'YYYY-MM-DD HH:MM:SS'
format (we return a datetime, not a string).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable


def _expand_field(token: str, lo: int, hi: int) -> list[int]:
    token = token.strip()
    if token == "*":
        return list(range(lo, hi + 1))
    if token.startswith("*/"):
        step = int(token[2:])
        return [n for n in range(lo, hi + 1) if (n - lo) % step == 0]
    out: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(n for n in out if lo <= n <= hi)


def _normalize_dow(vals: Iterable[int]) -> set[int]:
    """Cron DOW: 0 or 7 = Sunday, 1=Mon ... 6=Sat.
    Python weekday(): Mon=0 ... Sun=6.
    We return Python-convention values so callers can compare to dt.weekday()."""
    out: set[int] = set()
    for v in vals:
        # Cron convention lookup:
        # 0/7 -> Sun (Python 6)
        # 1..6 -> Mon..Sat (Python 0..5)
        if v in (0, 7):
            out.add(6)
        elif 1 <= v <= 6:
            out.add(v - 1)
    return out


def next_run(expr: str, from_dt: datetime | None = None) -> datetime:
    """Return the next datetime matching `expr`, at minute granularity.
    Raises ValueError on unsupported fields."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"cron must be 5 fields, got {len(parts)}: {expr!r}")
    m, h, dom, mon, dow = parts
    if dom != "*" or mon != "*":
        raise ValueError("day-of-month and month must be '*' in this simple parser")

    minutes = set(_expand_field(m, 0, 59))
    hours = set(_expand_field(h, 0, 23))
    dows = _normalize_dow(_expand_field(dow, 0, 7))

    base = (from_dt or datetime.now()).replace(second=0, microsecond=0)
    # Start from the next minute to avoid double-materializing the current one.
    candidate = base + timedelta(minutes=1)
    # Look up to ~2 weeks ahead — safety cap.
    for _ in range(60 * 24 * 14):
        if (candidate.minute in minutes
                and candidate.hour in hours
                and candidate.weekday() in dows):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"no match within 14 days for {expr!r}")


# Compatibility alias matching prompt vocabulary.
parse_cron_simple = next_run
