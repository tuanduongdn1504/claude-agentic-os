# v0.6.4 Amendment — Telegram reply-to-task-complete → follow-up `/run`

Apply on top of current `main` HEAD (post v0.6.3). Phase 2 feature from
the Telegram Remote Trigger PRD. Bridge-only — single file change to
`telegram_bridge.py` plus a CHANGELOG entry. No schema changes, no new
endpoints, no breaking changes.

**Numbered `v0.6.4`** — patch over v0.6.3. Same Telegram-bridge
subsystem; cadence consistent with v0.6.1 / v0.6.2 / v0.6.3.

**Verified against current `main` HEAD**:
- `_lookup_by_tg_message` (mvp2) already routes
  `decision` / `inbox` / `risk_gated` event types — extend with
  `task_complete`.
- `notification_log` already stores `event_type='task_complete'` rows
  (mvp1 outbound). `telegram_message_id` is recorded for reply lookup.
- `POST /api/tasks` already accepts `created_at_source='telegram'`
  (mvp2). No body shape change.
- `GET /api/tasks/{id}` exists and returns `title`, `output_summary`,
  `status`, etc.
- `_handle_message`'s reply-to-msg branch is the established extension
  point — same shape as mvp2's `risk_gated → /approve` routing.
- No collision with v0.6.0–v0.6.3 surfaces.

---

## Why this release

Today the operator can launch one task from Telegram (`/run`) but
chaining workflows requires retyping context. After `✅ task #42 done
· Saved to docs/release-notes-v2.4.md`, a follow-up like "now write
the PR description from those notes" means opening the dashboard or
typing `/run` with the full context from scratch.

v0.6.4 makes that natural: reply to the task-complete notification
with the follow-up instruction. Bridge fetches the previous task's
title + output summary, composes a new task with that context
prepended, dispatches as if the operator had typed `/run` with the
fuller prompt.

Closes Journey 1 in a deeper way — the PRD scenario "EAS build wait"
becomes "EAS build wait → chain three tasks from phone while waiting"
instead of "one task per build wait."

## Bridge delta — `telegram_bridge.py`

Single file. No new modules, no new dependencies.

### `_lookup_by_tg_message` — extend event-type recognition

Current function returns `(event_type, event_key)` tuples for
`decision`, `inbox`, `risk_gated`. The query already returns
`event_type` directly from `notification_log` — no SQL change needed.
Add `'task_complete'` to the recognized values in the calling
routing logic (see next section). The function itself works for any
event_type stored in `notification_log`.

### `_handle_message` — reply-to-msg routing for `task_complete`

In the existing reply-to-msg branch, after the `risk_gated → approve`
case, add the `task_complete` case:

```python
if event_type == 'task_complete':
    return _handle_task_followup(chat_id, int(event_id), body)
```

Other event types continue routing as today.

### `_handle_task_followup(chat_id, prev_task_id, body)` — new

1. **Validate reply body.** If `body` is None or empty after strip,
   reply `cannot create follow-up from empty reply` and return. (TG
   normally filters empty messages but reply-to-msg edge cases exist.)
2. **Fetch previous task.** `GET /api/tasks/{prev_task_id}` →
   `prev`. On 404 → reply `task #{prev_task_id} not found —
   notification may reference a deleted task` and return.
3. **Refuse chaining on failed tasks.** If `prev['status'] in
   ('failed', 'cancelled')`, reply
   `task #{prev_task_id} {status} — chain on a successful task or
   queue a fresh /run` and return. Failed tasks have no useful
   `output_summary` to chain on, and silently chaining would
   misleadingly imply continuity.
4. **Compose new task description.** Use this template (in order,
   line-separated):

   ```
   Follow-up to task #{prev_id} ("{prev_title_truncated_80}"):

   {prev_output_summary_truncated_300_or_'no summary'}

   ---

   {operator_reply}
   ```

   Truncation rules:
   - Previous title: first 80 chars, ellipsis suffix if longer.
   - Previous output summary: first 300 chars, ellipsis suffix if
     longer. If `output_summary` is None or empty (classic dispatched
     task that didn't emit one), substitute literal `(no output
     summary recorded)`.
   - Operator reply: not truncated locally — the existing 3000-char
     `_md_safe` cap in send-side will handle TG limits if echoed back
     in any error.

5. **Compose new task title.** Use `Follow-up: {operator_reply[:60]}`
   so the TaskBoard card surface is readable. Truncate at 60 chars
   with ellipsis if longer.
6. **POST `/api/tasks`** with body:

   ```python
   {
       "title": new_title,
       "description": composed_description,
       "execution_mode": "classic",   # follow-ups are fire-and-forget
       "priority": "normal",
       "quadrant": "do",
       "risk_level": "low",
       "requires_approval": False,
       "dry_run": False,
       "created_at_source": "telegram",
   }
   ```

   Do NOT inherit `assigned_skill` or `model` from `prev`. Let the
   existing dispatcher skill_router pick based on the new prompt —
   the operator's reply may want a different skill than the original
   task.

7. **Audit log.** INSERT into `activities` with
   `event_type='task_followup_created'`, `detail={prev_task_id,
   new_task_id, prev_title, operator_reply_first_60}`,
   `source='telegram'`. Matches mvp2's FR19 audit-everything-from-
   Telegram pattern for state-changing commands.
8. **Trigger dispatcher inline** via `POST /api/dispatcher/trigger`
   so the new task starts within ~1s instead of waiting for the next
   120s heartbeat. Same pattern as v0.6.0's `SkillLauncher.launch`.
9. **Reply.** `✅ task #{new_id} queued · follow-up to #{prev_id}`.
   Use `_now_local_str()` not needed here — reply is a state
   confirmation, not a snapshot.

### `/help` text — extend with the new pattern

Append a line to the existing help card:

```
Reply to a ✅ task-complete notification with a follow-up instruction
to chain a new task with the previous task's output as context.
```

## Context-size considerations

Telegram messages cap at 4096 chars; the bridge already truncates
at 3000 to leave room for headers. The composed description above is
NOT subject to that cap because it goes to `POST /api/tasks` (the
dashboard API), not back to Telegram. The 300-char truncation on
`prev_output_summary` is a UX choice (keep TaskBoard card readable)
rather than a transport constraint. Operators who want richer context
chain explicitly via `/run` with their own copy-paste from the
dashboard.

## Multi-hop chains

Reply to a follow-up's completion ping → triggers another follow-up.
The new task's description carries the immediate parent (e.g., `#43`),
which itself was a follow-up of `#42`. No special multi-hop
detection — the description chain breaks at one level deep on each
reply. Surfacing the full chain is a v0.7+ UI concern (TaskBoard
lineage view) and out of scope.

## Stop conditions

1. **Happy path.** Send `/run write hello to /tmp/hello.txt`, wait
   for `✅ task #N done` notification, reply to it with "now write
   goodbye to /tmp/goodbye.txt". Bridge replies
   `✅ task #M queued · follow-up to #N`. TaskBoard shows new task
   with title `Follow-up: now write goodbye to /tmp/goodbye.txt` and
   description containing the previous task's title + summary +
   `---` separator + operator's reply.
2. **Empty summary.** Manually clear `output_summary` on a `done`
   task. Reply to its notification. New task's description contains
   `(no output summary recorded)` in the summary slot; everything
   else renders correctly.
3. **Failed task refusal.** Reply to a `❌ task #N failed`
   notification. Bridge replies
   `task #N failed — chain on a successful task or queue a fresh
   /run`. No new task created. No `activities` row.
4. **Deleted task.** Manually `DELETE FROM ops_tasks WHERE id=N`,
   then reply to the (now-orphan) notification. Bridge replies
   `task #N not found — notification may reference a deleted task`.
5. **Truncation rules.** Seed a task with 500-char output_summary
   and 100-char title. Reply to its notification. New task's
   description shows title truncated at 80 chars + ellipsis, summary
   truncated at 300 chars + ellipsis.
6. **Multi-hop.** Run stop condition 1, then reply to the new task's
   completion notification with a third instruction. Verify the
   third task references `#M` (the immediate parent), not `#N` (the
   grandparent).
7. **Audit log.** Each successful follow-up writes an `activities`
   row with `event_type='task_followup_created'`, `source='telegram'`,
   `detail` containing `prev_task_id`, `new_task_id`,
   `prev_title`, `operator_reply_first_60`.
8. **Dispatcher triggered inline.** New task transitions to
   `running` within 2 seconds — not 120 seconds. Verifies the
   `/api/dispatcher/trigger` call landed.
9. **Backward compat.** Reply to a decision notification continues
   to answer the decision; reply to a RISK-GATED continues to
   approve; reply to an inbox continues to reply. None of those
   paths trigger task creation.
10. **`/help` updated.** `/help` reply includes the new follow-up
    line.

## Order of operations

1. **Add `_handle_task_followup`** function. Smoke against a fixture
   `prev` dict (no HTTP) to verify composition logic before wiring.
2. **Extend `_handle_message` reply-to-msg branch** with the
   `task_complete` case.
3. **Add `/help` line.**
4. **Smoke tests** — 10 stop conditions end-to-end. Pattern match
   v0.6.2 / v0.6.3's `scripts/dev/smoke_v0_6_2.py` shape: real
   FastAPI server, seeded fixtures (one done task with summary, one
   failed task, one deleted), stubbed `_send_message` and
   `_tg`-equivalents.
5. **CHANGELOG `v0.6.4` entry** flipped from DRAFT to shipped.
   Match v0.6.3 style.

## Not in this release (deferred)

- **`parent_task_id` column** on `ops_tasks` for proper lineage
  tracking. Context-in-description is sufficient for MVP; lineage
  becomes useful only if TaskBoard gets a chain visualization (v0.7+
  UI work).
- **Inherit `assigned_skill` from previous task** as an opt-in. The
  current design lets `skill_router.py` re-pick based on the new
  prompt. Operators who want skill continuity can still use
  `SkillLauncher` for the follow-up. Defer until usage signals
  demand it.
- **Multi-hop chain visualization** in either Telegram or dashboard
  UI. The description trails are sufficient for MVP.
- **Quote-reply detection** that captures Telegram's `quote.text`
  field separately from the operator's new text. Useful for chained
  context but adds complexity; defer until concrete examples
  demand it.

## Estimate

~1.5-2h:
- `_handle_task_followup` + composition logic: ~30-45 min
- Routing wire + `/help` extension: ~10 min
- Smoke tests (10 stop conditions, including failed-task refusal and
  multi-hop): ~30-45 min
- CHANGELOG flip: ~10 min

Larger than v0.6.2 (~1h) because the composition logic + failure
handling + multi-hop verification need more careful smoke coverage,
but smaller than the v0.6.1 / v0.6.3 builds because no schema
migration and no operator-config docs.

## Compat note with v0.6.0 – v0.6.3

No collisions:
- v0.6.0 SkillLauncher and `cost_source` surfaces untouched.
- v0.5.0-mvp2 `created_at_source` reused (new tasks tagged
  `'telegram'`).
- v0.6.1 `/status` snapshot untouched.
- v0.6.2 `/snooze` and `_already_notified` semantics untouched.
- v0.6.3 multi-account `account_id` stamping happens in
  `sync_sessions.py` on session-side, not task-side; new tasks
  created here will inherit account tagging through their dispatched
  session when it lands.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.3).
Quality bar matches v0.6.2 / v0.6.3 — terse, bridge-only, stdlib-
only, mobile-readable, deterministic audit trail.
