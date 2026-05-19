# v0.5.0-mvp2 Amendment — Telegram bridge: inbound `/run`, `/approve`, `/cancel`

Apply on top of v0.5.0-mvp1 (or current `main` HEAD — order-independent
with v0.6.0 since the two changes touch disjoint tables and disjoint code
paths). Closes Journey 4 of the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`) — the inbound counterpart
to mvp1's outbound `task_complete` + `risk_gated` push.

**Numbered `v0.5.0-mvp2`** per the PRD's "MVP commit 1 of 2" framing.
Lands chronologically after v0.6.0; CHANGELOG sits descending by version
so v0.5.0-mvp2 reads between v0.6.0 and v0.5.0-mvp1. Version zigzag
accepted as honest about the parallel-MVP plan documented in the PRD.

**Verified against current `main` HEAD**:
- `POST /api/tasks/{task_id}/approve` already exists (`routers/tasks.py:132`).
  No work for FR16's API side.
- `POST /api/tasks/{task_id}/cancel` does NOT exist — mvp2 adds it.
- `ops_tasks.status` enum already includes `'cancelled'` — no migration
  to extend the enum.
- `_CMD_RE` in `telegram_bridge.py:360` matches `(answer|reply)` only —
  mvp2 extends to `(answer|reply|run|approve|cancel)`.
- Format helpers `_format_task_complete` and `_format_risk_gated` already
  exist (mvp1, `telegram_bridge.py:210` and `:236`).
- No schema collisions with v0.6.0's `cost_source` column — mvp2 adds
  `created_at_source` instead. Both are additive `TEXT NOT NULL DEFAULT`
  columns on the same table; migration order does not matter.

---

## Why this release

mvp1 closed the feedback loop on the **outbound** side: dispatcher
completions and risk-gated tasks now push to Telegram. The feedback loop
is half-open until inbound matches: operator can READ task state from
Telegram but cannot WRITE new tasks or approve/cancel held ones.

Journey 4 of the PRD is the canonical case: 11:30pm couch, operator types
`/run delete all stale branches...`, the hard risk gate intercepts, and
the only recovery path today is to walk to the desk and open the
dashboard. mvp2 ships `/approve <id>` and `/cancel <id>` so recovery
stays on the phone.

mvp2 also adds the trigger-source column the PRD's "Async hours shifted"
success metric (NFR audit) depends on — without `created_at_source`, the
operator cannot filter TG-originated tasks from dashboard-originated ones
in the success-metric query.

## Schema delta

One column, additive, through the existing `_migrate_add_column` helper.

| Table | Column | Type | Default |
|---|---|---|---|
| `ops_tasks` | `created_at_source` | TEXT NOT NULL | `'dashboard'` |

Existing rows backfill to `'dashboard'`. New TG-originated rows write
`'telegram'`. Reserve room for `'schedule'` (materialised from
`ops_schedules`) and `'api'` (programmatic callers) without further
migration — validator at the create-task endpoint accepts any value but
the bridge only writes `'telegram'`.

Idempotent. Order-independent with v0.6.0's `cost_source` migration.

## Backend delta

### `POST /api/tasks` — accept `created_at_source` (modified)

Body gains one optional field:

```json
{
  "title": "draft release notes",
  "description": "...",
  "created_at_source": "telegram"
}
```

Default when absent: `'dashboard'` (matches the column default). Validator
accepts any non-empty TEXT; bridge sends `'telegram'`. No breaking change
for dashboard clients that omit the field.

### `POST /api/tasks/{task_id}/cancel` — new endpoint

Mirrors the existing `/approve` pattern at `tasks.py:132-146`. Body
empty. Behaviour:

- Look up task. 404 if not found.
- Accept transition from `pending`, `awaiting_approval`, or
  `risk_gated`-equivalent states. Reject (400) from `running`, `done`,
  `failed`, `cancelled` with an explicit error message — running tasks
  must use `/api/system/emergency-stop`; terminal states are no-ops.
- `UPDATE ops_tasks SET status='cancelled', completed_at=datetime('now')
  WHERE id = ?`.
- INSERT `activities` row: `event_type='task_cancelled'`, `detail` =
  serialised `{task_id, prior_status, source}` where `source` defaults
  to `'api'` but `'telegram'` when called via bridge (pass through a
  query param `?source=telegram` or a header — see Bridge delta for the
  call shape).
- Return `{cancelled: true, task_id, prior_status}`.

### `POST /api/tasks/{task_id}/approve` — extend for source audit

Existing endpoint, additive change only. Accept an optional `?source=`
query parameter (default `'api'`). When set, the activities-log row
inserted on approval records `source` in the detail JSON. No
breaking change — dashboard callers continue to work without the
parameter; the activities row falls back to `'api'`.

If `routers/tasks.py:132` does not currently log to `activities` on
approve, add the INSERT in this pass (it's a one-liner mirroring the new
`/cancel` audit row). FR19 requires every risk-gate override to land in
`activities` for forensic reconstruction.

## Bridge delta — `telegram_bridge.py`

### `_CMD_RE` — extend pattern

Current (single line at `:360`):

```python
_CMD_RE = re.compile(r"^/(answer|reply)\s+(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
```

Split into two patterns — `/run` does not take a leading integer ID, so
the existing pattern won't match it cleanly:

```python
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_CMD_RUN_RE = re.compile(r"^/run\s+(.+)$", re.IGNORECASE | re.DOTALL)
```

`approve` and `cancel` make the body group optional — neither needs a
body. `answer` and `reply` keep requiring a body (raise usage-hint on
missing). `run` is its own pattern — free-text remainder, multi-line
allowed (DOTALL).

### `_handle_message` — add three branches

Existing routing in the inbound handler is extended. Pseudo-order
(inserted after the existing `_CMD_RE` match block):

```python
m_run = _CMD_RUN_RE.match(text)
if m_run:
    prompt = m_run.group(1).strip()
    return _handle_run(chat_id, message_id, prompt)

m_cmd = _CMD_WITH_ID_RE.match(text)
if m_cmd:
    verb, task_id, body = m_cmd.group(1).lower(), int(m_cmd.group(2)), m_cmd.group(3)
    if verb in ("answer", "reply"):
        return _route_reply(verb, task_id, body)  # existing
    if verb == "approve":
        return _handle_approve(chat_id, task_id)
    if verb == "cancel":
        return _handle_cancel(chat_id, task_id)
```

### `_handle_run(chat_id, message_id, prompt)` — new

1. Validate: empty prompt → usage reply, no API call. Length > 3000
   chars → usage reply with "shorter please, max 3000 chars."
2. `POST /api/tasks` with body `{title: prompt[:80], description: prompt,
   created_at_source: 'telegram'}`. Title is the first 80 chars (cap to
   keep TaskBoard cards readable); description carries the full prompt.
3. On 2xx, reply: `✅ task #{id} queued · dispatcher pending`. Include
   `telegram_message_id` from the reply for future reply-to-msg-as-
   `/run` (a Growth feature, not MVP).
4. On 4xx/5xx, reply with the error verbatim (or truncated to 500
   chars) — the operator needs to know what failed.

### `_handle_approve(chat_id, task_id)` — new

1. `POST /api/tasks/{task_id}/approve?source=telegram`. No body.
2. On 200, reply: `✅ task #{task_id} approved · dispatcher resuming`.
3. On 404, reply: `task #{task_id} not found`.
4. On 400 (wrong status), reply: `task #{task_id} not awaiting approval
   (current status: <status>)`.
5. The backend already inserts an `activities` row tagged
   `source='telegram'` (per Backend delta).

### `_handle_cancel(chat_id, task_id)` — new

1. `POST /api/tasks/{task_id}/cancel?source=telegram`. No body.
2. On 200, reply: `✅ task #{task_id} cancelled`.
3. On 404, reply: `task #{task_id} not found`.
4. On 400 (terminal/running status), reply with the explicit error from
   the API (e.g., "running tasks must use emergency-stop").

### `_lookup_by_tg_message` extension — risk-gated reply-to-msg

Existing function maps a reply-to-message's parent ID to an
`(event_type, event_id)` tuple. Currently handles `decision` and `inbox`.
mvp2 adds `risk_gated`:

```
if event_type == 'risk_gated':
    return ('risk_gated', task_id)
```

Routing logic in `_handle_message`'s reply-to-msg branch:

```
if event_type == 'risk_gated':
    return _handle_approve(chat_id, int(event_id))
```

Reply-to-msg on a 🛑 RISK-GATED notification is treated as an
`/approve <task_id>` (FR17). Reply-to-cancel is NOT supported — operator
must type `/cancel <id>` explicitly to avoid accidental cancels from
casual replies.

### Audit logging

The backend handles audit-row inserts on approve/cancel (per Backend
delta). The bridge MUST pass `?source=telegram` on every call so the
backend tags the activities row correctly. No bridge-side audit insert
needed — single source of truth at the API layer.

### `_md_safe` reuse

All operator-typed content (`/run` prompts, error messages echoed back)
must pass through `_md_safe` before being placed in a Telegram reply.
Existing helper from mvp1. Reuse, do not duplicate.

## Format helpers (no new files)

mvp1 already shipped `_format_task_complete` and `_format_risk_gated`.
mvp2 needs no new format helpers — the new commands reply with terse
inline text (one line each), not structured notifications.

## Stop conditions

All four must pass before declaring done.

1. **`/run` happy path.** Send `/run write hello world to /tmp/hello.txt`
   from the configured Telegram chat. Within 5 seconds, receive
   `✅ task #N queued · dispatcher pending`. `ops_tasks` row exists with
   `created_at_source='telegram'`, `title="write hello world to
   /tmp/hello.txt"` (or first-80-chars cap), `description=` full prompt.
2. **`/cancel` happy path.** Queue a task via `/run`, immediately
   `/cancel <id>`. Receive `✅ task #N cancelled`. Status flips to
   `'cancelled'`. `activities` has a `task_cancelled` row with
   `source='telegram'`.
3. **`/approve` via reply-to-msg.** Trigger a risk-gated task. Receive
   the 🛑 RISK-GATED notification (mvp1 outbound). Reply to that
   message with any text. Task transitions `awaiting_approval → pending`.
   `activities` has `task_risk_approved` with `source='telegram'`.
4. **Backward-compat regression.** Existing `/answer`, `/reply`, `/help`
   commands continue to work unchanged. Decision push → reply-to-msg
   answer round-trip completes (PRD's mandatory regression test —
   "replay decision #8's notify+answer post-deploy").

Also verify malformed input (NFR11):

5. **Malformed `/run`.** Send `/run` (no prompt), `/run <4001-char
   string>`. Receive usage hints. Bridge does not crash; long-poll
   continues.
6. **Non-integer ID.** Send `/cancel abc`. Receive usage hint
   ("`/cancel <task_id>`"). No API call.

## Order of operations

1. **Schema migration.** Add `created_at_source` to `ops_tasks` through
   `_migrate_add_column`. Backfill defaults are automatic.
2. **`POST /api/tasks` body validator** accepts `created_at_source`.
   Confirm dashboard's existing POSTs still work without the field.
3. **`POST /api/tasks/{task_id}/cancel`** — new endpoint. Mirror the
   approve pattern. Audit-log row on success.
4. **Extend `/approve`** with `?source=` query param and the audit-log
   row (if not already present at `tasks.py:132`).
5. **Bridge `_CMD_RE` split.** Two patterns: one with ID, one for
   `/run`. Smoke-test the regex against fixture inputs first.
6. **`_handle_run`, `_handle_approve`, `_handle_cancel`** functions.
   Build them as thin wrappers around the API calls — no business logic
   in the bridge.
7. **`_lookup_by_tg_message` + reply-to-msg routing** extension for
   risk_gated → approve.
8. **Smoke tests.** Run the four stop conditions end-to-end against a
   real bot token (or a stdlib mock Bot API on port 8767, matching the
   mvp1 smoke approach).
9. **CHANGELOG.md v0.5.0-mvp2 entry** — DRAFT entry already exists from
   spec-commit time; flip it from `[x] Spec drafted` to `[x] Built` and
   replace the "What's planned" section with shipped "What ships /
   Pre-impl spike findings / Verified / Not in this release" sections,
   matching the v0.5.0-mvp1 style.

## Not in this release (deferred)

- **`/status` snapshot** (live sessions, pending decisions, today cost,
  free slots) — Phase 2 in PRD.
- **Inline keyboard `[Yes] [No]`** for DECISIONS — Phase 2.
- **`/snooze <decision_id> <duration>`** — Phase 2; wires up
  `notification_log.snoozed_until`.
- **`/schedule "<cron>" <prompt>`** — Phase 2; creates `ops_schedules`
  rows from TG.
- **Reply-to-task-complete starts follow-up `/run`** — Phase 2.
- **Topic-per-task in TG supergroup**, **inline-button risk approval**,
  **voice-memo → STT**, **multi-operator allowlist**, **mobile dashboard
  surface** — Phase 3 vision; no commitment.

## Compat note with v0.6.0 (already shipped)

v0.6.0 added `cost_source` to `ops_tasks` + `sessions`, the SkillLauncher
panel, and cap-rewiring to api_pool only. mvp2's `created_at_source`
adds to the same `ops_tasks` table without collision — different column,
different code paths. The only shared surface is `POST /api/tasks`'s
body validator, which v0.6.0 extended to accept `cost_source`-affecting
fields; mvp2 adds `created_at_source` next to it. Read the current
validator before adding the field.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.0 merge).
Estimated effort: 3-4h per the PRD's mvp2 estimate (revised from
earlier 1-2h given `/cancel` endpoint must be added from scratch).
