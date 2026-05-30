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
from datetime import datetime, timedelta, timezone
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

def _now_utc_iso() -> str:
    """`YYYY-MM-DD HH:MM:SS` in UTC — matches SQLite's `datetime('now')` so
    notification_log.snoozed_until comparisons stay lexicographic."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _already_notified(event_type: str, event_key: str) -> bool:
    """True when a notification has already been sent for this event AND
    its snooze (if any) is still in the future. v0.6.2 wires in the
    `snoozed_until` column shipped dormant in v0.3.0 — when it's set and
    still in the future the row is treated as "not yet notified" so the
    outbound tick re-fires once after the window elapses."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT snoozed_until FROM notification_log "
            "WHERE event_type=? AND event_key=? AND chat_id=? LIMIT 1",
            (event_type, event_key, CHAT_ID),
        ).fetchone()
        if not row:
            return False
        snoozed_until = row[0]
        if snoozed_until is None:
            return True
        # ISO8601 UTC strings — lexicographic comparison matches chronological.
        return snoozed_until > _now_utc_iso()


def _record_notify(event_type: str, event_key: str, telegram_message_id: str) -> None:
    """Record an outbound notification. INSERT-OR-IGNORE on the unique
    `(event_type, event_key, chat_id)` index so the existing row survives
    a re-fire. v0.6.2: when the row already existed with an elapsed
    `snoozed_until`, clear it back to NULL so dedupe reverts to the
    default "already notified" — preventing the re-fire loop."""
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notification_log "
            "(event_type, event_key, chat_id, telegram_message_id) "
            "VALUES (?, ?, ?, ?)",
            (event_type, event_key, CHAT_ID, str(telegram_message_id)),
        )
        conn.execute(
            "UPDATE notification_log SET snoozed_until=NULL "
            "WHERE event_type=? AND event_key=? AND chat_id=? "
            "AND snoozed_until IS NOT NULL AND snoozed_until <= ?",
            (event_type, event_key, CHAT_ID, _now_utc_iso()),
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


def _format_skill_budget_exceeded(skill: dict) -> str:
    """v0.6.7 — first-fire per skill per day push for budget exhaustion.

    Markdown V1; skill name is operator-controllable so flow it through
    `_md_safe` to keep the parser happy when names contain underscores."""
    name = _md_safe((skill.get("name") or "(unnamed)")[:120])
    today = float(skill.get("today_cost_usd") or 0)
    budget = float(skill.get("daily_budget_usd") or 0)
    ts = _now_local_str()
    return (
        f"🔒 *Skill budget reached*\n\n"
        f"{name} hit ${today:.2f} / ${budget:.2f} today ({ts}).\n\n"
        f"Dispatcher refusing new claims for this skill until midnight local. "
        f"Other skills + manual /run tasks continue normally."
    )


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


def _format_review_gated(t: dict) -> str:
    """v0.7.0 — format a review-escalated task (status=awaiting_approval with a
    non-NULL review_verdict) for Telegram. Distinct from _format_risk_gated so
    the operator reads WHY (the reviewer's verdict + reason) before approving.
    Approving re-runs the task with the feedback prepended (existing approve
    semantics); cancelling drops it. verdict + feedback are operator-uncontrolled
    text but flow through _md_safe to keep the Markdown parser happy."""
    title = _md_safe((t.get("title") or "(untitled)")[:200])
    verdict = t.get("review_verdict") or "NOT_VERIFIED"
    reason = _md_safe((t.get("review_feedback") or "")[:400])
    body = (
        f"❓ *REVIEW NEEDED* `#{t['id']}` · `{verdict}`"
        + f"\n\n{title}"
    )
    if reason:
        body += f"\n\n_reviewer:_ {reason}"
    # v0.7.1 — a review escalation now has THREE resolutions. /accept keeps the
    # implementer's output as-is (no re-run); /approve re-runs with the feedback
    # prepended; /cancel drops it.
    body += (
        f"\n\nReply `/accept {t['id']}` to keep the output, "
        f"`/approve {t['id']}` to re-run with this feedback, "
        f"or `/cancel {t['id']}` to drop."
    )
    return body


def _outbound_tick() -> dict:
    sent = {
        "decisions": 0, "inbox": 0, "task_complete": 0, "risk_gated": 0,
        "review_gated": 0, "skill_budget_exceeded": 0, "errors": 0,
    }
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

    # Awaiting-approval tasks. v0.7.0 — a task here is either risk-gated (NULL
    # review_verdict) or review-escalated (non-NULL review_verdict). Branch the
    # formatter + dedupe key so the operator sees WHY approval is needed and the
    # two kinds track separately. BOTH resolve via the existing /approve
    # /cancel (the reply-to-msg router treats review_gated like risk_gated).
    try:
        rg = _http_get_json(f"{DASHBOARD_URL}/api/tasks?status=awaiting_approval&limit=20")
        for t in rg.get("items", []):
            key = str(t["id"])
            is_review = t.get("review_verdict") is not None
            event_type = "review_gated" if is_review else "risk_gated"
            if _already_notified(event_type, key):
                continue
            try:
                text = _format_review_gated(t) if is_review else _format_risk_gated(t)
                mid = _send_message(text)
                if mid:
                    _record_notify(event_type, key, str(mid))
                    sent[event_type] += 1
            except Exception as exc:
                sent["errors"] += 1
                print(f"[telegram] {event_type} notify failed: {exc!r}", file=sys.stderr)
    except Exception as exc:
        sent["errors"] += 1
        print(f"[telegram] /api/tasks?status=awaiting_approval fetch failed: {exc!r}", file=sys.stderr)

    # v0.6.7 — skill budget exhaustion. First-fire push per skill per local
    # day. Dedupe key embeds today's local date so tomorrow's first exceed
    # triggers a fresh notification when the budget rolls over.
    try:
        skill_caps = _http_get_json(f"{DASHBOARD_URL}/api/skills")
        today_local = _today_local_date()
        for skill in skill_caps.get("items", []):
            budget = skill.get("daily_budget_usd")
            today = float(skill.get("today_cost_usd") or 0)
            if budget is None:
                continue
            if today < float(budget):
                continue
            event_key = f"{skill.get('name','?')}:{today_local}"
            if _already_notified("skill_budget_exceeded", event_key):
                continue
            try:
                mid = _send_message(_format_skill_budget_exceeded(skill))
                if mid:
                    _record_notify("skill_budget_exceeded", event_key, str(mid))
                    sent["skill_budget_exceeded"] += 1
            except Exception as exc:
                sent["errors"] += 1
                print(f"[telegram] skill_budget_exceeded notify failed: {exc!r}", file=sys.stderr)
    except Exception as exc:
        sent["errors"] += 1
        print(f"[telegram] /api/skills fetch failed: {exc!r}", file=sys.stderr)

    return sent


def _outbound_loop() -> None:
    while not _STOP.is_set():
        try:
            stats = _outbound_tick()
            if any(stats[k] for k in (
                "decisions", "inbox", "task_complete", "risk_gated",
                "review_gated", "skill_budget_exceeded",
            )):
                print(
                    f"[telegram] notified d={stats['decisions']} i={stats['inbox']} "
                    f"tc={stats['task_complete']} rg={stats['risk_gated']} "
                    f"rv={stats['review_gated']} "
                    f"sb={stats['skill_budget_exceeded']} "
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

# Slash command grammar (v0.5.0-mvp2 + v0.6.1 + v0.6.2):
#
#   /answer <decision_id> <body>          → answer a pending decision     (v0.3.0)
#   /reply  <inbox_id>    <body>          → reply to an inbox message     (v0.3.0)
#   /approve <task_id>                    → override risk gate            (v0.5.0-mvp2, FR16)
#   /cancel  <task_id>                    → cancel pending / risk-gated   (v0.5.0-mvp2, FR18)
#   /run <prompt>                         → POST /api/tasks                (v0.5.0-mvp2, FR1-4)
#   /status                               → read-only telemetry snapshot   (v0.6.1, Phase 2)
#   /snooze <decision_id> [duration]      → suppress re-fire for window    (v0.6.2, Phase 2)
#
# Two patterns because `/run` doesn't take a leading int ID — keeping the
# verbs in one regex would force a body for every verb. The body group is
# optional in _CMD_WITH_ID_RE so /approve and /cancel parse without one.
# `/status` is a third sibling — strict whole-message match, no args.
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel|snooze|yes|no|accept)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_CMD_RUN_RE = re.compile(r"^/run\s+(.+)$", re.IGNORECASE | re.DOTALL)
_CMD_STATUS_RE = re.compile(r"^/status\s*$", re.IGNORECASE)
# v0.6.2 — bare `/snooze` or `/snooze 30m` (no ID) inside a reply-to-msg
# branch; ID resolves via _lookup_by_tg_message on the quoted notification.
_CMD_SNOOZE_REPLY_RE = re.compile(
    r"^/snooze(?:\s+(\d+)([mhd]))?\s*$",
    re.IGNORECASE,
)
# v0.6.2 — strict duration grammar for explicit-ID snooze. Empty / None
# defaults to 30m at the parser level (`_parse_snooze_duration`).
_SNOOZE_DURATION_RE = re.compile(r"^\s*(\d+)([mhd])\s*$", re.IGNORECASE)
_SNOOZE_DEFAULT_S = 30 * 60
_SNOOZE_CAP_S = 24 * 3600

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


def _handle_accept(chat_id: Any, message_id: Any, task_id: int) -> None:
    """`/accept <task_id>` — POST /api/tasks/{id}/accept?source=telegram.

    v0.7.1 — keep a review-flagged task's output as-is (no re-run). Mirrors
    _handle_approve; the API guard refuses anything that isn't a review
    escalation (awaiting_approval + non-NULL review_verdict), so a 400 body is
    surfaced verbatim (via `_md_safe`) to the operator."""
    try:
        _http_post_json(
            f"{DASHBOARD_URL}/api/tasks/{task_id}/accept?source=telegram",
            {},
        )
        _reply_text(chat_id, message_id,
                    f"✅ task `#{task_id}` accepted as-is · output kept, "
                    f"review overridden")
    except Exception as exc:
        code, body = _http_status_from_exc(exc)
        safe_body = _md_safe(body) if body else ""
        if code == 404:
            _reply_text(chat_id, message_id, f"task `#{task_id}` not found")
        elif code in (400, 409):
            _reply_text(chat_id, message_id,
                        f"task `#{task_id}` cannot be accepted · {safe_body}")
        elif code is not None:
            _reply_text(chat_id, message_id,
                        f"⚠️ accept failed · {code} · {safe_body}")
        else:
            _reply_text(chat_id, message_id, f"⚠️ accept failed · {safe_body}")


# ---------------------------------------------------------------------------
# /snooze — v0.6.2, Phase 2.
# ---------------------------------------------------------------------------
#
# Suppress the outbound re-fire cycle for a single decision until the window
# elapses. Writes `notification_log.snoozed_until` (column shipped dormant
# in v0.3.0) and an `activities` audit row tagged `decision_snoozed` with
# `source='telegram'` — matches mvp2's FR19 audit-everything-from-Telegram
# pattern for state-changing commands. Departs from v0.6.1 `/status`'s
# no-audit policy on purpose: this mutates dedupe state.

def _parse_snooze_duration(s: Optional[str]) -> tuple[Optional[int], Optional[str], bool]:
    """Parse a `Nm|Nh|Nd` duration string. Returns
    `(seconds, normalized_label, capped)`. Empty/None → default 30m. Bad
    input → `(None, None, False)` so the caller can emit a usage hint."""
    if s is None or not s.strip():
        return _SNOOZE_DEFAULT_S, "30m", False
    m = _SNOOZE_DURATION_RE.match(s)
    if not m:
        return None, None, False
    n = int(m.group(1))
    unit = m.group(2).lower()
    if n <= 0:
        return None, None, False
    if unit == "m":
        secs = n * 60
    elif unit == "h":
        secs = n * 3600
    else:
        secs = n * 86400
    if secs > _SNOOZE_CAP_S:
        return _SNOOZE_CAP_S, "24h", True
    return secs, f"{n}{unit}", False


def _future_local_str(seconds_ahead: int) -> str:
    """Local-tz `HH:MM GMT+7` for `now + seconds_ahead`. Matches the
    v0.6.1 `_now_local_str()` convention so re-fire times read identically
    to the `/status` header timestamp."""
    target = _now_local() + timedelta(seconds=seconds_ahead)
    if _LOCAL_TZ:
        return target.strftime("%H:%M GMT+7")
    return target.strftime("%H:%M")


def _handle_snooze(chat_id: Any, message_id: Any, decision_id: int,
                   duration_str: Optional[str]) -> None:
    """`/snooze <decision_id> [Nm|Nh|Nd]` — suppress the next re-fire(s)
    for a pending decision until the window elapses. Decisions only for
    MVP (FR per amendment); inbox / task_complete / risk_gated are rejected
    upstream by the reply-to-msg dispatcher.

    NFR11: malformed input never crashes the long-poll loop — bad duration
    strings, missing rows, and DB errors all emit usage hints / error
    replies and return cleanly."""
    secs, normalized, capped = _parse_snooze_duration(duration_str)
    if secs is None:
        _reply_text(chat_id, message_id,
                    "usage: `/snooze <decision_id> [30m|2h|1d]` — default 30m")
        return

    try:
        with db.connect() as conn:
            drow = conn.execute(
                "SELECT id, status FROM ops_decisions WHERE id=?",
                (decision_id,),
            ).fetchone()
            if not drow:
                _reply_text(chat_id, message_id,
                            f"decision `#{decision_id}` not found")
                return
            if drow["status"] != "pending":
                _reply_text(chat_id, message_id,
                            f"decision `#{decision_id}` already answered")
                return

            nrow = conn.execute(
                "SELECT id FROM notification_log "
                "WHERE event_type='decision' AND event_key=? AND chat_id=? "
                "LIMIT 1",
                (str(decision_id), CHAT_ID),
            ).fetchone()
            if not nrow:
                _reply_text(chat_id, message_id,
                            f"decision `#{decision_id}` not yet notified "
                            f"— cannot snooze")
                return

            target_iso = (datetime.now(timezone.utc) + timedelta(seconds=secs)
                          ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE notification_log SET snoozed_until=? WHERE id=?",
                (target_iso, nrow["id"]),
            )
            conn.execute(
                "INSERT INTO activities(event_type, detail) "
                "VALUES ('decision_snoozed', ?)",
                (json.dumps({
                    "decision_id": decision_id,
                    "duration": normalized,
                    "snoozed_until": target_iso,
                    "source": "telegram",
                }),),
            )
    except Exception as exc:
        print(f"[telegram] /snooze failed: {exc!r}", file=sys.stderr)
        _reply_text(chat_id, message_id,
                    f"⚠️ snooze failed · {_md_safe(str(exc))[:200]}")
        return

    wake = _future_local_str(secs)
    if capped:
        _reply_text(chat_id, message_id,
                    f"⚠️ max 24h — capped · ✅ decision `#{decision_id}` "
                    f"snoozed for 24h — next re-fire {wake}")
    else:
        _reply_text(chat_id, message_id,
                    f"✅ decision `#{decision_id}` snoozed for {normalized} "
                    f"— next re-fire {wake}")


# ---------------------------------------------------------------------------
# /yes /no — v0.6.5, Phase 2.
# ---------------------------------------------------------------------------
#
# Binary-decision shortcuts: `/yes <id>` and `/no <id>` answer a pending
# decision with the literal string "yes" / "no". Same payload as
# `/answer <id> yes` — just two keystrokes on a phone. Also fires as a
# reply-to-DECISION-msg shortcut when the body is exactly `/yes`, `/no`,
# `yes`, or `no` (case-insensitive, strict whole-message). No audit row —
# matches the existing `/answer` slash pattern from v0.3.0 (the decisions
# answer endpoint is the audit source), deliberately departing from
# mvp2/v0.6.2/v0.6.4's audit-everything pattern for state-changing
# commands because `/yes`/`/no` route through the same endpoint as
# `/answer`, which itself does not audit.

def _handle_yes_no(chat_id: Any, message_id: Any, decision_id: int,
                   answer: str) -> None:
    """`/yes <id>` / `/no <id>` (or reply-to-DECISION shortcut) — POST
    /api/decisions/{decision_id}/answer with body `{"answer": "yes"|"no"}`.

    Surfaces the API's 200 / 404 / 400 responses directly. The endpoint
    returns 200 with `{"already": True}` for an already-answered decision
    (not 400) — handled as a distinct branch so the operator sees an
    accurate hint without ambiguity.

    NFR11: malformed input never crashes the long-poll loop — every
    branch emits a reply and returns."""
    answer = (answer or "").strip().lower()
    try:
        res = _http_post_json(
            f"{DASHBOARD_URL}/api/decisions/{decision_id}/answer",
            {"answer": answer},
        )
    except Exception as exc:
        code, body = _http_status_from_exc(exc)
        safe_body = _md_safe(body) if body else ""
        if code == 404:
            _reply_text(chat_id, message_id,
                        f"decision `#{decision_id}` not found")
        elif code == 400:
            _reply_text(chat_id, message_id,
                        f"decision `#{decision_id}` · {safe_body}")
        elif code is not None:
            _reply_text(chat_id, message_id,
                        f"⚠️ answer failed · {code} · {safe_body}")
        else:
            _reply_text(chat_id, message_id,
                        f"⚠️ answer failed · {safe_body}")
        return

    if isinstance(res, dict) and res.get("already"):
        _reply_text(chat_id, message_id,
                    f"decision `#{decision_id}` already answered")
        return
    _reply_text(chat_id, message_id,
                f"✅ decision `#{decision_id}` answered: {answer}")


# ---------------------------------------------------------------------------
# Follow-up task chaining — v0.6.4, Phase 2.
# ---------------------------------------------------------------------------
#
# Reply-to-task-complete routing: operator replies to a ✅/❌ task_complete
# notification with a follow-up instruction. Bridge fetches the previous
# task, composes a new task with prev title + output_summary as context,
# and dispatches as if the operator had typed `/run` with the fuller prompt.
#
# Mirrors mvp2's reply-to-RISK-GATED → /approve pattern: lookup via
# _lookup_by_tg_message → call handler. Refuses on failed/cancelled prev
# tasks (no useful summary to chain on, silent chaining would mislead) and
# on deleted prev tasks (404).
#
# Multi-hop is natural: a follow-up's completion ping receives its own
# /run-equivalent reply → description chain breaks at one level deep. The
# Pattern Library lineage view is v0.7+ UI work and out of scope.

_FOLLOWUP_PREV_TITLE_MAX = 80
_FOLLOWUP_PREV_SUMMARY_MAX = 300
_FOLLOWUP_NEW_TITLE_MAX = 60


def _truncate_ellipsis(s: str, n: int) -> str:
    """Trim `s` to `n` chars max, append '…' when truncation occurred."""
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def _fetch_prev_task(prev_task_id: int) -> Optional[dict]:
    """Read prev task from ops_tasks directly. Read-only lookup — same
    pattern as `_handle_snooze`'s ops_decisions query (no GET endpoint
    exists for a single task; the bridge already imports `db`). Returns
    None when the row is missing."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, title, status, output_summary FROM ops_tasks WHERE id=?",
            (prev_task_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)


def _compose_followup_description(prev: dict, operator_reply: str) -> str:
    """New task's `description`: prev context + `---` separator + reply.

    Truncation rules per amendment:
      - prev title: 80 chars + ellipsis if longer
      - prev output_summary: 300 chars + ellipsis if longer; literal
        '(no output summary recorded)' substitute when None / empty
      - operator reply: not truncated locally (the dashboard /api/tasks
        accepts the full body; TG's 3000-char body cap applies only
        on the outbound send-side, not on POSTs to the dashboard)."""
    prev_title = _truncate_ellipsis(prev.get("title") or "(untitled)",
                                    _FOLLOWUP_PREV_TITLE_MAX)
    raw_summary = (prev.get("output_summary") or "").strip()
    if not raw_summary:
        summary = "(no output summary recorded)"
    else:
        summary = _truncate_ellipsis(raw_summary, _FOLLOWUP_PREV_SUMMARY_MAX)
    return (
        f"Follow-up to task #{prev['id']} (\"{prev_title}\"):\n\n"
        f"{summary}\n\n"
        f"---\n\n"
        f"{operator_reply}"
    )


def _compose_followup_title(operator_reply: str) -> str:
    """Title surface for TaskBoard cards: `Follow-up: {first 60 chars}`,
    ellipsis if longer. Newlines flattened so cards render one-line."""
    flat = operator_reply.replace("\n", " ").strip()
    return f"Follow-up: {_truncate_ellipsis(flat, _FOLLOWUP_NEW_TITLE_MAX)}"


def _handle_task_followup(chat_id: Any, message_id: Any,
                          prev_task_id: int, body: str) -> None:
    """Reply-to-msg on a ✅/❌ task_complete notification → chain a new
    task with the previous task's output as context.

    Refuses on empty reply, deleted prev task (404), and prev status in
    `('failed', 'cancelled')` — explicit reply, no state mutation. On
    success: POST /api/tasks, INSERT activities row tagged
    `event_type='task_followup_created'` (FR19), trigger dispatcher
    inline via /api/dispatcher/trigger so the new task transitions
    within ~1s, reply with the new task id.

    NFR11: malformed input never crashes the long-poll loop — every
    branch emits a reply and returns. Audit + dispatcher-trigger
    failures log to stderr but do not block the operator reply (the
    task is already created; dispatcher will pick it up on the next
    120s heartbeat as the worst-case fallback)."""
    reply_body = (body or "").strip()
    if not reply_body:
        _reply_text(chat_id, message_id,
                    "cannot create follow-up from empty reply")
        return

    prev = _fetch_prev_task(prev_task_id)
    if prev is None:
        _reply_text(chat_id, message_id,
                    f"task `#{prev_task_id}` not found — notification may "
                    f"reference a deleted task")
        return

    status = (prev.get("status") or "").lower()
    if status in ("failed", "cancelled"):
        _reply_text(chat_id, message_id,
                    f"task `#{prev_task_id}` {status} — chain on a "
                    f"successful task or queue a fresh `/run`")
        return

    description = _compose_followup_description(prev, reply_body)
    title = _compose_followup_title(reply_body)
    prev_title_full = prev.get("title") or ""

    try:
        res = _http_post_json(
            f"{DASHBOARD_URL}/api/tasks",
            {
                "title": title,
                "description": description,
                "execution_mode": "classic",
                "priority": 0,
                "quadrant": "do",
                "risk_level": "low",
                "requires_approval": False,
                "dry_run": False,
                "created_at_source": "telegram",
            },
        )
    except Exception as exc:
        code, body_err = _http_status_from_exc(exc)
        safe_body = _md_safe(body_err) if body_err else ""
        if code:
            _reply_text(chat_id, message_id,
                        f"⚠️ follow-up create failed · {code} · {safe_body}")
        else:
            _reply_text(chat_id, message_id,
                        f"⚠️ follow-up create failed · {safe_body}")
        return

    new_task_id = res.get("id")
    if not new_task_id:
        _reply_text(chat_id, message_id,
                    f"⚠️ follow-up create returned no id · {res}")
        return

    # Audit row — FR19, matches mvp2 + v0.6.2 audit-everything-from-
    # Telegram pattern for state-changing commands. Best-effort: a DB
    # failure here logs to stderr but does not block the dispatcher
    # trigger or the operator reply (the task IS already created).
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO activities(event_type, detail) "
                "VALUES ('task_followup_created', ?)",
                (json.dumps({
                    "prev_task_id": prev_task_id,
                    "new_task_id": new_task_id,
                    "prev_title": prev_title_full,
                    "operator_reply_first_60": reply_body[:60],
                    "source": "telegram",
                }),),
            )
    except Exception as exc:
        print(f"[telegram] task_followup audit insert failed: {exc!r}",
              file=sys.stderr)

    # Inline dispatcher trigger — same pattern as v0.6.0's
    # SkillLauncher.launch. POST returns immediately; the endpoint
    # Popens `heartbeat.py --once` detached so the new task transitions
    # to `running` within ~1s instead of waiting for the 120s heartbeat.
    # Best-effort: trigger failure leaves the task pending until the
    # next heartbeat cycle picks it up.
    try:
        _http_post_json(f"{DASHBOARD_URL}/api/dispatcher/trigger", {})
    except Exception as exc:
        print(f"[telegram] task_followup dispatcher trigger failed: {exc!r}",
              file=sys.stderr)

    _reply_text(chat_id, message_id,
                f"✅ task `#{new_task_id}` queued · follow-up to "
                f"`#{prev_task_id}`")


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

    Routing order (v0.5.0-mvp2 + v0.6.1 + v0.6.2 + v0.6.4 + v0.6.5 + v0.7.1):
      1. reply-to-message → lookup notification_log
         - risk_gated → /approve <task_id>              (v0.5.0-mvp2, FR17)
         - review_gated + body `/accept`|`accept` (strict whole-message)
           → _handle_accept                             (v0.7.1)
         - review_gated (any other reply) → /approve    (v0.7.0)
         - task_complete → _handle_task_followup        (v0.6.4)
         - decision + body `/yes`|`yes`|`/no`|`no` (strict whole-message)
           → _handle_yes_no                              (v0.6.5)
         - decision  + body `/snooze [Nm|Nh|Nd]` → _handle_snooze  (v0.6.2)
         - decision  → POST /api/decisions/{id}/answer  (v0.3.0)
         - inbox     → POST /api/inbox/{id}/reply       (v0.3.0)
      2. slash command
         - /status                  → _handle_status    (v0.6.1, Phase 2)
         - /answer | /reply         → existing route_reply (v0.3.0)
         - /run <prompt>            → _handle_run       (v0.5.0-mvp2, FR1-4)
         - /approve <id>            → _handle_approve   (v0.5.0-mvp2, FR16)
         - /cancel <id>             → _handle_cancel    (v0.5.0-mvp2, FR18)
         - /accept <id>             → _handle_accept    (v0.7.1)
         - /snooze <id> [duration]  → _handle_snooze    (v0.6.2, Phase 2)
         - /yes <id> | /no <id>     → _handle_yes_no    (v0.6.5, Phase 2)
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
                # v0.7.0 — a ❓ REVIEW-NEEDED notification (review_gated) lands
                # in the SAME awaiting_approval state and is resolved by the SAME
                # /approve, so it routes here too. No new verb, no new parser.
                if event_type in ("risk_gated", "review_gated"):
                    # v0.7.1 — a review escalation can be resolved THREE ways. A
                    # reply of exactly `/accept` or bare `accept` (case-
                    # insensitive, strict whole-message — same shape as the
                    # v0.6.5 /yes /no shortcut) keeps the implementer's output
                    # as-is via /accept. Anything else (incl. a longer "accept
                    # but note X") falls through to the v0.7.0 default:
                    # reply-to-review = /approve (re-run). Scoped to review_gated
                    # — a risk_gated task has review_verdict NULL, so /accept
                    # would 400; its /approve path is untouched.
                    if (event_type == "review_gated"
                            and text.strip().lower() in ("/accept", "accept")):
                        try:
                            _handle_accept(chat, message_id, int(event_key))
                        except (ValueError, TypeError):
                            _reply_text(chat, message_id,
                                        f"⚠️ bad task id in notification_log: {event_key!r}")
                        return
                    try:
                        _handle_approve(chat, message_id, int(event_key))
                    except (ValueError, TypeError):
                        _reply_text(chat, message_id,
                                    f"⚠️ bad task id in notification_log: {event_key!r}")
                    return
                # v0.6.4 — reply-to-msg on a ✅/❌ task_complete notification
                # routes to _handle_task_followup, which chains a new task
                # with the previous task's title + output_summary as
                # context. Mirrors the risk_gated → /approve shape: lookup
                # via _lookup_by_tg_message → dispatch. Refusal on
                # failed/cancelled/deleted prev tasks lives inside the
                # handler so the routing layer stays thin.
                if event_type == "task_complete":
                    try:
                        _handle_task_followup(chat, message_id,
                                              int(event_key), text)
                    except (ValueError, TypeError):
                        _reply_text(chat, message_id,
                                    f"⚠️ bad task id in notification_log: {event_key!r}")
                    return
                # v0.6.5 — reply-to-DECISION-msg with bare `/yes` / `/no` /
                # `yes` / `no` (case-insensitive, strict whole-message) →
                # _handle_yes_no with the looked-up decision id. Longer
                # replies like `Yes, do it` or `No — defer` fall through to
                # the existing verbatim routing so operator nuance isn't
                # collapsed. Scoped to event_type=='decision' so a reply of
                # `/yes` to a task-complete notification stays a follow-up
                # body (the task_complete branch above already returned).
                if event_type == "decision":
                    stripped = text.strip().lower()
                    if stripped in ("/yes", "yes", "/no", "no"):
                        ans = "yes" if stripped in ("/yes", "yes") else "no"
                        try:
                            _handle_yes_no(chat, message_id,
                                           int(event_key), ans)
                        except (ValueError, TypeError):
                            _reply_text(chat, message_id,
                                        f"⚠️ bad decision id in notification_log: {event_key!r}")
                        return

                # v0.6.2 — reply-to-DECISION-msg body `/snooze [duration]`
                # routes to _handle_snooze with the looked-up decision id.
                # Scope: decisions only for MVP — non-decision targets fall
                # through to the answer-route below where they'll fail
                # gracefully (inbox would treat `/snooze 30m` as a literal
                # reply body, which is wrong but non-destructive).
                m_snooze_reply = _CMD_SNOOZE_REPLY_RE.match(text)
                if m_snooze_reply and event_type == "decision":
                    n_str = m_snooze_reply.group(1)
                    unit_str = m_snooze_reply.group(2)
                    dur = f"{n_str}{unit_str}" if n_str else None
                    try:
                        _handle_snooze(chat, message_id, int(event_key), dur)
                    except (ValueError, TypeError):
                        _reply_text(chat, message_id,
                                    f"⚠️ bad decision id in notification_log: {event_key!r}")
                    return
                if m_snooze_reply and event_type != "decision":
                    _reply_text(chat, message_id,
                                f"snooze is decisions-only — this is a "
                                f"{event_type} notification")
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
        elif verb == "accept":
            # v0.7.1 — keep a review-flagged task's output as-is (no re-run).
            _handle_accept(chat, message_id, ref_id)
            return
        elif verb == "snooze":
            # `ref_id` is the decision_id; `body` is the optional duration
            # (the with-ID regex's group(3) is named `body` but its job for
            # snooze is to carry `30m` / `2h` / `1d`). None or empty →
            # default 30m inside _handle_snooze.
            _handle_snooze(chat, message_id, ref_id, body)
            return
        elif verb in ("yes", "no"):
            # v0.6.5 — `/yes <id>` / `/no <id>` binary-decision shortcut.
            # Any body group after the id is ignored (these verbs take no
            # body; the regex's optional group(3) just keeps the alternation
            # consistent with answer/reply/snooze).
            _handle_yes_no(chat, message_id, ref_id, verb)
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
    if bare in ("/approve", "/cancel", "/accept"):
        _reply_text(chat, message_id, f"usage: `{bare} <task_id>` — numeric id required.")
        return
    if bare == "/snooze":
        _reply_text(chat, message_id,
                    "usage: `/snooze <decision_id> [30m|2h|1d]` — default 30m")
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
                "`/cancel <task_id>` — cancel a pending / risk-gated task\n"
                "`/accept <task_id>` — keep a review-flagged task's output as-is (no re-run)\n"
                "`/snooze <decision_id> [30m|2h|1d]` — suppress re-fire (default 30m, max 24h)\n"
                "`/yes <decision_id>` · `/no <decision_id>` — shortcut to answer a binary decision\n\n"
                "_Reply to a_ 🛑 _RISK-GATED notification with any text to approve._\n"
                "_Tasks under adversarial review that fail verification land in approval —_ "
                "`/accept <id>` _to keep the output as-is,_ `/approve <id>` _to re-run with "
                "the reviewer's feedback,_ `/cancel <id>` _to drop._\n"
                "_Reply to a_ ❓ _DECISION notification with_ `/snooze [duration]` _to defer it,_\n"
                "_or with just_ `/yes` _/_ `/no` _(or bare_ `yes` _/_ `no` _) to answer it._\n"
                "_Reply to a_ ✅ _task-complete notification with a follow-up instruction "
                "to chain a new task with the previous task's output as context._"
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
