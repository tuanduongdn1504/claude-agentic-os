"""Mission Control heartbeat — launchd-driven, 120s cadence.

Each tick:
  1. Materialize any due schedules into ops_tasks (BEGIN IMMEDIATE to
     guard against two heartbeats racing).
  2. Call dispatcher.run_once() — which itself claims pending tasks
     atomically.
  3. Write a heartbeat activity row so the UI + doctor can observe
     staleness.

Running standalone: `python heartbeat.py --once` is safe to invoke
manually; the atomic UPDATE prevents double-dispatch with the daemon.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

_INSTALL_DIR = Path(os.environ.get("CC_INSTALL_DIR") or
                    (Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(_INSTALL_DIR / "scripts"))

import db  # noqa: E402
import task_tracker  # noqa: E402
import dispatcher  # noqa: E402
from cron_parser import next_run  # noqa: E402


TICK_SECONDS = int(os.environ.get("MISSION_CONTROL_TICK_SECONDS", "120"))


# ---------------------------------------------------------------------------
# Schedule materialization
# ---------------------------------------------------------------------------

def materialize_schedules() -> int:
    """For each enabled schedule whose next_run_at has arrived (or is NULL),
    create an ops_tasks row and advance next_run_at. BEGIN IMMEDIATE
    avoids double-materialization across concurrent heartbeats."""
    created = 0
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            due = conn.execute(
                """
                SELECT id, name, cron_expression, task_title, task_description,
                       assigned_skill, next_run_at
                FROM ops_schedules
                WHERE enabled = 1
                  AND (next_run_at IS NULL OR next_run_at <= datetime('now'))
                """
            ).fetchall()
            for s in due:
                # Create task. If schedule never ran before, we still fire
                # it this tick (next_run_at IS NULL).
                conn.execute(
                    """
                    INSERT INTO ops_tasks(title, description, status, priority,
                                          assigned_skill, execution_mode)
                    VALUES (?, ?, 'pending', 0, ?, 'stream')
                    """,
                    (s["task_title"], s["task_description"], s["assigned_skill"]),
                )
                created += 1
                # Advance next_run_at.
                try:
                    nxt = next_run(s["cron_expression"])
                    conn.execute(
                        "UPDATE ops_schedules SET next_run_at = ?, last_run_at = datetime('now') WHERE id = ?",
                        (nxt.strftime("%Y-%m-%d %H:%M:%S"), s["id"]),
                    )
                except Exception as exc:
                    # Leave next_run_at as-is so the attention feed surfaces it.
                    conn.execute(
                        "UPDATE ops_schedules SET last_run_at = datetime('now') WHERE id = ?",
                        (s["id"],),
                    )
                    conn.execute(
                        "INSERT INTO activities(event_type, detail) VALUES (?, ?)",
                        ("schedule_cron_error", f"id={s['id']} expr={s['cron_expression']} err={exc!r}"),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return created


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------

def tick(verbose: bool = False) -> dict:
    t0 = time.monotonic()
    created = materialize_schedules()
    stats = dispatcher.run_once(verbose=verbose)
    stats["schedules_materialized"] = created
    stats["tick_ms"] = int((time.monotonic() - t0) * 1000)
    task_tracker.log_activity(
        "heartbeat",
        f"claimed={stats['claimed']} succ={stats['succeeded']} "
        f"fail={stats['failed']} sched={created} ms={stats['tick_ms']}",
        metadata=stats,
    )
    if verbose:
        print(json.dumps(stats, indent=2))
    return stats


# ---------------------------------------------------------------------------
# Daemon loop (for launchd KeepAlive)
# ---------------------------------------------------------------------------

_STOP = False


def _handle_stop(signum, _frame):
    global _STOP
    _STOP = True


def loop(interval_s: int = TICK_SECONDS, verbose: bool = False) -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    while not _STOP:
        try:
            tick(verbose=verbose)
        except Exception as exc:
            # Never crash the daemon — launchd would restart but we'd
            # lose transient state.
            task_tracker.log_activity("heartbeat_error", repr(exc))
            print(f"[heartbeat] error: {exc!r}", file=sys.stderr)
        # Interruptible sleep.
        for _ in range(interval_s):
            if _STOP:
                break
            time.sleep(1.0)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one tick and exit")
    ap.add_argument("--loop", action="store_true", help="daemon loop (default if called with no args)")
    ap.add_argument("--interval", type=int, default=TICK_SECONDS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.once:
        stats = tick(verbose=True)
        print(json.dumps(stats, indent=2))
        return 0
    return loop(interval_s=args.interval, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
