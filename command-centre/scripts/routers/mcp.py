"""/api/mcp, /api/mcp/{server}/tools, /api/mcp/sync, /api/mcp/measure."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

import db
import mcp_analyzer
from helpers import stats, timerange

router = APIRouter()


@router.get("/api/mcp")
async def mcp_servers(range: str = "7d") -> dict[str, Any]:
    """Per-server totals + avg latency + p95. Computes live from
    otel_events + tool_calls rather than reading mcp_stats, so numbers
    are always fresh."""
    pred_o, po = timerange.sql_predicate(range, "timestamp")
    pred_t, pt = timerange.sql_predicate(range, "ts")
    per_server: dict[str, dict[str, Any]] = {}

    with db.connect() as conn:
        for r in conn.execute(
            f"""
            SELECT mcp_server_name, mcp_tool_name, tool_duration_ms,
                   COALESCE(tool_success, 1) AS ok
            FROM otel_events
            WHERE mcp_server_name IS NOT NULL AND {pred_o}
            """,
            po,
        ):
            srv = r["mcp_server_name"]
            b = per_server.setdefault(srv, {
                "server": srv, "tools": set(), "durations": [], "errors": 0, "calls": 0,
            })
            b["tools"].add(r["mcp_tool_name"])
            b["calls"] += 1
            if r["tool_duration_ms"] is not None:
                b["durations"].append(r["tool_duration_ms"])
            if r["ok"] == 0:
                b["errors"] += 1

        for r in conn.execute(
            f"""
            SELECT tool_name, duration_ms, error FROM tool_calls
            WHERE tool_name LIKE 'mcp\\_\\_%\\_\\_%' ESCAPE '\\' AND {pred_t}
            """,
            pt,
        ):
            parts = (r["tool_name"] or "").split("__")
            if len(parts) < 3:
                continue
            srv, tool = parts[1], "__".join(parts[2:])
            b = per_server.setdefault(srv, {
                "server": srv, "tools": set(), "durations": [], "errors": 0, "calls": 0,
            })
            b["tools"].add(tool)
            b["calls"] += 1
            if r["duration_ms"] is not None:
                b["durations"].append(r["duration_ms"])
            if r["error"]:
                b["errors"] += 1

    items = []
    for srv, b in per_server.items():
        s = stats.summarize_durations(b["durations"])
        avg = (sum(b["durations"]) / len(b["durations"])) if b["durations"] else None
        items.append({
            "server": srv,
            "tool_count": len(b["tools"]),
            "calls": b["calls"],
            "errors": b["errors"],
            "error_rate": (b["errors"] / b["calls"]) if b["calls"] else 0,
            "avg_ms": int(avg) if avg is not None else None,
            "p50_ms": s["p50_ms"], "p95_ms": s["p95_ms"], "max_ms": s["max_ms"],
        })
    items.sort(key=lambda x: (x["p95_ms"] or 0), reverse=True)
    return {"range": timerange.normalize(range), "items": items}


@router.get("/api/mcp/{server}/tools")
async def mcp_tools(server: str, range: str = "7d") -> dict[str, Any]:
    pred_o, po = timerange.sql_predicate(range, "timestamp")
    pred_t, pt = timerange.sql_predicate(range, "ts")

    per_tool: dict[str, dict[str, Any]] = {}

    with db.connect() as conn:
        # OTEL — precise
        for r in conn.execute(
            f"""
            SELECT mcp_tool_name, tool_duration_ms,
                   COALESCE(tool_success, 1) AS ok
            FROM otel_events
            WHERE mcp_server_name = ? AND {pred_o}
            """,
            [server, *po],
        ):
            tname = r["mcp_tool_name"] or "?"
            b = per_tool.setdefault(tname, {"durations": [], "errors": 0, "calls": 0})
            b["calls"] += 1
            if r["tool_duration_ms"] is not None:
                b["durations"].append(r["tool_duration_ms"])
            if r["ok"] == 0:
                b["errors"] += 1
        # JSONL fallback
        prefix = f"mcp__{server}__"
        for r in conn.execute(
            f"""
            SELECT tool_name, duration_ms, error FROM tool_calls
            WHERE tool_name LIKE ? AND {pred_t}
            """,
            [prefix + "%", *pt],
        ):
            tname = (r["tool_name"] or "")[len(prefix):] or "?"
            b = per_tool.setdefault(tname, {"durations": [], "errors": 0, "calls": 0})
            b["calls"] += 1
            if r["duration_ms"] is not None:
                b["durations"].append(r["duration_ms"])
            if r["error"]:
                b["errors"] += 1

    items = []
    for tool, b in per_tool.items():
        s = stats.summarize_durations(b["durations"])
        items.append({
            "tool": tool,
            "calls": b["calls"],
            "errors": b["errors"],
            "error_rate": (b["errors"] / b["calls"]) if b["calls"] else 0,
            "p50_ms": s["p50_ms"], "p95_ms": s["p95_ms"], "max_ms": s["max_ms"],
        })
    items.sort(key=lambda x: (x["p95_ms"] or 0), reverse=True)
    return {"range": timerange.normalize(range), "server": server, "items": items}


@router.post("/api/mcp/sync")
async def mcp_sync() -> dict[str, Any]:
    result = await asyncio.to_thread(mcp_analyzer.rebuild_mcp_stats)
    return {"ok": True, **result}


@router.post("/api/mcp/measure")
async def mcp_measure() -> dict[str, Any]:
    result = await asyncio.to_thread(mcp_analyzer.measure_schemas)
    return {"ok": True, **result}
