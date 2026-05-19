"""/api/tasks/* + /api/dispatcher/trigger.

Dispatcher/trigger is a stub in this milestone — it just writes an
activity row so the API contract is live. Actual `subprocess.Popen`
lands with the Mission Control milestone.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

import db

router = APIRouter()


_ALLOWED_STATUSES = ("pending", "awaiting_approval", "running", "done",
                     "failed", "cancelled")
_ALLOWED_MODES = ("classic", "stream")
_ALLOWED_QUADRANTS = ("do", "schedule", "delegate", "archive")


@router.get("/api/tasks")
async def list_tasks(status: Optional[str] = None, quadrant: Optional[str] = None,
                     limit: int = 200) -> dict[str, Any]:
    clauses = ["1=1"]; params: list[Any] = []
    if status:
        if status not in _ALLOWED_STATUSES:
            raise HTTPException(400, "bad status")
        clauses.append("status = ?"); params.append(status)
    if quadrant:
        if quadrant not in _ALLOWED_QUADRANTS:
            raise HTTPException(400, "bad quadrant")
        clauses.append("quadrant = ?"); params.append(quadrant)
    params.append(max(1, min(limit, 500)))
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, description, status, priority, assigned_skill, model,
                   execution_mode, scheduled_for, requires_approval, risk_level,
                   dry_run, quadrant, approved_at, session_id, started_at,
                   completed_at, duration_ms, cost_usd, output_summary, error_message,
                   consecutive_failures, created_at,
                   COALESCE(cost_source, 'unknown') AS cost_source
            FROM ops_tasks
            WHERE {' AND '.join(clauses)}
            ORDER BY
              CASE status
                WHEN 'running' THEN 0 WHEN 'awaiting_approval' THEN 1
                WHEN 'pending' THEN 2 WHEN 'failed' THEN 3
                WHEN 'done' THEN 4 WHEN 'cancelled' THEN 5
              END,
              priority DESC, created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/api/tasks")
async def create_task(request: Request) -> dict[str, Any]:
    p = await request.json()
    title = (p.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    mode = p.get("execution_mode", "stream")
    if mode not in _ALLOWED_MODES:
        raise HTTPException(400, "execution_mode must be classic|stream")
    quadrant = p.get("quadrant")
    if quadrant and quadrant not in _ALLOWED_QUADRANTS:
        raise HTTPException(400, "bad quadrant")

    fields = {
        "title": title,
        "description": p.get("description"),
        "status": "awaiting_approval" if p.get("requires_approval") else "pending",
        "priority": int(p.get("priority", 0)),
        "assigned_skill": p.get("assigned_skill"),
        "model": p.get("model"),
        "execution_mode": mode,
        "scheduled_for": p.get("scheduled_for"),
        "requires_approval": int(bool(p.get("requires_approval", False))),
        "risk_level": p.get("risk_level"),
        "dry_run": int(bool(p.get("dry_run", False))),
        "quadrant": quadrant,
        # v0.6.0 — every dispatcher-bound task is api_pool spend.
        "cost_source": "api_pool",
    }
    keys = ", ".join(fields.keys())
    marks = ", ".join(f":{k}" for k in fields)
    with db.connect() as conn:
        cur = conn.execute(
            f"INSERT INTO ops_tasks({keys}) VALUES ({marks})", fields,
        )
    return {"id": cur.lastrowid, "created": True}


@router.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, request: Request) -> dict[str, Any]:
    p = await request.json()
    allowed = {"title", "description", "status", "priority", "assigned_skill",
               "model", "execution_mode", "scheduled_for", "requires_approval",
               "risk_level", "dry_run", "quadrant"}
    fields = {k: v for k, v in p.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "no updatable fields")
    if "status" in fields and fields["status"] not in _ALLOWED_STATUSES:
        raise HTTPException(400, "bad status")
    if "execution_mode" in fields and fields["execution_mode"] not in _ALLOWED_MODES:
        raise HTTPException(400, "bad execution_mode")
    if "quadrant" in fields and fields["quadrant"] and fields["quadrant"] not in _ALLOWED_QUADRANTS:
        raise HTTPException(400, "bad quadrant")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with db.connect() as conn:
        rc = conn.execute(
            f"UPDATE ops_tasks SET {set_clause} WHERE id = :id",
            {**fields, "id": task_id},
        ).rowcount
    if rc == 0:
        raise HTTPException(404, "task not found")
    return {"updated": True}


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        rc = conn.execute("DELETE FROM ops_tasks WHERE id = ?", (task_id,)).rowcount
    if rc == 0:
        raise HTTPException(404, "task not found")
    return {"deleted": task_id}


@router.post("/api/tasks/{task_id}/approve")
async def approve_task(task_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        if row["status"] != "awaiting_approval":
            raise HTTPException(409, f"task status is {row['status']}, not awaiting_approval")
        conn.execute(
            "UPDATE ops_tasks SET status='pending', approved_at=datetime('now') WHERE id = ?",
            (task_id,),
        )
    return {"approved": True}


@router.post("/api/tasks/{task_id}/rerun")
async def rerun_task(task_id: int) -> dict[str, Any]:
    """Only permitted when status='failed'. Reset to 'pending', preserve
    consecutive_failures."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        if row["status"] != "failed":
            raise HTTPException(400, f"rerun only permitted on failed tasks (status='{row['status']}')")
        conn.execute(
            """
            UPDATE ops_tasks SET
                status='pending',
                error_message=NULL,
                completed_at=NULL,
                started_at=NULL,
                duration_ms=NULL,
                output_summary=NULL,
                session_id=NULL
            WHERE id = ?
            """,
            (task_id,),
        )
    return {"rerun": True, "task_id": task_id}


@router.post("/api/dispatcher/trigger")
async def dispatcher_trigger() -> dict[str, Any]:
    """Spawn `heartbeat.py --once` detached. Returns immediately."""
    import asyncio, os, subprocess, sys
    from pathlib import Path

    heartbeat = (Path(db.INSTALL_DIR) / ".claude" / "skills" / "mission-control"
                 / "scripts" / "heartbeat.py")
    if not heartbeat.exists():
        return {"triggered": False, "error": f"heartbeat missing: {heartbeat}"}

    py = sys.executable or "/usr/bin/python3"

    def _spawn() -> int:
        env = os.environ.copy()
        env.setdefault("CC_INSTALL_DIR", str(db.INSTALL_DIR))
        proc = subprocess.Popen(
            [py, str(heartbeat), "--once"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            cwd=str(db.INSTALL_DIR),
        )
        return proc.pid

    pid = await asyncio.to_thread(_spawn)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO activities(event_type, detail) VALUES ('dispatcher_trigger', ?)",
            (f"spawned heartbeat pid={pid}",),
        )
    return {"triggered": True, "pid": pid}
