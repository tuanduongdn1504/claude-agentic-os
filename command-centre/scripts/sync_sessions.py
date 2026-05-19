"""Scrape ~/.claude/projects/**/*.jsonl into the Command Centre DB.

Re-parses files whose mtime changed since last sync, or whose session row
has no ended_at (still-active). Per schema-recon.md:

- `result` events don't exist in interactive sessions; we derive
  duration_ms / cost_usd / is_error / ended_at / stop_reason.
- Tool-call duration is outer-envelope ts(tool_result) - ts(tool_use),
  capped at 10 min.
- `system` events feed `system_events` for the PressurePanel.
- Titles come from `custom-title` > `ai-title` > first user message.
- `<synthetic>` model is excluded from rollups.
- Subagent lines are marked `is_subagent=1`; still ingested.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

CLAUDE_PROJECTS = Path(os.environ.get("CC_CLAUDE_PROJECTS_DIR") or
                       os.path.expanduser("~/.claude/projects"))
LINE_CAP_BYTES = 4_000_000
TOOL_DURATION_CAP_MS = 10 * 60 * 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local_date(iso_ts: str) -> str | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_ts_ms(iso_ts: str) -> int | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _scalar(v: Any) -> Any:
    """Coerce a value to something SQLite can bind: str/int/float/None."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    return str(v)


def _get_source(path: Path) -> str:
    if "local-agent-mode-sessions" in str(path):
        return "cowork"
    return "ide"


# ---------------------------------------------------------------------------
# Per-session accumulator
# ---------------------------------------------------------------------------

class SessionAgg:
    __slots__ = (
        "session_id", "source", "entrypoint", "cwd", "git_branch",
        "model", "service_tier",
        "first_ts", "last_ts",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens",
        "error_count", "is_error_any", "rate_limit_hit",
        "stop_reason",
        "title", "title_source", "first_user_text",
        "daily_usage",
        "tool_use_map",  # tid -> (ts_ms, name, caller, is_subagent, parent_uuid)
        "tool_calls",
        "system_events",
        "jsonl_path", "jsonl_mtime",
    )

    def __init__(self, session_id: str, path: Path, mtime: float, source: str) -> None:
        self.session_id = session_id
        self.source = source
        self.entrypoint = None
        self.cwd = None
        self.git_branch = None
        self.model = None
        self.service_tier = None
        self.first_ts = None
        self.last_ts = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_create_tokens = 0
        self.error_count = 0
        self.is_error_any = 0
        self.rate_limit_hit = 0
        self.stop_reason = None
        self.title = None
        self.title_source = None
        self.first_user_text = None
        self.daily_usage = {}
        self.tool_use_map = {}
        self.tool_calls = []
        self.system_events = []
        self.jsonl_path = str(path)
        self.jsonl_mtime = mtime


def _ingest_event(agg: SessionAgg, ev: dict[str, Any]) -> None:
    t = ev.get("type")
    ts = ev.get("timestamp")
    if ts:
        if agg.first_ts is None or ts < agg.first_ts:
            agg.first_ts = ts
        if agg.last_ts is None or ts > agg.last_ts:
            agg.last_ts = ts
    if ev.get("cwd") and agg.cwd is None:
        agg.cwd = ev["cwd"]
    if ev.get("gitBranch") and agg.git_branch is None:
        agg.git_branch = ev["gitBranch"]
    if ev.get("entrypoint") and agg.entrypoint is None:
        agg.entrypoint = ev["entrypoint"]
    is_sidechain = 1 if ev.get("isSidechain") else 0

    if t == "custom-title":
        title = ev.get("customTitle")
        if title:
            agg.title = str(title)[:200]
            agg.title_source = "custom"
        return
    if t == "ai-title":
        title = ev.get("aiTitle")
        if title and agg.title_source != "custom":
            agg.title = str(title)[:200]
            agg.title_source = "ai"
        return

    if t in ("user", "assistant"):
        msg = ev.get("message") or {}
        if not isinstance(msg, dict):
            return
        if t == "user" and agg.first_user_text is None:
            c = msg.get("content")
            if isinstance(c, str):
                agg.first_user_text = c
            elif isinstance(c, list):
                for blk in c:
                    if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text"):
                        agg.first_user_text = blk["text"]
                        break
        if t == "assistant":
            model = msg.get("model")
            if model and not str(model).startswith("<"):
                agg.model = model
            if msg.get("stop_reason"):
                agg.stop_reason = msg["stop_reason"]
            if ev.get("apiErrorStatus") or ev.get("isApiErrorMessage") or ev.get("error"):
                agg.is_error_any = 1
                agg.error_count += 1
            aes = str(ev.get("apiErrorStatus") or "")
            if "429" in aes or "rate" in aes.lower():
                agg.rate_limit_hit = 1
            usage = msg.get("usage")
            if isinstance(usage, dict) and model and not str(model).startswith("<"):
                pin = int(usage.get("input_tokens") or 0)
                pout = int(usage.get("output_tokens") or 0)
                pread = int(usage.get("cache_read_input_tokens") or 0)
                pcreate = int(usage.get("cache_creation_input_tokens") or 0)
                agg.input_tokens += pin
                agg.output_tokens += pout
                agg.cache_read_tokens += pread
                agg.cache_create_tokens += pcreate
                tier = usage.get("service_tier")
                if tier:
                    agg.service_tier = str(tier)
                date_local = _local_date(ts or "")
                if date_local:
                    key = (date_local, model)
                    bucket = agg.daily_usage.setdefault(key, [0, 0, 0, 0])
                    bucket[0] += pin
                    bucket[1] += pout
                    bucket[2] += pread
                    bucket[3] += pcreate
        content = msg.get("content")
        if isinstance(content, list):
            ts_ms = _parse_ts_ms(ts or "")
            for c in content:
                if not isinstance(c, dict):
                    continue
                ct = c.get("type")
                if ct == "tool_use":
                    tid = c.get("id")
                    if tid:
                        agg.tool_use_map[str(tid)] = (
                            ts_ms,
                            str(c.get("name") or "?"),
                            c.get("caller"),
                            is_sidechain,
                            ev.get("parentUuid"),
                        )
                elif ct == "tool_result":
                    tid = c.get("tool_use_id")
                    if not tid:
                        continue
                    start = agg.tool_use_map.pop(str(tid), None)
                    if start is None:
                        continue
                    start_ms, name, caller, is_sub, parent_uuid = start
                    dur = None
                    if ts_ms is not None and start_ms is not None:
                        dur = ts_ms - start_ms
                        if dur < 0 or dur > TOOL_DURATION_CAP_MS:
                            dur = None
                    agg.tool_calls.append({
                        "session_id": agg.session_id,
                        "tool_use_id": str(tid),
                        "tool_name": name,
                        "ts": ts,
                        "duration_ms": dur,
                        "error": 1 if c.get("is_error") else 0,
                        "caller": _scalar(caller),
                        "is_subagent": int(is_sub),
                        "parent_uuid": _scalar(parent_uuid),
                    })
    elif t == "system":
        agg.system_events.append({
            "session_id": agg.session_id,
            "timestamp": ts,
            "subtype": _scalar(ev.get("subtype")),
            "stop_reason": _scalar(ev.get("stopReason")),
            "retry_attempt": ev.get("retryAttempt"),
            "max_retries": ev.get("maxRetries"),
            "retry_in_ms": ev.get("retryInMs"),
            "compact_metadata": _scalar(ev.get("compactMetadata")),
            "hook_errors": _scalar(ev.get("hookErrors")),
            "content": (ev.get("content") or "")[:4000] if isinstance(ev.get("content"), str) else None,
        })
        if ev.get("error") or (ev.get("subtype") or "").startswith("error"):
            agg.is_error_any = 1


# ---------------------------------------------------------------------------
# File-level parse
# ---------------------------------------------------------------------------

def _parse_jsonl(path: Path) -> dict[str, SessionAgg]:
    aggs: dict[str, SessionAgg] = {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    source = _get_source(path)
    with path.open("r", errors="replace") as fh:
        for raw in fh:
            if len(raw) > LINE_CAP_BYTES:
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            sid = ev.get("sessionId")
            if not sid:
                continue
            sid = str(sid)
            agg = aggs.get(sid)
            if agg is None:
                agg = SessionAgg(sid, path, mtime, source)
                aggs[sid] = agg
            try:
                _ingest_event(agg, ev)
            except Exception as exc:
                # Never let one bad event crash the whole file parse.
                print(f"[sync] skip event in {path.name}: {exc!r}", file=sys.stderr)
    return aggs


def _finalize_title(agg: SessionAgg) -> None:
    if agg.title:
        return
    if agg.first_user_text:
        snippet = agg.first_user_text.strip().splitlines()[0][:140] if agg.first_user_text.strip() else None
        if snippet:
            agg.title = snippet
            agg.title_source = "first_user"


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def _upsert_session(conn: sqlite3.Connection, agg: SessionAgg, ended_at: str | None) -> None:
    _finalize_title(agg)
    total = agg.input_tokens + agg.output_tokens + agg.cache_read_tokens + agg.cache_create_tokens
    effective = agg.input_tokens + agg.output_tokens
    cost = db.session_cost_usd(
        agg.model, agg.input_tokens, agg.output_tokens,
        agg.cache_read_tokens, agg.cache_create_tokens,
    )
    duration_ms = None
    if agg.first_ts and agg.last_ts:
        start_ms = _parse_ts_ms(agg.first_ts)
        end_ms = _parse_ts_ms(agg.last_ts)
        if start_ms is not None and end_ms is not None:
            duration_ms = max(0, end_ms - start_ms)

    conn.execute(
        """
        INSERT INTO sessions (
            session_id, source, entrypoint, cwd, git_branch, model, service_tier,
            started_at, ended_at, input_tokens, output_tokens, cache_read_tokens,
            cache_create_tokens, total_tokens, effective_tokens, cost_usd,
            duration_ms, error_count, rate_limit_hit, is_error_any, stop_reason,
            title, synced_at, jsonl_path, jsonl_mtime
        ) VALUES (
            :session_id, :source, :entrypoint, :cwd, :git_branch, :model, :service_tier,
            :started_at, :ended_at, :input_tokens, :output_tokens, :cache_read_tokens,
            :cache_create_tokens, :total_tokens, :effective_tokens, :cost_usd,
            :duration_ms, :error_count, :rate_limit_hit, :is_error_any, :stop_reason,
            :title, datetime('now'), :jsonl_path, :jsonl_mtime
        )
        ON CONFLICT(session_id) DO UPDATE SET
            source              = excluded.source,
            entrypoint          = excluded.entrypoint,
            cwd                 = excluded.cwd,
            git_branch          = excluded.git_branch,
            model               = excluded.model,
            service_tier        = excluded.service_tier,
            started_at          = excluded.started_at,
            ended_at            = excluded.ended_at,
            input_tokens        = excluded.input_tokens,
            output_tokens       = excluded.output_tokens,
            cache_read_tokens   = excluded.cache_read_tokens,
            cache_create_tokens = excluded.cache_create_tokens,
            total_tokens        = excluded.total_tokens,
            effective_tokens    = excluded.effective_tokens,
            cost_usd            = excluded.cost_usd,
            duration_ms         = excluded.duration_ms,
            error_count         = excluded.error_count,
            rate_limit_hit      = excluded.rate_limit_hit,
            is_error_any        = excluded.is_error_any,
            stop_reason         = excluded.stop_reason,
            title               = excluded.title,
            synced_at           = datetime('now'),
            jsonl_path          = excluded.jsonl_path,
            jsonl_mtime         = excluded.jsonl_mtime
        """,
        {
            "session_id": agg.session_id,
            "source": agg.source,
            "entrypoint": _scalar(agg.entrypoint),
            "cwd": _scalar(agg.cwd),
            "git_branch": _scalar(agg.git_branch),
            "model": _scalar(agg.model),
            "service_tier": _scalar(agg.service_tier),
            "started_at": agg.first_ts,
            "ended_at": ended_at,
            "input_tokens": agg.input_tokens,
            "output_tokens": agg.output_tokens,
            "cache_read_tokens": agg.cache_read_tokens,
            "cache_create_tokens": agg.cache_create_tokens,
            "total_tokens": total,
            "effective_tokens": effective,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "error_count": agg.error_count,
            "rate_limit_hit": agg.rate_limit_hit,
            "is_error_any": agg.is_error_any,
            "stop_reason": _scalar(agg.stop_reason),
            "title": _scalar(agg.title),
            "jsonl_path": agg.jsonl_path,
            "jsonl_mtime": agg.jsonl_mtime,
        },
    )

    if agg.title is not None:
        conn.execute(
            """
            INSERT INTO session_titles (session_id, title, title_source, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(session_id) DO UPDATE SET
                title=excluded.title, title_source=excluded.title_source,
                updated_at=datetime('now')
            """,
            (agg.session_id, _scalar(agg.title), agg.title_source),
        )

    _derive_cost_source(conn, agg.session_id)

    conn.execute("DELETE FROM tool_calls WHERE session_id = ?", (agg.session_id,))
    if agg.tool_calls:
        conn.executemany(
            """
            INSERT OR REPLACE INTO tool_calls (
                session_id, tool_use_id, tool_name, ts, duration_ms, error,
                caller, is_subagent, parent_uuid
            ) VALUES (
                :session_id, :tool_use_id, :tool_name, :ts, :duration_ms, :error,
                :caller, :is_subagent, :parent_uuid
            )
            """,
            agg.tool_calls,
        )

    conn.execute("DELETE FROM system_events WHERE session_id = ?", (agg.session_id,))
    if agg.system_events:
        conn.executemany(
            """
            INSERT INTO system_events (
                session_id, timestamp, subtype, stop_reason, retry_attempt,
                max_retries, retry_in_ms, compact_metadata, hook_errors, content
            ) VALUES (
                :session_id, :timestamp, :subtype, :stop_reason, :retry_attempt,
                :max_retries, :retry_in_ms, :compact_metadata, :hook_errors, :content
            )
            """,
            agg.system_events,
        )


def _derive_cost_source(conn: sqlite3.Connection, session_id: str) -> None:
    """Tag the session row as api_pool (dispatcher-launched, real-dollar
    billing) or max_sub (interactive REPL/IDE, Pro/Max subsidised) by
    checking for a matching ops_tasks row. Idempotent — safe to call
    every sync tick. Race with the dispatcher's session_id back-fill is
    handled by a second update inside task_tracker.claim_pending."""
    row = conn.execute(
        "SELECT 1 FROM ops_tasks WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    source = "api_pool" if row else "max_sub"
    conn.execute(
        "UPDATE sessions SET cost_source = ? WHERE session_id = ?",
        (source, session_id),
    )


def _merge_daily(conn: sqlite3.Connection, daily: dict[tuple[str, str, str], list[int]]) -> None:
    if not daily:
        return
    conn.executemany(
        """
        INSERT INTO token_usage (date, model, source, input_tokens, output_tokens,
                                 cache_read_tokens, cache_create_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, model, source) DO UPDATE SET
            input_tokens        = excluded.input_tokens,
            output_tokens       = excluded.output_tokens,
            cache_read_tokens   = excluded.cache_read_tokens,
            cache_create_tokens = excluded.cache_create_tokens
        """,
        [(d, m, s, *vals) for (d, m, s), vals in daily.items()],
    )


def _compute_daily_from_sessions(conn: sqlite3.Connection) -> dict[tuple[str, str, str], list[int]]:
    """Re-derive token_usage from sessions. Simple and idempotent.
    Limitation: a session that spans midnight is all attributed to its
    start-day. Error < 5% of daily totals on typical < 4hr sessions."""
    daily: dict[tuple[str, str, str], list[int]] = {}
    rows = conn.execute(
        "SELECT started_at, model, source, input_tokens, output_tokens, "
        "cache_read_tokens, cache_create_tokens FROM sessions "
        "WHERE model IS NOT NULL AND model NOT LIKE '<%'"
    ).fetchall()
    for r in rows:
        date_local = _local_date(r["started_at"] or "")
        if not date_local:
            continue
        key = (date_local, r["model"], r["source"] or "ide")
        bucket = daily.setdefault(key, [0, 0, 0, 0])
        bucket[0] += r["input_tokens"] or 0
        bucket[1] += r["output_tokens"] or 0
        bucket[2] += r["cache_read_tokens"] or 0
        bucket[3] += r["cache_create_tokens"] or 0
    return daily


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_sync(full: bool = False, verbose: bool = False) -> dict[str, int]:
    db.init_db()
    stats = {"files_scanned": 0, "files_parsed": 0, "sessions_upserted": 0}

    if not CLAUDE_PROJECTS.exists():
        if verbose:
            print(f"warn: {CLAUDE_PROJECTS} missing, nothing to sync")
        return stats

    pattern = str(CLAUDE_PROJECTS / "**" / "*.jsonl")
    files = sorted(glob.glob(pattern, recursive=True))
    stats["files_scanned"] = len(files)

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT jsonl_path, MAX(jsonl_mtime) AS mtime, "
            "MAX(CASE WHEN ended_at IS NULL THEN 1 ELSE 0 END) AS has_open "
            "FROM sessions WHERE jsonl_path IS NOT NULL GROUP BY jsonl_path"
        ).fetchall()
        known = {r["jsonl_path"]: (r["mtime"] or 0.0, r["has_open"] or 0) for r in rows}

    now_ts = time.time()

    with db.connect() as conn:
        conn.execute("BEGIN")
        try:
            for path_str in files:
                path = Path(path_str)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                prev = known.get(path_str)
                if not full and prev and mtime <= prev[0] and not prev[1]:
                    continue

                aggs = _parse_jsonl(path)
                if not aggs:
                    continue
                stats["files_parsed"] += 1
                for agg in aggs.values():
                    ended_at = None
                    if now_ts - mtime > 300 and agg.last_ts:
                        ended_at = agg.last_ts
                    _upsert_session(conn, agg, ended_at)
                    stats["sessions_upserted"] += 1

            daily = _compute_daily_from_sessions(conn)
            _merge_daily(conn, daily)

            conn.execute(
                "INSERT INTO activities (event_type, detail, metadata) VALUES (?, ?, ?)",
                (
                    "sync_loop_heartbeat",
                    f"files_scanned={stats['files_scanned']} files_parsed={stats['files_parsed']} sessions_upserted={stats['sessions_upserted']}",
                    json.dumps(stats),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    if verbose:
        print(f"sync done: {stats}")
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="Re-parse all files regardless of mtime")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    stats = run_sync(full=args.full, verbose=True)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
