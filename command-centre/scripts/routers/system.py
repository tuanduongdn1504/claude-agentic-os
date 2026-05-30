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
# /api/system/dispatcher — live caps + usage for the hardening guards
# ---------------------------------------------------------------------------

@router.get("/api/system/dispatcher")
async def system_dispatcher() -> dict[str, Any]:
    """Live snapshot of the dispatcher's hardening state.

    Caps come from env (MAX_CONCURRENT / DAILY_COST_CAP_USD / HARD_RISK_GATE);
    usage is computed from ops_tasks. UI uses this to show 'used / cap'.
    """
    def _f(name: str, default: float = 0.0) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    max_concurrent = int(os.environ.get("MISSION_CONTROL_MAX_CONCURRENT") or 3)
    daily_cap = _f("MISSION_CONTROL_DAILY_COST_CAP_USD", 0.0)
    hard_risk_gate = os.environ.get("MISSION_CONTROL_HARD_RISK_GATE", "1") not in ("0", "false", "no")

    with db.connect() as conn:
        running = int(conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks WHERE status='running'"
        ).fetchone()["n"])
        # v0.6.0 — cost split by source. Cap reads api_pool only. Bucket
        # by completed_at to match dispatcher._today_cost_usd; that's when
        # the cost actually realises and prevents the strip from disagreeing
        # with the dispatcher's cap math.
        cost_rows = conn.execute(
            """
            SELECT COALESCE(cost_source, 'unknown') AS src,
                   COALESCE(SUM(cost_usd), 0) AS cost
            FROM ops_tasks
            WHERE cost_usd IS NOT NULL
              AND completed_at IS NOT NULL
              AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
            GROUP BY COALESCE(cost_source, 'unknown')
            """
        ).fetchall()
        risk_gated_today = int(conn.execute(
            """
            SELECT COUNT(*) AS n FROM activities
            WHERE event_type='task_risk_gated'
              AND DATE(created_at, 'localtime') = DATE('now', 'localtime')
            """
        ).fetchone()["n"])
        # v0.6.7 — per-skill budget health-summary. UI uses these so the
        # AttentionBar + DispatcherStrip don't fan out one query per skill.
        skills_with_budget = int(conn.execute(
            "SELECT COUNT(*) AS n FROM skills WHERE daily_budget_usd IS NOT NULL"
        ).fetchone()["n"])
        skills_at_budget = int(conn.execute(
            """
            SELECT COUNT(*) AS n FROM skills s
            WHERE s.daily_budget_usd IS NOT NULL
              AND (
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM ops_tasks
                WHERE assigned_skill = s.name
                  AND cost_source = 'api_pool'
                  AND cost_usd IS NOT NULL
                  AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
              ) >= s.daily_budget_usd
            """
        ).fetchone()["n"])
        # v0.7.0 — adversarial review rollup. skills_with_review = opted-in
        # skills; tasks_awaiting_review_approval = awaiting_approval rows whose
        # review_verdict is non-NULL (i.e. escalated by the reviewer, distinct
        # from risk-gated approvals which have a NULL verdict).
        skills_with_review = int(conn.execute(
            "SELECT COUNT(*) AS n FROM skills WHERE COALESCE(review_mode, 0) = 1"
        ).fetchone()["n"])
        tasks_awaiting_review_approval = int(conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks "
            "WHERE status='awaiting_approval' AND review_verdict IS NOT NULL"
        ).fetchone()["n"])

    by_src = {"api_pool": 0.0, "max_sub": 0.0, "unknown": 0.0}
    for r in cost_rows:
        by_src[r["src"]] = round(float(r["cost"] or 0.0), 6)
    today_cost_api = by_src["api_pool"]
    today_cost_max = by_src["max_sub"]
    today_cost_unknown = by_src["unknown"]
    today_cost_total = round(today_cost_api + today_cost_max + today_cost_unknown, 6)

    back_pressure = running >= max_concurrent
    # Cap is api_pool-only from v0.6.0. Max-sub spend is notional for Pro/Max
    # operators — capping on it would surprise users.
    cost_capped = daily_cap > 0 and today_cost_api >= daily_cap

    return {
        "max_concurrent": max_concurrent,
        "running": running,
        "free_slots": max(0, max_concurrent - running),
        "back_pressure": back_pressure,
        "daily_cost_cap_usd": daily_cap if daily_cap > 0 else None,
        "today_cost_usd": today_cost_total,
        "today_cost_api_pool_usd": today_cost_api,
        "today_cost_max_sub_usd": today_cost_max,
        "today_cost_unknown_usd": today_cost_unknown,
        "cost_capped": cost_capped,
        "hard_risk_gate": hard_risk_gate,
        "risk_gated_today": risk_gated_today,
        "skills_with_budget": skills_with_budget,
        "skills_at_budget": skills_at_budget,
        "skills_with_review": skills_with_review,
        "tasks_awaiting_review_approval": tasks_awaiting_review_approval,
    }


# ---------------------------------------------------------------------------
# /api/system/telegram — bridge health + notification stats
# ---------------------------------------------------------------------------

@router.get("/api/system/telegram")
async def system_telegram() -> dict[str, Any]:
    """Bridge process status + notification_log stats over the last 24h.

    The bridge daemon doesn't write a DB heartbeat, so liveness comes from
    pgrep + stdout log mtime. Errors come from tailing the stderr log.
    Token is never exposed — only its presence."""
    import subprocess

    chat_id = os.environ.get("TELEGRAM_DASH_CHAT_ID", "").strip()
    token_set = bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())

    pid: int | None = None
    alive = False
    try:
        out = subprocess.run(
            ["pgrep", "-f", "telegram_bridge.py"],
            capture_output=True, text=True, timeout=2,
        )
        first = (out.stdout or "").strip().split("\n")[0]
        if first.isdigit():
            pid = int(first)
            alive = True
    except Exception:
        pass

    last_outbound_at: str | None = None
    notified_24h = 0
    by_type: dict[str, int] = {}
    if chat_id:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT MAX(sent_at) AS last FROM notification_log WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            last_outbound_at = row["last"] if row and row["last"] else None
            notified_24h = int(conn.execute(
                "SELECT COUNT(*) AS n FROM notification_log "
                "WHERE chat_id = ? AND sent_at >= datetime('now', '-24 hours')",
                (chat_id,),
            ).fetchone()["n"])
            for r in conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM notification_log "
                "WHERE chat_id = ? AND sent_at >= datetime('now', '-24 hours') "
                "GROUP BY event_type",
                (chat_id,),
            ):
                by_type[r["event_type"]] = int(r["n"])

    log_dir = Path(os.environ.get("CC_LOG_DIR") or (Path.home() / ".command-centre" / "logs"))
    stdout_log = log_dir / "telegram-bridge.stdout.log"
    stderr_log = log_dir / "telegram-bridge.stderr.log"
    log_mtime_age_s: float | None = None
    try:
        log_mtime_age_s = max(0.0, time.time() - stdout_log.stat().st_mtime)
    except OSError:
        pass

    last_error: str | None = None
    last_error_at: str | None = None
    try:
        size = stderr_log.stat().st_size
        if size > 0:
            with stderr_log.open("rb") as f:
                f.seek(-min(4096, size), 2)
                tail = f.read().decode("utf-8", errors="replace")
            lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
            if lines:
                last_error = lines[-1][:240]
                from datetime import datetime as _dt
                last_error_at = _dt.fromtimestamp(stderr_log.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        pass

    return {
        "configured": token_set and bool(chat_id),
        "alive": alive,
        "pid": pid,
        "chat_id_set": bool(chat_id),
        "token_set": token_set,
        "last_outbound_at": last_outbound_at,
        "notified_24h": notified_24h,
        "notified_24h_by_type": by_type,
        "stdout_log_mtime_age_s": round(log_mtime_age_s, 1) if log_mtime_age_s is not None else None,
        "last_error": last_error,
        "last_error_at": last_error_at,
    }


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

        # Hardening: back-pressure (running == max_concurrent).
        try:
            max_concurrent = int(os.environ.get("MISSION_CONTROL_MAX_CONCURRENT") or 3)
        except ValueError:
            max_concurrent = 3
        running = int(conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks WHERE status='running'"
        ).fetchone()["n"])
        if running >= max_concurrent:
            issues.append({
                "kind": "back_pressure", "severity": "warn",
                "running": running, "max_concurrent": max_concurrent,
            })

        # Hardening: daily cost cap reached. v0.6.0 — api_pool only.
        try:
            cap = float(os.environ.get("MISSION_CONTROL_DAILY_COST_CAP_USD") or 0)
        except ValueError:
            cap = 0.0
        if cap > 0:
            today_cost_api = float(conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0) AS s
                FROM ops_tasks
                WHERE cost_source = 'api_pool'
                  AND cost_usd IS NOT NULL
                  AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
                """
            ).fetchone()["s"])
            if today_cost_api >= cap:
                issues.append({
                    "kind": "cost_capped", "severity": "error",
                    "today_cost_usd": today_cost_api,
                    "today_cost_api_pool_usd": today_cost_api,
                    "cap_usd": cap,
                    "message": (
                        f"API-pool spend reached cap (${today_cost_api:.2f} of "
                        f"${cap:.2f} today). Max-sub usage continues."
                    ),
                })

        # v0.6.7 — per-skill daily budget. Severity `warning` (yellow), NOT
        # `error` — operator-tuneable and expected to trigger more often than
        # the global cap. One issue covers N capped skills; UI surfaces the
        # count + first skill's spend / budget for context.
        capped_rows = conn.execute(
            """
            SELECT s.name AS name, s.daily_budget_usd AS budget,
                   COALESCE((
                     SELECT SUM(cost_usd) FROM ops_tasks
                     WHERE assigned_skill = s.name
                       AND cost_source = 'api_pool'
                       AND cost_usd IS NOT NULL
                       AND DATE(completed_at, 'localtime')
                           = DATE('now', 'localtime')
                   ), 0) AS today_cost
            FROM skills s
            WHERE s.daily_budget_usd IS NOT NULL
            ORDER BY s.name
            """
        ).fetchall()
        capped = [r for r in capped_rows
                  if float(r["today_cost"]) >= float(r["budget"])]
        if capped:
            first = capped[0]
            first_name = str(first["name"])
            first_today = float(first["today_cost"])
            first_budget = float(first["budget"])
            n = len(capped)
            issues.append({
                "kind": "skill_budget_capped",
                "severity": "warning",
                "count": n,
                "skill": first_name,
                "today_cost_usd": first_today,
                "daily_budget_usd": first_budget,
                "title": f"{n} skill{'s' if n != 1 else ''} at daily budget",
                "message": (
                    f"{first_name} blocked at ${first_today:.2f} / "
                    f"${first_budget:.2f}"
                    + (f" (+{n - 1} more)" if n > 1 else "")
                ),
            })

        # v0.7.0 — review-escalated tasks. A task in awaiting_approval with a
        # non-NULL review_verdict was escalated by the reviewer (failed
        # verification twice, or unverifiable). Severity `warning` (amber,
        # reusing the v0.6.7 tone) — folds into the same warning count. Distinct
        # `kind` from the risk-gated approval so the operator knows it needs
        # approval BECAUSE review failed, not because of the risk gate.
        review_n = int(conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks "
            "WHERE status='awaiting_approval' AND review_verdict IS NOT NULL"
        ).fetchone()["n"])
        if review_n:
            first_rv = conn.execute(
                "SELECT id, title, review_verdict, review_feedback FROM ops_tasks "
                "WHERE status='awaiting_approval' AND review_verdict IS NOT NULL "
                "ORDER BY COALESCE(completed_at, started_at, created_at) DESC LIMIT 1"
            ).fetchone()
            rv = first_rv["review_verdict"]
            fb = first_rv["review_feedback"]
            issues.append({
                "kind": "review_escalated",
                "severity": "warning",
                "count": review_n,
                "task_id": int(first_rv["id"]),
                "verdict": rv,
                "review_feedback": fb,
                "title": f"{review_n} task{'s' if review_n != 1 else ''} need review approval",
                "message": (
                    f"#{first_rv['id']} {rv}"
                    + (f" — {fb}" if fb else "")
                    + (f" (+{review_n - 1} more)" if review_n > 1 else "")
                ),
            })

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
