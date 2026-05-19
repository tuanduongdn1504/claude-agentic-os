#!/usr/bin/env python3
"""Smoke test for v0.6.2 — Telegram `/snooze <decision_id> [duration]`.

Walks the 12 stop conditions from the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.2-amendment.md`.

Architecture (matches v0.6.1's smoke shape):
  - Real FastAPI server (this repo's `server.py`) on 127.0.0.1:8866,
    backed by a temp SQLite DB under a temp $CC_INSTALL_DIR.
  - `telegram_bridge` imported in-process; `_tg` stubbed to capture every
    outbound sendMessage payload so no real Bot API call is made.
  - Tests drive `_handle_message` directly with synthetic message dicts
    and inspect both the captured replies AND the resulting DB rows
    (notification_log.snoozed_until + activities.event_type='decision_snoozed').

Usage:
  python3 scripts/dev/smoke_v0_6_2.py
  Exits 0 if all 12 stop conditions pass, 1 otherwise.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "command-centre" / "scripts"

API_PORT = 8866
BOT_TOKEN = "test-token-not-real-v062"
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


def _set_snoozed(install_dir: Path, event_type: str, event_key: str,
                 snoozed_until: str | None) -> None:
    with _open_db(install_dir) as conn:
        conn.execute(
            "UPDATE notification_log SET snoozed_until=? "
            "WHERE event_type=? AND event_key=? AND chat_id=?",
            (snoozed_until, event_type, event_key, CHAT_ID),
        )


def _get_snoozed(install_dir: Path, event_type: str, event_key: str) -> str | None:
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT snoozed_until FROM notification_log "
            "WHERE event_type=? AND event_key=? AND chat_id=?",
            (event_type, event_key, CHAT_ID),
        ).fetchone()
        return row["snoozed_until"] if row else None


def _answer_decision(install_dir: Path, decision_id: int) -> None:
    with _open_db(install_dir) as conn:
        conn.execute(
            "UPDATE ops_decisions SET status='answered', answer='manually answered', "
            "answered_at=datetime('now') WHERE id=?",
            (decision_id,),
        )


# ---------------------------------------------------------------------------
# Bridge stub harness
# ---------------------------------------------------------------------------

class TGCapture:
    """Stub `_tg` so no real Bot API call is made. Records every
    sendMessage payload; returns synthetic message_id back to the bridge."""

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

    # ----- S1: Happy path with default duration -----
    print("\nS1 · /snooze <id> happy path (default 30m)")
    d1 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d1), tg_msg_id="9001")
    cap.clear()
    t_before = datetime.now(timezone.utc)
    bridge._handle_message({
        "message_id": 1, "chat": {"id": chat},
        "text": f"/snooze {d1}",
    })
    snoozed = _get_snoozed(install_dir, "decision", str(d1))
    _check("S1a · snoozed_until set", bool(snoozed), f"value={snoozed}")
    if snoozed:
        target = datetime.strptime(snoozed, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta = (target - t_before).total_seconds()
        _check("S1b · ≈30 minutes ahead", 1700 < delta < 1900, f"delta={delta:.0f}s")
    _check("S1c · reply mentions 30m",
           any("snoozed for 30m" in t for t in cap.texts()),
           f"replies={cap.texts()}")
    _check("S1d · reply mentions re-fire time",
           any("next re-fire" in t for t in cap.texts()))
    # _already_notified must now suppress the next tick.
    _check("S1e · _already_notified=True while snoozed",
           bridge._already_notified("decision", str(d1)) is True)

    # ----- S2: Explicit duration -----
    print("\nS2 · /snooze <id> 2h explicit duration")
    d2 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d2), tg_msg_id="9002")
    cap.clear()
    t_before = datetime.now(timezone.utc)
    bridge._handle_message({
        "message_id": 2, "chat": {"id": chat},
        "text": f"/snooze {d2} 2h",
    })
    snoozed = _get_snoozed(install_dir, "decision", str(d2))
    if snoozed:
        target = datetime.strptime(snoozed, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta = (target - t_before).total_seconds()
        _check("S2a · ≈2 hours ahead", 7100 < delta < 7300, f"delta={delta:.0f}s")
    else:
        _check("S2a · ≈2 hours ahead", False, "snoozed_until not set")
    _check("S2b · reply mentions 2h",
           any("snoozed for 2h" in t for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S3: Re-snooze advances the timer -----
    print("\nS3 · re-snooze advances timer")
    d3 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d3), tg_msg_id="9003")
    cap.clear()
    bridge._handle_message({
        "message_id": 30, "chat": {"id": chat},
        "text": f"/snooze {d3} 30m",
    })
    first = _get_snoozed(install_dir, "decision", str(d3))
    time.sleep(1.1)
    bridge._handle_message({
        "message_id": 31, "chat": {"id": chat},
        "text": f"/snooze {d3} 2h",
    })
    second = _get_snoozed(install_dir, "decision", str(d3))
    _check("S3a · second snooze advances the timer",
           bool(first and second) and second > first,
           f"first={first} second={second}")
    if first and second:
        delta_s = (datetime.strptime(second, "%Y-%m-%d %H:%M:%S")
                   - datetime.strptime(first, "%Y-%m-%d %H:%M:%S")).total_seconds()
        # 2h - 30m = 90min ≈ 5400s, ±a few seconds for the sleep.
        _check("S3b · advance ≈ 5400s (90 min net)",
               5390 < delta_s < 5420, f"delta_s={delta_s:.0f}")

    # ----- S4: Re-fire after elapse -----
    print("\nS4 · re-fire after window elapses")
    d4 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d4), tg_msg_id="9004")
    # Force snoozed_until to the past, simulating an elapsed snooze.
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    _set_snoozed(install_dir, "decision", str(d4), past)
    _check("S4a · _already_notified=False once snooze elapses",
           bridge._already_notified("decision", str(d4)) is False)
    # The outbound tick would now re-send and call _record_notify; simulate it.
    bridge._record_notify("decision", str(d4), "9999")
    after = _get_snoozed(install_dir, "decision", str(d4))
    _check("S4b · _record_notify clears snoozed_until after re-fire",
           after is None, f"after={after}")
    _check("S4c · _already_notified=True after re-fire (no loop)",
           bridge._already_notified("decision", str(d4)) is True)

    # ----- S5: Reply-to-msg snooze -----
    print("\nS5 · reply-to-msg `/snooze 30m` on a DECISION ping")
    d5 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d5), tg_msg_id="9005")
    cap.clear()
    bridge._handle_message({
        "message_id": 5, "chat": {"id": chat},
        "text": "/snooze 30m",
        "reply_to_message": {"message_id": 9005},
    })
    snoozed = _get_snoozed(install_dir, "decision", str(d5))
    _check("S5a · reply-to-msg snooze sets snoozed_until", bool(snoozed),
           f"value={snoozed}")
    _check("S5b · reply confirms 30m",
           any("snoozed for 30m" in t for t in cap.texts()),
           f"replies={cap.texts()}")
    # Bare `/snooze` (no duration) on a reply-to-msg too.
    d5b = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d5b), tg_msg_id="9505")
    cap.clear()
    bridge._handle_message({
        "message_id": 6, "chat": {"id": chat},
        "text": "/snooze",
        "reply_to_message": {"message_id": 9505},
    })
    snoozed = _get_snoozed(install_dir, "decision", str(d5b))
    _check("S5c · bare /snooze (reply-to) uses default 30m",
           bool(snoozed)
           and any("snoozed for 30m" in t for t in cap.texts()),
           f"value={snoozed} replies={cap.texts()}")

    # ----- S6: Malformed duration -----
    print("\nS6 · malformed duration → usage hint, no crash, no DB change")
    d6 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d6), tg_msg_id="9006")
    before = _get_snoozed(install_dir, "decision", str(d6))
    cap.clear()
    for bad in (f"/snooze {d6} wat", f"/snooze {d6} 42 wat",
                f"/snooze {d6} 0m", f"/snooze {d6} -1m",
                f"/snooze {d6} 30s"):
        bridge._handle_message({
            "message_id": 60, "chat": {"id": chat}, "text": bad,
        })
    after = _get_snoozed(install_dir, "decision", str(d6))
    _check("S6a · snoozed_until unchanged on bad input",
           before == after, f"before={before} after={after}")
    _check("S6b · every bad call replied with usage hint",
           sum(1 for t in cap.texts() if "usage" in t.lower()) >= 5,
           f"replies={cap.texts()}")
    # Bare /snooze (no args) → usage hint, no crash.
    cap.clear()
    bridge._handle_message({
        "message_id": 61, "chat": {"id": chat}, "text": "/snooze",
    })
    _check("S6c · bare /snooze (no reply-to, no id) → usage hint",
           any("usage" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S7: Cap at 24h -----
    print("\nS7 · /snooze <id> 7d → capped at 24h")
    d7 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d7), tg_msg_id="9007")
    cap.clear()
    t_before = datetime.now(timezone.utc)
    bridge._handle_message({
        "message_id": 7, "chat": {"id": chat},
        "text": f"/snooze {d7} 7d",
    })
    snoozed = _get_snoozed(install_dir, "decision", str(d7))
    if snoozed:
        target = datetime.strptime(snoozed, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta = (target - t_before).total_seconds()
        # 24h = 86400 s; allow a couple of seconds for clock drift.
        _check("S7a · snoozed_until ≈ now + 24h",
               86390 < delta < 86410, f"delta={delta:.0f}s")
    else:
        _check("S7a · snoozed_until set", False)
    _check("S7b · reply mentions cap",
           any("capped" in t.lower() or "max 24h" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S8: Non-existent decision -----
    print("\nS8 · /snooze 9999 → 'not found'")
    cap.clear()
    bridge._handle_message({
        "message_id": 8, "chat": {"id": chat},
        "text": "/snooze 9999",
    })
    _check("S8 · reply mentions not found",
           any("not found" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S9: Already-answered decision -----
    print("\nS9 · /snooze on an answered decision → 'already answered'")
    d9 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d9), tg_msg_id="9009")
    _answer_decision(install_dir, d9)
    cap.clear()
    bridge._handle_message({
        "message_id": 9, "chat": {"id": chat},
        "text": f"/snooze {d9}",
    })
    snoozed = _get_snoozed(install_dir, "decision", str(d9))
    _check("S9a · reply mentions already answered",
           any("already answered" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")
    _check("S9b · snoozed_until untouched",
           snoozed is None, f"value={snoozed}")

    # ----- S10: Not-yet-notified decision -----
    print("\nS10 · /snooze on a pending decision with no notification_log row")
    d10 = _seed_decision(install_dir)
    # Deliberately NOT calling _seed_notification.
    cap.clear()
    bridge._handle_message({
        "message_id": 10, "chat": {"id": chat},
        "text": f"/snooze {d10}",
    })
    _check("S10 · reply mentions not yet notified",
           any("not yet notified" in t.lower() for t in cap.texts()),
           f"replies={cap.texts()}")

    # ----- S11: Audit log -----
    print("\nS11 · activities row written for every successful /snooze")
    d11 = _seed_decision(install_dir)
    _seed_notification(install_dir, "decision", str(d11), tg_msg_id="9011")
    cap.clear()
    bridge._handle_message({
        "message_id": 11, "chat": {"id": chat},
        "text": f"/snooze {d11} 1h",
    })
    with _open_db(install_dir) as conn:
        row = conn.execute(
            "SELECT detail FROM activities WHERE event_type='decision_snoozed' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    _check("S11a · activities row exists", row is not None)
    if row:
        try:
            detail = json.loads(row["detail"])
        except Exception:
            detail = {}
        _check("S11b · detail.source='telegram'",
               detail.get("source") == "telegram", f"detail={detail}")
        _check("S11c · detail.decision_id matches",
               detail.get("decision_id") == d11, f"got={detail.get('decision_id')}")
        _check("S11d · detail.duration normalized",
               detail.get("duration") == "1h", f"got={detail.get('duration')}")
        _check("S11e · detail.snoozed_until is an ISO-ish timestamp",
               isinstance(detail.get("snoozed_until"), str)
               and len(detail["snoozed_until"]) >= 19,
               f"got={detail.get('snoozed_until')}")

    # ----- S12: Backward compat -----
    print("\nS12 · existing verbs still parse + route correctly")
    # Regex check first.
    cases = [
        ("/answer 42 yes", bridge._CMD_WITH_ID_RE, True),
        ("/reply 99 thanks", bridge._CMD_WITH_ID_RE, True),
        ("/approve 7", bridge._CMD_WITH_ID_RE, True),
        ("/cancel 8", bridge._CMD_WITH_ID_RE, True),
        ("/snooze 42 30m", bridge._CMD_WITH_ID_RE, True),
        ("/run draft the PR", bridge._CMD_RUN_RE, True),
        ("/status", bridge._CMD_STATUS_RE, True),
    ]
    for text, regex, want in cases:
        _check(f"S12 · regex match: {text!r}", bool(regex.match(text)) == want)
    # End-to-end /answer still flows.
    d_compat = _seed_decision(install_dir, prompt="compat-check")
    cap.clear()
    bridge._handle_message({
        "message_id": 12, "chat": {"id": chat},
        "text": f"/answer {d_compat} option A",
    })
    with _open_db(install_dir) as conn:
        drow = conn.execute(
            "SELECT status FROM ops_decisions WHERE id=?", (d_compat,)
        ).fetchone()
    _check("S12 · /answer end-to-end flips status to 'answered'",
           bool(drow) and drow["status"] == "answered",
           f"status={drow['status'] if drow else None}")
    # /help still renders.
    cap.clear()
    bridge._handle_message({
        "message_id": 13, "chat": {"id": chat}, "text": "/help",
    })
    _check("S12 · /help still renders the usage card",
           any("Command Centre" in t for t in cap.texts()),
           f"replies={[t[:40] for t in cap.texts()]}")
    _check("S12 · /help mentions the new /snooze verb",
           any("/snooze" in t for t in cap.texts()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v062-smoke-")
    install_dir = Path(tmp)
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"smoke install dir: {install_dir}")

    server_proc = _start_server(install_dir)
    try:
        # Sanity-check the migration landed.
        with sqlite3.connect(install_dir / "data" / "command-centre.db") as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(notification_log)")}
        if "snoozed_until" not in cols:
            print("FATAL: notification_log.snoozed_until missing — schema mismatch")
            return 1
        print("migration ok — notification_log includes snoozed_until")

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
