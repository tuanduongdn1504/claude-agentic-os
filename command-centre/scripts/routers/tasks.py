"""/api/tasks/* + /api/dispatcher/trigger.

Dispatcher/trigger is a stub in this milestone — it just writes an
activity row so the API contract is live. Actual `subprocess.Popen`
lands with the Mission Control milestone.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

import db

router = APIRouter()


_ALLOWED_STATUSES = ("pending", "awaiting_approval", "running", "done",
                     "failed", "cancelled")
_ALLOWED_MODES = ("classic", "stream")
_ALLOWED_QUADRANTS = ("do", "schedule", "delegate", "archive")

# /cancel accepts these as the prior status; everything else is a no-op or
# requires emergency-stop. See amendment "Backend delta → /cancel".
_CANCELLABLE_STATUSES = ("pending", "awaiting_approval")
# Audit-log source values written into activities.detail JSON. Validator on
# /approve and /cancel accepts any non-empty string but bridge always
# sends 'telegram'; dashboard omits the param and lands at 'api'.
_DEFAULT_AUDIT_SOURCE = "api"


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
                   COALESCE(cost_source, 'unknown') AS cost_source,
                   success_criteria, review_verdict,
                   COALESCE(review_count, 0) AS review_count, review_feedback,
                   COALESCE(review_overridden, 0) AS review_overridden
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
    # v0.5.0-mvp2 — accept optional provenance marker. Validator: must be a
    # non-empty string when present. Defaults to 'dashboard' (column default
    # too). Bridge sends 'telegram'; reserve 'schedule' / 'api' without a
    # further migration. Existing dashboard POSTs that omit the field
    # remain unchanged (FR24).
    cas_raw = p.get("created_at_source")
    if cas_raw is None:
        created_at_source = "dashboard"
    else:
        if not isinstance(cas_raw, str) or not cas_raw.strip():
            raise HTTPException(400, "created_at_source must be a non-empty string")
        created_at_source = cas_raw.strip()

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
        # v0.5.0-mvp2 — trigger provenance (PRD FR20).
        "created_at_source": created_at_source,
        # v0.7.0 — optional operator-supplied success criteria. Free text the
        # reviewer LLM reads (no parser, Rule 5); same loose handling as
        # description. review_verdict / review_count / review_feedback are
        # dispatcher-owned and never set here.
        "success_criteria": p.get("success_criteria"),
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
async def approve_task(
    task_id: int,
    source: str = Query(_DEFAULT_AUDIT_SOURCE, max_length=64),
) -> dict[str, Any]:
    """Flip a risk-gated task from awaiting_approval → pending.

    v0.5.0-mvp2: accept `?source=` for audit provenance and insert an
    `activities` row tagged `event_type='task_risk_approved'` so every
    risk-gate override is forensically reconstructable (FR19). Default
    source='api' covers existing dashboard callers without code change.
    """
    src = (source or _DEFAULT_AUDIT_SOURCE).strip() or _DEFAULT_AUDIT_SOURCE
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        prior_status = row["status"]
        if prior_status != "awaiting_approval":
            raise HTTPException(409, f"task status is {prior_status}, not awaiting_approval")
        conn.execute(
            "UPDATE ops_tasks SET status='pending', approved_at=datetime('now') WHERE id = ?",
            (task_id,),
        )
        conn.execute(
            "INSERT INTO activities(event_type, detail) VALUES ('task_risk_approved', ?)",
            (json.dumps({"task_id": task_id, "prior_status": prior_status, "source": src}),),
        )
    return {"approved": True, "task_id": task_id, "source": src}


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    source: str = Query(_DEFAULT_AUDIT_SOURCE, max_length=64),
) -> dict[str, Any]:
    """Cancel a pending or risk-gated task.

    v0.5.0-mvp2 (FR18). Mirrors /approve. Reject (400) from running,
    done, failed, cancelled — running tasks must use
    `/api/system/emergency-stop`; terminal states are no-ops. Inserts
    an `activities` row tagged `event_type='task_cancelled'` for audit.
    """
    src = (source or _DEFAULT_AUDIT_SOURCE).strip() or _DEFAULT_AUDIT_SOURCE
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        prior_status = row["status"]
        if prior_status not in _CANCELLABLE_STATUSES:
            if prior_status == "running":
                msg = "running tasks must use /api/system/emergency-stop"
            else:
                msg = f"task already terminal (status='{prior_status}')"
            raise HTTPException(400, msg)
        conn.execute(
            "UPDATE ops_tasks SET status='cancelled', completed_at=datetime('now') "
            "WHERE id = ?",
            (task_id,),
        )
        conn.execute(
            "INSERT INTO activities(event_type, detail) VALUES ('task_cancelled', ?)",
            (json.dumps({"task_id": task_id, "prior_status": prior_status, "source": src}),),
        )
    return {"cancelled": True, "task_id": task_id, "prior_status": prior_status, "source": src}


@router.post("/api/tasks/{task_id}/accept")
async def accept_task(
    task_id: int,
    source: str = Query(_DEFAULT_AUDIT_SOURCE, max_length=64),
) -> dict[str, Any]:
    """Accept a review escalation as-is — complete it with the output the
    implementer already produced, WITHOUT re-running.

    v0.7.1 — the THIRD resolution for an `awaiting_approval` review escalation,
    beside /approve (re-run with feedback) and /cancel (drop). Use when the
    reviewer false-negatived: the work is fine, just mark it done.

    Guard — review escalations ONLY. An `awaiting_approval` task can also come
    from the risk gate (run_once ~600) or autonomy gate (~650); those NEVER RAN
    so there is no output to accept. `review_verdict IS NOT NULL` is the
    discriminator — refuse anything else (use /approve to run it). This is the
    key correctness boundary: accept marks a task done with EXISTING output.

    Pure state transition: NO dispatcher trigger, NO agent spawn — unlike
    /approve it does not re-dispatch and unlike the v0.7.0 reviewer it spawns no
    `claude -p`. Zero added cost. Sets `status='done'`, `review_overridden=1`,
    `completed_at=now`; KEEPS the preserved `output_summary` / `session_id` /
    `duration_ms` and the `review_verdict` / `review_feedback` (the audit trail
    of WHY it escalated). Writes an `activities` row `task_review_overridden`
    (load-bearing — an operator overriding an independent reviewer must be
    traceable). A clean VERIFIED completion never touches this path.
    """
    src = (source or _DEFAULT_AUDIT_SOURCE).strip() or _DEFAULT_AUDIT_SOURCE
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status, review_verdict, review_feedback "
            "FROM ops_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        prior_status = row["status"]
        prior_verdict = row["review_verdict"]
        if prior_status != "awaiting_approval":
            raise HTTPException(
                400, f"task #{task_id} is {prior_status}, not awaiting approval")
        if prior_verdict is None:
            raise HTTPException(
                400,
                f"task #{task_id} is not a review escalation — there is no "
                f"output to accept; use /approve to run it")
        # Pure state transition. output_summary / session_id / duration_ms were
        # preserved at escalation (dispatcher v0.7.1); review_verdict /
        # review_feedback stay for the audit trail. Only flip status + mark the
        # override + stamp completion.
        conn.execute(
            "UPDATE ops_tasks SET status='done', review_overridden=1, "
            "completed_at=datetime('now') WHERE id = ?",
            (task_id,),
        )
        fb = row["review_feedback"]
        conn.execute(
            "INSERT INTO activities(event_type, detail) "
            "VALUES ('task_review_overridden', ?)",
            (json.dumps({
                "task_id": task_id,
                "prior_verdict": prior_verdict,
                "review_feedback_first_120": (fb[:120] if fb else None),
                "source": src,
            }),),
        )
        updated = conn.execute(
            "SELECT * FROM ops_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return dict(updated) if updated else {"accepted": True, "task_id": task_id}


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
