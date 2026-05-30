#!/usr/bin/env python3
"""Smoke test for v0.7.0 — adversarial review gate.

Walks the 12 stop conditions from
`observability/(C) build-your-own-dashboard-prompt-v0.7.0-amendment.md`.

Shape mirrors smoke_v0_6_7.py:
  - Real FastAPI server on 127.0.0.1:8872, backed by a temp SQLite DB.
  - dispatcher.run_once() driven directly to assert the completion gate, with
    `_run_classic` / `_run_stream` stubbed (no real claude-CLI spawn) and
    `_run_review` stubbed to return each verdict in turn.
  - The REAL `_run_review` fail-safe paths (no-verdict / crash / timeout) are
    exercised with a FakePopen so we test the actual subprocess handling, not
    just the stub.
  - The verdict parser is unit-smoked against fixtures (each token, missing
    line, — vs - vs -- separators, case sensitivity, last-match-wins).
  - telegram_bridge imported in-process; `_tg` stubbed so no real Bot API call
    is made (review_gated formatter + routing).

Usage:
  ~/.command-centre/venv/bin/python3 scripts/dev/smoke_v0_7_0.py
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

API_PORT = 8872
BOT_TOKEN = "test-token-not-real-v070"
CHAT_ID = "707070"


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
                       success_criteria: str | None = None,
                       dry_run: int = 0) -> int:
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, description, status, assigned_skill, cost_source,
                risk_level, dry_run, success_criteria, execution_mode
            ) VALUES (?, ?, 'pending', ?, 'api_pool', 'low', ?, ?, 'stream')
            """,
            (f"task for {skill}", f"do the {skill} work", skill, dry_run,
             success_criteria),
        )
        return int(cur.lastrowid or 0)


def _task_row(install_dir: Path, task_id: int) -> dict | None:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT * FROM ops_tasks WHERE id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None


def _clear_tasks(install_dir: Path) -> None:
    """Wipe the task queue so each lifecycle test runs against only its own
    task — claim_pending claims up to MAX_CONCURRENT per sweep and the review
    stub returns ONE verdict per sweep, so leftover tasks would cross-talk."""
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

def _make_impl_stubs(dispatcher, cost: float | None = None):
    """Stub the implementer children so run_once stays hermetic. When `cost` is
    set, the stream stub records it on the task (simulates implementer spend)."""
    import task_tracker

    def _stub_classic(task: dict) -> dict:
        if cost is not None:
            task_tracker.update_task(task["id"], cost_usd=cost)
        return {"ok": True, "stdout": "impl-out\nline2", "stderr": "",
                "returncode": 0}

    def _stub_stream(task: dict) -> dict:
        if cost is not None:
            task_tracker.update_task(task["id"], cost_usd=cost)
        return {"ok": True, "stdout": "impl-out\nline2", "stderr": "",
                "returncode": 0, "session_id": "stub-sid"}

    dispatcher._run_classic = _stub_classic
    dispatcher._run_stream = _stub_stream


class ReviewStub:
    """Replaces dispatcher._run_review for the gate lifecycle tests. Records
    which task ids it was asked to review so stop conditions 7 (review off) and
    8 (dry-run) can assert the reviewer was NEVER spawned."""
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


def _make_fake_popen(stdout: str = "", returncode: int = 0,
                     raise_timeout: bool = False):
    """A drop-in for subprocess.Popen so the REAL _run_review can be exercised
    without a claude-CLI. Mimics the attributes/methods _run_review touches."""
    class _Fake:
        def __init__(self, *a, **k):
            self.pid = 4242424
            self.returncode = returncode

        def communicate(self, timeout=None):
            # Mirror real Popen: the FIRST call (with a timeout) raises; the
            # reaping call after kill() (no timeout) returns normally.
            if raise_timeout and timeout is not None:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
            return stdout, ""

        def kill(self):
            self.returncode = -9
    return _Fake


# ---------------------------------------------------------------------------
# Telegram capture
# ---------------------------------------------------------------------------

class TGCapture:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._next_mid = 7000

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
# Stop conditions
# ---------------------------------------------------------------------------

def run(install_dir: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.path.insert(0, str(MC_SCRIPTS_DIR))

    import db as serverdb
    import dispatcher
    import task_tracker  # noqa: F401

    # Keep a handle on the REAL _run_review for the fail-safe tests before any
    # lifecycle test swaps in a stub.
    real_run_review = dispatcher._run_review

    # ----- S1: schema migration idempotent + backward-compatible defaults ----
    print("\nS1 · schema migration (5 additive idempotent columns)")
    serverdb.apply_migrations()  # second call must be a no-op
    with _open_db(install_dir) as conn:
        scols = {r[1] for r in conn.execute("PRAGMA table_info(skills)")}
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(ops_tasks)")}
    _check("S1a · skills.review_mode column exists",
           "review_mode" in scols, f"skills cols={sorted(scols)}")
    _check("S1b · ops_tasks has the 4 review columns",
           {"success_criteria", "review_verdict", "review_count",
            "review_feedback"} <= tcols, f"ops_tasks cols={sorted(tcols)}")
    # Existing rows behave as pre-v0.7.0: review_mode 0, review_count 0.
    _seed_skill(install_dir, "legacy-skill", review_mode=0)
    legacy_task = _seed_pending_task(install_dir, "legacy-skill")
    row = _task_row(install_dir, legacy_task)
    _check("S1c · new task defaults: review_count=0, verdict NULL, criteria NULL",
           row is not None and row["review_count"] == 0
           and row["review_verdict"] is None and row["success_criteria"] is None,
           f"row review_count={row['review_count'] if row else '?'}")
    _clear_tasks(install_dir)

    # ----- S2: PATCH /api/skills/{name}/review -----
    print("\nS2 · PATCH .../review accepts 0/1, rejects junk, 404s missing")
    _seed_skill(install_dir, "rev-api", review_mode=0)
    st, body = _api_patch("/api/skills/rev-api/review", {"review_mode": 1})
    _check("S2a · PATCH review_mode=1 → 200", st == 200, f"status={st} body={body!r}")
    skills = _api_get("/api/skills")
    rev_api = next((s for s in skills["items"] if s["name"] == "rev-api"), None)
    _check("S2b · GET /api/skills reflects review_mode=1",
           rev_api is not None and rev_api.get("review_mode") == 1,
           f"row={rev_api}")
    st, _ = _api_patch("/api/skills/rev-api/review", {"review_mode": 2})
    _check("S2c · review_mode=2 → 400", st == 400, f"status={st}")
    st, _ = _api_patch("/api/skills/rev-api/review", {"review_mode": "yes"})
    _check("S2d · non-int → 400", st == 400, f"status={st}")
    st, _ = _api_patch("/api/skills/does-not-exist/review", {"review_mode": 1})
    _check("S2e · missing skill → 404", st == 404, f"status={st}")
    st, body2 = _api_patch("/api/skills/rev-api/review", {"review_mode": 0})
    _check("S2f · review_mode=0 → 200 + reflected",
           st == 200 and isinstance(body2, dict) and body2.get("review_mode") == 0,
           f"status={st} body={body2}")

    # ----- S9 (stop 9): success_criteria reaches the reviewer prompt -----
    print("\nS9 · success_criteria reaches the reviewer prompt verbatim")
    impl = {"stdout": "implementer said it wrote the file\nall good"}
    with_crit = dispatcher._build_review_prompt(
        {"title": "Write file", "description": "make x",
         "success_criteria": "WHEN run, THE system SHALL write /tmp/x.txt"}, impl)
    _check("S9a · criteria present → appears verbatim",
           "WHEN run, THE system SHALL write /tmp/x.txt" in with_crit,
           "criteria missing from prompt")
    _check("S9b · implementer output tail reused in prompt",
           "implementer said it wrote the file" in with_crit, "tail missing")
    no_crit = dispatcher._build_review_prompt(
        {"title": "t", "description": "d", "success_criteria": None}, impl)
    _check("S9c · criteria absent → '(none specified — judge against INTENT)'",
           "(none specified — judge against INTENT)" in no_crit,
           "placeholder missing")

    # ----- Verdict parser fixtures (spec order-of-ops step 2) -----
    print("\nSP · verdict parser fixtures")
    p = dispatcher._parse_review_verdict
    _check("SPa · VERIFIED (no reason)",
           p("VERDICT: VERIFIED") == {"verdict": "VERIFIED", "reason": None})
    _check("SPb · NOT_VERIFIED em-dash reason",
           p("VERDICT: NOT_VERIFIED — file absent")
           == {"verdict": "NOT_VERIFIED", "reason": "file absent"})
    _check("SPc · single-hyphen separator",
           p("VERDICT: NOT_VERIFIED - nope")
           == {"verdict": "NOT_VERIFIED", "reason": "nope"})
    _check("SPd · double-hyphen separator",
           p("VERDICT: MANUAL_VERIFY_REQUIRED -- unclear")
           == {"verdict": "MANUAL_VERIFY_REQUIRED", "reason": "unclear"})
    _check("SPe · last match wins",
           p("VERDICT: VERIFIED\nthinking...\nVERDICT: NOT_VERIFIED — final")
           == {"verdict": "NOT_VERIFIED", "reason": "final"})
    _check("SPf · no VERDICT line → None", p("just some text\nno verdict here") is None)
    _check("SPg · lowercase token is NOT a verdict (case-sensitive)",
           p("verdict: verified") is None)

    # ----- S5 (stop 5): reviewer no-verdict fail-safe (real _run_review) -----
    print("\nS5 · reviewer no-verdict fail-safe → MANUAL_VERIFY_REQUIRED")
    orig_popen = dispatcher.subprocess.Popen
    dispatcher.subprocess.Popen = _make_fake_popen(
        stdout=json.dumps({"result": "I looked but produced no marker line",
                           "total_cost_usd": 0.02}),
        returncode=0)
    try:
        res = real_run_review({"id": 1, "title": "t", "description": "d"},
                              {"stdout": "impl"})
    finally:
        dispatcher.subprocess.Popen = orig_popen
    _check("S5a · no VERDICT line → MANUAL_VERIFY_REQUIRED",
           res["verdict"] == "MANUAL_VERIFY_REQUIRED", f"res={res}")
    _check("S5b · cost still captured from the json envelope",
           abs((res.get("cost_usd") or 0) - 0.02) < 1e-9, f"res={res}")

    # A valid VERDICT inside the json `result` text parses correctly.
    dispatcher.subprocess.Popen = _make_fake_popen(
        stdout=json.dumps({"result": "checked the workspace\nVERDICT: VERIFIED",
                           "total_cost_usd": 0.05}),
        returncode=0)
    try:
        res_ok = real_run_review({"id": 1, "title": "t", "description": "d"},
                                 {"stdout": "impl"})
    finally:
        dispatcher.subprocess.Popen = orig_popen
    _check("S5c · valid VERDICT in json result → parsed + cost captured",
           res_ok["verdict"] == "VERIFIED"
           and abs((res_ok.get("cost_usd") or 0) - 0.05) < 1e-9,
           f"res={res_ok}")

    # ----- S6 (stop 6): reviewer crash / timeout fail-safe -----
    print("\nS6 · reviewer crash / timeout fail-safe → MANUAL_VERIFY_REQUIRED")
    dispatcher.subprocess.Popen = _make_fake_popen(stdout="boom", returncode=1)
    try:
        res_crash = real_run_review({"id": 1, "title": "t", "description": "d"},
                                    {"stdout": "impl"})
    finally:
        dispatcher.subprocess.Popen = orig_popen
    _check("S6a · non-zero exit → MANUAL_VERIFY_REQUIRED",
           res_crash["verdict"] == "MANUAL_VERIFY_REQUIRED", f"res={res_crash}")

    dispatcher.subprocess.Popen = _make_fake_popen(raise_timeout=True)
    try:
        res_to = real_run_review({"id": 1, "title": "t", "description": "d"},
                                 {"stdout": "impl"})
    finally:
        dispatcher.subprocess.Popen = orig_popen
    _check("S6b · timeout → MANUAL_VERIFY_REQUIRED",
           res_to["verdict"] == "MANUAL_VERIFY_REQUIRED", f"res={res_to}")

    # ----- S11 (stop 11): review PID marker mode + sweep coverage -----
    print("\nS11 · review PID marker (mode='review') + sweep coverage")
    dead_pid = 999_999_99
    marker = dispatcher._mark_child_pid(dead_pid, 4242, "review")
    data = json.loads(marker.read_text())
    _check("S11a · review marker written with mode='review'",
           data.get("mode") == "review" and data.get("task_id") == 4242,
           f"marker={data}")
    swept = dispatcher._sweep_stale_pids()
    _check("S11b · stale review marker swept (sweep is mode-agnostic)",
           not marker.exists() and swept >= 1, f"swept={swept}")

    # ===== Lifecycle tests — stub _run_review per sweep =====

    # ----- S3 (stop 1): VERIFIED happy path -----
    print("\nS3 · VERIFIED happy path → task done")
    _clear_tasks(install_dir)
    _seed_skill(install_dir, "rev-on", review_mode=1)
    _make_impl_stubs(dispatcher)
    rstub = ReviewStub("VERIFIED", reason=None)
    dispatcher._run_review = rstub
    t_ok = _seed_pending_task(install_dir, "rev-on")
    stats = dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_ok)
    _check("S3a · task status=done", row and row["status"] == "done",
           f"status={row['status'] if row else '?'}")
    _check("S3b · review_verdict='VERIFIED'",
           row and row["review_verdict"] == "VERIFIED",
           f"verdict={row['review_verdict'] if row else '?'}")
    _check("S3c · stats.review_verified incremented",
           stats.get("review_verified", 0) >= 1, f"stats={stats}")
    _check("S3d · reviewer was actually invoked",
           t_ok in rstub.calls, f"calls={rstub.calls}")

    # ----- S4 + S5 (stops 2 & 3): NOT_VERIFIED → one retry → escalate -----
    print("\nS4 · NOT_VERIFIED → one auto-retry, then escalate on 2nd failure")
    _clear_tasks(install_dir)
    rstub_nv = ReviewStub("NOT_VERIFIED", reason="file /tmp/x.txt absent")
    dispatcher._run_review = rstub_nv
    t_nv = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_nv)
    _check("S4a · first failure → back to pending",
           row and row["status"] == "pending",
           f"status={row['status'] if row else '?'}")
    _check("S4b · review_count bumped to 1",
           row and row["review_count"] == 1, f"count={row['review_count'] if row else '?'}")
    _check("S4c · review_feedback stored",
           row and row["review_feedback"] == "file /tmp/x.txt absent",
           f"feedback={row['review_feedback'] if row else '?'}")
    # The retry prompt must carry the prior feedback.
    retry_prompt = dispatcher._build_prompt(row)
    _check("S4d · retry prompt prepends 'PRIOR REVIEW REJECTED' + feedback",
           "PRIOR REVIEW REJECTED THIS WORK" in retry_prompt
           and "file /tmp/x.txt absent" in retry_prompt,
           "feedback not threaded into retry prompt")
    # Second sweep — same task, still NOT_VERIFIED → escalate (no infinite loop).
    dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_nv)
    _check("S5a · second failure → awaiting_approval (escalated)",
           row and row["status"] == "awaiting_approval",
           f"status={row['status'] if row else '?'}")
    _check("S5b · review_count stays capped at 1 (one retry only)",
           row and row["review_count"] == 1, f"count={row['review_count'] if row else '?'}")
    _check("S5c · review_verdict='NOT_VERIFIED'",
           row and row["review_verdict"] == "NOT_VERIFIED",
           f"verdict={row['review_verdict'] if row else '?'}")
    esc = _activities_for(install_dir, "task_review_escalated")
    _check("S5d · escalation activity logged", len(esc) >= 1, f"acts={len(esc)}")

    # ----- S6b (stop 4): MANUAL_VERIFY_REQUIRED → immediate escalate -----
    print("\nS6c · MANUAL_VERIFY_REQUIRED → immediate escalation (no retry)")
    _clear_tasks(install_dir)
    dispatcher._run_review = ReviewStub("MANUAL_VERIFY_REQUIRED", reason="ambiguous")
    t_mv = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_mv)
    _check("S6c1 · status=awaiting_approval", row and row["status"] == "awaiting_approval",
           f"status={row['status'] if row else '?'}")
    _check("S6c2 · review_count unchanged at 0 (no auto-retry)",
           row and row["review_count"] == 0, f"count={row['review_count'] if row else '?'}")
    _check("S6c3 · review_verdict='MANUAL_VERIFY_REQUIRED'",
           row and row["review_verdict"] == "MANUAL_VERIFY_REQUIRED",
           f"verdict={row['review_verdict'] if row else '?'}")

    # ----- S7 (stop 7): review_mode off = unchanged -----
    print("\nS7 · review_mode off → byte-identical completion, no reviewer")
    _clear_tasks(install_dir)
    _seed_skill(install_dir, "rev-off", review_mode=0)
    rstub_off = ReviewStub("VERIFIED")
    dispatcher._run_review = rstub_off
    t_off = _seed_pending_task(install_dir, "rev-off")
    stats = dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_off)
    _check("S7a · status=done", row and row["status"] == "done",
           f"status={row['status'] if row else '?'}")
    _check("S7b · review_verdict NULL (no review ran)",
           row and row["review_verdict"] is None,
           f"verdict={row['review_verdict'] if row else '?'}")
    _check("S7c · reviewer NOT invoked", t_off not in rstub_off.calls,
           f"calls={rstub_off.calls}")
    _check("S7d · counted as plain success, not review_verified",
           stats.get("succeeded", 0) >= 1 and stats.get("review_verified", 0) == 0,
           f"stats={stats}")

    # ----- S8 (stop 8): dry_run skips review -----
    print("\nS8 · dry_run task on a review skill → completes without reviewer")
    _clear_tasks(install_dir)
    rstub_dry = ReviewStub("VERIFIED")
    dispatcher._run_review = rstub_dry
    t_dry = _seed_pending_task(install_dir, "rev-on", dry_run=1)
    dispatcher.run_once(verbose=False)
    row = _task_row(install_dir, t_dry)
    _check("S8a · dry-run task status=done", row and row["status"] == "done",
           f"status={row['status'] if row else '?'}")
    _check("S8b · reviewer NOT invoked for dry-run", t_dry not in rstub_dry.calls,
           f"calls={rstub_dry.calls}")
    _check("S8c · review_verdict NULL", row and row["review_verdict"] is None,
           f"verdict={row['review_verdict'] if row else '?'}")

    # ----- S10 (stop 10): cost attribution — reviewed ≈ 2× non-reviewed -----
    print("\nS10 · cost attribution — reviewed task ≈ 2× a non-reviewed one")
    _clear_tasks(install_dir)
    _seed_skill(install_dir, "cost-rev", review_mode=1)
    _seed_skill(install_dir, "cost-norev", review_mode=0)
    _make_impl_stubs(dispatcher, cost=0.10)  # implementer "spends" $0.10
    dispatcher._run_review = ReviewStub("VERIFIED", cost_usd=0.10)  # reviewer +$0.10
    t_rev = _seed_pending_task(install_dir, "cost-rev")
    t_norev = _seed_pending_task(install_dir, "cost-norev")
    dispatcher.run_once(verbose=False)
    rev_row = _task_row(install_dir, t_rev)
    norev_row = _task_row(install_dir, t_norev)
    _check("S10a · non-reviewed task cost ≈ $0.10 (implementer only)",
           norev_row and abs((norev_row["cost_usd"] or 0) - 0.10) < 1e-9,
           f"cost={norev_row['cost_usd'] if norev_row else '?'}")
    _check("S10b · reviewed task cost ≈ $0.20 (both children)",
           rev_row and abs((rev_row["cost_usd"] or 0) - 0.20) < 1e-9,
           f"cost={rev_row['cost_usd'] if rev_row else '?'}")
    _check("S10c · reviewed ≈ 2× non-reviewed",
           rev_row and norev_row and rev_row["cost_usd"]
           and abs(rev_row["cost_usd"] - 2 * norev_row["cost_usd"]) < 1e-9,
           f"rev={rev_row['cost_usd'] if rev_row else '?'} "
           f"norev={norev_row['cost_usd'] if norev_row else '?'}")
    # The per-skill budget endpoint sees the reviewer-inclusive cost.
    skills = _api_get("/api/skills")
    cost_rev = next((s for s in skills["items"] if s["name"] == "cost-rev"), None)
    _check("S10d · /api/skills today_cost_usd for review skill ≈ $0.20",
           cost_rev and abs((cost_rev.get("today_cost_usd") or 0) - 0.20) < 1e-9,
           f"today={cost_rev.get('today_cost_usd') if cost_rev else '?'}")
    # Restore default impl stubs (no cost) for any later sweeps.
    _make_impl_stubs(dispatcher)

    # ----- S12a: dispatcher rollup -----
    print("\nS12 · /api/system/dispatcher review rollup + /api/attention issue")
    disp = _api_get("/api/system/dispatcher")
    _check("S12a · skills_with_review exposed + counts opted-in skills",
           "skills_with_review" in disp and disp["skills_with_review"] >= 2,
           f"skills_with_review={disp.get('skills_with_review')}")
    _check("S12b · tasks_awaiting_review_approval exposed",
           "tasks_awaiting_review_approval" in disp,
           f"keys={[k for k in disp if 'review' in k]}")

    # Create an escalation so the rollup + attention have something to report.
    _clear_tasks(install_dir)
    _make_impl_stubs(dispatcher)
    dispatcher._run_review = ReviewStub("MANUAL_VERIFY_REQUIRED",
                                        reason="needs a human")
    t_esc = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)
    disp2 = _api_get("/api/system/dispatcher")
    _check("S12c · tasks_awaiting_review_approval ≥ 1 after an escalation",
           disp2.get("tasks_awaiting_review_approval", 0) >= 1,
           f"count={disp2.get('tasks_awaiting_review_approval')}")
    feed = _api_get("/api/attention")
    kinds = [it["kind"] for it in feed["issues"]]
    _check("S12d · /api/attention surfaces review_escalated",
           "review_escalated" in kinds, f"kinds={kinds}")
    issue = next((it for it in feed["issues"]
                  if it["kind"] == "review_escalated"), None)
    _check("S12e · review_escalated severity is warning (amber, not error)",
           issue is not None and issue.get("severity") == "warning",
           f"issue={issue}")
    _check("S12f · review_escalated carries the verdict + feedback",
           issue is not None and issue.get("verdict") == "MANUAL_VERIFY_REQUIRED"
           and issue.get("review_feedback") == "needs a human",
           f"issue={issue}")

    # ----- S13: approve re-runs an escalated task (reuse, don't build) -----
    print("\nS13 · escalated task → approve re-dispatches with feedback")
    # t_esc is in awaiting_approval. Approve via the EXISTING endpoint.
    st, _ = _api_post(f"/api/tasks/{t_esc}/approve", {})
    _check("S13a · existing /approve endpoint accepts the escalated task",
           st == 200, f"status={st}")
    row = _task_row(install_dir, t_esc)
    _check("S13b · approve flips it back to pending (re-dispatch)",
           row and row["status"] == "pending",
           f"status={row['status'] if row else '?'}")

    # ----- S14 (stop 12): backward compat — v0.6.x surfaces intact -----
    print("\nS14 · backward compat — v0.6.x surfaces unaffected")
    disp = _api_get("/api/system/dispatcher")
    _check("S14a · v0.6.7 budget rollup still present",
           "skills_with_budget" in disp and "skills_at_budget" in disp,
           f"keys={list(disp.keys())}")
    _seed_skill(install_dir, "compat", review_mode=0)
    st, body = _api_patch("/api/skills/compat/budget", {"daily_budget_usd": 2.5})
    _check("S14b · v0.6.7 PATCH .../budget still works",
           st == 200 and isinstance(body, dict)
           and abs((body.get("daily_budget_usd") or 0) - 2.5) < 1e-9,
           f"status={st} body={body}")
    skills = _api_get("/api/skills")
    compat = next((s for s in skills["items"] if s["name"] == "compat"), None)
    _check("S14c · skill row keeps preset / launch_count / budget + adds review_mode",
           compat is not None and "preset" in compat and "launch_count" in compat
           and "daily_budget_usd" in compat and "review_mode" in compat,
           f"keys={sorted(compat.keys()) if compat else None}")
    # A POST /api/tasks with success_criteria round-trips through list.
    st, body = _api_post("/api/tasks",
                         {"title": "criteria task",
                          "success_criteria": "WHEN x, THE system SHALL y"})
    new_id = body.get("id") if isinstance(body, dict) else None
    _check("S14d · POST /api/tasks accepts success_criteria", st == 200 and new_id,
           f"status={st} body={body}")
    tasks = _api_get("/api/tasks")
    crow = next((t for t in tasks["items"] if t["id"] == new_id), None)
    _check("S14e · GET /api/tasks returns success_criteria + review fields",
           crow is not None
           and crow.get("success_criteria") == "WHEN x, THE system SHALL y"
           and "review_verdict" in crow and "review_count" in crow,
           f"row={crow}")

    # ----- S15: telegram review_gated formatter + routing -----
    print("\nS15 · telegram review-escalation notification (near-zero delta)")
    os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
    os.environ["TELEGRAM_DASH_CHAT_ID"] = CHAT_ID
    os.environ["CC_DASHBOARD_URL"] = f"http://127.0.0.1:{API_PORT}"
    import telegram_bridge as bridge
    bridge.BOT_TOKEN = BOT_TOKEN
    bridge.CHAT_ID = CHAT_ID
    bridge.DASHBOARD_URL = f"http://127.0.0.1:{API_PORT}"
    cap = TGCapture()
    bridge._tg = cap

    # Formatter renders verdict + reviewer reason + /approve /cancel.
    txt = bridge._format_review_gated(
        {"id": 42, "title": "ship the thing",
         "review_verdict": "NOT_VERIFIED", "review_feedback": "tests fail"})
    _check("S15a · review_gated formatter shows verdict + reason + /approve",
           "NOT_VERIFIED" in txt and "tests fail" in txt
           and "/approve 42" in txt and "/cancel 42" in txt,
           f"txt={txt!r}")

    # Create a fresh review-escalation so the awaiting_approval queue has a
    # review-gated task for the outbound tick (earlier ones were cleared /
    # approved by S10–S13). NOT_VERIFIED twice → escalate.
    _clear_tasks(install_dir)
    _make_impl_stubs(dispatcher)
    dispatcher._run_review = ReviewStub("NOT_VERIFIED", reason="tests still fail")
    t_tg = _seed_pending_task(install_dir, "rev-on")
    dispatcher.run_once(verbose=False)  # 1st failure → retry (pending)
    dispatcher.run_once(verbose=False)  # 2nd failure → escalate (awaiting_approval)
    esc_row = _task_row(install_dir, t_tg)
    _check("S15b1 · fresh task escalated to awaiting_approval for the TG test",
           esc_row and esc_row["status"] == "awaiting_approval"
           and esc_row["review_verdict"] == "NOT_VERIFIED",
           f"status={esc_row['status'] if esc_row else '?'}")

    # Outbound tick: the review-escalated awaiting_approval task makes the
    # bridge send a review_gated message (NOT the risk_gated copy).
    stats = bridge._outbound_tick()
    review_texts = [t for t in cap.texts() if "REVIEW NEEDED" in t]
    _check("S15b · outbound tick sends a REVIEW-NEEDED message for an escalation",
           stats.get("review_gated", 0) >= 1 and len(review_texts) >= 1,
           f"stats_review_gated={stats.get('review_gated')} sent={len(review_texts)}")
    _check("S15c · review escalations are NOT rendered as 🛑 RISK-GATED",
           all("RISK-GATED" not in t for t in review_texts),
           f"review_texts={review_texts}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v070-smoke-")
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
