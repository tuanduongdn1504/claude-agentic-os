"""/api/sessions/* — list, details, live, live-stream, live-message."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import StreamingResponse

import db
from helpers import timerange

router = APIRouter()

UUID_RE = re.compile(r"^[0-9a-fA-F\-]{8,40}$")
LIVE_WINDOW_SECONDS = 300

QUEUE_DIR = Path(os.environ.get("CC_QUEUE_DIR") or
                 (db.INSTALL_DIR / ".tmp" / "mission-control-queue"))


# ---------------------------------------------------------------------------
# List + details
# ---------------------------------------------------------------------------

@router.get("/api/sessions")
async def list_sessions(
    range: str = "7d",
    source: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = 100,
    q: Optional[str] = None,
    offset: int = 0,
) -> dict[str, Any]:
    frag, params = timerange.sql_predicate(range, "started_at")
    clauses = [frag]
    if source:
        clauses.append("source = ?"); params.append(source)
    if model:
        clauses.append("model = ?"); params.append(model)
    if q:
        clauses.append("(title LIKE ? OR cwd LIKE ? OR session_id LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    with db.connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM sessions WHERE {' AND '.join(clauses)}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT session_id, source, entrypoint, cwd, git_branch, model, title,
                   started_at, ended_at, duration_ms,
                   input_tokens, output_tokens, cache_read_tokens, cache_create_tokens,
                   total_tokens, effective_tokens, cost_usd, error_count, is_error_any,
                   rate_limit_hit, stop_reason, service_tier
            FROM sessions
            WHERE {' AND '.join(clauses)}
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, max(1, min(limit, 500)), max(0, offset)],
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total,
            "offset": offset, "limit": limit}


@router.get("/api/sessions/live")
async def sessions_live() -> dict[str, Any]:
    """Sessions with any event in the last 5 minutes (mtime-based proxy)."""
    cutoff = time.time() - LIVE_WINDOW_SECONDS
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id, source, entrypoint, cwd, model, title,
                   started_at, ended_at, duration_ms,
                   input_tokens, output_tokens, effective_tokens, cost_usd,
                   jsonl_path, jsonl_mtime
            FROM sessions
            WHERE jsonl_mtime IS NOT NULL AND jsonl_mtime >= ?
            ORDER BY jsonl_mtime DESC
            """,
            (cutoff,),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "window_s": LIVE_WINDOW_SECONDS}


@router.get("/api/sessions/{session_id}/details")
async def session_details(session_id: str = PathParam(...)) -> dict[str, Any]:
    if not UUID_RE.match(session_id):
        raise HTTPException(400, "bad session_id")
    with db.connect() as conn:
        sess = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not sess:
            raise HTTPException(404, "session not found")
        tools = [dict(r) for r in conn.execute(
            "SELECT tool_use_id, tool_name, ts, duration_ms, error, caller, "
            "is_subagent, parent_uuid FROM tool_calls "
            "WHERE session_id = ? ORDER BY ts",
            (session_id,),
        )]
        sys_events = [dict(r) for r in conn.execute(
            "SELECT timestamp, subtype, stop_reason, retry_attempt, max_retries, "
            "retry_in_ms, compact_metadata, hook_errors FROM system_events "
            "WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        )]
        title_row = conn.execute(
            "SELECT title, title_source FROM session_titles WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    token_breakdown = {
        "input_tokens": sess["input_tokens"],
        "output_tokens": sess["output_tokens"],
        "cache_read_tokens": sess["cache_read_tokens"],
        "cache_create_tokens": sess["cache_create_tokens"],
        "total_tokens": sess["total_tokens"],
        "effective_tokens": sess["effective_tokens"],
        "cost_usd": sess["cost_usd"],
    }
    return {
        "session": dict(sess),
        "tool_timeline": tools,
        "system_events": sys_events,
        "token_breakdown": token_breakdown,
        "title_source": (title_row["title_source"] if title_row else None),
    }


# ---------------------------------------------------------------------------
# Live state + stream
# ---------------------------------------------------------------------------

@router.get("/api/sessions/live/{session_id}/state")
async def live_state(session_id: str) -> dict[str, Any]:
    if not UUID_RE.match(session_id):
        raise HTTPException(400, "bad session_id")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT session_id, state, current_tool, updated_at "
            "FROM live_session_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return {"session_id": session_id, "state": None, "current_tool": None,
                "updated_at": None}
    return dict(row)


@router.get("/api/sessions/live/{session_id}/stream")
async def live_stream(session_id: str):
    """SSE tail of the session's JSONL file — line-by-line, last 50 lines on
    open, then follow. Disconnects when the file is idle for > 60s."""
    if not UUID_RE.match(session_id):
        raise HTTPException(400, "bad session_id")

    with db.connect() as conn:
        row = conn.execute(
            "SELECT jsonl_path FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if not row or not row["jsonl_path"]:
        raise HTTPException(404, "session has no jsonl path")
    path = Path(row["jsonl_path"])
    if not path.exists():
        raise HTTPException(404, "jsonl file missing on disk")

    async def gen():
        last_size = 0
        idle_ticks = 0
        # Prime with last 50 lines.
        try:
            with path.open("r", errors="replace") as fh:
                lines = fh.readlines()
            for ln in lines[-50:]:
                yield f"data: {ln.rstrip()}\n\n"
            last_size = path.stat().st_size
        except Exception as exc:
            yield f"event: error\ndata: {exc}\n\n"
            return
        while True:
            await asyncio.sleep(1.0)
            try:
                size = path.stat().st_size
            except OSError:
                yield "event: gone\ndata: file missing\n\n"
                return
            if size == last_size:
                idle_ticks += 1
                if idle_ticks > 60:
                    yield "event: idle\ndata: no data for 60s\n\n"
                    return
                continue
            idle_ticks = 0
            try:
                with path.open("r", errors="replace") as fh:
                    fh.seek(last_size)
                    chunk = fh.read()
                last_size = size
                for ln in chunk.splitlines():
                    if ln.strip():
                        yield f"data: {ln}\n\n"
            except Exception as exc:
                yield f"event: error\ndata: {exc}\n\n"
                return

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Follow-up message — queues to the dispatcher's mailbox
# ---------------------------------------------------------------------------

@router.get("/api/sessions/failures")
async def session_failures(range: str = "30d", limit: int = 25) -> dict[str, Any]:
    """Crashed / errored sessions. Sources `is_error_any` + any system_events
    with subtype='api_error'."""
    from helpers import timerange as tr
    pred, params = tr.sql_predicate(range, "started_at")
    limit = max(1, min(limit, 200))
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT s.session_id, s.title, s.cwd, s.model, s.started_at, s.ended_at,
                   s.error_count, s.is_error_any, s.rate_limit_hit, s.stop_reason,
                   s.cost_usd, s.effective_tokens,
                   (SELECT COUNT(*) FROM system_events e
                      WHERE e.session_id = s.session_id AND e.subtype = 'api_error') AS api_errors
            FROM sessions s
            WHERE {pred}
              AND (s.is_error_any = 1 OR s.error_count > 0 OR s.rate_limit_hit = 1)
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return {"range": tr.normalize(range), "items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/api/sessions/live/{session_id}/message")
async def live_message(session_id: str, request: Request) -> dict[str, Any]:
    if not UUID_RE.match(session_id):
        raise HTTPException(400, "bad session_id")
    payload = await request.json()
    body = payload.get("body") or payload.get("message") or ""
    if not body or not isinstance(body, str):
        raise HTTPException(400, "body required")

    # Only stream-mode tasks can receive follow-ups.
    with db.connect() as conn:
        row = conn.execute(
            "SELECT execution_mode, status FROM ops_tasks WHERE session_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if row and row["execution_mode"] != "stream":
        raise HTTPException(
            409,
            "Read-only — this task was queued as One-shot. Re-queue as Interactive to reply.",
        )

    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    mailbox = QUEUE_DIR / f"{session_id}.jsonl"
    with mailbox.open("a") as fh:
        fh.write(json.dumps({"body": body, "ts": time.time()}) + "\n")
    return {"queued": True, "mailbox": str(mailbox)}
