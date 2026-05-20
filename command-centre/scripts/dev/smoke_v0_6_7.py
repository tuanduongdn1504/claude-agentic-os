#!/usr/bin/env python3
"""Smoke test for v0.6.7 — per-skill daily cost budgets.

Walks the 13 stop conditions from
`observability/(C) build-your-own-dashboard-prompt-v0.6.7-amendment.md`.

Shape mirrors v0.6.5 / v0.6.4 smoke tests:
  - Real FastAPI server on 127.0.0.1:8871, backed by a temp SQLite DB.
  - dispatcher.run_once() driven directly to assert the pre-claim check.
  - telegram_bridge imported in-process; `_tg` stubbed so no real Bot API
    call is made; `_today_local_date` monkey-patched for the multi-day
    re-fire check.

Usage:
  python3 scripts/dev/smoke_v0_6_7.py
  Exits 0 if all stop conditions pass, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "command-centre" / "scripts"
MC_SCRIPTS_DIR = (
    REPO_ROOT / "command-centre" / ".claude" / "skills" /
    "mission-control" / "scripts"
)

API_PORT = 8871
BOT_TOKEN = "test-token-not-real-v067"
CHAT_ID = "646464"


# ---------------------------------------------------------------------------
# Server boot
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
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_get(path: str) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{API_PORT}{path}", timeout=5,
    ) as r:
        return json.loads(r.read())


def _api_patch(path: str, payload: dict) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        data=body, method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_skill(install_dir: Path, name: str, budget: float | None) -> None:
    with _open_db(install_dir) as conn:
        conn.execute(
            """
            INSERT INTO skills(name, environment, description, path,
                               autonomy_level, user_invocable, daily_budget_usd)
            VALUES (?, 'ide:project', ?, ?, 'auto', 1, ?)
            """,
            (name, f"test skill {name}", f"/tmp/{name}.md", budget),
        )


def _seed_completed_task(install_dir: Path, skill: str, cost: float) -> int:
    """Insert a completed api_pool task that counts toward today's spend."""
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, status, assigned_skill, cost_usd, cost_source,
                completed_at
            ) VALUES (?, 'done', ?, ?, 'api_pool', datetime('now'))
            """,
            (f"prior {skill} task", skill, cost),
        )
        return int(cur.lastrowid or 0)


def _seed_pending_task(install_dir: Path, skill: str) -> int:
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, status, assigned_skill, cost_source, risk_level
            ) VALUES (?, 'pending', ?, 'api_pool', 'low')
            """,
            (f"queue-{skill}", skill),
        )
        return int(cur.lastrowid or 0)


def _task_status(install_dir: Path, task_id: int) -> str | None:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id=?", (task_id,),
        ).fetchone()
        return row["status"] if row else None


def _activities_for(install_dir: Path, event_type: str) -> list[sqlite3.Row]:
    with _open_db(install_dir) as conn:
        return list(conn.execute(
            "SELECT * FROM activities WHERE event_type=? ORDER BY id",
            (event_type,),
        ).fetchall())


# ---------------------------------------------------------------------------
# Stub the actual claude-CLI spawn so dispatcher.run_once stays hermetic when
# it does reach the autonomy-allowed code path.
# ---------------------------------------------------------------------------

def _make_dispatcher_stub(dispatcher):
    def _stub_classic(task: dict) -> dict:
        return {"ok": True, "stdout": "stub-out", "stderr": "",
                "returncode": 0}
    def _stub_stream(task: dict) -> dict:
        return {"ok": True, "stdout": "stub-out", "stderr": "",
                "returncode": 0, "session_id": "stub-sid"}
    dispatcher._run_classic = _stub_classic
    dispatcher._run_stream = _stub_stream


# ---------------------------------------------------------------------------
# Telegram capture
# ---------------------------------------------------------------------------

class TGCapture:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._next_mid = 5000

    def __call__(self, method: str, payload: dict, timeout: float = 10.0) -> dict:
        if method == "sendMessage":
            self._next_mid += 1
            self.sent.append({**payload, "_mid": self._next_mid})
            return {"message_id": self._next_mid,
                    "chat": {"id": payload.get("chat_id")}}
        return {}

    def clear(self) -> None:
        self.sent.clear()

    def texts(self) -> list[str]:
        return [m.get("text", "") for m in self.sent]


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

def run(install_dir: Path) -> None:
    # Make sure both server-scripts and mission-control scripts are importable.
    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.path.insert(0, str(MC_SCRIPTS_DIR))

    # ----- S1: Migration idempotent -----
    print("\nS1 · migration idempotent")
    import db as serverdb
    serverdb.apply_migrations()  # second call must be a no-op
    with _open_db(install_dir) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(skills)")}
    _check("S1a · skills.daily_budget_usd column exists",
           "daily_budget_usd" in cols, f"cols={sorted(cols)}")

    # Existing rows should have NULL.
    _seed_skill(install_dir, "skill-null", None)
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT daily_budget_usd FROM skills WHERE name='skill-null'"
        ).fetchone()
    _check("S1b · existing row has NULL budget by default",
           row is not None and row["daily_budget_usd"] is None,
           f"row={dict(row) if row else None}")

    # Three skills as fixtures (no budget / under / over).
    _seed_skill(install_dir, "skill-under", 5.00)
    _seed_skill(install_dir, "skill-over",  1.00)
    _seed_completed_task(install_dir, "skill-under", 0.50)  # 50% of $5
    _seed_completed_task(install_dir, "skill-over",  1.50)  # 150% of $1

    # ----- S2: PATCH /api/skills/{name}/budget accepts valid payload -----
    print("\nS2 · PATCH .../budget accepts valid payload")
    status, body = _api_patch("/api/skills/skill-under/budget",
                              {"daily_budget_usd": 5.0})
    _check("S2a · PATCH 200", status == 200, f"status={status} body={body!r}")
    after = _api_get("/api/skills")
    under = next((s for s in after["items"] if s["name"] == "skill-under"), None)
    _check("S2b · GET /api/skills reflects new value",
           under is not None and abs(under["daily_budget_usd"] - 5.0) < 1e-9,
           f"under={under}")
    _check("S2c · GET /api/skills returns today_cost_usd",
           under is not None and under.get("today_cost_usd") is not None,
           f"today={under.get('today_cost_usd') if under else None}")

    # ----- S3: PATCH /api/skills/{name}/budget rejects invalid -----
    print("\nS3 · PATCH .../budget rejects invalid")
    status, _ = _api_patch("/api/skills/skill-under/budget",
                           {"daily_budget_usd": -1.0})
    _check("S3a · negative → 400", status == 400, f"status={status}")
    status, _ = _api_patch("/api/skills/skill-under/budget",
                           {"daily_budget_usd": "lots"})
    _check("S3b · non-numeric → 400", status == 400, f"status={status}")
    status, body = _api_patch("/api/skills/skill-under/budget",
                              {"daily_budget_usd": None})
    _check("S3c · null clears (200)",
           status == 200 and body.get("daily_budget_usd") is None,
           f"status={status} body={body}")
    # Put it back so subsequent checks have a meaningful budget.
    _api_patch("/api/skills/skill-under/budget", {"daily_budget_usd": 5.0})

    # ----- S4: Pre-claim refusal — at budget -----
    print("\nS4 · pre-claim refusal — at budget")
    # Push skill-under to exactly $5 (at budget).
    _seed_completed_task(install_dir, "skill-under", 4.50)  # 0.5 + 4.5 = $5.00
    task_id = _seed_pending_task(install_dir, "skill-under")

    import dispatcher
    _make_dispatcher_stub(dispatcher)
    stats = dispatcher.run_once(verbose=False)

    _check("S4a · stats.skill_budget_capped >= 1",
           stats.get("skill_budget_capped", 0) >= 1, f"stats={stats}")
    _check("S4b · task reverted to pending",
           _task_status(install_dir, task_id) == "pending",
           f"status={_task_status(install_dir, task_id)}")
    acts = _activities_for(install_dir, "dispatcher_skill_budget_capped")
    _check("S4c · activity row logged",
           len(acts) >= 1, f"acts={len(acts)}")
    _check("S4d · activity metadata captures spend + budget",
           bool(acts) and json.loads(acts[-1]["metadata"]).get("skill")
                == "skill-under",
           f"meta={acts[-1]['metadata'] if acts else None}")

    feed = _api_get("/api/attention")
    kinds = [it["kind"] for it in feed["issues"]]
    _check("S4e · /api/attention surfaces skill_budget_capped",
           "skill_budget_capped" in kinds, f"kinds={kinds}")
    issue = next((it for it in feed["issues"]
                  if it["kind"] == "skill_budget_capped"), None)
    _check("S4f · attention issue severity is warning (not error)",
           issue is not None and issue.get("severity") == "warning",
           f"issue={issue}")

    # ----- S5: Pre-claim refusal — over budget -----
    print("\nS5 · pre-claim refusal — over budget (operator-lowered mid-day)")
    # skill-over is already at $1.50 today; budget = $1.00.
    over_task = _seed_pending_task(install_dir, "skill-over")
    dispatcher.run_once(verbose=False)
    _check("S5 · over-budget task stays pending",
           _task_status(install_dir, over_task) == "pending",
           f"status={_task_status(install_dir, over_task)}")

    # ----- S6: NULL budget = unlimited -----
    print("\nS6 · NULL budget = unlimited")
    _seed_skill(install_dir, "skill-unlimited", None)
    _seed_completed_task(install_dir, "skill-unlimited", 999.99)
    unl_task = _seed_pending_task(install_dir, "skill-unlimited")
    dispatcher.run_once(verbose=False)
    final = _task_status(install_dir, unl_task)
    # With the stub, an auto-autonomy task should complete (done) or at least
    # leave 'pending' — what matters is that the budget guard didn't kick in.
    _check("S6 · NULL-budget skill is NOT refused",
           final in ("done", "running"),
           f"status={final}")

    # ----- S7: Zero budget = blocked -----
    print("\nS7 · zero budget = blocked (operator temp-disable)")
    _seed_skill(install_dir, "skill-zero", 0.0)
    zero_task = _seed_pending_task(install_dir, "skill-zero")
    dispatcher.run_once(verbose=False)
    _check("S7 · zero-budget task stays pending",
           _task_status(install_dir, zero_task) == "pending",
           f"status={_task_status(install_dir, zero_task)}")

    # ----- S8: Global cap fires before per-skill -----
    print("\nS8 · global cap still fires (order of checks verified)")
    # Set global cap = $8; today_cost total ≥ $8 should refuse via global cap.
    # Bump dispatcher's module-level cap (the run-time setting reads env at
    # import; mutate the constant directly for test purposes).
    prior_cap = dispatcher.DAILY_COST_CAP_USD
    dispatcher.DAILY_COST_CAP_USD = 8.0
    try:
        # Existing today's spend already exceeds $8 from skill-unlimited's
        # $999.99 (S6) — so this is the "both could trigger" condition.
        gcap_task = _seed_pending_task(install_dir, "skill-under")
        stats = dispatcher.run_once(verbose=False)
        _check("S8a · stats.cost_capped fires (global beats skill check)",
               stats.get("cost_capped", 0) == 1,
               f"stats={stats}")
        _check("S8b · skill_budget_capped did NOT advance (global short-circuits)",
               stats.get("skill_budget_capped", 0) == 0,
               f"stats={stats}")
        _check("S8c · queued task remains pending",
               _task_status(install_dir, gcap_task) == "pending")
    finally:
        dispatcher.DAILY_COST_CAP_USD = prior_cap

    # ----- S9: Telegram push fires once per skill per day, re-fires next day -----
    print("\nS9 · Telegram push first-fire + dedupe + next-day re-fire")
    os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
    os.environ["TELEGRAM_DASH_CHAT_ID"] = CHAT_ID
    os.environ["CC_DASHBOARD_URL"] = f"http://127.0.0.1:{API_PORT}"
    import telegram_bridge as bridge
    bridge.BOT_TOKEN = BOT_TOKEN
    bridge.CHAT_ID = CHAT_ID
    bridge.DASHBOARD_URL = f"http://127.0.0.1:{API_PORT}"

    cap = TGCapture()
    bridge._tg = cap

    # Force "today" so we know what dedupe key was used.
    bridge._today_local_date = lambda: "2026-05-20"
    stats = bridge._outbound_tick()
    sb_first = stats.get("skill_budget_exceeded", 0)
    fired_texts = [t for t in cap.texts() if "Skill budget reached" in t]
    _check("S9a · first tick fires push for over-budget skill(s)",
           sb_first >= 1 and len(fired_texts) >= 1,
           f"stats={stats} fired={fired_texts}")

    cap.clear()
    stats2 = bridge._outbound_tick()
    _check("S9b · second tick same day dedupes (no extra push)",
           stats2.get("skill_budget_exceeded", 0) == 0
           and not any("Skill budget reached" in t for t in cap.texts()),
           f"stats={stats2} replies={cap.texts()}")

    # Roll the local date forward — new dedupe key, fresh notification.
    bridge._today_local_date = lambda: "2026-05-21"
    cap.clear()
    stats3 = bridge._outbound_tick()
    _check("S9c · next local day → re-fires",
           stats3.get("skill_budget_exceeded", 0) >= 1
           and any("Skill budget reached" in t for t in cap.texts()),
           f"stats={stats3}")

    # ----- S10: AttentionBar count accuracy -----
    print("\nS10 · /api/attention count + dispatcher rollup accuracy")
    disp = _api_get("/api/system/dispatcher")
    _check("S10a · skills_with_budget exposed",
           "skills_with_budget" in disp, f"keys={list(disp.keys())}")
    _check("S10b · skills_at_budget exposed",
           "skills_at_budget" in disp, f"keys={list(disp.keys())}")
    before_count = int(disp["skills_at_budget"])
    _check("S10c · skills_at_budget reflects multiple capped skills",
           before_count >= 2, f"count={before_count}")
    # Drop skill-over's budget to a huge number → it's no longer over.
    _api_patch("/api/skills/skill-over/budget",
               {"daily_budget_usd": 1000.0})
    disp2 = _api_get("/api/system/dispatcher")
    _check("S10d · count drops after operator lifts one budget",
           int(disp2["skills_at_budget"]) < before_count,
           f"before={before_count} after={disp2['skills_at_budget']}")

    # ----- S11: UI 3-tier derivation (matches SkillLauncher logic) -----
    print("\nS11 · UI 3-tier thresholds match the API values")
    # Replicate the derivation here so the UI hook math is verified end-to-end
    # against the actual /api/skills response. Tier ladder: <0.8 dim, 0.8-1.0
    # amber, ≥1.0 red.
    def tier(budget: float | None, today: float) -> str:
        if budget is None:
            return "none"
        if budget <= 0:
            return "red"  # zero budget = blocked
        if today >= budget:
            return "red"
        if today >= budget * 0.8:
            return "amber"
        return "dim"

    # Configure three skills to hit each tier deterministically.
    _api_patch("/api/skills/skill-null/budget",
               {"daily_budget_usd": 100.0})  # 0 spend → dim
    _api_patch("/api/skills/skill-under/budget",
               {"daily_budget_usd": 6.0})    # $5 spend → amber (5/6 = 83%)
    _api_patch("/api/skills/skill-over/budget",
               {"daily_budget_usd": 1.0})    # $1.50 spend → red

    payload = _api_get("/api/skills")
    by = {s["name"]: s for s in payload["items"]}
    _check("S11a · skill-null → dim tier",
           tier(by["skill-null"]["daily_budget_usd"],
                by["skill-null"]["today_cost_usd"]) == "dim",
           f"row={by['skill-null']}")
    _check("S11b · skill-under → amber tier",
           tier(by["skill-under"]["daily_budget_usd"],
                by["skill-under"]["today_cost_usd"]) == "amber",
           f"row={by['skill-under']}")
    _check("S11c · skill-over → red tier",
           tier(by["skill-over"]["daily_budget_usd"],
                by["skill-over"]["today_cost_usd"]) == "red",
           f"row={by['skill-over']}")

    # ----- S12: Backward compat — skills without budget unchanged -----
    print("\nS12 · backward compat — no-budget skills behave as v0.6.6")
    _api_patch("/api/skills/skill-null/budget", {"daily_budget_usd": None})
    payload = _api_get("/api/skills")
    null_row = next(s for s in payload["items"] if s["name"] == "skill-null")
    _check("S12a · daily_budget_usd field present but null",
           null_row.get("daily_budget_usd") is None,
           f"row={null_row}")
    _check("S12b · preset / autonomy / launch_count fields still present",
           "preset" in null_row and "launch_count" in null_row
           and "avg_cost_usd_30d" in null_row,
           f"keys={sorted(null_row.keys())}")
    # New task on the null-budget skill should NEVER be tagged by the skill-
    # budget guard. Other still-pending capped tasks may sit alongside it in
    # the queue; assert specifically against THIS task id in the activity
    # log rather than the aggregate stats.
    nb_task = _seed_pending_task(install_dir, "skill-null")
    # Run several ticks so nb_task gets a fair claim shot (queue can be deep
    # from earlier stop conditions; MAX_CONCURRENT=3 by default).
    for _ in range(6):
        dispatcher.run_once(verbose=False)
    refused = [
        json.loads(r["metadata"]) for r
        in _activities_for(install_dir, "dispatcher_skill_budget_capped")
    ]
    nb_refused = any(m.get("task_id") == nb_task for m in refused)
    _check("S12c · NULL-budget skill never logged by skill-budget guard",
           not nb_refused,
           f"nb_task={nb_task} refused_ids="
           f"{[m.get('task_id') for m in refused]}")

    # ----- S13: cc doctor — optional warn on low budget -----
    print("\nS13 · cc doctor — warns when daily_budget_usd < $0.01")
    _api_patch("/api/skills/skill-null/budget",
               {"daily_budget_usd": 0.001})  # cents-vs-dollars typo
    import doctor
    res = doctor.chk_skill_budgets()
    _check("S13a · chk_skill_budgets fires on sub-cent budget",
           res.kind == "warn" and "0.01" in res.detail,
           f"res={res!r}")
    _api_patch("/api/skills/skill-null/budget", {"daily_budget_usd": None})
    res2 = doctor.chk_skill_budgets()
    _check("S13b · chk_skill_budgets green when no sub-cent budgets",
           res2.kind == "ok",
           f"res={res2!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v067-smoke-")
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
