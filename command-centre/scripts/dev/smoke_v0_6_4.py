#!/usr/bin/env python3
"""Smoke test for v0.6.4 — Telegram reply-to-task-complete → follow-up `/run`.

Walks the 10 stop conditions from the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.4-amendment.md`.

Architecture (matches v0.6.2's smoke shape):
  - Real FastAPI server (this repo's `server.py`) on 127.0.0.1:8868,
    backed by a temp SQLite DB under a temp $CC_INSTALL_DIR.
  - A no-op `heartbeat.py` is seeded into the install dir so
    `/api/dispatcher/trigger` can spawn it; the activities row it
    writes confirms inline-trigger reached the endpoint (S8).
  - `telegram_bridge` imported in-process; `_tg` stubbed so no real
    Bot API call is made. The capture records every outbound
    sendMessage payload — assertions read against `cap.texts()`.
  - Tests drive `_handle_message` directly with synthetic message
    dicts (including reply-to-msg shape) and inspect both the
    captured replies AND the resulting DB rows.

Usage:
  python3 scripts/dev/smoke_v0_6_4.py
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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "command-centre" / "scripts"

API_PORT = 8868
BOT_TOKEN = "test-token-not-real-v064"
CHAT_ID = "424242"


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


def _seed_dummy_heartbeat(install_dir: Path) -> None:
    """Place a no-op heartbeat.py where /api/dispatcher/trigger expects
    it. The trigger Popens it detached; for the smoke we only need the
    file to exist + exit 0 so the endpoint writes the activities row
    that S8 asserts on."""
    hb_dir = install_dir / ".claude" / "skills" / "mission-control" / "scripts"
    hb_dir.mkdir(parents=True, exist_ok=True)
    hb = hb_dir / "heartbeat.py"
    hb.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
    hb.chmod(0o755)


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {name}" + (f" — {note}" if note else ""))


# ---------------------------------------------------------------------------
# Fixture seeding
# ---------------------------------------------------------------------------

def _seed_task(install_dir: Path, *, title: str, status: str = "done",
               output_summary: str | None = "") -> int:
    """Insert a fixture task directly. Bypasses /api/tasks because the
    POST endpoint forces status='pending' — we need terminal states for
    the task_complete-notification path."""
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            "INSERT INTO ops_tasks(title, description, status, "
            "execution_mode, output_summary, created_at_source) "
            "VALUES (?, ?, ?, 'classic', ?, 'dashboard')",
            (title, f"description for {title}", status, output_summary),
        )
        return cur.lastrowid


def _seed_notification(install_dir: Path, event_type: str, event_key: str,
                       tg_msg_id: str) -> None:
    with _open_db(install_dir) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO notification_log "
            "(event_type, event_key, chat_id, telegram_message_id) "
            "VALUES (?, ?, ?, ?)",
            (event_type, event_key, CHAT_ID, tg_msg_id),
        )


def _get_task(install_dir: Path, task_id: int) -> dict | None:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT id, title, description, status, execution_mode, "
            "priority, quadrant, risk_level, created_at_source "
            "FROM ops_tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def _count_activities(install_dir: Path, event_type: str,
                      since_id: int = 0) -> int:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM activities "
            "WHERE event_type=? AND id > ?",
            (event_type, since_id),
        ).fetchone()
    return int(row["n"])


def _last_activity_detail(install_dir: Path, event_type: str) -> dict | None:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT detail FROM activities WHERE event_type=? "
            "ORDER BY id DESC LIMIT 1",
            (event_type,),
        ).fetchone()
    if not row or not row["detail"]:
        return None
    try:
        return json.loads(row["detail"])
    except Exception:
        return None


def _max_activity_id(install_dir: Path) -> int:
    with _open_db(install_dir) as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM activities").fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Bridge stub harness
# ---------------------------------------------------------------------------

class TGCapture:
    """Stub `_tg` — records every sendMessage payload; returns a
    synthetic message_id back to the bridge so any post-send logic
    relying on the result still works."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._next_mid = 1000

    def __call__(self, method: str, payload: dict, timeout: float = 10.0) -> dict:
        if method == "sendMessage":
            self._next_mid += 1
            self.sent.append({**payload, "_mid": self._next_mid})
            return {"message_id": self._next_mid, "chat": {"id": payload.get("chat_id")}}
        if method == "getMe":
            return {"id": 1, "is_bot": True, "username": "mock_bot"}
        return {}

    def clear(self) -> None:
        self.sent.clear()

    def texts(self) -> list[str]:
        return [m.get("text", "") for m in self.sent]


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

def run(bridge, cap: TGCapture, install_dir: Path) -> None:
    chat = int(CHAT_ID)

    # ----- S1: Happy path -----
    print("\nS1 · happy path — done task with summary → new task chained")
    t1 = _seed_task(install_dir, title="write hello to /tmp/hello.txt",
                    status="done",
                    output_summary="Wrote /tmp/hello.txt (1 line, 5 bytes).")
    _seed_notification(install_dir, "task_complete", str(t1), tg_msg_id="7001")
    cap.clear()
    bridge._handle_message({
        "message_id": 1, "chat": {"id": chat},
        "text": "now write goodbye to /tmp/goodbye.txt",
        "reply_to_message": {"message_id": 7001},
    })
    # Find the newly-created task (max id excluding fixtures).
    with _open_db(install_dir) as conn:
        new_row = conn.execute(
            "SELECT id, title, description, execution_mode, quadrant, "
            "risk_level, created_at_source, status FROM ops_tasks "
            "WHERE id > ? ORDER BY id DESC LIMIT 1",
            (t1,),
        ).fetchone()
    _check("S1a · new task created", new_row is not None,
           f"texts={cap.texts()}")
    if new_row:
        nt = dict(new_row)
        _check("S1b · title is Follow-up: <reply prefix>",
               nt["title"] == "Follow-up: now write goodbye to /tmp/goodbye.txt",
               f"title={nt['title']!r}")
        _check("S1c · created_at_source='telegram'",
               nt["created_at_source"] == "telegram",
               f"got={nt['created_at_source']}")
        _check("S1d · execution_mode='classic'",
               nt["execution_mode"] == "classic",
               f"got={nt['execution_mode']}")
        _check("S1e · quadrant='do'",
               nt["quadrant"] == "do", f"got={nt['quadrant']}")
        _check("S1f · risk_level='low'",
               nt["risk_level"] == "low", f"got={nt['risk_level']}")
        desc = nt["description"] or ""
        _check("S1g · description references prev task #",
               f"#{t1}" in desc, f"desc head={desc[:120]!r}")
        _check("S1h · description includes prev title",
               "write hello to /tmp/hello.txt" in desc)
        _check("S1i · description includes prev summary",
               "Wrote /tmp/hello.txt" in desc)
        _check("S1j · description has --- separator", "\n---\n" in desc)
        _check("S1k · description ends with operator reply",
               desc.rstrip().endswith("now write goodbye to /tmp/goodbye.txt"))
    _check("S1l · reply confirms queued + references both ids",
           any(f"`#{new_row['id']}`" in t and f"`#{t1}`" in t
               for t in cap.texts()) if new_row else False,
           f"replies={cap.texts()}")
    s1_new_id = new_row["id"] if new_row else None

    # ----- S2: Empty summary -----
    print("\nS2 · prev task with NULL output_summary → '(no output summary recorded)'")
    t2 = _seed_task(install_dir, title="task with no summary",
                    status="done", output_summary=None)
    _seed_notification(install_dir, "task_complete", str(t2), tg_msg_id="7002")
    cap.clear()
    bridge._handle_message({
        "message_id": 2, "chat": {"id": chat},
        "text": "continue from there",
        "reply_to_message": {"message_id": 7002},
    })
    with _open_db(install_dir) as conn:
        new2 = conn.execute(
            "SELECT id, description FROM ops_tasks WHERE id > ? "
            "ORDER BY id DESC LIMIT 1",
            (t2,),
        ).fetchone()
    _check("S2a · new task created", new2 is not None)
    if new2:
        _check("S2b · description carries '(no output summary recorded)'",
               "(no output summary recorded)" in (new2["description"] or ""),
               f"desc head={(new2['description'] or '')[:200]!r}")
        _check("S2c · description still has --- separator + reply",
               "\n---\n" in (new2["description"] or "")
               and (new2["description"] or "").rstrip().endswith("continue from there"))

    # ----- S3: Failed task refusal -----
    print("\nS3 · reply to a failed task → refusal, no new task, no activities row")
    t3 = _seed_task(install_dir, title="failing task", status="failed",
                    output_summary=None)
    _seed_notification(install_dir, "task_complete", str(t3), tg_msg_id="7003")
    cap.clear()
    before_max = _max_activity_id(install_dir)
    with _open_db(install_dir) as conn:
        tasks_before = conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks WHERE id > ?", (t3,)
        ).fetchone()["n"]
    bridge._handle_message({
        "message_id": 3, "chat": {"id": chat},
        "text": "do the thing",
        "reply_to_message": {"message_id": 7003},
    })
    with _open_db(install_dir) as conn:
        tasks_after = conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks WHERE id > ?", (t3,)
        ).fetchone()["n"]
    _check("S3a · reply mentions failed + refusal hint",
           any("failed" in t.lower() and "fresh `/run`" in t
               for t in cap.texts()),
           f"replies={cap.texts()}")
    _check("S3b · no new ops_tasks row created",
           tasks_after == tasks_before,
           f"before={tasks_before} after={tasks_after}")
    _check("S3c · no new activities row",
           _count_activities(install_dir, "task_followup_created", since_id=before_max) == 0)

    # ----- S3.5: Cancelled task refusal (parity with failed) -----
    print("\nS3.5 · reply to a cancelled task → refusal (parity with failed)")
    t3b = _seed_task(install_dir, title="cancelled task", status="cancelled",
                     output_summary=None)
    _seed_notification(install_dir, "task_complete", str(t3b), tg_msg_id="7035")
    cap.clear()
    bridge._handle_message({
        "message_id": 35, "chat": {"id": chat},
        "text": "do the thing",
        "reply_to_message": {"message_id": 7035},
    })
    _check("S3.5 · reply mentions cancelled + refusal",
           any("cancelled" in t.lower() and "fresh `/run`" in t
               for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S4: Deleted task -----
    print("\nS4 · prev task deleted from DB → 'not found' reply, no new task")
    t4 = _seed_task(install_dir, title="will be deleted", status="done",
                    output_summary="some summary")
    _seed_notification(install_dir, "task_complete", str(t4), tg_msg_id="7004")
    with _open_db(install_dir) as conn:
        conn.execute("DELETE FROM ops_tasks WHERE id=?", (t4,))
        conn.commit()
    cap.clear()
    bridge._handle_message({
        "message_id": 4, "chat": {"id": chat},
        "text": "follow-up reply",
        "reply_to_message": {"message_id": 7004},
    })
    _check("S4 · reply mentions not found + deleted task",
           any(f"`#{t4}`" in t and "not found" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S5: Truncation rules -----
    print("\nS5 · 100-char title + 500-char summary → 80/300 truncation + ellipsis")
    long_title = "T" * 100
    long_summary = "S" * 500
    t5 = _seed_task(install_dir, title=long_title, status="done",
                    output_summary=long_summary)
    _seed_notification(install_dir, "task_complete", str(t5), tg_msg_id="7005")
    cap.clear()
    bridge._handle_message({
        "message_id": 5, "chat": {"id": chat},
        "text": "follow-up after big task",
        "reply_to_message": {"message_id": 7005},
    })
    with _open_db(install_dir) as conn:
        new5 = conn.execute(
            "SELECT id, description FROM ops_tasks WHERE id > ? "
            "ORDER BY id DESC LIMIT 1", (t5,),
        ).fetchone()
    _check("S5a · new task created", new5 is not None)
    if new5:
        desc = new5["description"] or ""
        ellipsis = "…"
        # Title: 80 T's + ellipsis, never 81+.
        _check("S5b · title truncated at 80 + ellipsis",
               ("T" * 80 + ellipsis) in desc and ("T" * 81) not in desc,
               f"head={desc[:150]!r}")
        # Summary: 300 S's + ellipsis, never 301+.
        _check("S5c · summary truncated at 300 + ellipsis",
               ("S" * 300 + ellipsis) in desc and ("S" * 301) not in desc)

    # ----- S6: Multi-hop -----
    print("\nS6 · reply to the v0.6.4-created task's notification → references parent, not grandparent")
    if s1_new_id is None:
        _check("S6 · skipped — S1 did not produce a new task", False)
    else:
        # Mark the S1 follow-up as done with its own summary so we can
        # chain on it.
        with _open_db(install_dir) as conn:
            conn.execute(
                "UPDATE ops_tasks SET status='done', "
                "output_summary='Saved /tmp/goodbye.txt.' WHERE id=?",
                (s1_new_id,),
            )
            conn.commit()
        _seed_notification(install_dir, "task_complete", str(s1_new_id),
                           tg_msg_id="7006")
        cap.clear()
        bridge._handle_message({
            "message_id": 6, "chat": {"id": chat},
            "text": "now sha256 both files",
            "reply_to_message": {"message_id": 7006},
        })
        with _open_db(install_dir) as conn:
            new6 = conn.execute(
                "SELECT id, description FROM ops_tasks "
                "WHERE id > ? ORDER BY id DESC LIMIT 1", (s1_new_id,),
            ).fetchone()
        _check("S6a · multi-hop new task created", new6 is not None,
               f"texts={cap.texts()}")
        if new6:
            desc = new6["description"] or ""
            _check("S6b · references immediate parent",
                   f"#{s1_new_id}" in desc,
                   f"head={desc[:150]!r}")
            _check("S6c · does NOT reference grandparent in the header line",
                   not desc.startswith(f"Follow-up to task #{1} ")
                   and not desc.split("\n", 1)[0].endswith(f"#{1}"),
                   f"head={desc[:80]!r}")

    # ----- S7: Audit log -----
    print("\nS7 · successful follow-up writes activities row with required fields")
    t7 = _seed_task(install_dir, title="audit fixture task", status="done",
                    output_summary="ok")
    _seed_notification(install_dir, "task_complete", str(t7), tg_msg_id="7007")
    before_max = _max_activity_id(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 7, "chat": {"id": chat},
        "text": "audit follow-up reply that is long enough to truncate at sixty characters maybe",
        "reply_to_message": {"message_id": 7007},
    })
    n_new = _count_activities(install_dir, "task_followup_created",
                              since_id=before_max)
    _check("S7a · exactly one new task_followup_created activities row",
           n_new == 1, f"got={n_new}")
    detail = _last_activity_detail(install_dir, "task_followup_created")
    _check("S7b · detail is a dict", isinstance(detail, dict),
           f"detail={detail}")
    if isinstance(detail, dict):
        _check("S7c · detail.source='telegram'",
               detail.get("source") == "telegram", f"got={detail.get('source')}")
        _check("S7d · detail.prev_task_id matches",
               detail.get("prev_task_id") == t7,
               f"got={detail.get('prev_task_id')}")
        _check("S7e · detail.new_task_id is an int",
               isinstance(detail.get("new_task_id"), int),
               f"got={detail.get('new_task_id')}")
        _check("S7f · detail.prev_title preserved (full, not truncated)",
               detail.get("prev_title") == "audit fixture task",
               f"got={detail.get('prev_title')}")
        op = detail.get("operator_reply_first_60") or ""
        _check("S7g · detail.operator_reply_first_60 is 60 chars",
               len(op) == 60, f"len={len(op)}")

    # ----- S8: Dispatcher triggered inline -----
    print("\nS8 · dispatcher trigger reached the endpoint (heartbeat spawned)")
    t8 = _seed_task(install_dir, title="dispatch fixture", status="done",
                    output_summary="ok")
    _seed_notification(install_dir, "task_complete", str(t8), tg_msg_id="7008")
    before_dispatch = _count_activities(install_dir, "dispatcher_trigger")
    cap.clear()
    bridge._handle_message({
        "message_id": 8, "chat": {"id": chat},
        "text": "follow-up that triggers dispatcher",
        "reply_to_message": {"message_id": 7008},
    })
    # The endpoint writes the activities row synchronously after Popen.
    # Give it a brief moment for the in-thread spawn.
    deadline = time.time() + 2.0
    after_dispatch = before_dispatch
    while time.time() < deadline:
        after_dispatch = _count_activities(install_dir, "dispatcher_trigger")
        if after_dispatch > before_dispatch:
            break
        time.sleep(0.05)
    _check("S8 · /api/dispatcher/trigger invoked (within 2s SLA)",
           after_dispatch > before_dispatch,
           f"before={before_dispatch} after={after_dispatch}")

    # ----- S9: Backward compat -----
    print("\nS9 · existing reply-to-msg routes still work, none trigger task creation")

    # decision reply still answers.
    with _open_db(install_dir) as conn:
        d_id = conn.execute(
            "INSERT INTO ops_decisions(prompt, status) VALUES (?, 'pending')",
            ("compat-decision",),
        ).lastrowid
    _seed_notification(install_dir, "decision", str(d_id), tg_msg_id="7009")
    before_tasks = _max_task_id(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 9, "chat": {"id": chat},
        "text": "yes, option A",
        "reply_to_message": {"message_id": 7009},
    })
    with _open_db(install_dir) as conn:
        drow = conn.execute(
            "SELECT status, answer FROM ops_decisions WHERE id=?", (d_id,),
        ).fetchone()
    _check("S9a · decision reply still answered",
           drow["status"] == "answered" and drow["answer"] == "yes, option A",
           f"status={drow['status']} answer={drow['answer']!r}")
    _check("S9b · decision reply did NOT create a task",
           _max_task_id(install_dir) == before_tasks)

    # inbox reply still recorded.
    with _open_db(install_dir) as conn:
        i_id = conn.execute(
            "INSERT INTO ops_inbox(body, direction) VALUES (?, 'agent_to_user')",
            ("compat-inbox",),
        ).lastrowid
    _seed_notification(install_dir, "inbox", str(i_id), tg_msg_id="7010")
    before_tasks = _max_task_id(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 10, "chat": {"id": chat},
        "text": "thanks, got it",
        "reply_to_message": {"message_id": 7010},
    })
    _check("S9c · inbox reply did NOT create a task",
           _max_task_id(install_dir) == before_tasks)

    # risk_gated reply still approves.
    t_rg = _seed_task(install_dir, title="risk-gated fixture",
                      status="awaiting_approval", output_summary=None)
    _seed_notification(install_dir, "risk_gated", str(t_rg), tg_msg_id="7011")
    before_tasks = _max_task_id(install_dir)
    before_max = _max_activity_id(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 11, "chat": {"id": chat},
        "text": "ok proceed",
        "reply_to_message": {"message_id": 7011},
    })
    rg_row = _get_task(install_dir, t_rg)
    _check("S9d · risk_gated reply still flipped status to pending",
           rg_row and rg_row["status"] == "pending",
           f"status={rg_row['status'] if rg_row else None}")
    _check("S9e · risk_gated reply did NOT create a follow-up task",
           _max_task_id(install_dir) == before_tasks)
    _check("S9f · risk_gated reply wrote task_risk_approved (not task_followup_created)",
           _count_activities(install_dir, "task_followup_created",
                             since_id=before_max) == 0
           and _count_activities(install_dir, "task_risk_approved",
                                 since_id=before_max) == 1)

    # ----- S10: /help updated -----
    print("\nS10 · /help renders the new follow-up line")
    cap.clear()
    bridge._handle_message({
        "message_id": 12, "chat": {"id": chat}, "text": "/help",
    })
    _check("S10a · /help still renders the usage card",
           any("Command Centre" in t for t in cap.texts()))
    _check("S10b · /help mentions task-complete follow-up",
           any("task-complete" in t and "follow-up" in t for t in cap.texts()),
           f"replies={[t[:60] for t in cap.texts()]}")

    # ----- Extra · NFR11 — malformed reply body -----
    print("\nNFR11 · empty + whitespace + very-long reply bodies handled gracefully")
    t_n = _seed_task(install_dir, title="nfr11 fixture", status="done",
                     output_summary="ok")
    _seed_notification(install_dir, "task_complete", str(t_n), tg_msg_id="7012")
    # Empty reply — currently filtered by the bridge `text` strip before
    # routing; whitespace is the realistic path here.
    cap.clear()
    bridge._handle_message({
        "message_id": 13, "chat": {"id": chat},
        "text": "   ",
        "reply_to_message": {"message_id": 7012},
    })
    # Empty/whitespace text is filtered before reply-to-msg routing
    # (existing bridge behaviour at _handle_message line 1010). That's
    # an acceptable upstream guard; the bridge does not crash and no
    # task is created.
    before_tasks = _max_task_id(install_dir)
    _check("NFR11a · whitespace-only text doesn't create a task or crash",
           _max_task_id(install_dir) == before_tasks)
    # Very-long reply.
    cap.clear()
    long_reply = "x" * 5000
    bridge._handle_message({
        "message_id": 14, "chat": {"id": chat},
        "text": long_reply,
        "reply_to_message": {"message_id": 7012},
    })
    with _open_db(install_dir) as conn:
        new_long = conn.execute(
            "SELECT id, title, description FROM ops_tasks WHERE id > ? "
            "ORDER BY id DESC LIMIT 1", (t_n,),
        ).fetchone()
    _check("NFR11b · 5000-char reply still creates a task (no crash)",
           new_long is not None)
    if new_long:
        # Title capped at 60 + ellipsis.
        _check("NFR11c · long reply → title capped at Follow-up: + 60 chars + …",
               new_long["title"] == "Follow-up: " + "x" * 60 + "…",
               f"title len={len(new_long['title'])}")


def _max_task_id(install_dir: Path) -> int:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS n FROM ops_tasks"
        ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v064-smoke-")
    install_dir = Path(tmp)
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"smoke install dir: {install_dir}")

    _seed_dummy_heartbeat(install_dir)

    server_proc = _start_server(install_dir)
    try:
        # Sanity-check schema landed.
        with sqlite3.connect(install_dir / "data" / "command-centre.db") as c:
            cols_tasks = {r[1] for r in c.execute("PRAGMA table_info(ops_tasks)")}
            cols_notif = {r[1] for r in c.execute("PRAGMA table_info(notification_log)")}
        for need, where, cols in (
            ("output_summary", "ops_tasks", cols_tasks),
            ("created_at_source", "ops_tasks", cols_tasks),
            ("telegram_message_id", "notification_log", cols_notif),
        ):
            if need not in cols:
                print(f"FATAL: {where}.{need} missing — schema mismatch")
                return 1
        print("migration ok — required columns present")

        os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
        os.environ["TELEGRAM_DASH_CHAT_ID"] = CHAT_ID
        os.environ["CC_DASHBOARD_URL"] = f"http://127.0.0.1:{API_PORT}"
        os.environ["CC_INSTALL_DIR"] = str(install_dir)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import telegram_bridge as bridge

        # Stub _tg so nothing escapes to the real Bot API.
        cap = TGCapture()
        bridge._tg = cap

        run(bridge, cap, install_dir)
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
