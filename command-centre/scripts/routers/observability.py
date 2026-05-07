"""/api/usage/*, /api/tools/*, /api/hooks/*, /api/sessions/outcomes,
   /api/sessions/by-project, /api/activity/productivity.

All endpoints accept ?range=today|7d|30d and use local-time day
bucketing per prompt spec."""
from __future__ import annotations

import collections
from typing import Any, Optional

from fastapi import APIRouter, Query

import db
from helpers import stats, timerange

router = APIRouter()


# ---------------------------------------------------------------------------
# /api/usage/tokens — daily breakdown by model + source
# ---------------------------------------------------------------------------

@router.get("/api/usage/tokens")
async def usage_tokens(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "date")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT date, model, source,
                   input_tokens, output_tokens, cache_read_tokens, cache_create_tokens
            FROM token_usage
            WHERE {pred}
            ORDER BY date, model
            """,
            params,
        ).fetchall()

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "total": 0}
    for r in rows:
        totals["input"] += r["input_tokens"] or 0
        totals["output"] += r["output_tokens"] or 0
        totals["cache_read"] += r["cache_read_tokens"] or 0
        totals["cache_create"] += r["cache_create_tokens"] or 0
    totals["total"] = sum(totals.values()) - totals["total"]  # defensive; total is recomputed below
    totals["total"] = totals["input"] + totals["output"] + totals["cache_read"] + totals["cache_create"]

    return {
        "range": timerange.normalize(range),
        "daily": [dict(r) for r in rows],
        "totals": totals,
    }


# ---------------------------------------------------------------------------
# /api/usage/cache — hit rate + daily trend + low-sample badge
# ---------------------------------------------------------------------------

@router.get("/api/usage/cache")
async def usage_cache(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "date")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT date,
                   SUM(input_tokens) AS input_t,
                   SUM(output_tokens) AS output_t,
                   SUM(cache_read_tokens) AS cr,
                   SUM(cache_create_tokens) AS cc
            FROM token_usage
            WHERE {pred}
            GROUP BY date
            ORDER BY date
            """,
            params,
        ).fetchall()

    daily = []
    total_cr = 0
    total_billable = 0
    for r in rows:
        cr = r["cr"] or 0
        inp = r["input_t"] or 0
        cc = r["cc"] or 0
        billable = inp + cr + cc
        total_cr += cr
        total_billable += billable
        hit_rate = (cr / billable) if billable > 0 else None
        daily.append({
            "date": r["date"],
            "cache_read_tokens": cr,
            "input_tokens": inp,
            "cache_create_tokens": cc,
            "billable_tokens": billable,
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        })

    overall = (total_cr / total_billable) if total_billable > 0 else None
    return {
        "range": timerange.normalize(range),
        "overall_hit_rate": round(overall, 4) if overall is not None else None,
        "target_hit_rate": 0.70,
        "low_sample": total_billable < 10_000,
        "billable_tokens": total_billable,
        "daily": daily,
    }


# ---------------------------------------------------------------------------
# /api/sessions/outcomes — daily mutually-exclusive buckets
# ---------------------------------------------------------------------------

_OUTCOME_PRIORITY = ("errored", "rate_limited", "truncated", "unfinished", "ok")


def _classify_outcome(row) -> str:
    if row["is_error_any"] or row["error_count"]:
        return "errored"
    if row["rate_limit_hit"]:
        return "rate_limited"
    if row["stop_reason"] in ("max_tokens", "stop_sequence") and row["stop_reason"] == "max_tokens":
        return "truncated"
    if row["ended_at"] is None:
        return "unfinished"
    return "ok"


@router.get("/api/sessions/outcomes")
async def sessions_outcomes(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "started_at")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DATE(started_at, 'localtime') AS day,
                   is_error_any, error_count, rate_limit_hit, stop_reason, ended_at
            FROM sessions
            WHERE {pred} AND (model IS NULL OR model NOT LIKE '<%')
            """,
            params,
        ).fetchall()

    buckets: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {k: 0 for k in _OUTCOME_PRIORITY}
    )
    for r in rows:
        day = r["day"]
        if not day:
            continue
        buckets[day][_classify_outcome(r)] += 1

    daily = [
        {"date": day, **counts, "total": sum(counts.values())}
        for day, counts in sorted(buckets.items())
    ]
    totals = {k: sum(c[k] for c in buckets.values()) for k in _OUTCOME_PRIORITY}
    totals["total"] = sum(totals.values())
    return {"range": timerange.normalize(range), "daily": daily, "totals": totals,
            "buckets_priority": list(_OUTCOME_PRIORITY)}


# ---------------------------------------------------------------------------
# /api/tools/latency — p50/p95/max/error-rate/call-count, sort by p95 desc
# ---------------------------------------------------------------------------

@router.get("/api/tools/latency")
async def tools_latency(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "ts")
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT tool_name, duration_ms, error FROM tool_calls WHERE {pred}",
            params,
        ).fetchall()

    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = r["tool_name"] or "?"
        b = buckets.setdefault(name, {"durations": [], "errors": 0, "count": 0})
        b["count"] += 1
        if r["duration_ms"] is not None:
            b["durations"].append(r["duration_ms"])
        if r["error"]:
            b["errors"] += 1

    items = []
    for name, b in buckets.items():
        s = stats.summarize_durations(b["durations"])
        items.append({
            "tool_name": name,
            "call_count": b["count"],
            "error_count": b["errors"],
            "error_rate": round(b["errors"] / b["count"], 4) if b["count"] else 0,
            "p50_ms": s["p50_ms"],
            "p95_ms": s["p95_ms"],
            "max_ms": s["max_ms"],
        })
    items.sort(key=lambda x: (x["p95_ms"] or -1), reverse=True)
    return {"range": timerange.normalize(range), "items": items}


# ---------------------------------------------------------------------------
# /api/hooks/activity — pair hook_execution_start ↔ _complete
# ---------------------------------------------------------------------------

@router.get("/api/hooks/activity")
async def hooks_activity(range: str = "7d") -> dict[str, Any]:
    """Source: OTEL events first, JSONL system_events fallback for
    hook_success / hook errors. Pair by session FIFO, cap 60s outliers."""
    pred_otel, po = timerange.sql_predicate(range, "timestamp")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_name, session_id, timestamp, tool_duration_ms, tool_error
            FROM otel_events
            WHERE event_name IN ('hook_execution_start','hook_execution_complete')
              AND {pred_otel}
            ORDER BY session_id, timestamp
            """,
            po,
        ).fetchall()
        # JSONL-side fallback: count stop_hook_summary events as fires.
        pred_sys, ps = timerange.sql_predicate(range, "timestamp")
        sys_fires = conn.execute(
            f"SELECT COUNT(*) FROM system_events WHERE subtype='stop_hook_summary' AND {pred_sys}",
            ps,
        ).fetchone()[0]

    # Pair OTEL start/complete per session FIFO.
    per_session: dict[str, list] = collections.defaultdict(list)
    for r in rows:
        per_session[r["session_id"] or "?"].append(r)

    paired_durations: list[int] = []
    starts = 0
    completes = 0
    for sid, events in per_session.items():
        queue = []
        for e in events:
            if e["event_name"] == "hook_execution_start":
                starts += 1
                queue.append(e)
            else:
                completes += 1
                if queue:
                    queue.pop(0)  # FIFO pair
                    # Use the OTEL-reported duration if present.
                    d = e["tool_duration_ms"]
                    if d is not None and 0 <= d <= 60_000:
                        paired_durations.append(d)

    s = stats.summarize_durations(paired_durations)
    total_fires = starts or completes or sys_fires
    return {
        "range": timerange.normalize(range),
        "total_fires": total_fires,
        "starts": starts,
        "completes": completes,
        "jsonl_stop_hook_summaries": sys_fires,
        "paired_count": s["count"],
        "paired_p50_ms": s["p50_ms"],
        "paired_p95_ms": s["p95_ms"],
        "paired_max_ms": s["max_ms"],
    }


# ---------------------------------------------------------------------------
# /api/sessions/by-project — rollup by cwd
# ---------------------------------------------------------------------------

@router.get("/api/sessions/by-project")
async def sessions_by_project(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "started_at")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT cwd,
                   COUNT(*) AS sessions,
                   SUM(effective_tokens) AS effective_tokens,
                   SUM(cost_usd) AS cost_usd
            FROM sessions WHERE {pred} AND cwd IS NOT NULL
            GROUP BY cwd
            ORDER BY effective_tokens DESC
            """,
            params,
        ).fetchall()
        tool_rows = conn.execute(
            f"""
            SELECT s.cwd, COUNT(tc.tool_use_id) AS tool_count
            FROM tool_calls tc JOIN sessions s USING (session_id)
            WHERE {pred.replace('started_at', 's.started_at')} AND s.cwd IS NOT NULL
            GROUP BY s.cwd
            """,
            params,
        ).fetchall()
        tool_by_cwd = {r["cwd"]: r["tool_count"] for r in tool_rows}

    total_eff = sum((r["effective_tokens"] or 0) for r in rows) or 1
    items = []
    for r in rows:
        items.append({
            "cwd": r["cwd"],
            "sessions": r["sessions"],
            "effective_tokens": r["effective_tokens"] or 0,
            "cost_usd": r["cost_usd"] or 0,
            "tool_count": tool_by_cwd.get(r["cwd"], 0),
            "share_pct": round(100.0 * (r["effective_tokens"] or 0) / total_eff, 2),
        })
    return {"range": timerange.normalize(range), "items": items}


# ---------------------------------------------------------------------------
# /api/tools/agent-fanout — sessions that ran Agent
# ---------------------------------------------------------------------------

@router.get("/api/tools/agent-fanout")
async def agent_fanout(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "ts")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT tc.session_id,
                   COUNT(*) AS agent_calls,
                   s.title, s.cwd, s.started_at
            FROM tool_calls tc LEFT JOIN sessions s USING (session_id)
            WHERE tc.tool_name = 'Agent' AND {pred.replace('ts','tc.ts')}
            GROUP BY tc.session_id
            ORDER BY agent_calls DESC
            LIMIT 50
            """,
            params,
        ).fetchall()
    return {"range": timerange.normalize(range),
            "items": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# /api/tools/edit-decisions — accept/reject from tool_decision OTEL events
# ---------------------------------------------------------------------------

EDIT_FAMILY = ("Edit", "MultiEdit", "Write", "NotebookEdit")


@router.get("/api/tools/edit-decisions")
async def edit_decisions(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "timestamp")
    tools_placeholder = ",".join("?" * len(EDIT_FAMILY))
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT tool_name, decision, COUNT(*) AS n
            FROM otel_events
            WHERE event_name='tool_decision' AND tool_name IN ({tools_placeholder})
                  AND {pred}
            GROUP BY tool_name, decision
            """,
            [*EDIT_FAMILY, *params],
        ).fetchall()

    per_tool: dict[str, dict[str, Any]] = {t: {"accept": 0, "reject": 0, "other": 0}
                                           for t in EDIT_FAMILY}
    for r in rows:
        name = r["tool_name"]
        dec = (r["decision"] or "").lower()
        bucket = per_tool.setdefault(name, {"accept": 0, "reject": 0, "other": 0})
        if dec in ("accept", "accepted", "yes"):
            bucket["accept"] += r["n"]
        elif dec in ("reject", "rejected", "no"):
            bucket["reject"] += r["n"]
        else:
            bucket["other"] += r["n"]

    items = []
    total = 0
    for name, b in per_tool.items():
        n = b["accept"] + b["reject"] + b["other"]
        total += n
        items.append({
            "tool_name": name,
            "accept": b["accept"],
            "reject": b["reject"],
            "other": b["other"],
            "total": n,
            "accept_rate": (b["accept"] / n) if n else None,
        })
    return {
        "range": timerange.normalize(range),
        "items": items,
        "total": total,
        "low_sample": total < 10,
    }


# ---------------------------------------------------------------------------
# /api/activity/productivity — commit/PR/LoC OTEL counters
# ---------------------------------------------------------------------------

@router.get("/api/activity/productivity")
async def productivity(range: str = "7d") -> dict[str, Any]:
    pred, params = timerange.sql_predicate(range, "timestamp")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT metric_name, DATE(timestamp, 'localtime') AS day,
                   SUM(value) AS total
            FROM otel_metrics
            WHERE metric_name IN (
                'claude_code.commit.count',
                'claude_code.pull_request.count',
                'claude_code.lines_of_code.count'
            ) AND {pred}
            GROUP BY metric_name, day
            ORDER BY day
            """,
            params,
        ).fetchall()

    per_day: dict[str, dict[str, float]] = collections.defaultdict(
        lambda: {"commits": 0, "pull_requests": 0, "lines_of_code": 0}
    )
    totals = {"commits": 0, "pull_requests": 0, "lines_of_code": 0}
    for r in rows:
        day = r["day"]
        name = r["metric_name"]
        val = r["total"] or 0
        if name.endswith("commit.count"):
            per_day[day]["commits"] += val; totals["commits"] += val
        elif name.endswith("pull_request.count"):
            per_day[day]["pull_requests"] += val; totals["pull_requests"] += val
        elif name.endswith("lines_of_code.count"):
            per_day[day]["lines_of_code"] += val; totals["lines_of_code"] += val

    daily = [{"date": day, **vals} for day, vals in sorted(per_day.items())]
    return {
        "range": timerange.normalize(range),
        "daily": daily,
        "totals": totals,
        "note": "values are delta-counters — SUM(value) is correct",
    }


# ---------------------------------------------------------------------------
# /api/summary/sparklines — last 24 hourly buckets for the four KPIs
# ---------------------------------------------------------------------------

@router.get("/api/summary/sparklines")
async def summary_sparklines() -> dict[str, Any]:
    """24 hourly bins (oldest first) for the four KPI tiles.

    Buckets aligned to local-time hour boundaries. Sparklines aren't a
    real time-series so a missing hour reads as zero — fine because UI
    just plots them as a tiny line.
    """
    with db.connect() as conn:
        # Sessions — start hour of each session in the last 24h.
        sessions = conn.execute(
            """
            SELECT strftime('%Y-%m-%d %H', started_at, 'localtime') AS hr,
                   COUNT(*) AS n
            FROM sessions
            WHERE started_at >= datetime('now', '-24 hours')
              AND (model IS NULL OR model NOT LIKE '<%')
            GROUP BY hr
            """
        ).fetchall()
        tokens = conn.execute(
            """
            SELECT strftime('%Y-%m-%d %H', started_at, 'localtime') AS hr,
                   COALESCE(SUM(input_tokens + output_tokens), 0) AS n
            FROM sessions
            WHERE started_at >= datetime('now', '-24 hours')
              AND (model IS NULL OR model NOT LIKE '<%')
            GROUP BY hr
            """
        ).fetchall()
        cost = conn.execute(
            """
            SELECT strftime('%Y-%m-%d %H', started_at, 'localtime') AS hr,
                   COALESCE(SUM(cost_usd), 0) AS n
            FROM sessions
            WHERE started_at >= datetime('now', '-24 hours')
              AND (model IS NULL OR model NOT LIKE '<%')
            GROUP BY hr
            """
        ).fetchall()
        errors = conn.execute(
            """
            SELECT strftime('%Y-%m-%d %H', started_at, 'localtime') AS hr,
                   COALESCE(SUM(error_count), 0) AS n
            FROM sessions
            WHERE started_at >= datetime('now', '-24 hours')
              AND (model IS NULL OR model NOT LIKE '<%')
            GROUP BY hr
            """
        ).fetchall()

    # Build the 24 slot index from now backwards (local time).
    import datetime as _dt
    now = _dt.datetime.now()
    slots = [
        (now - _dt.timedelta(hours=h)).strftime("%Y-%m-%d %H")
        for h in range(23, -1, -1)
    ]

    def _series(rows: list) -> list[float]:
        idx = {r["hr"]: r["n"] for r in rows}
        return [float(idx.get(s, 0) or 0) for s in slots]

    return {
        "slots": slots,  # 24 hour-strings, oldest → newest
        "sessions": _series(sessions),
        "tokens":   _series(tokens),
        "cost_usd": _series(cost),
        "errors":   _series(errors),
    }


# ---------------------------------------------------------------------------
# /api/activity/heatmap — 7×24 grid (day-of-week × hour-of-day)
# ---------------------------------------------------------------------------

@router.get("/api/activity/heatmap")
async def activity_heatmap(range: str = "30d") -> dict[str, Any]:
    """Aggregate session count by (weekday, hour-of-day) for the range.

    weekday: 0=Sunday … 6=Saturday (sqlite strftime('%w')).
    hour: 0..23 local time.
    Returns a 7×24 grid (rows=weekday, cols=hour).
    """
    pred, params = timerange.sql_predicate(range, "started_at")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST(strftime('%w', started_at, 'localtime') AS INTEGER) AS dow,
                   CAST(strftime('%H', started_at, 'localtime') AS INTEGER) AS hr,
                   COUNT(*) AS n
            FROM sessions
            WHERE {pred}
              AND (model IS NULL OR model NOT LIKE '<%')
            GROUP BY dow, hr
            """,
            params,
        ).fetchall()

    # Note: `range` is the request parameter here, so don't call range() —
    # use a fixed 7-tuple to build the empty grid.
    grid: list[list[int]] = [[0] * 24 for _ in (0, 1, 2, 3, 4, 5, 6)]
    total = 0
    peak = 0
    for r in rows:
        dow = int(r["dow"]); hr = int(r["hr"]); n = int(r["n"])
        if 0 <= dow < 7 and 0 <= hr < 24:
            grid[dow][hr] = n
            total += n
            if n > peak:
                peak = n
    return {
        "range": timerange.normalize(range),
        "grid": grid,    # 7 rows × 24 cols
        "total": total,
        "peak": peak,    # largest single-cell count for color scaling
        "weekdays": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    }
