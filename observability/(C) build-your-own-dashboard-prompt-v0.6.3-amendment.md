# v0.6.3 Amendment — multi-account backend port + FSEvents opt-in + wip cleanup

Apply on top of current `main` HEAD (post v0.6.2). This release is
**clean-up, not new feature work** — three wip commits (`c877468`,
`9e51c76`, `b1f5650`) landed on main between the v0.6.1 spec and the
v0.6.1 ship without CHANGELOG mention, leaving two surfaces in a
half-shipped state:

1. **Multi-account UI** rendering an empty state because the backend
   pieces (helpers, hooks, schema migration, API block) shipped only
   in the operator's installed copy at `~/.command-centre/` and were
   never copied to the repo worktree.
2. **FSEvents watcher** as the default code path in `server.py` but
   reverted to polling in the installed copy — operators rebuilding
   from a clean checkout would pick up the unproven experimental path
   by surprise.

This amendment ports the multi-account backend across, gates FSEvents
behind an opt-in env var, and writes a single backfill CHANGELOG entry
labelling all four wip-derived bits with their actual production
status (GMT+7 finish + token attribution fix already shipped clean;
multi-account + FSEvents shipped here).

**Numbered `v0.6.3`** — patch over v0.6.2, same incremental cadence as
v0.6.1 / v0.6.2. Alternative `v0.7.0` is defensible (multi-account
end-to-end is a real feature ship, not just polish) — the installed
copy's `db.py` actually labels the account migration as "v0.7.0 —
multi-account tagging." Recommendation: `v0.6.3`, because the work
that USED to be v0.7.0 is finishing/landing rather than starting
fresh. Override at commit time if you want minor-bump semantics.

**Verified against current `main` HEAD**:
- `scripts/helpers/accounts.py` ABSENT in repo, PRESENT in install
  (106 lines).
- `scripts/hooks/` directory ABSENT in repo, PRESENT in install with
  `session_start_account_snapshot.py` (91 lines).
- `scripts/db.py` missing `sessions.account_id` migration + index.
  Other v0.6.2 migrations intact; port is additive.
- `scripts/sync_sessions.py` missing `account_id` stamping + the
  `from helpers import accounts as accounts_helper` import.
- `scripts/server.py` missing the `/api/summary by_account` block
  + the same import. (Both v0.6.2 trigger-source logic and the
  multi-account block coexist cleanly.)
- `scripts/server.py:34-132+` contains the FSEvents code path with a
  `try/except ImportError` fallback to polling — opt-in gate must be
  added.
- `ui/src/components/panels/AccountBreakdownCard.tsx` already in repo
  (commit `9e51c76`). Renders against `summary.by_account` — will
  populate once the server endpoint emits it.
- `requirements.txt` includes `watchdog>=4.0` (added in `9e51c76`).
  Keep, but document it as optional now that FSEvents is gated off
  by default.

---

## Multi-account backend port

The wire diagram:

```
SessionStart hook    ─ writes ─►   data/account-hints/{sid}.json
       │                                    │
       │                                    ▼
~/.claude.json oauthAccount         helpers/accounts.py
                                            │
                                            ▼  account_id_for(entrypoint, session_id)
                                  sync_sessions.py
                                            │
                                            ▼  UPDATE sessions SET account_id=?
                                       sessions.account_id
                                            │
                                            ▼  GROUP BY in /api/summary
                                  AccountBreakdownCard
```

### 1. Port `scripts/helpers/accounts.py` (new file)

Copy verbatim from `~/.command-centre/scripts/helpers/accounts.py`.
106 lines. Module exports four public symbols:

- `account_id_for(entrypoint=None, session_id=None) -> Optional[str]`
  — hook-hint > entrypoint-proxy fallback chain.
- `all_account_ids() -> list[str]` — configured keys for UI / join
  validation.
- `label_for(account_id: str) -> str` — human label, falls back to id.
- `reload_config()` — `lru_cache(maxsize=1)` invalidation for live
  edits to `accounts.json`.

Config path: `db.INSTALL_DIR / "data" / "accounts.json"`. Hints dir:
`db.INSTALL_DIR / "data" / "account-hints"`. Both `Path` objects.

Helper handles missing config / corrupt JSON / missing hint gracefully
— never raises during normal sync.

### 2. Port `scripts/hooks/session_start_account_snapshot.py` (new file)

Create `scripts/hooks/` directory and copy from install. 91 lines.
Stdlib only. Reads session_id from stdin JSON (preferred) or
`CLAUDE_SESSION_ID` env (fallback). Reads
`~/.claude.json:oauthAccount` block. Writes
`~/.command-centre/data/account-hints/{session_id}.json` with
`{session_id, account_uuid, email, organization_uuid, display_name,
captured_at}`.

Design constraints (already in the source):
- Never blocks session start. Always exits 0.
- Every error → stderr only, never stdout (would pollute Claude's
  transcript).
- Falls back gracefully when `~/.claude.json` is unreadable or
  `oauthAccount` is missing.

### 3. `scripts/db.py` migration (additive)

Add immediately after the v0.6.2 `created_at_source` migration block
(near line 388 in current main):

```python
# v0.6.3 — multi-account tagging. Sessions stamped via entrypoint proxy
# or SessionStart hook hint (see helpers/accounts.py). Orthogonal with
# cost_source / created_at_source — different column, additive on same
# table.
_migrate_add_column(conn, "sessions", "account_id", "TEXT")
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_sessions_account "
    "ON sessions(account_id)"
)
```

Idempotent. Re-running `init_db()` or `apply_migrations()` is a no-op.

### 4. `scripts/sync_sessions.py` stamping

Add at the top of the file (matching the install copy):

```python
from helpers import accounts as accounts_helper  # noqa: E402
```

In the per-session upsert path — right after the existing
`_derive_cost_source` call from v0.6.0 — resolve and stamp
`account_id`:

```python
aid = accounts_helper.account_id_for(
    entrypoint=session.get("entrypoint"),
    session_id=session_id,
)
if aid:
    conn.execute(
        "UPDATE sessions SET account_id = ? WHERE session_id = ?",
        (aid, session_id),
    )
```

Idempotent (the UPDATE re-applies the same `aid` on each sync). The
fresh session should read the install's `sync_sessions.py` to see the
exact line placement — there may be a small block of code that wraps
this with logging or skip-if-unchanged checks.

### 5. `scripts/server.py` API extension

Top-of-file import (matching install):

```python
from helpers import accounts as accounts_helper  # noqa: E402
```

In the `/api/summary` endpoint, after the existing today-rollup query
(near line 232 in current main, between the cost_by_source block and
the response assembly), add the `by_account` block:

```python
# v0.6.3 — split today's spend by account (Desktop vs VS Code).
acc_rows = conn.execute(
    """
    SELECT COALESCE(account_id, 'unknown') AS account_id,
           COUNT(*) AS sessions,
           COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
           COALESCE(SUM(cost_usd), 0) AS cost
    FROM sessions
    WHERE DATE(COALESCE(ended_at, started_at), 'localtime') = DATE('now', 'localtime')
      AND (model IS NULL OR model NOT LIKE '<%')
    GROUP BY COALESCE(account_id, 'unknown')
    """
).fetchall()

by_account: dict[str, dict[str, Any]] = {}
for r in acc_rows:
    aid = r["account_id"]
    by_account[aid] = {
        "label": accounts_helper.label_for(aid),
        "sessions": int(r["sessions"] or 0),
        "tokens": int(r["tokens"] or 0),
        "cost_usd": round(float(r["cost"] or 0.0), 6),
    }
# Ensure every configured account appears even if zero usage today.
for aid in accounts_helper.all_account_ids():
    by_account.setdefault(aid, {
        "label": accounts_helper.label_for(aid),
        "sessions": 0, "tokens": 0, "cost_usd": 0.0,
    })
```

Add `"by_account": by_account` to the response dict alongside
`cost_by_source` etc. Update `Summary` Pydantic model (or the response
shape, depending on what the worktree uses) to include the new field.

Verify the field name matches `types.ts` `Summary.by_account?` which
already landed in `9e51c76`.

### 6. Operator config — `accounts.json` example

The port itself doesn't require an `accounts.json` — the helper
returns the configured `fallback` (default `"unknown"`) when no rules
match. But the operator can't actually USE multi-account until they
configure it. Ship a documented example:

`data/accounts.json.example` (new):

```json
{
  "fallback": "unknown",
  "accounts": {
    "personal": {
      "label": "Personal",
      "entrypoints": ["claude-code", "vscode"],
      "uuids": ["00000000-0000-0000-0000-000000000000"]
    },
    "work": {
      "label": "Work",
      "entrypoints": ["claude-desktop"],
      "uuids": ["11111111-1111-1111-1111-111111111111"]
    }
  }
}
```

`README.md` gains a short subsection under Install: "Multi-account
setup (optional)" pointing at the example, the hook registration
snippet for `~/.claude/settings.json`, and the `cc sync` step to
backfill `sessions.account_id` for existing rows.

### 7. Hook registration — operator action

Document but do not auto-install. The operator's
`~/.claude/settings.json` needs a `SessionStart` hook entry pointing
at the new hook script:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "command": "/Users/<you>/.command-centre/scripts/hooks/session_start_account_snapshot.py"
      }
    ]
  }
}
```

`install.sh` could prompt to add this entry interactively, but
defer — operators who don't want multi-account shouldn't be hassled
about it. README documents the manual step.

## FSEvents opt-in switch

Current state in `scripts/server.py:34-132+`:
- FSEvents Observer is the active code path when `watchdog` imports
  successfully.
- Polling is the fallback when the import fails (`watchdog not
  installed — falling back to polling`).

Target state:
- Polling is the **default** code path regardless of `watchdog`
  availability.
- FSEvents is opt-in via `CC_USE_FSEVENTS=1` env var.
- README + `.env.example` document the trade-off.

### Implementation

Wrap the FSEvents init in an env-gate:

```python
USE_FSEVENTS = os.environ.get("CC_USE_FSEVENTS", "").lower() in ("1", "true", "yes")
FALLBACK_POLL_SECONDS = 300
SYNC_INTERVAL_SECONDS = 120

try:
    if USE_FSEVENTS:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    else:
        raise ImportError("FSEvents disabled — set CC_USE_FSEVENTS=1 to enable")
except ImportError as exc:
    print(f"[server] {exc} — using {SYNC_INTERVAL_SECONDS}s polling loop", file=sys.stderr)
    USE_FSEVENTS = False
```

Then guard the `_JsonlWatcher` class definition and the FSEvents-driven
`_sync_loop` body with `if USE_FSEVENTS:`. The else branch is the
original 120s polling loop that lived in `server.py` before the
experiment landed — restore that path as the default.

### `.env.example` (add)

```
# Experimental: use FSEvents (watchdog) for sub-2s JSONL → DB lag.
# Default is the 120s polling loop, which is proven and zero-dep.
# Requires `pip install watchdog>=4.0`. Off by default.
# CC_USE_FSEVENTS=1
```

### `requirements.txt`

Move `watchdog` to an `optional-dependencies.txt` OR leave it in
`requirements.txt` with an inline comment marking it optional.
Recommendation: leave in `requirements.txt` so `install.sh` keeps the
single-file install path; the env-gate ensures it's not exercised by
default.

### README (add a small section)

"Sync lag (experimental)" — single paragraph noting that
`CC_USE_FSEVENTS=1` enables sub-2-second JSONL→DB latency at the cost
of an experimental code path. Default polling loop is proven and zero
config.

## Operator flow after v0.6.3 lands

```bash
cc restart           # picks up the schema migration in lifespan.
cc doctor            # no new checks; existing ones pass.
cc sync              # backfills account_id on existing sessions
                     # (via entrypoint proxy — hook hints only appear
                     # for sessions started AFTER hook registration).
```

For multi-account specifically:
```bash
# 1. Edit ~/.command-centre/data/accounts.json from the .example.
# 2. Add the SessionStart hook to ~/.claude/settings.json.
# 3. Restart Claude Code so the hook fires for new sessions.
# 4. Wait one sync cycle. The dashboard's Account breakdown card
#    populates.
```

For FSEvents (optional):
```bash
echo "CC_USE_FSEVENTS=1" >> ~/.command-centre/.env
cc restart
# Verify in logs: "[sync_loop] FSEvents watching ~/.claude/projects"
```

## Stop conditions

1. **Multi-account migration runs idempotent.** Start server fresh,
   restart twice. `sessions.account_id` column exists.
   `idx_sessions_account` index exists. Pre-existing rows have
   `account_id IS NULL` and surface as `'unknown'` in the
   `by_account` rollup.
2. **`/api/summary` includes `by_account`.** With no `accounts.json`:
   response has `by_account = {}` (empty dict — fallback case) or a
   single `'unknown'` row when sessions exist. With a valid
   `accounts.json` configuring `personal` + `work`: response includes
   both entries even when zero usage today (setdefault path).
3. **AccountBreakdownCard renders.** Open dashboard. Card no longer
   shows the empty state. Rows match `/api/summary` `by_account`
   payload.
4. **Hook smoke.** Register the hook in
   `~/.claude/settings.json` pointing at the ported script. Start
   a new Claude Code session. Verify
   `~/.command-centre/data/account-hints/{session_id}.json` is
   written within 1 second of session start. Verify a malformed
   `~/.claude.json` (move it away temporarily) does NOT block session
   start and writes a stderr log line.
5. **FSEvents opt-out (default).** With `CC_USE_FSEVENTS` unset:
   server log line reads `using 120s polling loop`. No `Observer` is
   instantiated. Sync still runs every 120s.
6. **FSEvents opt-in.** Set `CC_USE_FSEVENTS=1` in `.env`, restart.
   Log line: `[sync_loop] FSEvents watching ~/.claude/projects`. Write
   a fresh JSONL line into a session file; sync runs within 2 seconds
   (verify by checking `sessions.synced_at` timestamp).
7. **Backward compat (no config).** With NO `accounts.json` AND
   `CC_USE_FSEVENTS` unset (the default after `cc restart`): server
   starts cleanly, dashboard renders, all v0.6.2 features (Telegram
   `/run`/`/status`/`/snooze`) keep working. Card renders with single
   `unknown` row containing today's sessions.
8. **Idempotent on re-install.** Run `install.sh` against an existing
   v0.6.2 install. v0.6.3 schema + helpers land in place. No data
   drops. `cc doctor` exits 0.

## Order of operations

1. **Schema migration first.** Add `sessions.account_id` column +
   index in `db.py`. Test by deleting `data/command-centre.db` and
   re-running `init_db()` to verify migration is in the boot path.
2. **Port `helpers/accounts.py`** verbatim. Add to repo.
3. **Port `hooks/session_start_account_snapshot.py`** verbatim. Add
   to repo. Make executable (`chmod +x`).
4. **Update `sync_sessions.py`** — import + `UPDATE sessions SET
   account_id` after `_derive_cost_source` call. Verify on a real
   sync that the column populates.
5. **Update `server.py`** — import + `by_account` block in
   `/api/summary`. Add to `Summary` Pydantic shape if used.
6. **FSEvents env-gate** in `server.py`. Default polling. Restore the
   pre-experiment 120s polling loop from git history if it was
   removed (commit message says it was reverted in the install copy
   only — worktree's polling loop might still be in place as the
   `except ImportError` branch).
7. **`.env.example`** — add `CC_USE_FSEVENTS` line with comment.
8. **`data/accounts.json.example`** — add documented example.
9. **README** — small section on multi-account setup + FSEvents
   experimental flag.
10. **`AccountBreakdownCard.tsx`** — verify it renders against the
    new payload. No code change expected; just smoke-test.
11. **Smoke tests** — all 8 stop conditions end-to-end.
12. **CHANGELOG v0.6.3 entry** flipped from DRAFT to shipped with the
    four-bit honest status report (see "CHANGELOG copy" below).

## CHANGELOG copy (insert after v0.6.2)

Single retroactive-flavoured entry. Honest about which bits already
landed in main as wip and which finished now.

```
## v0.6.3 — multi-account port + FSEvents opt-in + wip cleanup

Three wip commits (c877468, 9e51c76, b1f5650) landed on main between
the v0.6.1 spec and the v0.6.1 ship without CHANGELOG mention. This
release labels them, completes the half-shipped pieces, and gates the
experimental ones behind opt-in switches.

### What ships

- GMT+7 finish (c877468) — production. Already-clean threading of
  fmtDateTimeUTC7 / fmtTimeUTC7 through AttentionBar, DecisionsCard,
  InboxCard, LiveSessionsCard, SchedulesCard, SkillsRegistry,
  SystemHealthStrip, DecisionsPage. Continuation of a505d24.
- Per-event daily token attribution (part of 9e51c76) — production.
  Sessions that span midnight now split tokens across calendar days
  correctly via per-event daily_usage keyed by (date, model, source).
- Multi-account backend port (NEW IN v0.6.3) — completes the UI
  half from 9e51c76. helpers/accounts.py, hooks/
  session_start_account_snapshot.py, sessions.account_id schema +
  index, sync_sessions.py stamping, server.py /api/summary
  by_account block. Pre-shipped UI in AccountBreakdownCard.tsx now
  renders real data instead of an empty state.
- FSEvents opt-in (NEW IN v0.6.3) — gates the experimental
  watchdog-based JSONL watcher behind CC_USE_FSEVENTS=1.  Default is
  the proven 120s polling loop. Restores the install-copy's
  reverted-to-polling behaviour as the worktree default and
  documents the trade-off.

### What didn't ship

(Nothing — all four wip-derived bits now have explicit handling.)
```

## Compat note with v0.6.0 / v0.5.0-mvp2 / v0.6.1 / v0.6.2

No collisions. `sessions.account_id` is additive next to v0.6.0's
`cost_source` and v0.5.0-mvp2's `created_at_source`. Three columns on
the same table, all `TEXT`, all additive, all idempotent.

Telegram bridge (mvp1/mvp2/v0.6.1/v0.6.2) untouched. SkillLauncher
(v0.6.0) untouched.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.2).
Quality bar: deterministic port, idempotent migration, zero-config
default behaviour (so operators who don't configure multi-account
keep working unchanged). Estimate ~1-2h: port (~30 min) + FSEvents
gate (~15 min) + smoke tests (~30 min) + CHANGELOG flip (~10 min).
