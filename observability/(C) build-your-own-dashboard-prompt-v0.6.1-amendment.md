# v0.6.1 Amendment — Telegram `/status` snapshot

Apply on top of current `main` HEAD (post v0.5.0-mvp2). Phase 2 feature
from the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Bridge-only — single
file change to `telegram_bridge.py` plus a CHANGELOG entry. No schema
changes, no new endpoints, no breaking changes.

**Numbered `v0.6.1`** — patch-level over v0.6.0 because the change is
small, additive, and confined to the Telegram bridge subsystem.
Alternative: `v0.7.0` (next minor — more semver-correct for a new
feature). Recommendation: `v0.6.1`. Not `v0.5.0-mvp3` — Phase 2 features
per the PRD are not paired-MVP, so the `mvp` suffix would mislead a
reader scanning the version history.

**Verified against current `main` HEAD**:
- `_CMD_WITH_ID_RE` and `_CMD_RUN_RE` already exist (mvp2,
  `telegram_bridge.py`). Adding `_CMD_STATUS_RE` as a third pattern is
  consistent with the established split.
- All data sources are existing GET endpoints — no API changes.
- `_send_message` already handles Markdown V1 formatting safely.
- `_md_safe` exists for user content sanitisation (not needed here —
  `/status` template is fully operator-controlled).

---

## Why this release

mvp1 + mvp2 shipped the read/write Telegram loop. Operator can launch
tasks, answer decisions, and approve/cancel risk-gated work — all from
phone. But there's no glanceable answer to the most common operator
question between actions: **"What's my agent doing right now?"**

Today, checking that means: open dashboard on laptop, scan KpiRow +
DispatcherStrip + AttentionBar + live sessions. Five places to look,
one of them not on phone. `/status` collapses this into a single
Telegram reply.

This is the first feature in the corpus designed for **passive
awareness**, not active control. Operator types `/status`, glances,
puts the phone away. No state change, no decision required. The
expected use pattern is multiple times per day — morning check, lunch
break, post-meeting, before bed.

## Bridge delta

Single file: `command-centre/scripts/telegram_bridge.py`. No new modules,
no new dependencies.

### `_CMD_STATUS_RE` — new pattern

Add as a third sibling to `_CMD_WITH_ID_RE` and `_CMD_RUN_RE`:

```python
_CMD_STATUS_RE = re.compile(r"^/status\s*$", re.IGNORECASE)
```

No args, no body. Strict whole-message match.

### `_handle_message` — fourth routing branch

Insert before the `_CMD_WITH_ID_RE` block (so `/status` is checked
early, fails fast for malformed `/status <extra>`):

```python
if _CMD_STATUS_RE.match(text):
    return _handle_status(chat_id)
```

If `/status` is followed by any args, the regex won't match and the
message falls through to the help branch (or silent ignore). That's the
desired behaviour — `/status foo` is operator confusion, not a valid
command.

### `_handle_status(chat_id)` — new

1. Fetch from 5 endpoints in sequence (serial is fine — total round
   trip is <1s for a single operator's local install):
   - `GET /api/system/dispatcher` → `{running, max_concurrent,
     free_slots, today_cost_api_pool_usd, today_cost_max_sub_usd,
     today_cost_unknown_usd, daily_cost_cap_usd, cost_capped,
     back_pressure, hard_risk_gate}`
   - `GET /api/decisions?status=pending` → list, count via `len()`
   - `GET /api/sessions/live` → list, count via `len()`,
     `max(now - started_at)` for longest-running age
   - `GET /api/tasks?status=done` and `?status=failed` filtered to
     today via local-time date bucket — counts only (could be one
     call returning all today's tasks, grouped client-side; pick
     whichever is simpler against the current `/api/tasks` query
     surface)
   - `GET /api/system/state` → `emergency_stop` flag (key='emergency_stop')

2. Each call wrapped in try/except inside `_handle_status`. A failed
   endpoint shows `?` for its metric line — never crashes the whole
   response, never silently drops a section.

3. Format via `_format_status(...)`.

4. `_send_message(formatted_text)` via existing helper.

### `_format_status(...)` — new

Returns the multi-line Telegram message body. Mobile-readable: 7-10
lines steady state, no horizontal scroll on 390 px width. Markdown V1
formatting (existing bridge convention; matches mvp1's
`_format_task_complete` style).

Template (concrete values for illustration — real call substitutes):

```
*STATUS*  _14:32 GMT+7_

⚙️  Dispatcher  *2/3* running · *1* free
💰 Today        api *$3.42* · max *$1.18* · cap $10.00
📨 Decisions    *2* pending
🟢 Live         *1* session · 4m active
✅ Tasks today  5 done · 1 failed · 0 risk-gated
```

Conditional alert lines appended only when the state is active —
hidden from steady-state responses to keep the message tight:

```
🛑 *Emergency stop ON* — dispatcher refusing new work
⚠️  *Cost cap reached* (api_pool $10.00 / $10.00)
⚠️  *Back-pressure active* — 3/3 slots full
```

Formatting rules:
- Time + timezone from bridge process local time. Format
  `HH:MM TZ` — match the same `Asia/Ho_Chi_Minh` GMT+7 convention used
  in the v0.5.0-mvp1 fix (commit `a505d24`). Use a single
  `_now_local_str()` helper or inline.
- Cost values: 2 decimal places, prefix `$`. Render as `—` when zero
  for the whole day.
- Pending decisions: omit the line entirely when count is zero (no
  noise on idle days). Same rule for live sessions.
- Tasks today: always show (even if all zeros) — operators want to
  see "nothing happened today" explicitly, not infer it from absence.
- Bold (`*...*`) only for highlighted numerics + alert headers. Italic
  (`_..._`) for the timestamp. Don't over-format — mobile renderers
  vary.

### Failure handling

If `_http_get_json` raises (network error, timeout, server 500):
- Catch per endpoint.
- Substitute `?` for that section's primary metric.
- Append a small footer line: `_some metrics unavailable —
  see logs_`. Don't enumerate which endpoint failed (privacy: error
  messages may include URL paths the operator doesn't want in TG).
- Bridge logs the actual error to its stderr (existing behaviour).

If ALL endpoints fail (server down):
- Send: `⚠️ status check failed — dashboard server may be down.
  Try \`cc status\` or \`cc restart\`.`
- Do not crash the long-poll loop.

## Audit logging

`/status` is read-only. No `activities` row required — the operator
asking for a status snapshot is not a state change. This is a
deliberate departure from mvp2's audit-everything-from-telegram
pattern: that was for state-changing operations (FR19). `/status` is
glanceable telemetry, not an operator action.

## Stop conditions

1. **Happy path.** Send `/status` from the configured Telegram chat.
   Within 5 seconds, receive a multi-line reply with at least the
   four steady-state lines (Dispatcher, Today, Tasks today, plus
   either Decisions or Live or both if active).
2. **Conditional alerts.** Trip the cost cap (mock or wait for natural
   trip). Re-send `/status`. The reply now includes the `⚠️ Cost cap
   reached` line. Reset cap. `/status` reply no longer includes that
   line.
3. **Emergency stop.** `POST /api/system/emergency-stop`. Send
   `/status`. Reply includes `🛑 Emergency stop ON`. Clear stop. Reply
   no longer includes it.
4. **Partial-failure resilience.** Stop the FastAPI server. Send
   `/status`. Bridge replies with `⚠️ status check failed`. Bridge
   long-poll loop continues — verify by sending `/help` after server
   restart, expect normal reply.
5. **Mobile-screen render check.** Visually inspect the steady-state
   reply on iPhone-size viewport (390 × 844 px). All lines fit
   without horizontal scroll. Numbers right-aligned via spaces (the
   table-ish look in the template).
6. **Backward compat.** Existing `/answer`, `/reply`, `/run`,
   `/approve`, `/cancel`, `/help`, `/start` continue to work. Send
   each at least once post-deploy.

## Order of operations

1. **Add `_CMD_STATUS_RE`** pattern next to existing regexes.
2. **Add `_handle_status` function** with the 5 endpoint calls and
   per-call try/except. Test against a running server before wiring
   into `_handle_message`.
3. **Add `_format_status`** function. Smoke against a fixture dict
   first (no HTTP) to verify the rendered string matches the template.
4. **Wire `_handle_message`** — fourth routing branch.
5. **Smoke tests** — run the 6 stop conditions end-to-end against a
   real bot token, or a stdlib mock Bot API on port 8767 (matching
   the mvp1/mvp2 smoke approach).
6. **CHANGELOG `v0.6.1` entry** following the v0.5.0-mvp2 style:
   What ships / Verified / Operator flow.

## Not in this release (deferred — other Phase 2 features)

- **`/snooze <decision_id> <duration>`** — wires up the existing
  `notification_log.snoozed_until` column. ~1h. Highest-ROI Phase 2
  candidate after `/status`; spec separately if signal supports it.
- **Inline keyboard `[Yes] [No]` for binary DECISIONS** — needs a
  "what's binary?" heuristic. Defer until concrete examples accumulate
  in `ops_decisions`.
- **`/schedule "<cron>" <prompt>`** — creates `ops_schedules` rows from
  TG. Defer; dashboard `ScheduleComposer` already handles this well
  and the TG-only use case is rare.
- **Reply-to-task-complete starts follow-up `/run`** — chained
  workflows. Defer until usage signals demand it.

## Estimate

~1-2h:
- Pattern + handler + format helper: ~30-45 min
- Smoke tests (6 stop conditions, including mobile-render check):
  ~30-45 min
- CHANGELOG flip + manual phone-screen test: ~15 min

Lower than v0.6.0 / mvp2 because scope is intentionally narrow:
single file, no schema, no new endpoints, no UI surface beyond the TG
reply itself.

## Compat note with v0.6.0 and v0.5.0-mvp2

No collisions:
- v0.6.0's `cost_source` column on `ops_tasks` / `sessions` is read
  via the existing dispatcher endpoint — `today_cost_api_pool_usd` /
  `today_cost_max_sub_usd` are exactly the data `/status` surfaces.
- v0.5.0-mvp2's `created_at_source` column is not displayed (would
  add noise). Future: a Phase 3 `/status verbose` could break down
  TG vs dashboard provenance, but defer.
- mvp2's `_CMD_WITH_ID_RE` / `_CMD_RUN_RE` split is preserved.
  `_CMD_STATUS_RE` is a third sibling.

---

End of amendment. Apply against current `main` HEAD (post-v0.5.0-mvp2
merge). Quality bar: match existing bridge code style — stdlib only, no
`python-telegram-bot` dependency, mobile-readable output.
