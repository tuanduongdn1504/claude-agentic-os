"""Command Centre — FastAPI server.

Mounts domain routers. /api/health and OTEL ingest stay in this file
because they're load-bearing for every other surface and share the
lifespan's app.state clock.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402
import sync_sessions  # noqa: E402
import sync_cowork  # noqa: E402
import sync_skills  # noqa: E402

from routers import (  # noqa: E402
    context, firehose, hitl, mcp, observability, schedules, sessions, skills, system, tasks,
)

SYNC_INTERVAL_SECONDS = 120


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.apply_migrations()

    stop_evt = asyncio.Event()
    app.state.stop_evt = stop_evt
    app.state.server_started_at = time.time()
    app.state.last_sync_tick = 0.0
    app.state.last_otel_event_at = 0.0

    task = asyncio.create_task(_sync_loop(app, stop_evt))
    try:
        yield
    finally:
        stop_evt.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _sync_loop(app: FastAPI, stop: asyncio.Event) -> None:
    await _run_sync(app)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=SYNC_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        await _run_sync(app)


async def _run_sync(app: FastAPI) -> None:
    try:
        await asyncio.to_thread(sync_sessions.run_sync, False, False)
        await asyncio.to_thread(sync_cowork.run_sync, False, False)
        app.state.last_sync_tick = time.time()
    except Exception as exc:
        print(f"[sync_loop] error: {exc!r}", file=sys.stderr)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Command Centre", lifespan=lifespan)

# Mount domain routers.
for r in (system.router, sessions.router, observability.router,
          mcp.router, skills.router, hitl.router,
          tasks.router, schedules.router, firehose.router, context.router):
    app.include_router(r)


# ---------------------------------------------------------------------------
# Health + summary + sync (kept here — they talk to app.state)
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    started = getattr(request.app.state, "server_started_at", time.time())
    return {"ok": True, "uptime_s": int(time.time() - started)}


@app.post("/api/sync")
async def manual_sync(request: Request) -> dict[str, Any]:
    stats_s = await asyncio.to_thread(sync_sessions.run_sync, False, False)
    stats_c = await asyncio.to_thread(sync_cowork.run_sync, False, False)
    stats_k = await asyncio.to_thread(sync_skills.run_sync, False)
    request.app.state.last_sync_tick = time.time()
    return {"sessions": stats_s, "cowork": stats_c, "skills": stats_k}


@app.get("/api/summary")
async def summary() -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS sessions_today,
              COALESCE(SUM(input_tokens + output_tokens), 0) AS effective_tokens_today,
              COALESCE(SUM(error_count), 0) AS errors_today,
              COALESCE(SUM(cost_usd), 0) AS cost_usd_today
            FROM sessions
            WHERE DATE(started_at, 'localtime') = DATE('now', 'localtime')
              AND (model IS NULL OR model NOT LIKE '<%')
            """
        ).fetchone()
        tools = conn.execute(
            """
            SELECT COUNT(*) AS tools_today FROM tool_calls
            WHERE DATE(ts, 'localtime') = DATE('now', 'localtime')
            """
        ).fetchone()
        # v0.6.0 — split today's cost by source.
        cbs_rows = conn.execute(
            """
            SELECT COALESCE(cost_source, 'unknown') AS src,
                   COALESCE(SUM(cost_usd), 0) AS cost
            FROM sessions
            WHERE DATE(started_at, 'localtime') = DATE('now', 'localtime')
              AND (model IS NULL OR model NOT LIKE '<%')
            GROUP BY COALESCE(cost_source, 'unknown')
            """
        ).fetchall()
    cost_by_source = {"api_pool": 0.0, "max_sub": 0.0, "unknown": 0.0}
    for r in cbs_rows:
        cost_by_source[r["src"]] = round(float(r["cost"] or 0.0), 6)
    return {
        "sessions_today": row["sessions_today"],
        "effective_tokens_today": row["effective_tokens_today"],
        "errors_today": row["errors_today"],
        "cost_usd_today": row["cost_usd_today"],
        "cost_by_source": cost_by_source,
        "tools_today": tools["tools_today"],
    }


# ---------------------------------------------------------------------------
# OTEL ingest
# ---------------------------------------------------------------------------

def _flatten_attrs(attr_list) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not attr_list:
        return out
    for a in attr_list:
        if not isinstance(a, dict):
            continue
        k = a.get("key"); v = a.get("value")
        if not k or not isinstance(v, dict):
            continue
        for vk in ("stringValue", "boolValue", "doubleValue"):
            if vk in v:
                out[k] = v[vk]; break
        else:
            if "intValue" in v:
                try: out[k] = int(v["intValue"])
                except Exception: out[k] = v["intValue"]
            elif "arrayValue" in v:
                out[k] = v["arrayValue"]
            elif "kvlistValue" in v:
                out[k] = v["kvlistValue"]
    return out


def _nano_to_iso(nano) -> str | None:
    if not nano:
        return None
    try:
        n = int(nano)
        from datetime import datetime
        return datetime.utcfromtimestamp(n / 1e9).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except Exception:
        return None


_OTEL_EVENT_COLS = (
    "event_name", "session_id", "prompt_id", "timestamp", "model", "tool_name",
    "tool_success", "tool_duration_ms", "tool_error", "cost_usd",
    "api_duration_ms", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_create_tokens", "speed", "error_message", "status_code",
    "attempt_count", "skill_name", "skill_source", "prompt_length",
    "decision", "decision_source", "request_id", "tool_result_size_bytes",
    "mcp_server_scope", "plugin_name", "plugin_version", "marketplace_name",
    "install_trigger", "mcp_server_name", "mcp_tool_name",
)
_OTEL_SQL = (
    "INSERT INTO otel_events (" + ",".join(_OTEL_EVENT_COLS) + ") VALUES ("
    + ",".join(f":{c}" for c in _OTEL_EVENT_COLS) + ")"
)


def _log_record_to_row(rec: dict, resource_attrs: dict) -> dict | None:
    attrs = _flatten_attrs(rec.get("attributes"))
    event_name = attrs.get("event.name") or attrs.get("event_name")
    ts = _nano_to_iso(rec.get("timeUnixNano") or rec.get("observedTimeUnixNano"))
    tool_name = attrs.get("tool_name")
    mcp_server = attrs.get("mcp_server_name")
    mcp_tool = attrs.get("mcp_tool_name")
    if tool_name == "mcp_tool" and "tool_parameters" in attrs:
        try:
            tp = json.loads(attrs["tool_parameters"])
            mcp_server = mcp_server or tp.get("mcp_server_name")
            mcp_tool = mcp_tool or tp.get("mcp_tool_name")
        except Exception:
            pass
    body = rec.get("body")
    body_str = body["stringValue"] if isinstance(body, dict) and "stringValue" in body else None
    return {
        "event_name": event_name,
        "session_id": attrs.get("session.id") or attrs.get("session_id"),
        "prompt_id": attrs.get("prompt_id"),
        "timestamp": ts,
        "model": attrs.get("model") or resource_attrs.get("service.version"),
        "tool_name": tool_name,
        "tool_success": int(bool(attrs.get("success"))) if "success" in attrs else None,
        "tool_duration_ms": attrs.get("duration_ms"),
        "tool_error": attrs.get("tool_error"),
        "cost_usd": attrs.get("cost_usd"),
        "api_duration_ms": attrs.get("duration_ms") if event_name == "api_request" else None,
        "input_tokens": attrs.get("input_tokens"),
        "output_tokens": attrs.get("output_tokens"),
        "cache_read_tokens": attrs.get("cache_read_tokens") or attrs.get("cache_read_input_tokens"),
        "cache_create_tokens": attrs.get("cache_creation_tokens") or attrs.get("cache_creation_input_tokens"),
        "speed": attrs.get("speed"),
        "error_message": attrs.get("error_message") or body_str,
        "status_code": attrs.get("status_code"),
        "attempt_count": attrs.get("attempt_count") or attrs.get("attempt"),
        "skill_name": attrs.get("skill_name"),
        "skill_source": attrs.get("skill_source"),
        "prompt_length": attrs.get("prompt_length"),
        "decision": attrs.get("decision"),
        "decision_source": attrs.get("decision_source"),
        "request_id": attrs.get("request_id"),
        "tool_result_size_bytes": attrs.get("tool_result_size_bytes"),
        "mcp_server_scope": attrs.get("mcp_server_scope"),
        "plugin_name": attrs.get("plugin_name"),
        "plugin_version": attrs.get("plugin_version"),
        "marketplace_name": attrs.get("marketplace_name"),
        "install_trigger": attrs.get("install_trigger"),
        "mcp_server_name": mcp_server,
        "mcp_tool_name": mcp_tool,
    }


@app.post("/v1/logs")
async def otel_logs(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse({"accepted": 0, "dropped": 0, "error": f"bad_json:{exc}"}, status_code=200)
    accepted = 0
    dropped = 0
    request.app.state.last_otel_event_at = time.time()
    rows: list[dict] = []
    for rl in payload.get("resourceLogs", []) or []:
        resource_attrs = _flatten_attrs(rl.get("resource", {}).get("attributes"))
        for sl in rl.get("scopeLogs", []) or []:
            for rec in sl.get("logRecords", []) or []:
                try:
                    r = _log_record_to_row(rec, resource_attrs)
                    if r is not None:
                        rows.append(r); accepted += 1
                except Exception as exc:
                    dropped += 1
                    print(f"[otel/logs] dropped: {exc!r}", file=sys.stderr)
    if rows:
        with db.connect() as conn:
            try:
                conn.executemany(_OTEL_SQL, rows)
            except sqlite3.Error:
                for r in rows:
                    try:
                        conn.execute(_OTEL_SQL, r)
                    except Exception as iexc:
                        accepted -= 1; dropped += 1
                        print(f"[otel/logs row] dropped: {iexc!r}", file=sys.stderr)
    return JSONResponse({"accepted": accepted, "dropped": dropped}, status_code=200)


@app.post("/v1/metrics")
async def otel_metrics(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"accepted": 0, "dropped": 0, "error": "bad_json"}, status_code=200)
    accepted = 0; dropped = 0
    rows: list[tuple[Any, ...]] = []
    for rm in payload.get("resourceMetrics", []) or []:
        for sm in rm.get("scopeMetrics", []) or []:
            for m in sm.get("metrics", []) or []:
                try:
                    name = m.get("name")
                    for kind, key in (("counter", "sum"), ("gauge", "gauge")):
                        data = m.get(key)
                        if not data: continue
                        for pt in data.get("dataPoints", []) or []:
                            attrs = _flatten_attrs(pt.get("attributes"))
                            val = pt.get("asDouble")
                            if val is None:
                                try: val = float(pt.get("asInt", 0))
                                except Exception: val = 0
                            ts = _nano_to_iso(pt.get("timeUnixNano") or pt.get("startTimeUnixNano"))
                            rows.append((name, kind, val,
                                         attrs.get("session.id") or attrs.get("session_id"),
                                         attrs.get("model"), ts))
                            accepted += 1
                except Exception as exc:
                    dropped += 1
                    print(f"[otel/metrics] dropped: {exc!r}", file=sys.stderr)
    if rows:
        with db.connect() as conn:
            conn.executemany(
                "INSERT INTO otel_metrics(metric_name, metric_type, value, session_id, model, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
    return JSONResponse({"accepted": accepted, "dropped": dropped}, status_code=200)


# ---------------------------------------------------------------------------
# UI static serving (mount LAST so API routes win)
# ---------------------------------------------------------------------------
# Serve the Vite build from ui/dist when present. Unknown non-API paths fall
# back to index.html so TanStack Router's client-side routing works on reload.
UI_DIST = db.INSTALL_DIR / "ui" / "dist"
UI_ASSETS = UI_DIST / "assets"
if UI_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(UI_ASSETS)), name="ui-assets")


@app.get("/", include_in_schema=False)
async def root_index() -> FileResponse:
    index = UI_DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(404, "ui/dist/index.html missing — run `npm --prefix ui run build`")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    # Reserved prefixes: API, OTEL, docs, openapi. Let those 404 normally.
    if full_path.startswith(("api/", "v1/", "docs", "openapi.json", "redoc")):
        raise HTTPException(404)
    # Serve a static file if it actually exists (favicon, manifest, etc.).
    target = UI_DIST / full_path
    if target.is_file():
        return FileResponse(str(target))
    # Otherwise SPA fallback.
    index = UI_DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(404, "ui/dist not built")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    import uvicorn
    host = os.environ.get("CC_HOST", "127.0.0.1")
    port = int(os.environ.get("CC_PORT", "8765"))
    uvicorn.run("server:app", host=host, port=port, reload=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
