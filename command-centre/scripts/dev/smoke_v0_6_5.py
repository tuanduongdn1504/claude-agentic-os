#!/usr/bin/env python3
"""Smoke test for v0.6.5 — Telegram `/yes <id>` + `/no <id>` slash commands.

Walks the 9 stop conditions from the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.5-amendment.md`.

Architecture (matches v0.6.2 / v0.6.4 smoke shape):
  - Real FastAPI server (this repo's `server.py`) on 127.0.0.1:8869,
    backed by a temp SQLite DB under a temp $CC_INSTALL_DIR.
  - `telegram_bridge` imported in-process; `_tg` stubbed to capture every
    outbound sendMessage payload so no real Bot API call is made.
  - Tests drive `_handle_message` directly with synthetic message dicts
    and inspect both the captured replies AND the resulting DB rows
    (ops_decisions.status / answer + activities for the no-audit check).

Usage:
  python3 scripts/dev/smoke_v0_6_5.py
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

API_PORT = 8869
BOT_TOKEN = "test-token-not-real-v065"
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

def _seed_decision(install_dir: Path, prompt: str = "Pick A or B?",
                   status: str = "pending") -> int:
    with _open_db(install_dir) as conn:
        cur = conn.execute(
            "INSERT INTO ops_decisions(prompt, status) VALUES (?, ?)",
            (prompt, status),
        )
        return cur.lastrowid


def _seed_notification(install_dir: Path, event_type: str, event_key: str,
                       tg_msg_id: str = "9000") -> None:
    with _open_db(install_dir) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO notification_log "
            "(event_type, event_key, chat_id, telegram_message_id) "
            "VALUES (?, ?, ?, ?)",
            (event_type, event_key, CHAT_ID, tg_msg_id),
        )


def _decision_row(install_dir: Path, decision_id: int) -> sqlite3.Row | None:
    with _open_db(install_dir) as conn:
        return conn.execute(
            "SELECT status, answer FROM ops_decisions WHERE id=?",
            (decision_id,),
        ).fetchone()


def _activities_count(install_dir: Path) -> int:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM activities"
        ).fetchone()
        return int(row["n"])


# ---------------------------------------------------------------------------
# Bridge stub harness
# ---------------------------------------------------------------------------

class TGCapture:
    """Stub `_tg` so no real Bot API call is made. Records every
    sendMessage payload; returns a synthetic message_id back to the bridge."""

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

    # ----- Regex fixture check (pre-wiring sanity, part of amendment step 1) -----
    print("\nR · _CMD_WITH_ID_RE fixture parses")
    cases = [
        ("/yes 42", ("yes", "42", None)),
        ("/no 42", ("no", "42", None)),
        ("/YES 42", ("YES", "42", None)),
        ("/Yes 42", ("Yes", "42", None)),
        ("/answer 42 body", ("answer", "42", "body")),
        ("/snooze 42 30m", ("snooze", "42", "30m")),
        ("/yes abc", None),
        ("/yes", None),
        ("/yesno 42", None),
    ]
    for text, want in cases:
        m = bridge._CMD_WITH_ID_RE.match(text)
        got = m.groups() if m else None
        _check(f"R · regex {text!r}", got == want, f"got={got}")

    # ----- S1: /yes <id> happy path -----
    print("\nS1 · /yes <id> happy path")
    d1 = _seed_decision(install_dir)
    activities_before = _activities_count(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 1, "chat": {"id": chat},
        "text": f"/yes {d1}",
    })
    row = _decision_row(install_dir, d1)
    _check("S1a · status='answered'",
           bool(row) and row["status"] == "answered",
           f"row={dict(row) if row else None}")
    _check("S1b · answer='yes'",
           bool(row) and row["answer"] == "yes",
           f"answer={row['answer'] if row else None}")
    _check("S1c · reply confirms",
           any("answered: yes" in t for t in cap.texts())
           and any(f"#{d1}" in t for t in cap.texts()),
           f"replies={cap.texts()}")
    _check("S1d · no audit row (matches /answer pattern)",
           _activities_count(install_dir) == activities_before,
           f"before={activities_before} after={_activities_count(install_dir)}")

    # ----- S2: /no <id> happy path + no-audit check -----
    print("\nS2 · /no <id> happy path + no-audit verification")
    d2 = _seed_decision(install_dir)
    activities_before = _activities_count(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 2, "chat": {"id": chat},
        "text": f"/no {d2}",
    })
    row = _decision_row(install_dir, d2)
    _check("S2a · status='answered'",
           bool(row) and row["status"] == "answered")
    _check("S2b · answer='no' (lowercase, normalized)",
           bool(row) and row["answer"] == "no",
           f"answer={row['answer'] if row else None}")
    _check("S2c · reply confirms",
           any("answered: no" in t for t in cap.texts()),
           f"replies={cap.texts()}")
    _check("S2d · activities has NO new row after /no",
           _activities_count(install_dir) == activities_before,
           f"before={activities_before} after={_activities_count(install_dir)}")

    # Case-insensitive variant — /YES 42 should still answer "yes" lowercase.
    d2c = _seed_decision(install_dir)
    cap.clear()
    bridge._handle_message({
        "message_id": 22, "chat": {"id": chat},
        "text": f"/YES {d2c}",
    })
    row = _decision_row(install_dir, d2c)
    _check("S2e · /YES <id> normalizes answer to lowercase 'yes'",
           bool(row) and row["answer"] == "yes",
           f"answer={row['answer'] if row else None}")

    # ----- S3: Reply-to-msg /yes shortcut -----
    print("\nS3 · reply-to-msg `/yes` shortcut")
    d3 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d3), tg_msg_id="9003")
    cap.clear()
    bridge._handle_message({
        "message_id": 3, "chat": {"id": chat},
        "text": "/yes",
        "reply_to_message": {"message_id": 9003},
    })
    row = _decision_row(install_dir, d3)
    _check("S3a · status='answered'",
           bool(row) and row["status"] == "answered")
    _check("S3b · API received clean 'yes' (NOT '/yes')",
           bool(row) and row["answer"] == "yes",
           f"answer={row['answer'] if row else None}")

    # ----- S4: Reply-to-msg bare `yes` shortcut -----
    print("\nS4 · reply-to-msg bare `yes` shortcut")
    d4 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d4), tg_msg_id="9004")
    cap.clear()
    bridge._handle_message({
        "message_id": 4, "chat": {"id": chat},
        "text": "yes",
        "reply_to_message": {"message_id": 9004},
    })
    row = _decision_row(install_dir, d4)
    _check("S4a · status='answered'",
           bool(row) and row["status"] == "answered")
    _check("S4b · answer='yes'",
           bool(row) and row["answer"] == "yes",
           f"answer={row['answer'] if row else None}")

    # Symmetry: bare `no` and `/no` reply also fire.
    d4b = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d4b), tg_msg_id="9404")
    cap.clear()
    bridge._handle_message({
        "message_id": 41, "chat": {"id": chat},
        "text": "no",
        "reply_to_message": {"message_id": 9404},
    })
    row = _decision_row(install_dir, d4b)
    _check("S4c · bare `no` reply → answer='no'",
           bool(row) and row["answer"] == "no")

    d4c = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d4c), tg_msg_id="9504")
    cap.clear()
    bridge._handle_message({
        "message_id": 42, "chat": {"id": chat},
        "text": "/No",
        "reply_to_message": {"message_id": 9504},
    })
    row = _decision_row(install_dir, d4c)
    _check("S4d · `/No` (mixed-case) reply → answer='no'",
           bool(row) and row["answer"] == "no",
           f"answer={row['answer'] if row else None}")

    # ----- S5: Reply-to-msg verbatim fallback preserved -----
    print("\nS5 · reply-to-msg verbatim fallback preserved for nuanced replies")
    d5 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d5), tg_msg_id="9005")
    cap.clear()
    bridge._handle_message({
        "message_id": 5, "chat": {"id": chat},
        "text": "Yes, do it",
        "reply_to_message": {"message_id": 9005},
    })
    row = _decision_row(install_dir, d5)
    _check("S5a · answer captured verbatim, NOT collapsed to 'yes'",
           bool(row) and row["answer"] == "Yes, do it",
           f"answer={row['answer'] if row else None}")

    d5b = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d5b), tg_msg_id="9505")
    cap.clear()
    bridge._handle_message({
        "message_id": 51, "chat": {"id": chat},
        "text": "No — defer",
        "reply_to_message": {"message_id": 9505},
    })
    row = _decision_row(install_dir, d5b)
    _check("S5b · 'No — defer' captured verbatim, NOT collapsed to 'no'",
           bool(row) and row["answer"] == "No — defer",
           f"answer={row['answer'] if row else None}")

    # ----- S6: Already-answered decision -----
    print("\nS6 · /yes on an already-answered decision → already-answered reply")
    d6 = _seed_decision(install_dir)
    # Answer it first.
    bridge._handle_message({
        "message_id": 60, "chat": {"id": chat},
        "text": f"/yes {d6}",
    })
    cap.clear()
    bridge._handle_message({
        "message_id": 61, "chat": {"id": chat},
        "text": f"/yes {d6}",
    })
    _check("S6 · second /yes reply mentions already-answered",
           any("already" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S7: Non-existent decision -----
    print("\nS7 · /yes 9999 → 'not found'")
    cap.clear()
    bridge._handle_message({
        "message_id": 7, "chat": {"id": chat},
        "text": "/yes 9999",
    })
    _check("S7 · reply mentions not found",
           any("not found" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S8: Malformed ID — NFR11 -----
    print("\nS8 · /yes abc → no regex match, long-poll continues, no crash")
    activities_before = _activities_count(install_dir)
    cap.clear()
    # Should not crash. Regex won't match; falls through to /help bare-prefix
    # checks (none match), then to /help literal check (no match), then drops.
    bridge._handle_message({
        "message_id": 8, "chat": {"id": chat},
        "text": "/yes abc",
    })
    bridge._handle_message({
        "message_id": 81, "chat": {"id": chat},
        "text": "/yes",
    })
    bridge._handle_message({
        "message_id": 82, "chat": {"id": chat},
        "text": "/no",
    })
    _check("S8a · no DB activity from malformed input",
           _activities_count(install_dir) == activities_before,
           f"delta={_activities_count(install_dir) - activities_before}")
    # Bridge MAY silently drop or hint — either is NFR11-compliant. We just
    # require no crash and no decision-state mutation.

    # ----- S9: Backward compat -----
    print("\nS9 · existing verbs still parse + route correctly")
    # Regex sanity (subset reused).
    regex_cases = [
        ("/answer 42 yes", True),
        ("/reply 99 thanks", True),
        ("/approve 7", True),
        ("/cancel 8", True),
        ("/snooze 42 30m", True),
        ("/yes 42", True),
        ("/no 42", True),
    ]
    for text, want in regex_cases:
        _check(f"S9 · regex match {text!r}",
               bool(bridge._CMD_WITH_ID_RE.match(text)) == want)
    _check("S9 · /run regex still matches",
           bool(bridge._CMD_RUN_RE.match("/run draft the PR")))
    _check("S9 · /status regex still matches",
           bool(bridge._CMD_STATUS_RE.match("/status")))
    # End-to-end /answer still flows.
    d_compat = _seed_decision(install_dir, prompt="compat-check")
    cap.clear()
    bridge._handle_message({
        "message_id": 9, "chat": {"id": chat},
        "text": f"/answer {d_compat} option A",
    })
    row = _decision_row(install_dir, d_compat)
    _check("S9 · /answer end-to-end flips status to 'answered'",
           bool(row) and row["status"] == "answered",
           f"row={dict(row) if row else None}")
    _check("S9 · /answer preserves full body verbatim",
           bool(row) and row["answer"] == "option A",
           f"answer={row['answer'] if row else None}")
    # /help still renders + now mentions /yes /no.
    cap.clear()
    bridge._handle_message({
        "message_id": 90, "chat": {"id": chat}, "text": "/help",
    })
    _check("S9 · /help still renders the usage card",
           any("Command Centre" in t for t in cap.texts()),
           f"replies={[t[:40] for t in cap.texts()]}")
    _check("S9 · /help mentions /yes",
           any("/yes" in t for t in cap.texts()))
    _check("S9 · /help mentions /no",
           any("/no" in t for t in cap.texts()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v065-smoke-")
    install_dir = Path(tmp)
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"smoke install dir: {install_dir}")

    server_proc = _start_server(install_dir)
    try:
        os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
        os.environ["TELEGRAM_DASH_CHAT_ID"] = CHAT_ID
        os.environ["CC_DASHBOARD_URL"] = f"http://127.0.0.1:{API_PORT}"
        os.environ["CC_INSTALL_DIR"] = str(install_dir)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import telegram_bridge as bridge

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
