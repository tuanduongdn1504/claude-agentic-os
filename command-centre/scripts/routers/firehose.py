"""/api/firehose — SSE stream of recent OTEL events.

On connect: send the most recent 50 events as backfill, then poll the
DB every 2s and emit rows whose `id` > the running cursor. Disconnect
when the client hangs up.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

import db

router = APIRouter()


def _fetch_backfill(conn, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, event_name, session_id, timestamp, model, tool_name,
               mcp_server_name, mcp_tool_name, tool_duration_ms,
               cost_usd, input_tokens, output_tokens, error_message
        FROM otel_events
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows][::-1]


def _fetch_since(conn, since_id: int, event_name: str | None) -> list[dict[str, Any]]:
    clauses = ["id > ?"]
    params: list[Any] = [since_id]
    if event_name:
        clauses.append("event_name = ?"); params.append(event_name)
    rows = conn.execute(
        f"""
        SELECT id, event_name, session_id, timestamp, model, tool_name,
               mcp_server_name, mcp_tool_name, tool_duration_ms,
               cost_usd, input_tokens, output_tokens, error_message
        FROM otel_events
        WHERE {' AND '.join(clauses)}
        ORDER BY id ASC LIMIT 500
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/firehose")
async def firehose(event_name: Optional[str] = None, backfill: int = 50):
    backfill = max(0, min(int(backfill), 200))

    async def gen():
        cursor = 0
        # Backfill phase.
        try:
            with db.connect() as conn:
                rows = _fetch_backfill(conn, backfill)
            for row in rows:
                cursor = max(cursor, int(row["id"]))
                if event_name and row.get("event_name") != event_name:
                    continue
                yield f"data: {json.dumps(row, default=str)}\n\n"
            yield "event: ready\ndata: backfill-complete\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {exc}\n\n"
            return

        while True:
            await asyncio.sleep(2.0)
            try:
                with db.connect() as conn:
                    rows = _fetch_since(conn, cursor, event_name)
            except Exception as exc:
                yield f"event: error\ndata: {exc}\n\n"
                return
            for row in rows:
                cursor = max(cursor, int(row["id"]))
                yield f"data: {json.dumps(row, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
