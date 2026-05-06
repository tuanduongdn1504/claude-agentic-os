"""Percentile + sparkline helpers. SQLite has no native percentile(), so
we compute in Python on grouped rows."""
from __future__ import annotations

from typing import Iterable


def percentile(sorted_values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile. `sorted_values` MUST be sorted asc.
    pct in [0, 100]."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def summarize_durations(durations_ms: Iterable[int | None]) -> dict:
    """Return p50/p95/max/count over an iterable of int|None durations."""
    vals = sorted(d for d in durations_ms if d is not None and d >= 0)
    n = len(vals)
    return {
        "count": n,
        "p50_ms": int(percentile(vals, 50)) if n else None,
        "p95_ms": int(percentile(vals, 95)) if n else None,
        "max_ms": vals[-1] if n else None,
    }
