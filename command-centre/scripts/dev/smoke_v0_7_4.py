#!/usr/bin/env python3
"""Smoke test for v0.7.4 — a transient usage/rate limit (HTTP 429) is RETRYABLE.

Before v0.7.4 the dispatcher terminal-`failed` any child that exited non-zero,
including a 429 ("You've hit your limit · resets …"). On a usage-capped plan
that silently burned every queued task during a cap window. v0.7.4 classifies a
429 as transient and RE-QUEUES the task (pending + a future scheduled_for
backoff) without bumping consecutive_failures.

Shape mirrors smoke_v0_7_0.py / smoke_v0_7_1.py:
  - Real FastAPI server on 127.0.0.1:8874 backed by a temp SQLite DB (gives a
    fully-migrated schema).
  - dispatcher.run_once() driven directly with the implementer/reviewer children
    stubbed (no real claude-CLI spawn — so this is unaffected by the very 429
    it tests).
  - `_looks_rate_limited` is unit-smoked against fixtures (the real headless 429
    message + structured envelope + negatives incl. the false-positive trap
    "rate limiting").

Usage:
  ~/.command-centre/venv/bin/python3 scripts/dev/smoke_v0_7_4.py
  Exits 0 if all stop conditions pass, 1 otherwise.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "command-centre" / "scripts"
MC_SCRIPTS_DIR = (
    REPO_ROOT / "command-centre" / ".claude" / "skills" /
    "mission-control" / "scripts"
)

API_PORT = 8874


# ---------------------------------------------------------------------------
# Server boot (mirrors smoke_v0_7_0.py)
# ---------------------------------------------------------------------------

def _wait_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_server(install_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["CC_INSTALL_DIR"] = str(install_dir)
    env["CC_HOST"] = "127.0.0.1"
    env["CC_PORT"] = str(API_PORT)
    venv_py = Path.home() / ".command-centre" / "venv" / "bin" / "python3"
    py = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.Popen(
        [py, str(SCRIPTS_DIR / "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(SCRIPTS_DIR),
    )
    if not _wait_port("127.0.0.1", API_PORT):
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        raise RuntimeError(
            f"server failed to come up on :{API_PORT}\n"
            f"stdout:\n{out.decode(errors='replace')}\n"
            f"stderr:\n{err.decode(errors='replace')}"
        )
    return proc


def _open_db(install_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(install_dir / "data" / "command-centre.db", timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {name}" + (f" — {note}" if note else ""))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_skill(install_dir: Path, name: str, review_mode: int = 0,
                autonomy: str = "auto") -> None:
    with _open_db(install_dir) as conn:
        conn.execute(
            """
            INSERT INTO skills(name, environment, description, path,
                               autonomy_level, user_invocable, review_mode)
            VALUES (?, 'ide:project', ?, ?, ?, 1, ?)
            """,
            (name, f"test skill {name}", f"/tmp/{name}.md", autonomy, review_mode),
        )


def _seed_pending_task(install_dir: Path, skill: str) -> int:
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, description, status, assigned_skill, cost_source,
                risk_level, dry_run, execution_mode
            ) VALUES (?, ?, 'pending', ?, 'api_pool', 'low', 0, 'stream')
            """,
            (f"task for {skill}", f"do the {skill} work", skill),
        )
        return int(cur.lastrowid or 0)


def _task_row(install_dir: Path, task_id: int) -> dict | None:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT * FROM ops_tasks WHERE id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None


def _clear_tasks(install_dir: Path) -> None:
    with _open_db(install_dir) as conn:
        conn.execute("DELETE FROM ops_tasks")


def _activities_for(install_dir: Path, event_type: str) -> list[sqlite3.Row]:
    with _open_db(install_dir) as conn:
        return list(conn.execute(
            "SELECT * FROM activities WHERE event_type=? ORDER BY id",
            (event_type,),
        ).fetchall())


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _stub_impl(dispatcher, *, ok: bool, rate_limited: bool = False,
               stdout: str = "impl-out") -> None:
    """Replace BOTH implementer children with a canned result."""
    def _stub(task: dict) -> dict:
        return {"ok": ok, "stdout": stdout, "stderr": "",
                "returncode": 0 if ok else 1, "session_id": "stub-sid",
                "rate_limited": rate_limited}
    dispatcher._run_classic = _stub
    dispatcher._run_stream = _stub


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

def run(install_dir: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.path.insert(0, str(MC_SCRIPTS_DIR))

    import dispatcher
    import task_tracker  # noqa: F401

    # ----- S1: _looks_rate_limited classifier ----------------------------
    print("\nS1 · _looks_rate_limited classifier (positive + negative)")
    lrl = dispatcher._looks_rate_limited
    _check("S1a · real headless 429 message → True",
           lrl("You've hit your limit · resets 1:50pm (Asia/Saigon)"))
    _check("S1b · structured error envelope rate_limit → True",
           lrl('{"type":"assistant","error":"rate_limit","apiErrorStatus":429}'))
    _check("S1c · apiErrorStatus 429 → True",
           lrl('some text "apiErrorStatus":429 more'))
    _check("S1d · 'usage limit' → True", lrl("Your usage limit was reached"))
    _check("S1e · normal output → False", not lrl("wrote the file /tmp/x.txt"))
    _check("S1f · None / empty → False", (not lrl(None)) and (not lrl("")))
    _check("S1g · false-positive trap 'rate limiting' → False",
           not lrl("This task implements rate limiting for the API"))

    # ----- S2: rate-limited implementer → RE-QUEUED, not failed ----------
    print("\nS2 · rate-limited implementer → re-queued (pending), not failed")
    _clear_tasks(install_dir)
    _seed_skill(install_dir, "auto-skill", review_mode=0, autonomy="auto")
    _stub_impl(dispatcher, ok=False, rate_limited=True)
    t_rl = _seed_pending_task(install_dir, "auto-skill")
    stats = dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_rl)
    _check("S2a · status back to 'pending' (re-queued, not failed)",
           row and row["status"] == "pending",
           f"status={row['status'] if row else '?'}")
    _check("S2b · scheduled_for set in the FUTURE (backoff)",
           row and row["scheduled_for"] is not None,
           f"scheduled_for={row['scheduled_for'] if row else '?'}")
    _check("S2c · consecutive_failures NOT bumped (stays 0)",
           row and (row["consecutive_failures"] or 0) == 0,
           f"cfail={row['consecutive_failures'] if row else '?'}")
    _check("S2d · stats.rate_limited incremented",
           stats.get("rate_limited", 0) >= 1, f"stats={stats}")
    _check("S2e · NOT counted as a failure",
           stats.get("failed", 0) == 0, f"failed={stats.get('failed')}")
    acts = _activities_for(install_dir, "task_rate_limited")
    _check("S2f · task_rate_limited activity logged", len(acts) >= 1,
           f"acts={len(acts)}")

    # ----- S3: backoff blocks an immediate re-claim ----------------------
    print("\nS3 · re-queued task is NOT claimed again until the backoff elapses")
    stats2 = dispatcher.run_once(verbose=False)
    row2 = _task_row(install_dir, t_rl)
    _check("S3a · still pending (scheduled_for in the future blocks claim)",
           row2 and row2["status"] == "pending",
           f"status={row2['status'] if row2 else '?'}")
    _check("S3b · second sweep claimed 0",
           stats2.get("claimed", 0) == 0, f"claimed={stats2.get('claimed')}")

    # ----- S4: a REAL failure still fails (regression) -------------------
    print("\nS4 · non-rate-limited failure still → failed (regression)")
    _clear_tasks(install_dir)
    _stub_impl(dispatcher, ok=False, rate_limited=False)
    t_fail = _seed_pending_task(install_dir, "auto-skill")
    stats = dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_fail)
    _check("S4a · status=failed", row and row["status"] == "failed",
           f"status={row['status'] if row else '?'}")
    _check("S4b · consecutive_failures bumped to 1",
           row and (row["consecutive_failures"] or 0) == 1,
           f"cfail={row['consecutive_failures'] if row else '?'}")
    _check("S4c · stats.failed incremented, rate_limited not",
           stats.get("failed", 0) >= 1 and stats.get("rate_limited", 0) == 0,
           f"stats={stats}")

    # ----- S5: normal success still completes (regression) ---------------
    print("\nS5 · normal success still completes (review off)")
    _clear_tasks(install_dir)
    _stub_impl(dispatcher, ok=True)
    t_ok = _seed_pending_task(install_dir, "auto-skill")
    stats = dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_ok)
    _check("S5a · status=done", row and row["status"] == "done",
           f"status={row['status'] if row else '?'}")
    _check("S5b · counted as succeeded",
           stats.get("succeeded", 0) >= 1, f"stats={stats}")

    # ----- S6: reviewer 429 → re-queued, not escalated -------------------
    print("\nS6 · reviewer hits a 429 → task re-queued, NOT escalated to human")
    _clear_tasks(install_dir)
    _seed_skill(install_dir, "rev-skill", review_mode=1, autonomy="auto")
    _stub_impl(dispatcher, ok=True)  # implementer succeeds
    dispatcher._run_review = lambda task, impl: {
        "verdict": "MANUAL_VERIFY_REQUIRED", "reason": "reviewer exited rc=1",
        "cost_usd": None, "rate_limited": True}
    t_rev = _seed_pending_task(install_dir, "rev-skill")
    stats = dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_rev)
    _check("S6a · re-queued to 'pending' (NOT awaiting_approval)",
           row and row["status"] == "pending",
           f"status={row['status'] if row else '?'}")
    _check("S6b · review_verdict NOT stored (re-queued before the verdict branch)",
           row and row["review_verdict"] is None,
           f"verdict={row['review_verdict'] if row else '?'}")
    _check("S6c · scheduled_for backoff set",
           row and row["scheduled_for"] is not None,
           f"scheduled_for={row['scheduled_for'] if row else '?'}")
    _check("S6d · stats.rate_limited incremented, not escalated",
           stats.get("rate_limited", 0) >= 1
           and stats.get("review_escalated", 0) == 0, f"stats={stats}")

    # ----- S7: VERIFIED review still completes (regression) --------------
    print("\nS7 · a VERIFIED review still completes (rate-limit check is narrow)")
    _clear_tasks(install_dir)
    _stub_impl(dispatcher, ok=True)
    dispatcher._run_review = lambda task, impl: {
        "verdict": "VERIFIED", "reason": None, "cost_usd": None}
    t_v = _seed_pending_task(install_dir, "rev-skill")
    dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_v)
    _check("S7a · VERIFIED task is done",
           row and row["status"] == "done" and row["review_verdict"] == "VERIFIED",
           f"status={row['status'] if row else '?'}")

    # ----- S8: requeue_for_retry unit ------------------------------------
    print("\nS8 · requeue_for_retry — pending + future scheduled_for, no cfail bump")
    _clear_tasks(install_dir)
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            "INSERT INTO ops_tasks(title, status, cost_source, consecutive_failures) "
            "VALUES ('rq', 'running', 'api_pool', 0)")
        rq_id = int(cur.lastrowid or 0)
    task_tracker.requeue_for_retry(rq_id, 900)
    row = _task_row(install_dir, rq_id)
    _check("S8a · status='pending'", row and row["status"] == "pending",
           f"status={row['status'] if row else '?'}")
    _check("S8b · consecutive_failures still 0",
           row and (row["consecutive_failures"] or 0) == 0,
           f"cfail={row['consecutive_failures'] if row else '?'}")
    # scheduled_for should be ~900s ahead of now → strictly greater than now.
    with _open_db(install_dir) as conn:
        future = conn.execute(
            "SELECT scheduled_for > datetime('now') AS f FROM ops_tasks WHERE id=?",
            (rq_id,)).fetchone()
    _check("S8c · scheduled_for is in the future",
           future and future["f"] == 1,
           f"scheduled_for_future={future['f'] if future else '?'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v074-smoke-")
    install_dir = Path(tmp)
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"smoke install dir: {install_dir}")

    server_proc = _start_server(install_dir)
    try:
        os.environ["CC_INSTALL_DIR"] = str(install_dir)
        run(install_dir)
    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()

    print("\n=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"  {passed} / {total} checks passed")
    failed = [name for name, ok, _ in RESULTS if not ok]
    if failed:
        print("\n  FAILED:")
        for name in failed:
            print(f"    - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
