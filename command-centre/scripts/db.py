"""SQLite schema + idempotent migrations for Command Centre.

All CREATE TABLE IF NOT EXISTS. WAL mode on. No ORM.

Single source of truth for the DB path is `get_db_path()`. Every other
script imports from here — never hard-code the path.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Install dir. In production install.sh sets CC_INSTALL_DIR. For dev, fall
# back to this file's grandparent (command-centre/).
INSTALL_DIR = Path(os.environ.get("CC_INSTALL_DIR") or Path(__file__).resolve().parent.parent)
DATA_DIR = INSTALL_DIR / "data"
DB_PATH = DATA_DIR / "command-centre.db"

# USD per 1M tokens: (input, output, cache_read, cache_create).
# Applied in derive_session_cost(). <synthetic> and unknown models → 0 and
# are excluded from stats queries anyway.
MODEL_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7":           (15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-6":           (15.00, 75.00, 1.50, 18.75),
    "claude-sonnet-4-6":         (3.00, 15.00, 0.30, 3.75),
    "claude-haiku-4-5-20251001": (1.00, 5.00, 0.10, 1.25),
    "claude-haiku-4-5":          (1.00, 5.00, 0.10, 1.25),
}


def get_db_path() -> Path:
    return DB_PATH


def connect() -> sqlite3.Connection:
    """Open a connection with WAL + sane defaults. Callers close."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: tuple[str, ...] = (
    # One row per session. JSONL source of truth. See docs/00-schema-recon.md
    # for why ended_at / duration_ms / cost_usd / is_error are *derived*,
    # not read from a (non-existent-in-interactive-sessions) `result` event.
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id           TEXT PRIMARY KEY,
        source               TEXT,           -- 'ide' | 'cowork'
        entrypoint           TEXT,           -- claude-desktop | claude-vscode | claude-cli
        cwd                  TEXT,
        git_branch           TEXT,
        model                TEXT,           -- most-recent non-synthetic assistant.message.model
        service_tier         TEXT,           -- most-recent usage.service_tier
        started_at           TEXT,
        ended_at             TEXT,
        input_tokens         INTEGER DEFAULT 0,
        output_tokens        INTEGER DEFAULT 0,
        cache_read_tokens    INTEGER DEFAULT 0,
        cache_create_tokens  INTEGER DEFAULT 0,
        total_tokens         INTEGER DEFAULT 0,
        effective_tokens     INTEGER DEFAULT 0,   -- input+output (excludes cache reads from "spend")
        cost_usd             REAL    DEFAULT 0,
        duration_ms          INTEGER,
        error_count          INTEGER DEFAULT 0,
        rate_limit_hit       INTEGER DEFAULT 0,
        is_error_any         INTEGER DEFAULT 0,
        stop_reason          TEXT,
        title                TEXT,
        synced_at            TEXT,
        jsonl_path           TEXT,
        jsonl_mtime          REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_cwd ON sessions(cwd)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)",
    # Daily rollup per (date, model, source). Built by sync_sessions.
    """
    CREATE TABLE IF NOT EXISTS token_usage (
        date                 TEXT NOT NULL,
        model                TEXT NOT NULL,
        source               TEXT NOT NULL,
        input_tokens         INTEGER DEFAULT 0,
        output_tokens        INTEGER DEFAULT 0,
        cache_read_tokens    INTEGER DEFAULT 0,
        cache_create_tokens  INTEGER DEFAULT 0,
        PRIMARY KEY (date, model, source)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage(date DESC)",
    # Flattened per tool invocation. Duration derived from outer envelope
    # timestamps (see schema-recon.md §2).
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        session_id    TEXT NOT NULL,
        tool_use_id   TEXT NOT NULL,
        tool_name     TEXT NOT NULL,
        ts            TEXT NOT NULL,
        duration_ms   INTEGER,
        error         INTEGER DEFAULT 0,
        caller        TEXT,
        is_subagent   INTEGER DEFAULT 0,
        parent_uuid   TEXT,
        PRIMARY KEY (session_id, tool_use_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_name_ts ON tool_calls(tool_name, ts)",
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id)",
    # OTEL logs ingest. One row per LogRecord.
    """
    CREATE TABLE IF NOT EXISTS otel_events (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name             TEXT,
        session_id             TEXT,
        prompt_id              TEXT,
        timestamp              TEXT,
        model                  TEXT,
        tool_name              TEXT,
        tool_success           INTEGER,
        tool_duration_ms       INTEGER,
        tool_error             TEXT,
        cost_usd               REAL,
        api_duration_ms        INTEGER,
        input_tokens           INTEGER,
        output_tokens          INTEGER,
        cache_read_tokens      INTEGER,
        cache_create_tokens    INTEGER,
        speed                  TEXT,
        error_message          TEXT,
        status_code            INTEGER,
        attempt_count          INTEGER,
        skill_name             TEXT,
        skill_source           TEXT,
        prompt_length          INTEGER,
        decision               TEXT,
        decision_source        TEXT,
        request_id             TEXT,
        tool_result_size_bytes INTEGER,
        mcp_server_scope       TEXT,
        plugin_name            TEXT,
        plugin_version         TEXT,
        marketplace_name       TEXT,
        install_trigger        TEXT,
        mcp_server_name        TEXT,
        mcp_tool_name          TEXT,
        received_at            TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_otel_events_name_ts ON otel_events(event_name, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_otel_events_session ON otel_events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_otel_events_mcp_server ON otel_events(mcp_server_name)",
    # OTEL metrics ingest.
    """
    CREATE TABLE IF NOT EXISTS otel_metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        metric_name  TEXT,
        metric_type  TEXT,       -- counter | gauge
        value        REAL,
        session_id   TEXT,
        model        TEXT,
        timestamp    TEXT,
        received_at  TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_otel_metrics_name_ts ON otel_metrics(metric_name, timestamp)",
    # System-event pressure data — retries, compactions, hook errors.
    # New table per schema-recon.md §3. Sourced from JSONL `type='system'`.
    """
    CREATE TABLE IF NOT EXISTS system_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id        TEXT,
        timestamp         TEXT,
        subtype           TEXT,
        stop_reason       TEXT,
        retry_attempt     INTEGER,
        max_retries       INTEGER,
        retry_in_ms       INTEGER,
        compact_metadata  TEXT,
        hook_errors       TEXT,
        content           TEXT,
        received_at       TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_system_events_session ON system_events(session_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_system_events_subtype ON system_events(subtype, timestamp)",
    # Sidecar: title resolution from ai-title / custom-title events.
    """
    CREATE TABLE IF NOT EXISTS session_titles (
        session_id    TEXT PRIMARY KEY,
        title         TEXT,
        title_source  TEXT,        -- 'custom' | 'ai' | 'first_user'
        updated_at    TEXT
    )
    """,
    # Task queue.
    """
    CREATE TABLE IF NOT EXISTS ops_tasks (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        title                TEXT NOT NULL,
        description          TEXT,
        status               TEXT NOT NULL DEFAULT 'pending',
        priority             INTEGER DEFAULT 0,
        assigned_skill       TEXT,
        model                TEXT,
        execution_mode       TEXT DEFAULT 'stream',   -- classic | stream
        scheduled_for        TEXT,
        requires_approval    INTEGER DEFAULT 0,
        risk_level           TEXT,
        dry_run              INTEGER DEFAULT 0,
        quadrant             TEXT,   -- do | schedule | delegate | archive
        approved_at          TEXT,
        session_id           TEXT,
        started_at           TEXT,
        completed_at         TEXT,
        duration_ms          INTEGER,
        cost_usd             REAL,
        output_summary       TEXT,
        error_message        TEXT,
        consecutive_failures INTEGER DEFAULT 0,
        created_at           TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ops_tasks_status ON ops_tasks(status, priority DESC, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_ops_tasks_scheduled ON ops_tasks(scheduled_for)",
    """
    CREATE TABLE IF NOT EXISTS ops_schedules (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        name              TEXT,
        cron_expression   TEXT,
        task_title        TEXT,
        task_description  TEXT,
        assigned_skill    TEXT,
        enabled           INTEGER DEFAULT 1,
        next_run_at       TEXT,
        last_run_at       TEXT,
        created_at        TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ops_schedules_next ON ops_schedules(enabled, next_run_at)",
    # HITL decisions — dispatcher parses DECISION: from stream. Partial
    # unique index matches prompt spec; dedupes duplicate marker lines
    # inside a single session while leaving manual creates unconstrained.
    """
    CREATE TABLE IF NOT EXISTS ops_decisions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id      INTEGER,
        session_id   TEXT,
        prompt       TEXT NOT NULL,
        answer       TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT DEFAULT (datetime('now')),
        answered_at  TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_ops_decisions_session_prompt "
    "ON ops_decisions(session_id, prompt) WHERE session_id IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS ops_inbox (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id      INTEGER,
        session_id   TEXT,
        direction    TEXT NOT NULL,  -- 'agent_to_user' | 'user_to_agent'
        body         TEXT,
        read         INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ops_inbox_unread ON ops_inbox(direction, read, created_at DESC)",
    # Append-only event log.
    """
    CREATE TABLE IF NOT EXISTS activities (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type   TEXT NOT NULL,
        detail       TEXT,
        metadata     TEXT,
        created_at   TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_activities_type_ts ON activities(event_type, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS live_session_state (
        session_id    TEXT PRIMARY KEY,
        state         TEXT,
        current_tool  TEXT,
        updated_at    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_stats (
        server        TEXT PRIMARY KEY,
        tools         INTEGER,
        total_tokens  INTEGER,
        error         TEXT,
        measured_at   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_schemas (
        server        TEXT NOT NULL,
        tool          TEXT NOT NULL,
        schema_json   TEXT,
        tokens        INTEGER,
        collected_at  TEXT,
        PRIMARY KEY (server, tool)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skills (
        name            TEXT PRIMARY KEY,
        environment     TEXT,   -- ide:project | ide:global | cowork:plugin | cowork:scheduled
        description     TEXT,
        path            TEXT,
        autonomy_level  TEXT DEFAULT 'review',   -- auto | review | manual
        user_invocable  INTEGER DEFAULT 0,
        script_count    INTEGER DEFAULT 0,
        last_modified   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_state (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_log (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type            TEXT NOT NULL,
        event_key             TEXT NOT NULL,
        chat_id               TEXT,
        sent_at               TEXT DEFAULT (datetime('now')),
        telegram_message_id   TEXT,
        snoozed_until         TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_notification_log "
    "ON notification_log(event_type, event_key, chat_id)",
)


def init_db() -> None:
    """Apply schema. Safe to call many times."""
    with connect() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)


# ---------------------------------------------------------------------------
# Idempotent column-add helper (per prompt spec)
# ---------------------------------------------------------------------------

def _migrate_add_column(conn: sqlite3.Connection, table: str, col: str, coltype: str) -> None:
    """ALTER TABLE ADD COLUMN, ignoring if it already exists."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {r["name"] for r in rows}
    if col in existing:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def apply_migrations() -> None:
    """Runtime migrations for schema drift across updates. Re-runnable —
    idempotent against any v0.x install."""
    with connect() as conn:
        # v0.6.0 — SkillLauncher + cost-source disambiguation.
        _migrate_add_column(conn, "skills",    "preset_json",      "TEXT")
        _migrate_add_column(conn, "skills",    "last_launched_at", "TEXT")
        _migrate_add_column(conn, "skills",    "launch_count",     "INTEGER NOT NULL DEFAULT 0")
        _migrate_add_column(conn, "ops_tasks", "cost_source",      "TEXT NOT NULL DEFAULT 'unknown'")
        _migrate_add_column(conn, "sessions",  "cost_source",      "TEXT NOT NULL DEFAULT 'unknown'")
        # v0.5.0-mvp2 — trigger-source provenance for "Async hours shifted"
        # success metric (PRD FR20/21). Orthogonal with cost_source above:
        # different column, different code path, additive on the same table.
        _migrate_add_column(conn, "ops_tasks", "created_at_source", "TEXT NOT NULL DEFAULT 'dashboard'")
        # v0.6.3 — multi-account tagging. Sessions stamped via entrypoint proxy
        # or SessionStart hook hint (see helpers/accounts.py). Orthogonal with
        # cost_source / created_at_source — different column, additive on same
        # table.
        _migrate_add_column(conn, "sessions", "account_id", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_account "
            "ON sessions(account_id)"
        )
        # v0.6.7 — per-skill daily cost budget. NULL = unlimited (default for
        # every existing skill); 0 = blocks all claims (operator temp-disable);
        # > 0 = post-hoc cap matching the v0.2.0 / v0.6.0 global cap shape.
        _migrate_add_column(conn, "skills", "daily_budget_usd", "REAL")
        # v0.7.0 — adversarial review gate. All additive + idempotent; the
        # NULL/0 defaults make every existing row behave exactly as
        # pre-v0.7.0 (review_mode 0 = off → byte-identical completion path;
        # review_count 0 = no auto-retry consumed; the three TEXT columns NULL
        # = "not yet reviewed"). review_mode is per-skill (mirrors
        # autonomy_level / daily_budget_usd above); the four ops_tasks columns
        # are dispatcher-owned except success_criteria, which is operator-
        # writable on task create (free text the reviewer LLM reads — no parser).
        _migrate_add_column(conn, "skills",    "review_mode",      "INTEGER NOT NULL DEFAULT 0")
        _migrate_add_column(conn, "ops_tasks", "success_criteria", "TEXT")
        _migrate_add_column(conn, "ops_tasks", "review_verdict",   "TEXT")
        _migrate_add_column(conn, "ops_tasks", "review_count",     "INTEGER NOT NULL DEFAULT 0")
        _migrate_add_column(conn, "ops_tasks", "review_feedback",  "TEXT")
        # v0.7.1 — accept-review-output-as-is. 0 = normal completion; 1 = the
        # operator accepted the output despite a non-VERIFIED review (the
        # review_verdict is preserved as-is for the audit trail). Additive +
        # idempotent; every existing row reads 0 = behaves exactly as pre-v0.7.1
        # (a clean VERIFIED completion also keeps review_overridden=0).
        _migrate_add_column(conn, "ops_tasks", "review_overridden", "INTEGER NOT NULL DEFAULT 0")


# ---------------------------------------------------------------------------
# Cost derivation
# ---------------------------------------------------------------------------

def session_cost_usd(
    model: str | None,
    input_t: int, output_t: int, cache_read_t: int, cache_create_t: int,
) -> float:
    """Apply MODEL_PRICES to token totals. Unknown / synthetic → 0."""
    if not model or model.startswith("<"):
        return 0.0
    price = MODEL_PRICES.get(model)
    if not price:
        return 0.0
    pin, pout, pread, pcreate = price
    return round(
        (input_t * pin + output_t * pout + cache_read_t * pread + cache_create_t * pcreate) / 1_000_000,
        6,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    init_db()
    apply_migrations()
    print(f"DB ready at {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
