"""/api/decisions/* and /api/inbox/*.

Both read from + write to the mission-control queue files when we have
a live session to route the answer to. For the API-surface milestone
the writes here just enqueue; the dispatcher (next milestone) picks them
up and injects into the running `claude -p` stdin.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

import db

router = APIRouter()

QUEUE_DIR = Path(os.environ.get("CC_QUEUE_DIR") or
                 (db.INSTALL_DIR / ".tmp" / "mission-control-queue"))


def _enqueue(session_id: str | None, payload: dict[str, Any]) -> None:
    if not session_id:
        return
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    mailbox = QUEUE_DIR / f"{session_id}.jsonl"
    with mailbox.open("a") as fh:
        fh.write(json.dumps({**payload, "ts": time.time()}) + "\n")


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@router.get("/api/decisions")
async def list_decisions(status: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
    clauses = ["1=1"]; params: list[Any] = []
    if status in ("pending", "answered"):
        clauses.append("status = ?"); params.append(status)
    params.append(max(1, min(limit, 200)))
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, task_id, session_id, prompt, answer, status,
                   created_at, answered_at
            FROM ops_decisions
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/api/decisions")
async def create_decision(request: Request) -> dict[str, Any]:
    p = await request.json()
    prompt = (p.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt required")
    task_id = p.get("task_id")
    session_id = p.get("session_id")
    with db.connect() as conn:
        # Partial UNIQUE(session_id, prompt) where session_id IS NOT NULL —
        # INSERT OR IGNORE respects that.
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO ops_decisions(task_id, session_id, prompt, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (task_id, session_id, prompt),
        )
        created = cur.rowcount > 0
        if created:
            new_id = cur.lastrowid
        else:
            row = conn.execute(
                "SELECT id FROM ops_decisions WHERE session_id IS ? AND prompt = ?",
                (session_id, prompt),
            ).fetchone()
            new_id = row["id"] if row else None
    return {"id": new_id, "created": created}


@router.post("/api/decisions/{decision_id}/answer")
async def answer_decision(decision_id: int, request: Request) -> dict[str, Any]:
    p = await request.json()
    answer = p.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise HTTPException(400, "answer required (string)")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, session_id, status FROM ops_decisions WHERE id = ?",
            (decision_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "decision not found")
        if row["status"] == "answered":
            return {"answered": True, "already": True}
        conn.execute(
            "UPDATE ops_decisions SET answer = ?, status='answered', answered_at=datetime('now') WHERE id = ?",
            (answer, decision_id),
        )
    _enqueue(row["session_id"], {"kind": "decision_answer", "decision_id": decision_id,
                                 "answer": answer})
    return {"answered": True}


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

@router.get("/api/inbox")
async def list_inbox(unread: int = 0, max_age_days: int = 30,
                     direction: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
    clauses = ["created_at >= datetime('now', ?)"]
    params: list[Any] = [f"-{max(1, max_age_days)} days"]
    if unread:
        clauses.append("read = 0")
    if direction in ("agent_to_user", "user_to_agent"):
        clauses.append("direction = ?"); params.append(direction)
    params.append(max(1, min(limit, 500)))
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, task_id, session_id, direction, body, read, created_at
            FROM ops_inbox
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/api/inbox")
async def create_inbox(request: Request) -> dict[str, Any]:
    p = await request.json()
    body = (p.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "body required")
    direction = p.get("direction", "agent_to_user")
    if direction not in ("agent_to_user", "user_to_agent"):
        raise HTTPException(400, "bad direction")
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_inbox(task_id, session_id, direction, body, read)
            VALUES (?, ?, ?, ?, 0)
            """,
            (p.get("task_id"), p.get("session_id"), direction, body),
        )
    return {"id": cur.lastrowid, "created": True}


@router.post("/api/inbox/{inbox_id}/read")
async def mark_read(inbox_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        rc = conn.execute(
            "UPDATE ops_inbox SET read=1 WHERE id = ?", (inbox_id,)
        ).rowcount
    if rc == 0:
        raise HTTPException(404, "inbox item not found")
    return {"read": True}


@router.post("/api/inbox/{inbox_id}/reply")
async def reply_inbox(inbox_id: int, request: Request) -> dict[str, Any]:
    p = await request.json()
    body = (p.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "body required")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT session_id, task_id FROM ops_inbox WHERE id = ?", (inbox_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "inbox item not found")
        cur = conn.execute(
            """
            INSERT INTO ops_inbox(task_id, session_id, direction, body, read)
            VALUES (?, ?, 'user_to_agent', ?, 1)
            """,
            (row["task_id"], row["session_id"], body),
        )
        conn.execute("UPDATE ops_inbox SET read=1 WHERE id = ?", (inbox_id,))
    _enqueue(row["session_id"], {"kind": "inbox_reply", "body": body,
                                 "parent_inbox_id": inbox_id})
    return {"replied": True, "id": cur.lastrowid}
