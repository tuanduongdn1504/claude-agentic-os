"""/api/schedules/* + /api/schedules/parse-nl.

Cron-derivation is client-side (ScheduleComposer builds it). Server just
stores/updates. `parse-nl` is a stub that echoes a reasonable example
cron — the real Haiku call lands when Mission Control ships skill_router.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

import db

router = APIRouter()

CRON_RE = re.compile(r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$")


def _validate_cron(expr: str) -> None:
    if not expr or not CRON_RE.match(expr):
        raise HTTPException(400, f"cron expression must be 5 space-separated fields, got: {expr!r}")


@router.get("/api/schedules")
async def list_schedules() -> dict[str, Any]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, cron_expression, task_title, task_description,
                   assigned_skill, enabled, next_run_at, last_run_at, created_at
            FROM ops_schedules
            ORDER BY enabled DESC, next_run_at
            """
        ).fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/api/schedules")
async def create_schedule(request: Request) -> dict[str, Any]:
    p = await request.json()
    cron = p.get("cron_expression") or ""
    _validate_cron(cron)
    title = p.get("task_title") or ""
    if not title.strip():
        raise HTTPException(400, "task_title required")
    fields = {
        "name": p.get("name") or title[:60],
        "cron_expression": cron,
        "task_title": title,
        "task_description": p.get("task_description"),
        "assigned_skill": p.get("assigned_skill"),
        "enabled": int(bool(p.get("enabled", True))),
        "next_run_at": p.get("next_run_at"),
    }
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_schedules(name, cron_expression, task_title, task_description,
                                      assigned_skill, enabled, next_run_at)
            VALUES (:name, :cron_expression, :task_title, :task_description,
                    :assigned_skill, :enabled, :next_run_at)
            """,
            fields,
        )
    return {"id": cur.lastrowid, "created": True}


@router.patch("/api/schedules/{sched_id}")
async def update_schedule(sched_id: int, request: Request) -> dict[str, Any]:
    p = await request.json()
    allowed = {"name", "cron_expression", "task_title", "task_description",
               "assigned_skill", "enabled", "next_run_at"}
    fields = {k: v for k, v in p.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "no updatable fields")
    if "cron_expression" in fields:
        _validate_cron(fields["cron_expression"])
        # Per prompt: clear next_run_at on cron change so heartbeat recomputes.
        fields["next_run_at"] = None
    if "enabled" in fields:
        fields["enabled"] = int(bool(fields["enabled"]))
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with db.connect() as conn:
        rc = conn.execute(
            f"UPDATE ops_schedules SET {set_clause} WHERE id = :id",
            {**fields, "id": sched_id},
        ).rowcount
    if rc == 0:
        raise HTTPException(404, "schedule not found")
    return {"updated": True}


@router.delete("/api/schedules/{sched_id}")
async def delete_schedule(sched_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        rc = conn.execute(
            "DELETE FROM ops_schedules WHERE id = ?", (sched_id,)
        ).rowcount
    if rc == 0:
        raise HTTPException(404, "schedule not found")
    return {"deleted": sched_id}


@router.get("/api/schedules/{sched_id}/runs")
async def schedule_runs(sched_id: int, limit: int = 10) -> dict[str, Any]:
    """Last N tasks materialized by this schedule.
    We match by task title equal to schedule's task_title AND creation
    order — cheap heuristic (the dispatcher does the actual materialization).
    """
    limit = max(1, min(limit, 50))
    with db.connect() as conn:
        sched = conn.execute(
            "SELECT task_title FROM ops_schedules WHERE id = ?", (sched_id,)
        ).fetchone()
        if not sched:
            raise HTTPException(404, "schedule not found")
        rows = conn.execute(
            """
            SELECT id, title, status, created_at, completed_at, duration_ms,
                   cost_usd, error_message
            FROM ops_tasks WHERE title = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (sched["task_title"], limit),
        ).fetchall()
    return {"schedule_id": sched_id, "items": [dict(r) for r in rows]}


@router.post("/api/schedules/parse-nl")
async def parse_nl(request: Request) -> dict[str, Any]:
    """Natural-language → cron. Stub — returns a plausible cron based on
    simple keyword matching. Real Haiku call lands with skill_router."""
    p = await request.json()
    text = (p.get("text") or p.get("nl") or "").strip().lower()
    if not text:
        raise HTTPException(400, "text required")
    # Cheap rules for smoke-test responsiveness.
    cron = "0 9 * * *"   # default: 9am daily
    if "hour" in text:
        cron = "0 * * * *"
    elif "every 15" in text or "15 min" in text:
        cron = "*/15 * * * *"
    elif "mon" in text and "fri" in text:
        cron = "0 9 * * 1-5"
    elif "sunday" in text:
        cron = "0 9 * * 0"
    elif "noon" in text:
        cron = "0 12 * * *"
    return {
        "cron_expression": cron,
        "note": "stubbed — real parser lands with Mission Control",
        "input": text,
    }
