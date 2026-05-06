# Data-layer recon — before the schema freeze

Ran a field-shape scan across **35 JSONL files / 12 distinct sessions / 4,133
events** in `~/.claude/projects/`. Skipped lines over 2 MB to protect memory
(zero were skipped — none were that large in the first 600 events/file).

Scanner at `/tmp/cc-recon/scan3.py`, raw output at `/tmp/cc-recon/out.txt`.

The prompt's description of the JSONL is 80% right. The 20% that's wrong
matters, and would have silently broken ingest. Surfacing **before** the
schema freezes.

## What the prompt got right

- `user` / `assistant` messages exist, with `message.usage` carrying
  `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`. ✓
- `tool_use` content blocks with `id`, `name`, `input`. ✓
- `tool_result` content blocks with `tool_use_id`, `content`, `is_error`. ✓
- MCP tools arrive as `mcp__<server>__<tool>` in `tool_use.name`. ✓ (1 seen,
  sample-small but shape confirmed.)

## Deltas vs prompt — the five that matter

### 1. There are **no `result` events** in interactive sessions

The prompt says:

> `result` events at session end — carry `total_cost_usd`, `duration_ms`,
> `is_error`, `stop_reason`.

Reality: **0 `result` events in 4,133 scanned lines.** All sessions are
`claude-desktop` (3,242 events) or `claude-vscode` (539 events). `result`
events only appear in CLI `claude -p` one-shot runs — i.e. dispatcher-
launched sessions, not interactive ones. The dashboard has to derive these
fields for the 90%+ interactive case:

| field           | derivation                                                               |
| --------------- | ------------------------------------------------------------------------ |
| `duration_ms`   | `last_event.ts − first_event.ts` for that `sessionId`                    |
| `total_cost_usd`| sum of `assistant.message.usage` × per-model price table (hardcoded)     |
| `is_error`      | `1` if any event has `error`, any `system.subtype='error'`, or any assistant `apiErrorStatus` is set |
| `stop_reason`   | `stop_reason` from the **last** assistant message on that session        |
| `ended_at`      | mtime-stable heuristic: file unchanged ≥ 5 min ⇒ ended, else NULL        |

### 2. Tool-call duration is pairing **outer** event timestamps, not inner

The prompt says:

> Duration is `tool_result.timestamp − tool_use.timestamp`.

Reality: `tool_use` blocks have keys `{caller, id, input, name, type}` —
**no `timestamp`**. Same for `tool_result`. The timestamp lives on the
**outer envelope line** (the `assistant` / `user` event whose
`message.content[]` contains the block).

Fix: pair by `tool_use_id`, and for each side use `event.timestamp` of the
containing line. Cap at 10 min as prompt says.

### 3. More event types than the prompt names

The prompt mentions `user` / `assistant` / `tool_use` / `tool_result` /
`result`. Actual distinct top-level types seen:

```
assistant           2079
user                1480
attachment           172
queue-operation      144
last-prompt          100
custom-title          57
system                50
file-history-snapshot 38
ai-title              12
teleported-from        1
```

Two of these are **load-bearing** for panels the prompt specs, and the
prompt is silent on them:

- **`system` events** carry `subtype`, `compactMetadata`, `retryAttempt`,
  `maxRetries`, `retryInMs`, `stopReason`, `hookErrors`. This is **the
  source of truth** for the `PressurePanel` (retry exhaustion, compaction
  count) and parts of `HookActivityCard`. Without ingesting these, those
  panels sit empty.
- **`ai-title` / `custom-title` events** carry the session title. The
  prompt specs `sessions.title` but doesn't say where to source it.
  Decision: prefer `custom-title.customTitle`, fall back to
  `ai-title.aiTitle`, fall back to first user message truncated.

Also useful but lower priority:

- `attachment` sub-type `skill_listing` → seed `skills` registry without
  filesystem scan.
- `attachment` sub-type `hook_success` / `system.hookErrors` → hook
  activity as a JSONL-side fallback when OTEL is off.

### 4. Richer `usage` block than documented

Prompt names four token keys. Reality has **ten**:

```
input_tokens, output_tokens, cache_read_input_tokens,
cache_creation_input_tokens  — as spec'd
+ service_tier               — "standard" / "batch" / "priority"; per-line
+ cache_creation             — object, not scalar (granular cache-type breakdown)
+ inference_geo              — region
+ server_tool_use            — API-side tool invocations count
+ iterations                 — turn count for this API call
+ speed                      — "fast" / "slow" — interesting OTEL-free signal
```

Proposal: capture `service_tier` + `speed` as columns on `sessions`.
Ignore the rest — noise.

### 5. `<synthetic>` model pseudo-value

7 assistant events had `message.model = "<synthetic>"`. These are
client-generated assistant messages (not real API calls). Any cost /
token rollup **must exclude** these or daily cost numbers inflate.
Simple `WHERE model NOT LIKE '<%'` filter throughout the stats queries.

### Smaller notes worth capturing

- `tool_use` has a **`caller`** field (not in prompt). Values TBD — worth
  persisting as `tool_calls.caller` column for AgentFanout and debugging.
- `isSidechain: true` on `user` / `assistant` events marks subagent
  execution. `agentId` is also present. Subagent JSONLs live in
  `subagents/` sub-folders. Decision: **include via `**/*.jsonl` recursive
  glob**, mark rows with `is_subagent = 1` derived from `isSidechain`.
- `entrypoint` is one of `claude-desktop`, `claude-vscode` (in scan). Both
  map to prompt's `source='ide'`. Capture the raw entrypoint as
  `sessions.entrypoint` too — it's useful filter signal.
- `parentUuid` chains events into a tree — not needed for stats panels
  but useful for the LiveSessionDetail drawer's tool-call timeline order.
  Capture `parent_uuid` on `tool_calls` if the cost is cheap.
- No `git_branch` value in the real data is the string "HEAD" for
  detached-HEAD sessions. The prompt's `sessions.git_branch` schema
  tolerates strings, so fine — just note that `HEAD` is literal and
  shouldn't be rendered as a branch name without a guard.

## Schema proposal — the diff from the prompt

Building the prompt's schema **as-written** with these adjustments:

### `sessions` — add 3 cols

```
+ entrypoint       TEXT    -- claude-desktop | claude-vscode | claude-cli
+ service_tier     TEXT    -- standard | batch | priority (last-seen value)
+ is_error_any     INTEGER -- 1 if any assistant.apiErrorStatus / system.error / event.error
```

(Prompt's `error_count` stays; `is_error_any` is the cheap-to-compute boolean.)

### `tool_calls` — add 2 cols

```
+ caller     TEXT     -- from tool_use.caller
+ is_subagent INTEGER -- from event.isSidechain
```

### New table: `system_events`

The `system` event type has pressure-panel data and currently has no
table. Adding one rather than shoehorning into `otel_events`:

```sql
CREATE TABLE IF NOT EXISTS system_events (
  session_id       TEXT,
  timestamp        TEXT,
  subtype          TEXT,       -- compact | retry | error | hook_error | ...
  stop_reason      TEXT,
  retry_attempt    INTEGER,
  max_retries      INTEGER,
  retry_in_ms      INTEGER,
  compact_metadata TEXT,       -- raw JSON
  hook_errors      TEXT,       -- raw JSON
  content          TEXT,
  received_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_system_events_session ON system_events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_system_events_subtype ON system_events(subtype, timestamp);
```

This lets `/api/system/pressure` source retry-exhaustion + compaction
counts from **both** JSONL (via `system_events`) **and** OTEL (via
`otel_events` when telemetry is on). JSONL-only users get a working
panel, not empty state.

### New table: `session_titles`

Sidecar for titles coming from `ai-title` / `custom-title` events:

```sql
CREATE TABLE IF NOT EXISTS session_titles (
  session_id    TEXT PRIMARY KEY,
  title         TEXT,          -- resolved: custom > ai > first-user-msg
  title_source  TEXT,          -- 'custom' | 'ai' | 'first_user' | NULL
  updated_at    TEXT
);
```

Keeps `sessions.title` populated without the sync script having to hold
every title candidate in memory during a re-parse.

### Price table (constant, in `db.py`)

```python
# USD per 1M tokens. Input / output / cache_read / cache_create.
# Updated from Anthropic pricing page 2026-04-24.
MODEL_PRICES = {
  "claude-opus-4-7":        (15.00, 75.00,  1.50, 18.75),
  "claude-opus-4-6":        (15.00, 75.00,  1.50, 18.75),
  "claude-sonnet-4-6":       (3.00, 15.00,  0.30,  3.75),
  "claude-haiku-4-5-20251001": (1.00,  5.00,  0.10,  1.25),
  # <synthetic> and unknowns -> 0.0 (excluded from stats anyway)
}
```

Derive `sessions.cost_usd` on upsert. `ops_tasks.cost_usd` same.

### Everything else: build as prompt specifies

All other tables from the prompt (`token_usage`, `otel_events`,
`otel_metrics`, `ops_tasks`, `ops_schedules`, `ops_decisions`, `ops_inbox`,
`activities`, `live_session_state`, `mcp_stats`, `mcp_schemas`, `skills`,
`system_state`, `notification_log`) built verbatim.

## Decisions needed from you

Three things before I turn the schema into `db.py` + start building ingest.
Answering "proceed with my defaults" is fine if you agree with the recon.

1. **Include subagent sub-files?** `**/*.jsonl` recursive glob picks them
   up. Subagents can be high-volume (TDD loops, multi-step research) and
   inflate counts. Options:
   - **(a) include, mark `is_subagent=1`** — truest picture, default off in
     panel filters so the headline numbers stay "human sessions".
   - **(b) exclude entirely** — smaller DB, but AgentFanoutCard becomes
     less useful.
   - **Default: (a).**

2. **Price table drift.** Cost shown in the dashboard is only as accurate
   as the hardcoded prices. Options:
   - **(a) hardcode now, document in ARCHITECTURE.md** that updating
     requires a 1-line change in `db.py`.
   - **(b) move to a `config/prices.json`** overridable at install time.
   - **Default: (a)** — one file of truth, drift is user's problem and
     infrequent.

3. **Cowork JSONLs.** `~/Library/Application Support/Claude/local-agent-
   mode-sessions/` — the prompt has `sync_cowork.py` as optional. I
   haven't scanned that folder yet. Options:
   - **(a) wire the table column `source`, write a stub `sync_cowork.py`
     with the same pairing logic, check the path at boot, skip silently
     if missing.**
   - **(b) skip entirely, drop the column value `cowork` from the schema.**
   - **Default: (a).**

If all three defaults are OK, I'll freeze the schema and move to
ingest (`db.py` + `sync_sessions.py` + `/v1/logs` + `/v1/metrics`).

## One more thing worth saying out loud

The prompt's quality bar is Linear/Raycast/Vercel and the panel list is
33 items across 3 routes. At the pace of ~5 min / panel once the shell is
up, the **back end** (ingest + all 40+ API endpoints + dispatcher + PID
safety + Telegram + install.sh + launchd + doctor) is the 70% of this
build, not the UI. I'll build it in the order the prompt spec'd:
schema → ingest → API → dispatcher → shell → panels → HITL → Telegram →
setup → tests → verify. No surprises.
