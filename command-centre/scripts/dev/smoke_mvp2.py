#!/usr/bin/env python3
"""Smoke test for v0.5.0-mvp2 — Telegram bridge inbound `/run`, `/approve`,
`/cancel` + reply-to-msg risk-gated routing.

Walks the 6 stop conditions from the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.5.0-mvp2-amendment.md`.

Architecture (mirrors the mvp1 stdlib-mock approach):

  - Real FastAPI server (this repo's `server.py`) on 127.0.0.1:8866,
    backed by a temp SQLite DB under a temp $CC_INSTALL_DIR.
  - Stdlib http.server mocking `api.telegram.org` on 127.0.0.1:8767.
    Captures every `sendMessage` payload for assertions.
  - `telegram_bridge` imported in-process; `_handle_message` is driven
    directly with synthetic message dicts (no actual long-poll). This
    keeps the test deterministic and the bridge's restart-safe inbound
    path (NFR9) is asserted separately by examining offset-file behaviour.

Usage:
  python3 scripts/dev/smoke_mvp2.py
  Exits 0 if all 6 scenarios pass, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "command-centre" / "scripts"

API_PORT = 8866
MOCK_TG_PORT = 8767
BOT_TOKEN = "test-token-not-real-1234567890"   # synthetic; never leaves loopback
CHAT_ID = "424242"


# ---------------------------------------------------------------------------
# Mock Telegram Bot API
# ---------------------------------------------------------------------------

# All captured `sendMessage` payloads land here. List of dicts.
mock_sent: list[dict] = []
_mock_lock = threading.Lock()
_mock_msg_id = [1000]


class _MockBotHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kw):  # silence default access log
        pass

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        method = self.path.rsplit("/", 1)[-1]
        result: dict[str, Any]
        if method == "getMe":
            result = {"id": 1, "is_bot": True, "username": "mock_bot"}
        elif method == "sendMessage":
            with _mock_lock:
                _mock_msg_id[0] += 1
                mid = _mock_msg_id[0]
                mock_sent.append({**payload, "_assigned_message_id": mid})
            result = {"message_id": mid, "chat": {"id": payload.get("chat_id")}}
        else:
            result = {}
        body = json.dumps({"ok": True, "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_mock_tg() -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", MOCK_TG_PORT), _MockBotHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ---------------------------------------------------------------------------
# Real FastAPI server (subprocess) against a temp install dir
# ---------------------------------------------------------------------------

def _wait_port(host: str, port: int, timeout: float = 10.0) -> bool:
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
    # Use the venv python if it's available, else system python3.
    venv_py = Path.home() / ".command-centre" / "venv" / "bin" / "python3"
    py = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.Popen(
        [py, str(SCRIPTS_DIR / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(SCRIPTS_DIR),
    )
    if not _wait_port("127.0.0.1", API_PORT, timeout=15):
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        raise RuntimeError(
            f"server failed to come up on :{API_PORT}\n"
            f"stdout:\n{out.decode(errors='replace')}\n"
            f"stderr:\n{err.decode(errors='replace')}"
        )
    return proc


def _http_get(path: str) -> Any:
    with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}{path}", timeout=5) as r:
        return json.loads(r.read())


def _http_post(path: str, payload: dict | None = None) -> tuple[int, Any]:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


# ---------------------------------------------------------------------------
# DB helpers — talk to the same SQLite the server is using.
# ---------------------------------------------------------------------------

def _open_db(install_dir: Path):
    import sqlite3
    db_path = install_dir / "data" / "command-centre.db"
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {name}" + (f" — {note}" if note else ""))


def run_scenarios(bridge, install_dir: Path) -> None:
    # ----- Scenario 1: /run happy path -----
    print("\nScenario 1 — /run happy path")
    mock_sent.clear()
    bridge._handle_message({
        "message_id": 1,
        "chat": {"id": int(CHAT_ID)},
        "text": "/run write hello world to /tmp/hello.txt",
    })
    time.sleep(0.2)
    # Inspect the latest ops_tasks row.
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT id, title, description, created_at_source FROM ops_tasks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    _check("S1a · ops_tasks row created", row is not None,
           f"id={row['id'] if row else None}")
    _check("S1b · created_at_source = 'telegram'",
           bool(row) and row["created_at_source"] == "telegram",
           f"value={row['created_at_source'] if row else None}")
    _check("S1c · title is first-80 of prompt",
           bool(row) and row["title"].startswith("write hello world"),
           f"title={row['title'] if row else None!r}")
    _check("S1d · description carries full prompt",
           bool(row) and "write hello world to /tmp/hello.txt" in (row["description"] or ""),
           "")
    reply_texts = [m.get("text", "") for m in mock_sent]
    _check("S1e · bot replied with task id",
           any("queued" in t and "#" in t for t in reply_texts),
           f"replies={reply_texts}")
    task_id_s1 = row["id"] if row else None

    # ----- Scenario 2: /cancel happy path -----
    print("\nScenario 2 — /cancel happy path")
    mock_sent.clear()
    bridge._handle_message({
        "message_id": 2,
        "chat": {"id": int(CHAT_ID)},
        "text": f"/cancel {task_id_s1}",
    })
    time.sleep(0.2)
    with _open_db(install_dir) as conn:
        status_row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id = ?", (task_id_s1,)
        ).fetchone()
        act_row = conn.execute(
            "SELECT detail FROM activities WHERE event_type='task_cancelled' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    _check("S2a · status flipped to 'cancelled'",
           bool(status_row) and status_row["status"] == "cancelled",
           f"status={status_row['status'] if status_row else None}")
    _check("S2b · activities row written", act_row is not None)
    if act_row:
        try:
            detail = json.loads(act_row["detail"])
        except Exception:
            detail = {}
        _check("S2c · activities.source = 'telegram'",
               detail.get("source") == "telegram",
               f"detail={detail}")
        _check("S2d · activities records prior_status='pending'",
               detail.get("prior_status") == "pending",
               f"prior={detail.get('prior_status')}")
    reply_texts = [m.get("text", "") for m in mock_sent]
    _check("S2e · bot replied with cancellation confirmation",
           any("cancelled" in t for t in reply_texts),
           f"replies={reply_texts}")

    # ----- Scenario 3: /approve via reply-to-msg on risk_gated -----
    print("\nScenario 3 — /approve via reply-to-msg on risk_gated")
    # Seed: create a task already in awaiting_approval state.
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            "INSERT INTO ops_tasks(title, status, requires_approval, risk_level, "
            "execution_mode, cost_source, created_at_source) "
            "VALUES (?, 'awaiting_approval', 1, 'high', 'classic', 'api_pool', 'dashboard')",
            ("delete stale branches",),
        )
        rg_task_id = cur.lastrowid
        # Seed notification_log so reply-to-msg routes here.
        conn.execute(
            "INSERT OR REPLACE INTO notification_log "
            "(event_type, event_key, chat_id, telegram_message_id) "
            "VALUES ('risk_gated', ?, ?, ?)",
            (str(rg_task_id), CHAT_ID, "9999"),
        )
    mock_sent.clear()
    # Operator replies to the 🛑 RISK-GATED message (Telegram message_id 9999).
    bridge._handle_message({
        "message_id": 100,
        "chat": {"id": int(CHAT_ID)},
        "text": "yes go ahead",
        "reply_to_message": {"message_id": 9999},
    })
    time.sleep(0.2)
    with _open_db(install_dir) as conn:
        status_row = conn.execute(
            "SELECT status FROM ops_tasks WHERE id = ?", (rg_task_id,)
        ).fetchone()
        act_row = conn.execute(
            "SELECT detail FROM activities WHERE event_type='task_risk_approved' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    _check("S3a · status flipped to 'pending'",
           bool(status_row) and status_row["status"] == "pending",
           f"status={status_row['status'] if status_row else None}")
    _check("S3b · activities task_risk_approved row written", act_row is not None)
    if act_row:
        try:
            detail = json.loads(act_row["detail"])
        except Exception:
            detail = {}
        _check("S3c · activities.source = 'telegram'",
               detail.get("source") == "telegram",
               f"detail={detail}")
    reply_texts = [m.get("text", "") for m in mock_sent]
    _check("S3d · bot replied with approval confirmation",
           any("approved" in t for t in reply_texts),
           f"replies={reply_texts}")

    # ----- Scenario 4: Backward-compat -----
    print("\nScenario 4 — backward compat (/answer, /reply, /help)")
    # Seed a pending decision so /answer has a target.
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            "INSERT INTO ops_decisions(prompt, status) VALUES (?, 'pending')",
            ("Pick option A or B?",),
        )
        decision_id = cur.lastrowid
    mock_sent.clear()
    bridge._handle_message({
        "message_id": 200,
        "chat": {"id": int(CHAT_ID)},
        "text": f"/answer {decision_id} option A",
    })
    time.sleep(0.2)
    with _open_db(install_dir) as conn:
        d_row = conn.execute(
            "SELECT status, answer FROM ops_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
    _check("S4a · /answer flipped decision to 'answered'",
           bool(d_row) and d_row["status"] == "answered",
           f"row={dict(d_row) if d_row else None}")
    # /help
    mock_sent.clear()
    bridge._handle_message({
        "message_id": 201,
        "chat": {"id": int(CHAT_ID)},
        "text": "/help",
    })
    time.sleep(0.2)
    help_texts = [m.get("text", "") for m in mock_sent]
    _check("S4b · /help replied with usage card",
           any("Command Centre" in t for t in help_texts),
           f"replies={[t[:60] for t in help_texts]}")
    # /reply on an inbox row.
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            "INSERT INTO ops_inbox(direction, body) VALUES ('agent_to_user', ?)",
            ("FYI: build finished",),
        )
        inbox_id = cur.lastrowid
    mock_sent.clear()
    bridge._handle_message({
        "message_id": 202,
        "chat": {"id": int(CHAT_ID)},
        "text": f"/reply {inbox_id} thanks got it",
    })
    time.sleep(0.2)
    with _open_db(install_dir) as conn:
        in_row = conn.execute(
            "SELECT direction, body FROM ops_inbox "
            "WHERE direction='user_to_agent' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    _check("S4c · /reply inserted user_to_agent row",
           bool(in_row) and "thanks" in (in_row["body"] or ""),
           f"row={dict(in_row) if in_row else None}")

    # ----- Scenario 5: NFR11 — malformed /run -----
    print("\nScenario 5 — NFR11 malformed /run inputs")
    mock_sent.clear()
    # Empty /run.
    bridge._handle_message({
        "message_id": 300, "chat": {"id": int(CHAT_ID)}, "text": "/run",
    })
    bridge._handle_message({
        "message_id": 301, "chat": {"id": int(CHAT_ID)}, "text": "/run   ",
    })
    # Over-long /run (3001 chars after the verb + space).
    over = "x" * 3100
    bridge._handle_message({
        "message_id": 302, "chat": {"id": int(CHAT_ID)}, "text": f"/run {over}",
    })
    time.sleep(0.3)
    reply_texts = [m.get("text", "") for m in mock_sent]
    _check("S5a · empty /run → usage hint",
           any("usage" in t.lower() for t in reply_texts),
           f"replies={[t[:60] for t in reply_texts]}")
    _check("S5b · over-long /run → 'shorter' hint",
           any("shorter" in t.lower() or "too long" in t.lower() for t in reply_texts),
           f"replies={[t[:60] for t in reply_texts]}")
    # Bridge survived all three malformed calls — we got this far without
    # an exception bubbling up, which is the NFR11 promise.
    _check("S5c · loop did not crash on malformed input", True)

    # ----- Scenario 6: NFR11 — non-integer ID -----
    print("\nScenario 6 — NFR11 non-integer task id")
    mock_sent.clear()
    bridge._handle_message({
        "message_id": 400, "chat": {"id": int(CHAT_ID)}, "text": "/cancel abc",
    })
    bridge._handle_message({
        "message_id": 401, "chat": {"id": int(CHAT_ID)}, "text": "/approve xyz",
    })
    time.sleep(0.2)
    reply_texts = [m.get("text", "") for m in mock_sent]
    _check("S6a · /cancel abc → usage hint",
           any("/cancel" in t and "id" in t.lower() for t in reply_texts),
           f"replies={[t[:80] for t in reply_texts]}")
    _check("S6b · /approve xyz → usage hint",
           any("/approve" in t and "id" in t.lower() for t in reply_texts),
           f"replies={[t[:80] for t in reply_texts]}")


# ---------------------------------------------------------------------------
# NFR5 grep audit — token must never appear in DB rows or stdout/stderr capture.
# ---------------------------------------------------------------------------

def _nfr5_audit(install_dir: Path, server_proc: subprocess.Popen) -> None:
    print("\nNFR5 audit — bot token must not appear in DB or server output")
    import sqlite3
    db_path = install_dir / "data" / "command-centre.db"
    conn = sqlite3.connect(db_path)
    found = False
    # Scan every TEXT column of every user table.
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for (tname,) in cur.fetchall():
        if tname.startswith("sqlite_"):
            continue
        try:
            for row in conn.execute(f"SELECT * FROM {tname}"):
                for col in row:
                    if isinstance(col, str) and BOT_TOKEN in col:
                        print(f"    [!] token leaked into {tname}: {col[:100]!r}")
                        found = True
        except Exception:
            pass
    conn.close()
    _check("NFR5a · token absent from every DB row", not found)
    # Stop the server so we can drain its stdout/stderr.
    server_proc.terminate()
    try:
        out, err = server_proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        server_proc.kill()
        out, err = server_proc.communicate(timeout=2)
    server_text = (out + err).decode(errors="replace")
    _check("NFR5b · token absent from server stdout/stderr",
           BOT_TOKEN not in server_text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-mvp2-smoke-")
    install_dir = Path(tmp)
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"smoke install dir: {install_dir}")

    # Bring the API server up first; lifespan creates the schema + migration.
    server_proc = _start_server(install_dir)
    try:
        # Sanity-check the migration landed.
        import sqlite3
        with sqlite3.connect(install_dir / "data" / "command-centre.db") as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(ops_tasks)")}
        if "created_at_source" not in cols:
            print("FATAL: created_at_source migration did not run")
            return 1
        print(f"migration ok — ops_tasks columns include created_at_source")

        # Start mock TG.
        mock_srv = _start_mock_tg()

        # Configure env for the bridge and import it.
        os.environ["TELEGRAM_API_BASE"] = f"http://127.0.0.1:{MOCK_TG_PORT}"
        os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
        os.environ["TELEGRAM_DASH_CHAT_ID"] = CHAT_ID
        os.environ["CC_DASHBOARD_URL"] = f"http://127.0.0.1:{API_PORT}"
        os.environ["CC_INSTALL_DIR"] = str(install_dir)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import telegram_bridge as bridge

        run_scenarios(bridge, install_dir)

        # NFR5 audit (stops the server as part of capturing its output).
        _nfr5_audit(install_dir, server_proc)

        mock_srv.shutdown()
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
