#!/usr/bin/env python3
"""Smoke test for v0.7.1 — accept review output as-is.

Walks the 12 stop conditions from
`observability/(C) build-your-own-dashboard-prompt-v0.7.1-amendment.md`.

Shape mirrors smoke_v0_7_0.py:
  - Real FastAPI server on 127.0.0.1:8873, backed by a temp SQLite DB.
  - dispatcher.run_once() driven directly to DRIVE A REAL ESCALATION (impl
    children stubbed, `_run_review` stubbed to NOT_VERIFIED twice →
    awaiting_approval), then the accept action is exercised over the HTTP API.
  - telegram_bridge imported in-process; `_tg` stubbed so no real Bot API call
    is made (/accept slash + reply-to-review router + escalation text).

The make-or-break dependency (stop 1): v0.7.0's escalation branch discarded the
implementer's `output_summary`; v0.7.1 preserves it so accept has something to
accept. Every escalation here is asserted to carry a non-NULL output_summary.

Usage:
  ~/.command-centre/venv/bin/python3 scripts/dev/smoke_v0_7_1.py
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

API_PORT = 8873
BOT_TOKEN = "test-token-not-real-v071"
CHAT_ID = "717171"

# The implementer stub's stdout — its last-20-line tail is what the dispatcher
# preserves as output_summary at escalation, and what /accept marks done with.
IMPL_STDOUT = "impl-out\nline2"
IMPL_TAIL = "impl-out\nline2"
IMPL_SID = "stub-sid"


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


def _api_post(path: str, payload: dict) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        data=body, method="POST",
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


def _seed_pending_task(install_dir: Path, skill: str,
                       dry_run: int = 0) -> int:
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, description, status, assigned_skill, cost_source,
                risk_level, dry_run, execution_mode
            ) VALUES (?, ?, 'pending', ?, 'api_pool', 'low', ?, 'stream')
            """,
            (f"task for {skill}", f"do the {skill} work", skill, dry_run),
        )
        return int(cur.lastrowid or 0)


def _seed_status_task(install_dir: Path, status: str,
                      review_verdict: str | None = None,
                      output_summary: str | None = None,
                      session_id: str | None = None) -> int:
    """Insert a task already in `status`. review_verdict=None mimics a
    risk/autonomy gate (the task never ran → no output); a non-NULL verdict
    mimics a v0.7.0 review escalation."""
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, description, status, cost_source,
                review_verdict, output_summary, session_id
            ) VALUES (?, ?, ?, 'api_pool', ?, ?, ?)
            """,
            (f"{status} task", "x", status, review_verdict, output_summary,
             session_id),
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
# Stubs (mirror smoke_v0_7_0.py)
# ---------------------------------------------------------------------------

def _make_impl_stubs(dispatcher, cost: float | None = None):
    import task_tracker

    def _stub_classic(task: dict) -> dict:
        if cost is not None:
            task_tracker.update_task(task["id"], cost_usd=cost)
        return {"ok": True, "stdout": IMPL_STDOUT, "stderr": "", "returncode": 0}

    def _stub_stream(task: dict) -> dict:
        if cost is not None:
            task_tracker.update_task(task["id"], cost_usd=cost)
        return {"ok": True, "stdout": IMPL_STDOUT, "stderr": "",
                "returncode": 0, "session_id": IMPL_SID}

    dispatcher._run_classic = _stub_classic
    dispatcher._run_stream = _stub_stream


class ReviewStub:
    """Replaces dispatcher._run_review for the escalation-driving tests."""
    def __init__(self, verdict: str, reason: str | None = None,
                 cost_usd: float | None = None):
        self.verdict = verdict
        self.reason = reason
        self.cost_usd = cost_usd
        self.calls: list[int] = []

    def __call__(self, task: dict, impl_result: dict) -> dict:
        self.calls.append(task["id"])
        return {"verdict": self.verdict, "reason": self.reason,
                "cost_usd": self.cost_usd}


class TGCapture:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._next_mid = 7100

    def __call__(self, method: str, payload: dict, timeout: float = 10.0) -> dict:
        if method == "sendMessage":
            self._next_mid += 1
            self.sent.append({**payload, "_mid": self._next_mid})
            return {"message_id": self._next_mid,
                    "chat": {"id": payload.get("chat_id")}}
        return {}

    def texts(self) -> list[str]:
        return [m.get("text", "") for m in self.sent]


# ---------------------------------------------------------------------------
# Escalation driver — the v0.7.1 fixture
# ---------------------------------------------------------------------------

def _drive_escalation(install_dir: Path, dispatcher, *,
                      reason: str = "tests still fail",
                      cost: float | None = None) -> int:
    """Seed + run a review-mode task to NOT_VERIFIED twice → awaiting_approval
    (escalation), with the implementer output preserved by the v0.7.1 fix.
    Returns the escalated task id. One task at a time (claim_pending claims up
    to MAX_CONCURRENT per sweep)."""
    _clear_tasks(install_dir)
    _make_impl_stubs(dispatcher, cost=cost)
    dispatcher._run_review = ReviewStub("NOT_VERIFIED", reason=reason,
                                        cost_usd=cost)
    tid = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)  # 1st NOT_VERIFIED → retry (pending)
    dispatcher.run_once(verbose=False)  # 2nd NOT_VERIFIED → escalate
    return tid


def _notify_and_find_mid(bridge, cap: TGCapture, task_id: int) -> int | None:
    """Send the review_gated escalation notification (records notification_log)
    and return the telegram message_id for THIS task's ping."""
    cap.sent.clear()
    bridge._outbound_tick()
    for m in cap.sent:
        txt = m.get("text", "")
        if "REVIEW NEEDED" in txt and f"#{task_id}" in txt:
            return int(m["_mid"])
    return None


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

def run(install_dir: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.path.insert(0, str(MC_SCRIPTS_DIR))

    import db as serverdb
    import dispatcher
    import task_tracker  # noqa: F401

    _seed_skill(install_dir, "rev-on", review_mode=1)

    # ----- S12 (stop 12): idempotent migration -----
    print("\nS12 · migration idempotent + existing rows read review_overridden=0")
    serverdb.apply_migrations()  # second call must be a no-op (server already ran it)
    with _open_db(install_dir) as conn:
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(ops_tasks)")}
    _check("S12a · ops_tasks.review_overridden column exists",
           "review_overridden" in tcols, f"cols={sorted(tcols)}")
    legacy = _seed_status_task(install_dir, "pending")
    lrow = _task_row(install_dir, legacy)
    _check("S12b · a fresh row defaults review_overridden=0",
           lrow is not None and (lrow["review_overridden"] or 0) == 0,
           f"review_overridden={lrow['review_overridden'] if lrow else '?'}")

    # ----- S1 (stop 1): output preserved at escalation (the gotcha fix) -----
    print("\nS1 · output preserved at escalation (NOT_VERIFIED + MANUAL_VERIFY)")
    t_nv = _drive_escalation(install_dir, dispatcher)
    row = _task_row(install_dir, t_nv)
    _check("S1a · escalated task is awaiting_approval w/ NOT_VERIFIED",
           row and row["status"] == "awaiting_approval"
           and row["review_verdict"] == "NOT_VERIFIED",
           f"status={row['status'] if row else '?'}")
    _check("S1b · output_summary PRESERVED (= implementer tail, not NULL)",
           row and row["output_summary"] == IMPL_TAIL,
           f"output_summary={row['output_summary'] if row else '?'!r}")
    _check("S1c · session_id + duration_ms preserved too",
           row and row["session_id"] == IMPL_SID and row["duration_ms"] is not None,
           f"sid={row['session_id'] if row else '?'} dur={row['duration_ms'] if row else '?'}")
    # MANUAL_VERIFY_REQUIRED escalates immediately — output preserved there too.
    _clear_tasks(install_dir)
    _make_impl_stubs(dispatcher)
    dispatcher._run_review = ReviewStub("MANUAL_VERIFY_REQUIRED", reason="ambiguous")
    t_mv = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)
    mrow = _task_row(install_dir, t_mv)
    _check("S1d · MANUAL_VERIFY escalation also preserves output_summary",
           mrow and mrow["status"] == "awaiting_approval"
           and mrow["output_summary"] == IMPL_TAIL,
           f"status={mrow['status'] if mrow else '?'} "
           f"out={mrow['output_summary'] if mrow else '?'!r}")

    # ----- S2 (stop 2): accept happy path — done, overridden, output kept, no spawn -----
    print("\nS2 · accept happy path → done · review_overridden=1 · zero cost · no spawn")
    t_ok = _drive_escalation(install_dir, dispatcher)  # NULL-cost escalation
    before = _task_row(install_dir, t_ok)
    today_before = dispatcher._today_cost_usd()
    st, body = _api_post(f"/api/tasks/{t_ok}/accept", {})
    after = _task_row(install_dir, t_ok)
    today_after = dispatcher._today_cost_usd()
    _check("S2a · POST /accept → 200", st == 200, f"status={st} body={body!r}")
    _check("S2b · status flips to done", after and after["status"] == "done",
           f"status={after['status'] if after else '?'}")
    _check("S2c · review_overridden=1", after and after["review_overridden"] == 1,
           f"review_overridden={after['review_overridden'] if after else '?'}")
    _check("S2d · output_summary unchanged (the preserved tail)",
           after and after["output_summary"] == IMPL_TAIL
           and after["output_summary"] == (before["output_summary"] if before else None),
           f"out={after['output_summary'] if after else '?'!r}")
    _check("S2e · review_verdict still NOT_VERIFIED (kept for audit)",
           after and after["review_verdict"] == "NOT_VERIFIED",
           f"verdict={after['review_verdict'] if after else '?'}")
    _check("S2f · completed_at stamped", after and after["completed_at"] is not None,
           f"completed_at={after['completed_at'] if after else '?'}")
    # No spawn evidence: session_id is the preserved one (a re-run would mint a
    # new one), cost_usd is unchanged, and today's cost is unmoved (zero-cost).
    _check("S2g · no re-run — session_id is the preserved stub, not new",
           after and after["session_id"] == IMPL_SID,
           f"sid={after['session_id'] if after else '?'}")
    _check("S2h · cost_usd unchanged by accept (NULL → NULL; no new child)",
           after and after["cost_usd"] == (before["cost_usd"] if before else None),
           f"cost={after['cost_usd'] if after else '?'}")
    _check("S2i · today_cost_usd unchanged by accept (stays $0 — zero cost)",
           abs(today_after - today_before) < 1e-9 and abs(today_after) < 1e-9,
           f"before=${today_before:.4f} after=${today_after:.4f}")
    _check("S2j · response body is the updated task carrying review_overridden=1",
           isinstance(body, dict) and body.get("review_overridden") == 1
           and body.get("status") == "done",
           f"body_keys={sorted(body.keys()) if isinstance(body, dict) else body!r}")

    # ----- S3 (stop 3): accept rejects a non-review awaiting_approval task -----
    print("\nS3 · accept refuses a risk-gated awaiting_approval (review_verdict NULL)")
    t_risk = _seed_status_task(install_dir, "awaiting_approval", review_verdict=None)
    st, body = _api_post(f"/api/tasks/{t_risk}/accept", {})
    rrow = _task_row(install_dir, t_risk)
    _check("S3a · 400 (not a review escalation)", st == 400, f"status={st}")
    _check("S3b · message points the operator at /approve",
           isinstance(body, str) and "not a review escalation" in body
           and "/approve" in body, f"body={body!r}")
    _check("S3c · state unchanged (still awaiting_approval, not overridden)",
           rrow and rrow["status"] == "awaiting_approval"
           and (rrow["review_overridden"] or 0) == 0,
           f"status={rrow['status'] if rrow else '?'}")

    # ----- S4 (stop 4): accept rejects wrong states -----
    print("\nS4 · accept refuses pending / running / done")
    for state in ("pending", "running", "done"):
        tid = _seed_status_task(install_dir, state, review_verdict="NOT_VERIFIED",
                                output_summary="x")
        st, body = _api_post(f"/api/tasks/{tid}/accept", {})
        srow = _task_row(install_dir, tid)
        _check(f"S4·{state} → 400 + state unchanged",
               st == 400 and srow and srow["status"] == state
               and (srow["review_overridden"] or 0) == 0,
               f"status={st} row_status={srow['status'] if srow else '?'} body={body!r}")
    st, _ = _api_post("/api/tasks/999999/accept", {})
    _check("S4·missing → 404", st == 404, f"status={st}")

    # ----- S9 (stop 9): audit row written -----
    print("\nS9 · accept writes a task_review_overridden audit row")
    t_aud = _drive_escalation(install_dir, dispatcher, reason="output looks fine actually")
    st, _ = _api_post(f"/api/tasks/{t_aud}/accept?source=dashboard", {})
    acts = _activities_for(install_dir, "task_review_overridden")
    matched = [a for a in acts
               if json.loads(a["detail"]).get("task_id") == t_aud]
    _check("S9a · /accept → 200", st == 200, f"status={st}")
    _check("S9b · activities row event_type='task_review_overridden' exists",
           len(matched) >= 1, f"rows={len(matched)}")
    if matched:
        d = json.loads(matched[-1]["detail"])
        _check("S9c · audit carries prior_verdict='NOT_VERIFIED'",
               d.get("prior_verdict") == "NOT_VERIFIED", f"detail={d}")
        _check("S9d · audit carries source from ?source= (='dashboard')",
               d.get("source") == "dashboard", f"detail={d}")
        _check("S9e · audit carries review_feedback_first_120",
               (d.get("review_feedback_first_120") or "").startswith(
                   "output looks fine"), f"detail={d}")
    else:
        _check("S9c · audit carries prior_verdict", False, "no audit row")
        _check("S9d · audit carries source", False, "no audit row")
        _check("S9e · audit carries review_feedback_first_120", False, "no audit row")

    # ----- S7 (stop 7): approve still re-runs (review_overridden stays 0) -----
    print("\nS7 · approve still re-dispatches an escalation; review_overridden stays 0")
    t_app = _drive_escalation(install_dir, dispatcher)
    st, _ = _api_post(f"/api/tasks/{t_app}/approve", {})
    arow = _task_row(install_dir, t_app)
    _check("S7a · /approve → 200", st == 200, f"status={st}")
    _check("S7b · flips back to pending (re-dispatch, not done)",
           arow and arow["status"] == "pending",
           f"status={arow['status'] if arow else '?'}")
    _check("S7c · review_overridden untouched (0) by approve",
           arow and (arow["review_overridden"] or 0) == 0,
           f"review_overridden={arow['review_overridden'] if arow else '?'}")

    # ----- S8 (stop 8): cancel still drops -----
    print("\nS8 · cancel still drops an escalation")
    t_can = _drive_escalation(install_dir, dispatcher)
    st, _ = _api_post(f"/api/tasks/{t_can}/cancel", {})
    crow = _task_row(install_dir, t_can)
    _check("S8a · /cancel → 200", st == 200, f"status={st}")
    _check("S8b · status=cancelled", crow and crow["status"] == "cancelled",
           f"status={crow['status'] if crow else '?'}")

    # ----- S11 (stop 11): backward compat — VERIFIED keeps review_overridden=0 -----
    print("\nS11 · VERIFIED completion + risk /approve unaffected")
    _clear_tasks(install_dir)
    _make_impl_stubs(dispatcher)
    dispatcher._run_review = ReviewStub("VERIFIED")
    t_v = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)
    vrow = _task_row(install_dir, t_v)
    _check("S11a · VERIFIED task is done with review_overridden=0",
           vrow and vrow["status"] == "done"
           and (vrow["review_overridden"] or 0) == 0
           and vrow["review_verdict"] == "VERIFIED",
           f"status={vrow['status'] if vrow else '?'} "
           f"override={vrow['review_overridden'] if vrow else '?'}")
    # Risk-gated /approve untouched: a verdict-NULL awaiting_approval still
    # approves to pending (and accept correctly refused it in S3).
    t_rg = _seed_status_task(install_dir, "awaiting_approval", review_verdict=None)
    st, _ = _api_post(f"/api/tasks/{t_rg}/approve", {})
    rgrow = _task_row(install_dir, t_rg)
    _check("S11b · risk-gated /approve still flips to pending",
           st == 200 and rgrow and rgrow["status"] == "pending",
           f"status={st} row={rgrow['status'] if rgrow else '?'}")

    # ----- S10 (stop 10): the UI's data driver — list exposes review_overridden -----
    # Self-contained: drive + accept a FRESH escalation and read it straight back
    # (earlier accepted tasks get wiped by the next _drive_escalation's
    # _clear_tasks, so we can't reuse t_ok here). The Playwright spec covers the
    # button → badge UI itself; this asserts the API contract the badge needs.
    print("\nS10 · GET /api/tasks exposes review_overridden (UI badge driver)")
    t_ui = _drive_escalation(install_dir, dispatcher)
    _api_post(f"/api/tasks/{t_ui}/accept", {})
    tasks = _api_get("/api/tasks")
    a_done = next((t for t in tasks["items"]
                   if t["id"] == t_ui), None)
    _check("S10a · accepted task appears with review_overridden=1 in the list",
           a_done is not None and a_done.get("review_overridden") == 1
           and a_done.get("status") == "done"
           and a_done.get("review_verdict") == "NOT_VERIFIED",
           f"row={a_done}")
    _check("S10b · accepted task still carries its preserved output_summary",
           a_done is not None and a_done.get("output_summary") == IMPL_TAIL,
           f"out={a_done.get('output_summary') if a_done else '?'!r}")

    # ===== Telegram surfaces =====
    print("\nS5/S6 · telegram /accept slash + reply-to-review router + escalation text")
    os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
    os.environ["TELEGRAM_DASH_CHAT_ID"] = CHAT_ID
    os.environ["CC_DASHBOARD_URL"] = f"http://127.0.0.1:{API_PORT}"
    import telegram_bridge as bridge
    bridge.BOT_TOKEN = BOT_TOKEN
    bridge.CHAT_ID = CHAT_ID
    bridge.DASHBOARD_URL = f"http://127.0.0.1:{API_PORT}"
    cap = TGCapture()
    bridge._tg = cap

    # Escalation notification text now offers all three resolutions.
    txt = bridge._format_review_gated(
        {"id": 42, "title": "ship the thing",
         "review_verdict": "NOT_VERIFIED", "review_feedback": "tests fail"})
    _check("S6a · escalation text offers /accept, /approve, /cancel",
           "/accept 42" in txt and "/approve 42" in txt and "/cancel 42" in txt,
           f"txt={txt!r}")

    # ----- S5 (stop 5): /accept <id> slash command -----
    t_tg = _drive_escalation(install_dir, dispatcher)
    cap.sent.clear()
    bridge._handle_message({"chat": {"id": CHAT_ID}, "message_id": 9001,
                            "text": f"/accept {t_tg}"})
    tgrow = _task_row(install_dir, t_tg)
    _check("S5a · /accept <id> replies ✅ accepted as-is",
           any("accepted as-is" in t for t in cap.texts()),
           f"sent={cap.texts()}")
    _check("S5b · row flipped to done · review_overridden=1",
           tgrow and tgrow["status"] == "done" and tgrow["review_overridden"] == 1,
           f"status={tgrow['status'] if tgrow else '?'} "
           f"override={tgrow['review_overridden'] if tgrow else '?'}")

    # ----- S6 (stop 6): reply-to-notification /accept + bare accept -----
    t_r1 = _drive_escalation(install_dir, dispatcher)
    mid = _notify_and_find_mid(bridge, cap, t_r1)
    cap.sent.clear()
    bridge._handle_message({"chat": {"id": CHAT_ID}, "message_id": 9101,
                            "text": "/accept",
                            "reply_to_message": {"message_id": mid}})
    r1 = _task_row(install_dir, t_r1)
    _check("S6b · reply `/accept` to the ❓ ping → accepted (done · overridden)",
           mid is not None and r1 and r1["status"] == "done"
           and r1["review_overridden"] == 1,
           f"mid={mid} status={r1['status'] if r1 else '?'}")

    t_r2 = _drive_escalation(install_dir, dispatcher)
    mid2 = _notify_and_find_mid(bridge, cap, t_r2)
    cap.sent.clear()
    bridge._handle_message({"chat": {"id": CHAT_ID}, "message_id": 9102,
                            "text": "accept",
                            "reply_to_message": {"message_id": mid2}})
    r2 = _task_row(install_dir, t_r2)
    _check("S6c · reply bare `accept` (strict) → accepted",
           mid2 is not None and r2 and r2["status"] == "done"
           and r2["review_overridden"] == 1,
           f"mid={mid2} status={r2['status'] if r2 else '?'}")

    # A longer reply falls through to the v0.7.0 default (approve = re-run) —
    # v0.6.5 strict-match semantics. review_overridden stays 0.
    t_r3 = _drive_escalation(install_dir, dispatcher)
    mid3 = _notify_and_find_mid(bridge, cap, t_r3)
    cap.sent.clear()
    bridge._handle_message({"chat": {"id": CHAT_ID}, "message_id": 9103,
                            "text": "accept but note the edge case",
                            "reply_to_message": {"message_id": mid3}})
    r3 = _task_row(install_dir, t_r3)
    _check("S6d · longer 'accept but note X' falls through to approve (re-run)",
           mid3 is not None and r3 and r3["status"] == "pending"
           and (r3["review_overridden"] or 0) == 0,
           f"mid={mid3} status={r3['status'] if r3 else '?'} "
           f"override={r3['review_overridden'] if r3 else '?'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v071-smoke-")
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
