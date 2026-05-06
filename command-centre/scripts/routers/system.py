"""/api/system/* and /api/attention.

Emergency-stop in this cut only flips the system_state flag + fails any
running ops_tasks. Actual PID-kill logic lives in the Mission Control
milestone (not this one).
"""
from __future__ import annotations

import os
import resource
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

import db  # noqa: F401 (used by Path resolution below as db.INSTALL_DIR)

router = APIRouter()


# ---------------------------------------------------------------------------
# /api/system/health
# ---------------------------------------------------------------------------

def _file_mtime_age(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _last_activity_age(conn, event_type: str) -> float | None:
    row = conn.execute(
        "SELECT (julianday('now') - julianday(MAX(created_at))) * 86400.0 AS age "
        "FROM activities WHERE event_type = ?",
        (event_type,),
    ).fetchone()
    if not row or row["age"] is None:
        return None
    return float(row["age"])


def _rss_mb() -> float:
    # Platform-aware. macOS ru_maxrss is bytes since 10.6; Linux is KB.
    import sys
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(raw / (1024.0 * 1024.0), 1)
    return round(raw / 1024.0, 1)


@router.get("/api/system/health")
async def system_health(request: Request) -> dict[str, Any]:
    app = request.app
    now = time.time()
    last_sync = getattr(app.state, "last_sync_tick", 0.0)
    last_otel = getattr(app.state, "last_otel_event_at", 0.0)
    started = getattr(app.state, "server_started_at", now)

    # Tz label — env override wins per prompt.
    tzname = os.environ.get("TZ") or datetime.now().astimezone().tzname() or "UTC"

    with db.connect() as conn:
        daemon_age = _last_activity_age(conn, "heartbeat")
        notifier_age = _last_activity_age(conn, "notifier_heartbeat")
        sync_age = _last_activity_age(conn, "sync_loop_heartbeat")
        # DB file size.
        try:
            db_size = db.DB_PATH.stat().st_size
        except OSError:
            db_size = None

    return {
        "ok": True,
        "uptime_s": int(now - started),
        "tz": tzname,
        "mem_rss_mb": _rss_mb(),
        "db_size_bytes": db_size,
        "last_otel_event_age_s": round(now - last_otel, 1) if last_otel else None,
        "last_sync_tick_age_s": round(now - last_sync, 1) if last_sync else None,
        "daemon_last_tick_age_s": daemon_age,
        "notifier_last_tick_age_s": notifier_age,
        "sync_loop_heartbeat_age_s": sync_age,
    }


# ---------------------------------------------------------------------------
# /api/system/state (KV)
# ---------------------------------------------------------------------------

@router.get("/api/system/state")
async def system_state() -> dict[str, Any]:
    with db.connect() as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM system_state").fetchall()
    return {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}


# ---------------------------------------------------------------------------
# Emergency stop / resume (stubs — PID kill in Mission Control milestone)
# ---------------------------------------------------------------------------

QUEUE_DIR = Path(os.environ.get("CC_QUEUE_DIR") or
                 (db.INSTALL_DIR / ".tmp" / "mission-control-queue"))
PID_DIR = QUEUE_DIR / "pids"


def _argv_for(pid: int) -> str:
    """Read the target process's argv via `ps -p <pid> -o command=`.
    Used to verify we're about to SIGTERM a real `claude -p`, not a PID
    that got recycled to something else."""
    import subprocess
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=3,
        )
        return out.stdout.strip()
    except Exception:
        return ""


@router.post("/api/system/emergency-stop")
async def emergency_stop() -> dict[str, Any]:
    """Real PID-kill implementation.

    1. Walk PID_DIR for marker files.
    2. For each PID: os.kill(pid, 0) to probe liveness. If dead, unlink.
    3. Verify argv contains 'claude' and '-p' before SIGTERM. Defence
       against PID recycling (prompt §Emergency stop).
    4. SIGTERM matching PIDs, unlink markers, tally.
    5. Flip system_state.emergency_stop = '1'.
    6. Fail any still-running ops_tasks.
    """
    killed = 0
    swept_dead = 0
    spared_interactive = 0
    if PID_DIR.exists():
        for f in PID_DIR.iterdir():
            if not f.is_file() or not f.name.isdigit():
                continue
            pid = int(f.name)
            try:
                os.kill(pid, 0)  # liveness probe
            except OSError:
                f.unlink(missing_ok=True)
                swept_dead += 1
                continue
            argv = _argv_for(pid)
            # Zombie child (already dead, parent hasn't reaped): ps reports
            # "<defunct>". Treat like a dead pid — drop the stale marker.
            if not argv or "<defunct>" in argv:
                f.unlink(missing_ok=True)
                swept_dead += 1
                continue
            if "claude" not in argv or "-p" not in argv:
                spared_interactive += 1
                continue
            try:
                os.kill(pid, 15)  # SIGTERM
                killed += 1
            except OSError:
                pass
            f.unlink(missing_ok=True)

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO system_state(key, value, updated_at) VALUES ('emergency_stop', '1', datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value='1', updated_at=datetime('now')"
        )
        failed = conn.execute(
            "UPDATE ops_tasks SET status='failed', error_message='Emergency stop triggered', "
            "completed_at=datetime('now') WHERE status='running'"
        ).rowcount
        conn.execute(
            "INSERT INTO activities(event_type, detail, metadata) VALUES (?, ?, ?)",
            ("emergency_stop", f"killed={killed} failed_tasks={failed}",
             f'{{"killed":{killed},"swept_dead":{swept_dead},"spared_interactive":{spared_interactive},"running_tasks_failed":{failed}}}'),
        )
    return {"stopped": True, "processes_killed": killed,
            "interactive_spared": spared_interactive,
            "swept_dead_pid_markers": swept_dead,
            "running_tasks_failed": failed}


@router.post("/api/system/emergency-resume")
async def emergency_resume() -> dict[str, Any]:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO system_state(key, value, updated_at) VALUES ('emergency_stop', '0', datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value='0', updated_at=datetime('now')"
        )
        conn.execute("INSERT INTO activities(event_type, detail) VALUES ('emergency_resume', 'cleared')")
    return {"resumed": True}


# ---------------------------------------------------------------------------
# /api/attention — aggregated issue feed
# ---------------------------------------------------------------------------

@router.get("/api/attention")
async def attention() -> dict[str, Any]:
    """Issues for the red banner. Empty list = banner hides."""
    issues: list[dict[str, Any]] = []
    with db.connect() as conn:
        # Stuck loops — sessions that look active but haven't advanced in a while.
        # Proxy: session with ended_at IS NULL AND started_at > 12h ago.
        for r in conn.execute(
            "SELECT session_id, title, started_at, cwd FROM sessions "
            "WHERE ended_at IS NULL AND started_at < datetime('now', '-12 hours') "
            "ORDER BY started_at DESC LIMIT 5"
        ):
            issues.append({
                "kind": "stuck_session",
                "severity": "warn",
                "session_id": r["session_id"],
                "title": r["title"],
                "started_at": r["started_at"],
                "cwd": r["cwd"],
            })
        # Failed tasks in last 24h.
        for r in conn.execute(
            "SELECT id, title, error_message, completed_at FROM ops_tasks "
            "WHERE status='failed' AND completed_at >= datetime('now', '-24 hours') "
            "ORDER BY completed_at DESC LIMIT 5"
        ):
            issues.append({
                "kind": "failed_task",
                "severity": "error",
                "task_id": r["id"],
                "title": r["title"],
                "error_message": r["error_message"],
                "completed_at": r["completed_at"],
            })
        # Pending decisions.
        pending = conn.execute(
            "SELECT COUNT(*) FROM ops_decisions WHERE status='pending'"
        ).fetchone()[0]
        if pending:
            issues.append({"kind": "decisions_pending", "severity": "warn", "count": pending})
        # Dispatcher staleness. Heartbeat older than 5 min when MC is supposed to be on.
        age = _last_activity_age(conn, "heartbeat")
        if age is not None and age > 300:
            issues.append({"kind": "dispatcher_stale", "severity": "warn",
                           "age_s": int(age)})
        # Overdue schedules.
        for r in conn.execute(
            "SELECT id, name, next_run_at FROM ops_schedules "
            "WHERE enabled=1 AND next_run_at IS NOT NULL "
            "AND next_run_at < datetime('now', '-5 minutes') LIMIT 5"
        ):
            issues.append({"kind": "schedule_overdue", "severity": "warn",
                           "schedule_id": r["id"], "name": r["name"],
                           "next_run_at": r["next_run_at"]})
    return {"issues": issues, "count": len(issues)}


# ---------------------------------------------------------------------------
# /api/system/pressure — retry + compaction + api_errors
# ---------------------------------------------------------------------------

@router.get("/api/system/pressure")
async def system_pressure() -> dict[str, Any]:
    """Retry exhaustion count, compactions, recent api_errors.

    `CLAUDE_CODE_MAX_RETRIES` env (default 10) is the threshold for
    counting a retry exhaustion. Wrapped in try/except per prompt
    (some deployments set malformed values)."""
    try:
        threshold = int(os.environ.get("CLAUDE_CODE_MAX_RETRIES", "10"))
    except ValueError:
        threshold = 10

    with db.connect() as conn:
        retry_exhaust = conn.execute(
            "SELECT COUNT(*) FROM system_events "
            "WHERE subtype='api_error' AND retry_attempt >= ?",
            (threshold,),
        ).fetchone()[0]
        # Also count OTEL api_error events exceeding threshold if we've
        # received any.
        retry_exhaust_otel = conn.execute(
            "SELECT COUNT(*) FROM otel_events "
            "WHERE event_name='api_error' AND attempt_count >= ?",
            (threshold,),
        ).fetchone()[0]
        compactions = conn.execute(
            "SELECT COUNT(*) FROM system_events WHERE subtype='compact_boundary'"
        ).fetchone()[0]
        recent_errors = [dict(r) for r in conn.execute(
            "SELECT timestamp, session_id, subtype, retry_attempt, max_retries, "
            "retry_in_ms, content FROM system_events "
            "WHERE subtype='api_error' ORDER BY timestamp DESC LIMIT 10"
        )]
    return {
        "retry_exhaust_threshold": threshold,
        "retry_exhaust_count_jsonl": retry_exhaust,
        "retry_exhaust_count_otel": retry_exhaust_otel,
        "compaction_count": compactions,
        "recent_api_errors": recent_errors,
    }
