"""Telegram bridge — outbound notify + inbound reply routing.

Runs as a launchd daemon. Two loops sharing a process:

  Outbound (every NOTIFY_INTERVAL_S):
    GET /api/decisions?status=pending     → notify each not yet logged
    GET /api/inbox?unread=1               → notify each agent_to_user
    Save (event_type, event_key, telegram_message_id) into notification_log.

  Inbound (Telegram long-poll, 30s timeout):
    For each incoming message:
      - If it's a `reply_to_message`, look up the original via
        notification_log → POST to /api/decisions/{id}/answer or
        /api/inbox/{id}/reply.
      - If text starts with "/answer <id> <body>" or "/reply <id> <body>",
        route directly (escape hatch for clients that don't support reply-to).
      - Otherwise, send a short help message.

Stdlib only — urllib + sqlite3. No `python-telegram-bot` dep.
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

# Resolve install dir + DB path the same way other scripts do.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def _load_env_file() -> None:
    """launchd doesn't source .env. Read $CC_INSTALL_DIR/.env (if any) and
    push into os.environ — but never overwrite an already-set variable, so
    operators can override per-launch via the plist or a wrapping shell."""
    install_dir = Path(os.environ.get("CC_INSTALL_DIR") or _HERE.parent)
    env_path = install_dir / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception as exc:
        print(f"[telegram] could not read {env_path}: {exc!r}", file=sys.stderr)


_load_env_file()

import db  # noqa: E402

DASHBOARD_URL = (
    os.environ.get("CC_DASHBOARD_URL")
    or f"http://{os.environ.get('CC_HOST', '127.0.0.1')}:{os.environ.get('CC_PORT', '8765')}"
)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_DASH_CHAT_ID", "").strip()
NOTIFY_INTERVAL_S = int(os.environ.get("TELEGRAM_NOTIFY_INTERVAL_S", "30"))
LONG_POLL_S = int(os.environ.get("TELEGRAM_LONG_POLL_S", "30"))
TELEGRAM_API_BASE = (
    os.environ.get("TELEGRAM_API_BASE")
    or "https://api.telegram.org"
).rstrip("/")

_STOP = threading.Event()


# ---------------------------------------------------------------------------
# Tiny HTTP helpers — stdlib only.
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_post_json(url: str, payload: dict, timeout: float = 10.0) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _tg(method: str, payload: dict, timeout: float = 10.0) -> dict:
    """Call a Telegram Bot API method. Returns the parsed `result` field."""
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN unset — cannot call Telegram API")
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/{method}"
    res = _http_post_json(url, payload, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(f"telegram {method} failed: {res}")
    return res.get("result") or {}


# ---------------------------------------------------------------------------
# notification_log helpers — dedupe + reply lookup.
# ---------------------------------------------------------------------------

def _already_notified(event_type: str, event_key: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notification_log "
            "WHERE event_type=? AND event_key=? AND chat_id=? LIMIT 1",
            (event_type, event_key, CHAT_ID),
        ).fetchone()
        return row is not None


def _record_notify(event_type: str, event_key: str, telegram_message_id: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notification_log "
            "(event_type, event_key, chat_id, telegram_message_id) "
            "VALUES (?, ?, ?, ?)",
            (event_type, event_key, CHAT_ID, str(telegram_message_id)),
        )


def _lookup_by_tg_message(tg_message_id: int) -> Optional[tuple[str, str]]:
    """Given a Telegram message id (the one being replied to), return
    (event_type, event_key) — the decision id or inbox id we originally
    notified for. None if unknown."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT event_type, event_key FROM notification_log "
            "WHERE telegram_message_id=? AND chat_id=? "
            "ORDER BY id DESC LIMIT 1",
            (str(tg_message_id), CHAT_ID),
        ).fetchone()
        if not row:
            return None
        return (row["event_type"], row["event_key"])


# ---------------------------------------------------------------------------
# Outbound — push pending decisions + unread inbox to Telegram.
# ---------------------------------------------------------------------------

def _send_message(text: str) -> int:
    """Send a markdown-formatted message; return the new telegram message_id."""
    res = _tg("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text[:4000],   # Telegram cap is 4096
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    })
    return int(res.get("message_id") or 0)


def _format_decision(d: dict) -> str:
    sid = (d.get("session_id") or "")[:8]
    task = d.get("task_id")
    return (
        f"❓ *DECISION* `#{d['id']}`"
        + (f" · task `{task}`" if task else "")
        + (f" · session `{sid}`" if sid else "")
        + f"\n\n{d.get('prompt','')[:3000]}\n\n"
        + f"_Reply to this message with your answer, or send_ `/answer {d['id']} <text>`"
    )


def _format_inbox(m: dict) -> str:
    sid = (m.get("session_id") or "")[:8]
    task = m.get("task_id")
    return (
        f"📨 *INBOX* `#{m['id']}`"
        + (f" · task `{task}`" if task else "")
        + (f" · session `{sid}`" if sid else "")
        + f"\n\n{m.get('body','')[:3000]}\n\n"
        + f"_Reply to this message to send a response, or_ `/reply {m['id']} <text>`"
    )


def _outbound_tick() -> dict:
    sent = {"decisions": 0, "inbox": 0, "errors": 0}
    # Decisions.
    try:
        ds = _http_get_json(f"{DASHBOARD_URL}/api/decisions?status=pending")
        for d in ds.get("items", []):
            key = str(d["id"])
            if _already_notified("decision", key):
                continue
            try:
                mid = _send_message(_format_decision(d))
                if mid:
                    _record_notify("decision", key, str(mid))
                    sent["decisions"] += 1
            except Exception as exc:
                sent["errors"] += 1
                print(f"[telegram] decision notify failed: {exc!r}", file=sys.stderr)
    except Exception as exc:
        sent["errors"] += 1
        print(f"[telegram] /api/decisions fetch failed: {exc!r}", file=sys.stderr)

    # Inbox (agent → user, unread).
    try:
        inbox = _http_get_json(f"{DASHBOARD_URL}/api/inbox?unread=1&max_age_days=7")
        for m in inbox.get("items", []):
            if m.get("direction") != "agent_to_user":
                continue
            key = str(m["id"])
            if _already_notified("inbox", key):
                continue
            try:
                mid = _send_message(_format_inbox(m))
                if mid:
                    _record_notify("inbox", key, str(mid))
                    sent["inbox"] += 1
            except Exception as exc:
                sent["errors"] += 1
                print(f"[telegram] inbox notify failed: {exc!r}", file=sys.stderr)
    except Exception as exc:
        sent["errors"] += 1
        print(f"[telegram] /api/inbox fetch failed: {exc!r}", file=sys.stderr)
    return sent


def _outbound_loop() -> None:
    while not _STOP.is_set():
        try:
            stats = _outbound_tick()
            if stats["decisions"] or stats["inbox"]:
                print(f"[telegram] notified d={stats['decisions']} i={stats['inbox']} err={stats['errors']}", flush=True)
        except Exception as exc:
            print(f"[telegram] outbound tick crashed: {exc!r}", file=sys.stderr)
        # Interruptible sleep.
        for _ in range(NOTIFY_INTERVAL_S):
            if _STOP.is_set():
                return
            time.sleep(1.0)


# ---------------------------------------------------------------------------
# Inbound — long-poll Telegram, route replies back to the API.
# ---------------------------------------------------------------------------

# /answer 5 yes proceed
# /reply 12 acknowledged
_CMD_RE = re.compile(r"^/(answer|reply)\s+(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)


def _route_reply(event_type: str, event_id: str, body: str) -> bool:
    """POST the body to the right /api endpoint."""
    body = body.strip()
    if not body:
        return False
    if event_type == "decision":
        url = f"{DASHBOARD_URL}/api/decisions/{event_id}/answer"
        _http_post_json(url, {"answer": body})
        return True
    if event_type == "inbox":
        url = f"{DASHBOARD_URL}/api/inbox/{event_id}/reply"
        _http_post_json(url, {"body": body})
        return True
    return False


def _handle_message(msg: dict) -> None:
    """Process a single Telegram message — reply-to or slash-command."""
    chat = (msg.get("chat") or {}).get("id")
    if CHAT_ID and str(chat) != CHAT_ID:
        # Ignore messages from other chats — bot might be in a group.
        return
    text = (msg.get("text") or "").strip()
    if not text:
        return

    # 1. Reply-to-message: look up original.
    reply_to = msg.get("reply_to_message")
    if reply_to:
        orig_id = reply_to.get("message_id")
        if orig_id:
            found = _lookup_by_tg_message(int(orig_id))
            if found:
                event_type, event_key = found
                try:
                    if _route_reply(event_type, event_key, text):
                        _tg("sendMessage", {
                            "chat_id": chat,
                            "text": f"✅ recorded · {event_type} #{event_key}",
                            "reply_to_message_id": msg.get("message_id"),
                        })
                        return
                except Exception as exc:
                    _tg("sendMessage", {
                        "chat_id": chat,
                        "text": f"⚠️ failed to route reply: {exc}",
                        "reply_to_message_id": msg.get("message_id"),
                    })
                    return
            else:
                _tg("sendMessage", {
                    "chat_id": chat,
                    "text": "⚠️ couldn't find the original notification — try `/answer <id>` or `/reply <id>`",
                    "reply_to_message_id": msg.get("message_id"),
                    "parse_mode": "Markdown",
                })
                return

    # 2. Slash command escape hatch.
    m = _CMD_RE.match(text)
    if m:
        kind, ref_id, body = m.group(1).lower(), m.group(2), m.group(3)
        event_type = "decision" if kind == "answer" else "inbox"
        try:
            if _route_reply(event_type, ref_id, body):
                _tg("sendMessage", {
                    "chat_id": chat,
                    "text": f"✅ recorded · {event_type} #{ref_id}",
                    "reply_to_message_id": msg.get("message_id"),
                })
                return
        except Exception as exc:
            _tg("sendMessage", {
                "chat_id": chat,
                "text": f"⚠️ failed: {exc}",
                "reply_to_message_id": msg.get("message_id"),
            })
            return

    # 3. Help fallthrough.
    if text.lower() in ("/help", "/start", "help"):
        _tg("sendMessage", {
            "chat_id": chat,
            "text": (
                "Command Centre · Telegram bridge\n\n"
                "I forward `DECISION:` and `INBOX:` from Claude Code sessions.\n\n"
                "*Reply to a notification* with your answer, or use:\n"
                "`/answer <id> <text>` — answer a pending decision\n"
                "`/reply <id> <text>` — reply to an inbox message"
            ),
            "parse_mode": "Markdown",
        })


def _inbound_loop() -> None:
    """Long-poll getUpdates. Persist the offset across crashes via env-var-ish file."""
    state_path = Path(os.environ.get("CC_INSTALL_DIR") or _HERE.parent) / ".tmp" / "telegram-offset"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        offset = int(state_path.read_text().strip()) if state_path.exists() else 0
    except Exception:
        offset = 0

    while not _STOP.is_set():
        try:
            url = (f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/getUpdates"
                   f"?timeout={LONG_POLL_S}&offset={offset}")
            res = _http_get_json(url, timeout=LONG_POLL_S + 5)
            if not res.get("ok"):
                print(f"[telegram] getUpdates returned: {res}", file=sys.stderr)
                time.sleep(5)
                continue
            for update in res.get("result", []):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                msg = update.get("message")
                if msg:
                    try:
                        _handle_message(msg)
                    except Exception as exc:
                        print(f"[telegram] handle_message failed: {exc!r}", file=sys.stderr)
            try:
                state_path.write_text(str(offset))
            except Exception:
                pass
        except Exception as exc:
            print(f"[telegram] inbound loop error: {exc!r}", file=sys.stderr)
            time.sleep(3)


# ---------------------------------------------------------------------------
# Entrypoint.
# ---------------------------------------------------------------------------

def _on_signal(signum, _frame):
    _STOP.set()


def main() -> int:
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_DASH_CHAT_ID unset — bridge disabled",
              file=sys.stderr)
        # launchd will respect KeepAlive if we exit non-zero too quickly; sleep
        # so the daemon doesn't churn-restart on a missing config.
        time.sleep(60)
        return 0

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    print(f"[telegram] bridge starting · dash={DASHBOARD_URL} chat={CHAT_ID}", flush=True)
    # Verify token early.
    try:
        me = _tg("getMe", {})
        print(f"[telegram] bot @{me.get('username')} (id={me.get('id')})", flush=True)
    except Exception as exc:
        print(f"[telegram] getMe failed: {exc!r} — exiting", file=sys.stderr)
        return 1

    out = threading.Thread(target=_outbound_loop, daemon=True)
    inn = threading.Thread(target=_inbound_loop, daemon=True)
    out.start()
    inn.start()

    while not _STOP.is_set():
        time.sleep(1.0)
    print("[telegram] stopping", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
