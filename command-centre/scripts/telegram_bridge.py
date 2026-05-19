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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo  # py3.9+
    _LOCAL_TZ: Optional["ZoneInfo"] = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    # Fallback: naive system local time. /status timestamp loses the
    # explicit `GMT+7` suffix but otherwise renders fine.
    _LOCAL_TZ = None

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


def _fmt_duration_ms(ms: Optional[int]) -> str:
    if not ms:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{ms/1000:.1f}s"
    mm, ss = divmod(int(ms / 1000), 60)
    return f"{mm}m {ss}s"


def _md_safe(s: str) -> str:
    """Strip Markdown V1 control chars from user-content fields so the
    Telegram parser can't trip on unbalanced _ * ` [."""
    if not s:
        return ""
    return s.replace("_", " ").replace("*", " ").replace("`", "'").replace("[", "(").replace("]", ")")


def _format_task_complete(t: dict) -> str:
    """Format a finished task (status=done|failed) for Telegram."""
    icon = "✅" if t.get("status") == "done" else "❌"
    label = "DONE" if t.get("status") == "done" else "FAILED"
    title = _md_safe((t.get("title") or "(untitled)")[:200])
    dur = _fmt_duration_ms(t.get("duration_ms"))
    cost = f"${(t.get('cost_usd') or 0):.2f}"
    sid = (t.get("session_id") or "")[:8]

    header = f"{icon} *TASK {label}* `#{t['id']}`"
    if sid:
        header += f" · session `{sid}`"

    body = f"\n\n{title}\n\nduration {dur} · cost {cost}"

    extra = ""
    if t.get("status") == "failed" and t.get("error_message"):
        err = _md_safe(t["error_message"][:500])
        extra = f"\n\nerror: {err}"
    elif t.get("output_summary"):
        summary = _md_safe(t["output_summary"][:500])
        extra = f"\n\n{summary}"

    return header + body + extra


def _format_risk_gated(t: dict) -> str:
    """Format a risk-gated task (status=awaiting_approval) for Telegram."""
    title = _md_safe((t.get("title") or "(untitled)")[:200])
    risk = t.get("risk_level") or "high"
    return (
        f"🛑 *RISK-GATED* `#{t['id']}` · risk_level=`{risk}`"
        + f"\n\n{title}\n\n"
        + f"_Hard risk gate blocked dispatch._\n"
        + f"Reply `/approve {t['id']}` to override, or `/cancel {t['id']}` to drop."
    )


def _outbound_tick() -> dict:
    sent = {"decisions": 0, "inbox": 0, "task_complete": 0, "risk_gated": 0, "errors": 0}
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

    # Task completion (done + failed).
    # /api/tasks supports status param; query both done and failed in one call
    # by hitting twice — the listing endpoint doesn't support status_in.
    for status in ("done", "failed"):
        try:
            tasks = _http_get_json(f"{DASHBOARD_URL}/api/tasks?status={status}&limit=50")
            for t in tasks.get("items", []):
                key = str(t["id"])
                if _already_notified("task_complete", key):
                    continue
                try:
                    mid = _send_message(_format_task_complete(t))
                    if mid:
                        _record_notify("task_complete", key, str(mid))
                        sent["task_complete"] += 1
                except Exception as exc:
                    sent["errors"] += 1
                    print(f"[telegram] task_complete notify failed: {exc!r}", file=sys.stderr)
        except Exception as exc:
            sent["errors"] += 1
            print(f"[telegram] /api/tasks?status={status} fetch failed: {exc!r}", file=sys.stderr)

    # Risk-gated tasks (awaiting_approval).
    try:
        rg = _http_get_json(f"{DASHBOARD_URL}/api/tasks?status=awaiting_approval&limit=20")
        for t in rg.get("items", []):
            key = str(t["id"])
            if _already_notified("risk_gated", key):
                continue
            try:
                mid = _send_message(_format_risk_gated(t))
                if mid:
                    _record_notify("risk_gated", key, str(mid))
                    sent["risk_gated"] += 1
            except Exception as exc:
                sent["errors"] += 1
                print(f"[telegram] risk_gated notify failed: {exc!r}", file=sys.stderr)
    except Exception as exc:
        sent["errors"] += 1
        print(f"[telegram] /api/tasks?status=awaiting_approval fetch failed: {exc!r}", file=sys.stderr)

    return sent


def _outbound_loop() -> None:
    while not _STOP.is_set():
        try:
            stats = _outbound_tick()
            if any(stats[k] for k in ("decisions", "inbox", "task_complete", "risk_gated")):
                print(
                    f"[telegram] notified d={stats['decisions']} i={stats['inbox']} "
                    f"tc={stats['task_complete']} rg={stats['risk_gated']} "
                    f"err={stats['errors']}",
                    flush=True,
                )
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

# Slash command grammar (v0.5.0-mvp2 + v0.6.1):
#
#   /answer <decision_id> <body>          → answer a pending decision     (v0.3.0)
#   /reply  <inbox_id>    <body>          → reply to an inbox message     (v0.3.0)
#   /approve <task_id>                    → override risk gate            (v0.5.0-mvp2, FR16)
#   /cancel  <task_id>                    → cancel pending / risk-gated   (v0.5.0-mvp2, FR18)
#   /run <prompt>                         → POST /api/tasks                (v0.5.0-mvp2, FR1-4)
#   /status                               → read-only telemetry snapshot   (v0.6.1, Phase 2)
#
# Two patterns because `/run` doesn't take a leading int ID — keeping the
# verbs in one regex would force a body for every verb. The body group is
# optional in _CMD_WITH_ID_RE so /approve and /cancel parse without one.
# `/status` is a third sibling — strict whole-message match, no args.
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_CMD_RUN_RE = re.compile(r"^/run\s+(.+)$", re.IGNORECASE | re.DOTALL)
_CMD_STATUS_RE = re.compile(r"^/status\s*$", re.IGNORECASE)

# `/run` prompt cap — amendment says 3000 chars (PRD says 4000 for outbound
# body truncation, but inbound /run uses the tighter limit to match the
# 3000-char body cap used by _format_decision / _format_inbox).
_RUN_PROMPT_MAX = 3000
_RUN_TITLE_MAX = 80


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


# ---------------------------------------------------------------------------
# /run, /approve, /cancel — v0.5.0-mvp2.
# ---------------------------------------------------------------------------

def _reply_text(chat_id: Any, message_id: Any, text: str,
                parse_mode: Optional[str] = "Markdown") -> None:
    """Send a reply quoting the inbound message. Falls back silently on send
    failure — the bridge must never crash the inbound long-poll (NFR11)."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4000],
        "reply_to_message_id": message_id,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        _tg("sendMessage", payload)
    except Exception as exc:
        # Retry once without parse_mode in case the Markdown parser tripped
        # on operator-typed content. Then give up.
        if parse_mode:
            try:
                payload.pop("parse_mode", None)
                _tg("sendMessage", payload)
                return
            except Exception:
                pass
        print(f"[telegram] reply send failed: {exc!r}", file=sys.stderr)


def _http_status_from_exc(exc: Exception) -> tuple[Optional[int], str]:
    """Extract (status_code, body) from an HTTPError raised by urllib. Returns
    (None, str(exc)) if it isn't an HTTPError. Body is truncated to 500 chars."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        # FastAPI errors come back as {"detail": "..."} — pull the message
        # out so the operator sees a usable line, not raw JSON.
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and "detail" in parsed:
                body = str(parsed["detail"])
        except Exception:
            pass
        return exc.code, body[:500]
    return None, str(exc)[:500]


def _handle_run(chat_id: Any, message_id: Any, prompt: str) -> None:
    """`/run <prompt>` — POST a new task to /api/tasks with source='telegram'.
    Validates length; never crashes the loop on malformed input (NFR11)."""
    prompt = prompt.strip()
    if not prompt:
        _reply_text(chat_id, message_id,
                    "usage: `/run <prompt>` — first 80 chars become the task title.")
        return
    if len(prompt) > _RUN_PROMPT_MAX:
        _reply_text(chat_id, message_id,
                    f"prompt too long ({len(prompt)} chars) — shorter please, "
                    f"max {_RUN_PROMPT_MAX} chars.")
        return

    title = prompt[:_RUN_TITLE_MAX].replace("\n", " ").strip() or "telegram task"
    try:
        res = _http_post_json(
            f"{DASHBOARD_URL}/api/tasks",
            {"title": title, "description": prompt, "created_at_source": "telegram"},
        )
        task_id = res.get("id")
        if not task_id:
            _reply_text(chat_id, message_id, f"⚠️ task create returned no id · {res}")
            return
        _reply_text(chat_id, message_id,
                    f"✅ task `#{task_id}` queued · dispatcher pending")
    except Exception as exc:
        code, body = _http_status_from_exc(exc)
        safe_body = _md_safe(body) if body else ""
        if code:
            _reply_text(chat_id, message_id, f"⚠️ create failed · {code} · {safe_body}")
        else:
            _reply_text(chat_id, message_id, f"⚠️ create failed · {safe_body}")


def _handle_approve(chat_id: Any, message_id: Any, task_id: int) -> None:
    """`/approve <task_id>` — POST /api/tasks/{id}/approve?source=telegram."""
    try:
        _http_post_json(
            f"{DASHBOARD_URL}/api/tasks/{task_id}/approve?source=telegram",
            {},
        )
        _reply_text(chat_id, message_id,
                    f"✅ task `#{task_id}` approved · dispatcher resuming")
    except Exception as exc:
        code, body = _http_status_from_exc(exc)
        safe_body = _md_safe(body) if body else ""
        if code == 404:
            _reply_text(chat_id, message_id, f"task `#{task_id}` not found")
        elif code in (400, 409):
            _reply_text(chat_id, message_id,
                        f"task `#{task_id}` cannot be approved · {safe_body}")
        elif code is not None:
            _reply_text(chat_id, message_id,
                        f"⚠️ approve failed · {code} · {safe_body}")
        else:
            _reply_text(chat_id, message_id, f"⚠️ approve failed · {safe_body}")


def _handle_cancel(chat_id: Any, message_id: Any, task_id: int) -> None:
    """`/cancel <task_id>` — POST /api/tasks/{id}/cancel?source=telegram."""
    try:
        _http_post_json(
            f"{DASHBOARD_URL}/api/tasks/{task_id}/cancel?source=telegram",
            {},
        )
        _reply_text(chat_id, message_id, f"✅ task `#{task_id}` cancelled")
    except Exception as exc:
        code, body = _http_status_from_exc(exc)
        safe_body = _md_safe(body) if body else ""
        if code == 404:
            _reply_text(chat_id, message_id, f"task `#{task_id}` not found")
        elif code in (400, 409):
            _reply_text(chat_id, message_id,
                        f"task `#{task_id}` cannot be cancelled · {safe_body}")
        elif code is not None:
            _reply_text(chat_id, message_id,
                        f"⚠️ cancel failed · {code} · {safe_body}")
        else:
            _reply_text(chat_id, message_id, f"⚠️ cancel failed · {safe_body}")


# ---------------------------------------------------------------------------
# /status — v0.6.1, Phase 2.
# ---------------------------------------------------------------------------
#
# Read-only telemetry snapshot. Fetches 5 GET endpoints in sequence with
# per-call try/except; failed endpoints render `?` for their metric and a
# footer line flags partial data. Total server failure → fallback message,
# never crashes the inbound long-poll loop. No `activities` audit-log row —
# `/status` is passive awareness, not a state-changing operator action
# (deliberate departure from mvp2's audit-everything-from-Telegram pattern).


def _now_local() -> datetime:
    """`datetime.now()` pinned to `Asia/Ho_Chi_Minh` when zoneinfo is
    available. Used for the /status header timestamp + today-bucketing."""
    return datetime.now(_LOCAL_TZ) if _LOCAL_TZ else datetime.now()


def _now_local_str() -> str:
    """`HH:MM GMT+7` — matches the v0.5.0-mvp1 timezone-fix convention."""
    if _LOCAL_TZ:
        return _now_local().strftime("%H:%M GMT+7")
    return _now_local().strftime("%H:%M")


def _today_local_date() -> str:
    """`YYYY-MM-DD` in the bridge's local tz. Used to bucket task counts."""
    return _now_local().strftime("%Y-%m-%d")


def _parse_db_utc(s: Optional[str]) -> Optional[datetime]:
    """Parse a SQLite-style `YYYY-MM-DD HH:MM:SS` string as UTC. Returns
    None on failure or empty input. Also accepts ISO-8601 with explicit
    offset for forward-compat."""
    if not s:
        return None
    try:
        # SQLite default — no T, no TZ — treat as UTC (datetime('now')).
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _utc_to_local_date(s: Optional[str]) -> Optional[str]:
    """Project a UTC timestamp string to its local-tz `YYYY-MM-DD`."""
    dt = _parse_db_utc(s)
    if not dt:
        return None
    if _LOCAL_TZ:
        return dt.astimezone(_LOCAL_TZ).strftime("%Y-%m-%d")
    return dt.astimezone().strftime("%Y-%m-%d")


def _age_short(seconds: Optional[int]) -> str:
    """Compact age string for the live-sessions line: `12s`, `4m`, `1h 20m`,
    `2d`. `?` when the input is None or negative (clock skew). Tuned for
    mobile readability — never more than 6 chars."""
    if seconds is None or seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{seconds // 86400}d"


def _format_status(metrics: dict[str, Any]) -> str:
    """Render the `/status` Telegram reply body. `metrics` keys (any value
    may be None to mean failed-to-fetch — substituted as `?`):
      - `dispatcher`: dict | None         (whole /api/system/dispatcher)
      - `pending_decisions`: int | None   (count of pending decisions)
      - `live_sessions`: list | None      (items from /api/sessions/live)
      - `today_counts`: dict              ({'done': int|None, 'failed': ...,
                                           'awaiting_approval': int|None})
      - `emergency_stop`: bool | None     (system_state.emergency_stop)

    Mobile-readable (390 px iPhone — NFR4). Markdown V1 (existing bridge
    convention). All template-controlled (no user-content fields) so
    `_md_safe` is not needed."""
    d = metrics.get("dispatcher") or {}
    lines: list[str] = [f"*STATUS*  _{_now_local_str()}_", ""]

    # 1. Dispatcher.
    if metrics.get("dispatcher") is None:
        lines.append("⚙️  Dispatcher  *?*")
    else:
        running = d.get("running")
        cap = d.get("max_concurrent")
        free = d.get("free_slots")
        running_s = "?" if running is None else str(running)
        cap_s = "?" if cap is None else str(cap)
        free_s = "?" if free is None else str(free)
        lines.append(f"⚙️  Dispatcher  *{running_s}/{cap_s}* running · *{free_s}* free")

    # 2. Cost (today).
    if metrics.get("dispatcher") is None:
        lines.append("💰 Today        *?*")
    else:
        api = float(d.get("today_cost_api_pool_usd") or 0.0)
        mx = float(d.get("today_cost_max_sub_usd") or 0.0)
        cap_usd = d.get("daily_cost_cap_usd")
        api_s = f"*${api:.2f}*" if api > 0 else "—"
        mx_s = f"*${mx:.2f}*" if mx > 0 else "—"
        cap_s = f"cap ${float(cap_usd):.2f}" if cap_usd else "no cap"
        lines.append(f"💰 Today        api {api_s} · max {mx_s} · {cap_s}")

    # 3. Pending decisions — omit on zero (no idle-day noise).
    pd = metrics.get("pending_decisions")
    if pd is None:
        lines.append("📨 Decisions    *?* pending")
    elif pd > 0:
        lines.append(f"📨 Decisions    *{pd}* pending")

    # 4. Live sessions — omit on zero. Age = longest-running (oldest started_at).
    live = metrics.get("live_sessions")
    if live is None:
        lines.append("🟢 Live         *?* active")
    elif live:
        now_utc = datetime.now(timezone.utc)
        oldest_s: Optional[int] = None
        for s in live:
            dt = _parse_db_utc(s.get("started_at"))
            if not dt:
                continue
            age = int((now_utc - dt).total_seconds())
            if oldest_s is None or age > oldest_s:
                oldest_s = age
        age_str = _age_short(oldest_s) if oldest_s is not None else "?"
        n = len(live)
        word = "session" if n == 1 else "sessions"
        lines.append(f"🟢 Live         *{n}* {word} · {age_str} active")

    # 5. Tasks today — always show, even all-zeros (operators want to see
    # "nothing happened today" explicitly).
    tc = metrics.get("today_counts") or {}
    def _f(v: Optional[int]) -> str:
        return "?" if v is None else str(v)
    lines.append(
        f"✅ Tasks today  {_f(tc.get('done'))} done · "
        f"{_f(tc.get('failed'))} failed · "
        f"{_f(tc.get('awaiting_approval'))} risk-gated"
    )

    # Conditional alerts — only when active. Order: 🛑 first (loudest),
    # then cost cap, then back-pressure.
    alerts: list[str] = []
    if metrics.get("emergency_stop") is True:
        alerts.append("🛑 *Emergency stop ON* — dispatcher refusing new work")
    if metrics.get("dispatcher") is not None and d.get("cost_capped"):
        api = float(d.get("today_cost_api_pool_usd") or 0.0)
        cap_usd = float(d.get("daily_cost_cap_usd") or 0.0)
        alerts.append(f"⚠️  *Cost cap reached* (api_pool ${api:.2f} / ${cap_usd:.2f})")
    if metrics.get("dispatcher") is not None and d.get("back_pressure"):
        running = d.get("running") or 0
        cap = d.get("max_concurrent") or 0
        alerts.append(f"⚠️  *Back-pressure active* — {running}/{cap} slots full")
    if alerts:
        lines.append("")
        lines.extend(alerts)

    # Partial-failure footer — any `?` substitution → append the line.
    today_partial = any(v is None for v in (metrics.get("today_counts") or {}).values())
    any_partial = (
        metrics.get("dispatcher") is None
        or metrics.get("pending_decisions") is None
        or metrics.get("live_sessions") is None
        or metrics.get("emergency_stop") is None
        or today_partial
    )
    if any_partial:
        lines.append("")
        lines.append("_some metrics unavailable — see logs_")

    return "\n".join(lines)


def _handle_status(chat_id: Any) -> None:
    """`/status` — read-only telemetry snapshot. Hits 5 GET endpoints in
    sequence with per-call try/except. If ALL fail, sends a clear
    "dashboard down?" fallback message instead of a half-rendered template.
    Never crashes the inbound loop (NFR11). No audit-log row by design."""
    metrics: dict[str, Any] = {}

    def _log(name: str, exc: Exception) -> None:
        print(f"[telegram] /status {name} fetch failed: {exc!r}", file=sys.stderr)

    # 1. Dispatcher state — drives the run/cap/free + today-cost + cap-status lines.
    try:
        metrics["dispatcher"] = _http_get_json(
            f"{DASHBOARD_URL}/api/system/dispatcher", timeout=3.0)
    except Exception as exc:
        _log("dispatcher", exc)
        metrics["dispatcher"] = None

    # 2. Pending decisions count.
    try:
        d = _http_get_json(
            f"{DASHBOARD_URL}/api/decisions?status=pending", timeout=3.0)
        metrics["pending_decisions"] = len(d.get("items", []))
    except Exception as exc:
        _log("decisions", exc)
        metrics["pending_decisions"] = None

    # 3. Live sessions (5-min window from /api/sessions/live).
    try:
        d = _http_get_json(f"{DASHBOARD_URL}/api/sessions/live", timeout=3.0)
        metrics["live_sessions"] = d.get("items", [])
    except Exception as exc:
        _log("sessions_live", exc)
        metrics["live_sessions"] = None

    # 4. Today's tasks by status. Three calls — done + failed bucketed by
    # `completed_at`, awaiting_approval by `created_at` (the natural
    # entry-time field for tasks that haven't completed). Per-status
    # try/except so a single failure doesn't black-out the whole line.
    today = _today_local_date()
    counts: dict[str, Optional[int]] = {
        "done": None, "failed": None, "awaiting_approval": None,
    }
    tasks_any_ok = False
    for status in ("done", "failed", "awaiting_approval"):
        try:
            d = _http_get_json(
                f"{DASHBOARD_URL}/api/tasks?status={status}&limit=200",
                timeout=3.0,
            )
            field = "completed_at" if status != "awaiting_approval" else "created_at"
            n = 0
            for t in d.get("items", []):
                if _utc_to_local_date(t.get(field)) == today:
                    n += 1
            counts[status] = n
            tasks_any_ok = True
        except Exception as exc:
            _log(f"tasks?status={status}", exc)
    metrics["today_counts"] = counts

    # 5. Emergency stop flag. `/api/system/state` returns
    #   {}                                          ← never set
    #   {"emergency_stop": {"value": "0|1", ...}}    ← set; "1" = ON
    try:
        d = _http_get_json(f"{DASHBOARD_URL}/api/system/state", timeout=3.0)
        es = d.get("emergency_stop")
        metrics["emergency_stop"] = bool(
            isinstance(es, dict) and es.get("value") == "1"
        )
    except Exception as exc:
        _log("system_state", exc)
        metrics["emergency_stop"] = None

    # Total failure → fallback message. "Total" = every endpoint failed
    # (no dispatcher, no decisions, no live sessions, no task counts,
    # no system_state). Mid-failures fall through to the partial template.
    if (
        metrics.get("dispatcher") is None
        and metrics.get("pending_decisions") is None
        and metrics.get("live_sessions") is None
        and not tasks_any_ok
        and metrics.get("emergency_stop") is None
    ):
        try:
            _send_message(
                "⚠️ status check failed — dashboard server may be down.\n"
                "Try `cc status` or `cc restart`."
            )
        except Exception as exc:
            print(f"[telegram] /status fallback send failed: {exc!r}", file=sys.stderr)
        return

    try:
        _send_message(_format_status(metrics))
    except Exception as exc:
        print(f"[telegram] /status send failed: {exc!r}", file=sys.stderr)


def _handle_message(msg: dict) -> None:
    """Process a single Telegram message — reply-to or slash-command.

    Routing order (v0.5.0-mvp2 + v0.6.1):
      1. reply-to-message → lookup notification_log
         - decision  → POST /api/decisions/{id}/answer  (v0.3.0)
         - inbox     → POST /api/inbox/{id}/reply       (v0.3.0)
         - risk_gated → /approve <task_id>              (v0.5.0-mvp2, FR17)
      2. slash command
         - /status           → _handle_status           (v0.6.1, Phase 2)
         - /answer | /reply  → existing route_reply     (v0.3.0)
         - /run <prompt>     → _handle_run              (v0.5.0-mvp2, FR1-4)
         - /approve <id>     → _handle_approve          (v0.5.0-mvp2, FR16)
         - /cancel <id>      → _handle_cancel           (v0.5.0-mvp2, FR18)
      3. /help | /start → usage text
    """
    chat = (msg.get("chat") or {}).get("id")
    if CHAT_ID and str(chat) != CHAT_ID:
        # NFR6 — silently ignore cross-chat traffic.
        return
    text = (msg.get("text") or "").strip()
    if not text:
        return
    message_id = msg.get("message_id")

    # 1. Reply-to-message: look up original.
    reply_to = msg.get("reply_to_message")
    if reply_to:
        orig_id = reply_to.get("message_id")
        if orig_id:
            found = _lookup_by_tg_message(int(orig_id))
            if found:
                event_type, event_key = found
                # v0.5.0-mvp2 — reply-to-msg on a 🛑 RISK-GATED notification
                # routes to /approve. Reply-to-cancel is NOT supported per
                # amendment (avoid accidental cancels from casual replies).
                if event_type == "risk_gated":
                    try:
                        _handle_approve(chat, message_id, int(event_key))
                    except (ValueError, TypeError):
                        _reply_text(chat, message_id,
                                    f"⚠️ bad task id in notification_log: {event_key!r}")
                    return
                try:
                    if _route_reply(event_type, event_key, text):
                        _tg("sendMessage", {
                            "chat_id": chat,
                            "text": f"✅ recorded · {event_type} #{event_key}",
                            "reply_to_message_id": message_id,
                        })
                        return
                except Exception as exc:
                    _tg("sendMessage", {
                        "chat_id": chat,
                        "text": f"⚠️ failed to route reply: {exc}",
                        "reply_to_message_id": message_id,
                    })
                    return
            else:
                _tg("sendMessage", {
                    "chat_id": chat,
                    "text": "⚠️ couldn't find the original notification — try `/answer <id>` or `/reply <id>`",
                    "reply_to_message_id": message_id,
                    "parse_mode": "Markdown",
                })
                return

    # 2a. /status — read-only telemetry snapshot. v0.6.1, Phase 2. Checked
    # before /run so `/status` with stray args fails fast (regex is
    # whole-message; `/status foo` won't match and falls through to /help).
    if _CMD_STATUS_RE.match(text):
        _handle_status(chat)
        return

    # 2b. /run <prompt> — free-text remainder, multi-line, no leading ID.
    m_run = _CMD_RUN_RE.match(text)
    if m_run:
        _handle_run(chat, message_id, m_run.group(1))
        return

    # 2c. /answer | /reply | /approve | /cancel — all share a leading int ID.
    m_cmd = _CMD_WITH_ID_RE.match(text)
    if m_cmd:
        verb = m_cmd.group(1).lower()
        try:
            ref_id = int(m_cmd.group(2))
        except (TypeError, ValueError):
            # Should be unreachable — regex enforces \d+ — but guard NFR11.
            _reply_text(chat, message_id, "usage: numeric task / event id required")
            return
        body = m_cmd.group(3)
        if verb in ("answer", "reply"):
            event_type = "decision" if verb == "answer" else "inbox"
            if not body or not body.strip():
                _reply_text(chat, message_id,
                            f"usage: `/{verb} <id> <text>` — body required")
                return
            try:
                if _route_reply(event_type, str(ref_id), body):
                    _tg("sendMessage", {
                        "chat_id": chat,
                        "text": f"✅ recorded · {event_type} #{ref_id}",
                        "reply_to_message_id": message_id,
                    })
                    return
            except Exception as exc:
                _tg("sendMessage", {
                    "chat_id": chat,
                    "text": f"⚠️ failed: {exc}",
                    "reply_to_message_id": message_id,
                })
                return
        elif verb == "approve":
            _handle_approve(chat, message_id, ref_id)
            return
        elif verb == "cancel":
            _handle_cancel(chat, message_id, ref_id)
            return

    # 2d. Bare /run, /approve, /cancel without args → usage hint (NFR11).
    # /status with stray args (e.g. `/status foo`) falls through here too —
    # the regex is whole-message, so the bare-prefix check doesn't catch it;
    # it lands in /help below, which is the intended NFR11 behaviour.
    bare = text.lower().split()[0] if text else ""
    if bare == "/run":
        _reply_text(chat, message_id,
                    "usage: `/run <prompt>` — first 80 chars become the task title.")
        return
    if bare in ("/approve", "/cancel"):
        _reply_text(chat, message_id, f"usage: `{bare} <task_id>` — numeric id required.")
        return

    # 3. Help fallthrough.
    if text.lower() in ("/help", "/start", "help"):
        _tg("sendMessage", {
            "chat_id": chat,
            "text": (
                "Command Centre · Telegram bridge\n\n"
                "I forward `DECISION:` and `INBOX:` from Claude Code sessions, "
                "and let you launch / approve / cancel headless tasks from here.\n\n"
                "*Reply to a notification* with your answer, or use:\n"
                "`/status` — glanceable snapshot (dispatcher, cost, decisions, live)\n"
                "`/answer <id> <text>` — answer a pending decision\n"
                "`/reply <id> <text>` — reply to an inbox message\n"
                "`/run <prompt>` — queue a new headless task\n"
                "`/approve <task_id>` — override a risk-gated task\n"
                "`/cancel <task_id>` — cancel a pending / risk-gated task\n\n"
                "_Reply to a_ 🛑 _RISK-GATED notification with any text to approve._"
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
