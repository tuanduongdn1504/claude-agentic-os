"""Task lifecycle — INSERTs, atomic claim, completion/failure.

All writes go through here so the dispatcher never builds ad-hoc UPDATE
strings. `claim_pending` is the one that matters: atomic with rowcount
check so the launchd daemon + a manual `heartbeat --once` can't double-
claim the same row.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Resolve the command-centre scripts dir so we can `import db`. This file
# may run standalone from launchd or from the dispatcher module — both
# need the same import path.
_SCRIPTS = Path(os.environ.get("CC_INSTALL_DIR") or
                (Path(__file__).resolve().parents[4])) / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import db  # noqa: E402


def create_task(
    title: str,
    description: str | None = None,
    priority: int = 0,
    assigned_skill: str | None = None,
    scheduled_for: str | None = None,
    model: str | None = None,
    execution_mode: str = "stream",
    risk_level: str | None = None,
    requires_approval: bool = False,
    dry_run: bool = False,
    quadrant: str | None = None,
) -> int:
    """Insert a task row. Returns new id."""
    status = "awaiting_approval" if requires_approval else "pending"
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, description, status, priority, assigned_skill, model,
                execution_mode, scheduled_for, requires_approval, risk_level,
                dry_run, quadrant
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, description, status, priority, assigned_skill, model,
             execution_mode, scheduled_for, int(requires_approval), risk_level,
             int(dry_run), quadrant),
        )
        return int(cur.lastrowid or 0)


def claim_pending(max_rows: int = 1) -> list[dict[str, Any]]:
    """Atomically claim up to `max_rows` pending tasks.

    We `UPDATE ... WHERE status='pending' AND id IN (subselect)` and then
    read back the affected rows by marking them `running` first. The
    rowcount check prevents a race with another heartbeat process.
    """
    if max_rows <= 0:
        return []
    claimed: list[dict[str, Any]] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Pick ids.
            rows = conn.execute(
                """
                SELECT id FROM ops_tasks
                WHERE status='pending'
                  AND (scheduled_for IS NULL OR scheduled_for <= datetime('now'))
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (max_rows,),
            ).fetchall()
            if not rows:
                conn.execute("COMMIT")
                return []
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            # Atomic mark.
            rc = conn.execute(
                f"""
                UPDATE ops_tasks SET status='running', started_at=datetime('now')
                 WHERE status='pending' AND id IN ({placeholders})
                """,
                ids,
            ).rowcount
            if rc == 0:
                conn.execute("COMMIT")
                return []
            # Read back the rows that actually landed in 'running' for us.
            got = conn.execute(
                f"""
                SELECT * FROM ops_tasks
                 WHERE id IN ({placeholders}) AND status='running'
                """,
                ids,
            ).fetchall()
            claimed = [dict(r) for r in got]
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return claimed


def update_task(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with db.connect() as conn:
        conn.execute(
            f"UPDATE ops_tasks SET {set_clause} WHERE id = :id",
            {**fields, "id": task_id},
        )


def complete_task(task_id: int, output_summary: str | None = None,
                  session_id: str | None = None,
                  cost_usd: float | None = None,
                  duration_ms: int | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE ops_tasks SET
                status='done',
                completed_at=datetime('now'),
                output_summary=COALESCE(?, output_summary),
                session_id=COALESCE(?, session_id),
                cost_usd=COALESCE(?, cost_usd),
                duration_ms=COALESCE(?, duration_ms),
                consecutive_failures=0
            WHERE id = ?
            """,
            (output_summary, session_id, cost_usd, duration_ms, task_id),
        )


def fail_task(task_id: int, error_message: str,
              session_id: str | None = None,
              duration_ms: int | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE ops_tasks SET
                status='failed',
                completed_at=datetime('now'),
                error_message=?,
                session_id=COALESCE(?, session_id),
                duration_ms=COALESCE(?, duration_ms),
                consecutive_failures = COALESCE(consecutive_failures, 0) + 1
            WHERE id = ?
            """,
            (error_message, session_id, duration_ms, task_id),
        )


def log_activity(event_type: str, detail: str | None = None,
                 metadata: dict[str, Any] | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO activities(event_type, detail, metadata) VALUES (?, ?, ?)",
            (event_type, detail, json.dumps(metadata) if metadata else None),
        )
