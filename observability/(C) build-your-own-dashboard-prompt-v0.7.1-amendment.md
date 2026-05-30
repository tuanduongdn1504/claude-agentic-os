# v0.7.1 Amendment — "accept review output as-is" (third resolution for review escalations)

Apply on top of current `main` HEAD (post v0.7.0, commit `233acf8`). A
**patch** over v0.7.0 — it extends the existing review-escalation resolution
with one new action; no new architectural concept (contrast v0.7.0, which
added the reviewer execution path and earned the minor bump). Cadence
consistent with the v0.6.x patch series.

**Why now:** chosen as the natural v0.7.0 follow-up. v0.7.0 escalates a
review-failed task to `awaiting_approval`, where `/approve` **re-runs** it and
`/cancel` **drops** it. There is no way to say *"the reviewer was wrong — the
output is fine, just mark it done."* The first time a reviewer false-negatives
(flags real work as NOT_VERIFIED), the operator is forced to either waste a
re-run or lose the work. v0.7.1 adds **accept**: complete the task with the
output the implementer already produced, without re-running.

**Verified against current `main` HEAD** (read before writing — Rule 8):
- **The make-or-break gotcha:** the v0.7.0 escalation branch in `run_once`
  (dispatcher.py, the `else:` after the NOT_VERIFIED-retry arm) does
  `task_tracker.update_task(task["id"], status="awaiting_approval",
  started_at=None)` and **discards `summary_head`** (the implementer's
  output). `complete_task(output_summary=...)` is called ONLY on the VERIFIED
  arm. So today an escalated task has `output_summary = NULL`. v0.7.1 must
  preserve it at escalation or "accept" has nothing to accept.
- Resolution endpoints to mirror: `POST /api/tasks/{id}/approve` (tasks.py:165),
  `/cancel` (198), `/rerun` (236).
- Telegram: `_handle_approve` (bridge:619) → `POST /api/tasks/{id}/approve?
  source=telegram`; verb regex `_CMD_WITH_ID_RE` (bridge:494) alternation
  `(answer|reply|approve|cancel|snooze|yes|no)`; the review-escalation
  notification text (bridge:320-321) already offers `/approve` + `/cancel`.
- `task_tracker.update_task` already takes arbitrary ops_tasks columns as
  kwargs (the gate calls it with `review_verdict=`, `review_feedback=`,
  `status=`, `review_count=`), so `output_summary=`, `session_id=`,
  `duration_ms=` work the same way.
- An `awaiting_approval` task can arrive 3 ways: risk gate (run_once ~600),
  autonomy gate (~650), **review escalation (v0.7.0)**. Only the review path
  sets `review_verdict` non-NULL — that is the discriminator for "accept".

---

## The gotcha fix (v0.7.1 depends on it) — preserve output at escalation

In `run_once`'s escalation `else:` branch, change the `update_task` call to
also store what the implementer produced, so it survives into
`awaiting_approval`:

```python
task_tracker.update_task(
    task["id"], status="awaiting_approval", started_at=None,
    output_summary=summary_head,
    session_id=result.get("session_id"),
    duration_ms=elapsed_ms,
)
```

Side benefit: the escalated TaskBoard card can now SHOW the implementer's
output, so the operator can read it before deciding accept / approve / cancel
(today the card shows nothing). This is a strict improvement even before the
accept action exists.

## Schema delta — `db.py`

One additive column (idempotent, mirrors the v0.7.0 migration block):

```python
# v0.7.1 — accept-review-output-as-is.
_migrate_add_column(conn, "ops_tasks", "review_overridden", "INTEGER NOT NULL DEFAULT 0")
```

`review_overridden` — `0` = normal; `1` = the task was completed by an operator
**accepting it despite a non-VERIFIED review**. The `review_verdict` is
preserved as-is (e.g., `NOT_VERIFIED`) so the audit trail shows WHY it was
escalated; `review_overridden=1` records that the operator overrode it. A
clean `VERIFIED` completion keeps `review_overridden=0`.

## API delta — new endpoint, mirror approve/cancel

**`POST /api/tasks/{task_id}/accept`** — new, alongside tasks.py:165/198.

1. **Guard — review escalations only.** Load the task. Require
   `status='awaiting_approval'` AND `review_verdict IS NOT NULL`. Otherwise:
   - Not `awaiting_approval` → `400 task #{id} is {status}, not awaiting approval`.
   - `awaiting_approval` but `review_verdict IS NULL` (a risk-gated or
     autonomy-gated task that never ran) → `400 task #{id} is not a review
     escalation — there is no output to accept; use /approve to run it`.
   This is the key correctness boundary: accept marks a task **done with
   existing output**; a task that never ran has none.
2. **Complete with the preserved output.** Set `status='done'`,
   `review_overridden=1`, `completed_at=now`. Keep the existing
   `output_summary` / `session_id` / `duration_ms` (preserved at escalation).
   Do NOT clear `review_verdict` / `review_feedback`.
3. **Audit.** INSERT `activities` row `event_type='task_review_overridden'`,
   `detail={task_id, prior_verdict, review_feedback_first_120}`,
   `source` from the `?source=` query param (matches the approve/cancel audit
   pattern). This is the load-bearing audit event — an operator overrode an
   independent reviewer; it must be traceable.
4. **No dispatcher trigger, no agent spawn.** Accept is a pure state
   transition. Unlike `/approve` (re-dispatch) it does not re-run, and unlike
   v0.7.0 review it spawns no `claude -p`. **Zero added cost** — accept is the
   cheap resolution.
5. Return the updated task.

## Telegram delta — `/accept <id>`

Mirror `_handle_approve` (bridge:619):
- **Extend `_CMD_WITH_ID_RE`** (bridge:494) verb alternation:
  `(answer|reply|approve|cancel|snooze|yes|no|accept)`.
- **`_handle_accept(chat_id, message_id, task_id)`** → `POST /api/tasks/{id}/
  accept?source=telegram`. On 200 → `✅ task #{id} accepted as-is · output kept,
  review overridden`. On 400 → reply the API error verbatim (`_md_safe`).
- **Reply-to-review-notification.** The review-escalation notification is a
  `review_gated` event; its reply router already maps to approve/cancel. Add
  `/accept` (and bare `accept`) to that router, like the v0.6.5 `/yes` `/no`
  strict-match shortcut.
- **Update the escalation notification text** (bridge:320-321) to offer all
  three: `Reply /accept {id} to keep the output, /approve {id} to re-run with
  this feedback, or /cancel {id} to drop.`
- **`/help`** — one line: `/accept <id> — keep a review-flagged task's output
  as-is (no re-run).`

## UI delta — `TaskBoard`

- The review-escalated card gains a **third button: "Accept output"** beside
  the existing Approve (re-run) and Cancel (drop). Calls `POST /accept`.
  Tooltip: "Mark done with the current output — the reviewer flagged it but
  you've judged it fine."
- The card now renders the preserved `output_summary` (available after the
  gotcha fix) so the operator can read what they're accepting.
- After accept, the card shows a distinct **done badge: "✓ done · accepted
  over review"** (driven by `status='done' && review_overridden=1`), visually
  separate from a clean "✓ VERIFIED" so the override is never invisible.
- Optional: `GET /api/system/dispatcher` rollup could add
  `tasks_review_overridden_today`; defer unless the count is wanted.

## Cost note

Accept spawns **no agent** and triggers **no dispatch** — pure state
transition. It is the zero-cost resolution, in deliberate contrast to
`/approve` (one more implementer run) and the v0.7.0 review itself (the
reviewer child). No interaction with the v0.2.0 cap / v0.6.7 budget.

## Stop conditions

1. **Output preserved at escalation (the gotcha fix).** A task that escalates
   (NOT_VERIFIED after retry, or MANUAL_VERIFY_REQUIRED) now has a non-NULL
   `output_summary` = the implementer's tail. Regression-guards the discard
   bug.
2. **Accept happy path.** Escalated task (`awaiting_approval`,
   `review_verdict='NOT_VERIFIED'`). `POST /accept` → `status='done'`,
   `review_overridden=1`, `output_summary` unchanged, `review_verdict` still
   `NOT_VERIFIED`. **No `claude -p` child spawned** (assert via PID markers /
   no new cost).
3. **Accept rejects non-review awaiting_approval.** A risk-gated task
   (`awaiting_approval`, `review_verdict IS NULL`, never ran) → `POST /accept`
   → 400 "not a review escalation … use /approve". State unchanged.
4. **Accept rejects wrong state.** `pending` / `running` / `done` task →
   `POST /accept` → 400. State unchanged.
5. **Telegram `/accept <id>`.** Seeds an escalated task; `/accept N` →
   `✅ task #N accepted as-is`; row flips to `done`, `review_overridden=1`.
6. **Reply-to-notification accept.** Reply to a ❓ review-escalation ping with
   `/accept` (and bare `accept`) → same outcome via the review_gated router.
   A longer reply like "accept but note X" falls through to verbatim (v0.6.5
   strict-match semantics).
7. **Approve still re-runs (v0.7.0 unchanged).** `/approve` on an escalated
   task re-dispatches with feedback prepended; `review_overridden` stays 0.
8. **Cancel still drops.**
9. **Audit.** Accept writes `activities` `event_type='task_review_overridden'`
   with `prior_verdict` + `source`.
10. **UI.** Escalated card shows Accept/Approve/Cancel + the preserved output;
    after accept, the "done · accepted over review" badge renders, distinct
    from VERIFIED.
11. **Backward compat.** VERIFIED completion path unchanged (`review_overridden`
    stays 0). v0.6.x + v0.7.0 surfaces (budgets, /yes /no, review gate,
    risk gate) all still pass their smoke tests. Risk-gated `/approve` is
    untouched.
12. **Idempotent migration.** `review_overridden` adds cleanly to an existing
    v0.7.0 DB; existing rows read 0.

## Order of operations

1. **Schema migration** (1 column) — verify idempotent on the live v0.7.0 DB.
2. **Escalation output-preservation** in `run_once` (the gotcha fix) — smoke
   that an escalated task now carries `output_summary`.
3. **`POST /api/tasks/{id}/accept`** endpoint + guards + audit.
4. **Telegram** `/accept` (regex + handler + reply-router + notification text +
   /help).
5. **TaskBoard** Accept button + preserved-output render + done-over-review
   badge.
6. **Smoke** — `scripts/dev/smoke_v0_7_1.py`, all 12 stop conditions, match the
   `smoke_v0_7_0.py` shape (real server + stubbed reviewer to drive an
   escalation, then exercise accept). + Playwright: the Accept button →
   `/accept` → badge.
7. **CHANGELOG `v0.7.1`** flipped DRAFT → shipped (patch over v0.7.0).

## Not in this release (deferred)

- **Bulk accept** (accept all currently-escalated tasks) — wait for evidence
  operators have enough escalations to want it.
- **Auto-accept policy** (e.g., "auto-accept NOT_VERIFIED from skill X after N
  manual accepts") — speculative; needs accept-rate data first. The
  `task_review_overridden` audit rows are the data source for designing it
  later.
- **`tasks_review_overridden_today` rollup + AttentionBar tile** — defer unless
  the count proves useful.
- **Re-review on accept** (a second reviewer to confirm the override) — defeats
  the purpose (accept = trust the human over the reviewer).

## Estimate

~2-2.5h:
- Schema + escalation output-preservation: ~30 min
- Accept endpoint + guards + audit: ~30-40 min
- Telegram /accept (mirror approve): ~25 min
- TaskBoard button + output render + badge: ~40 min
- Smoke (12 conditions) + Playwright: ~30-40 min
- CHANGELOG: ~10 min

Smaller than v0.7.0 — one new endpoint + one Telegram verb + one button + one
column, all mirroring existing shapes. The only subtle part is the escalation
output-preservation (without it, accept is hollow) and the review-only guard.

## Compat note with v0.6.0 – v0.7.0

No collisions:
- v0.7.0 review gate: accept is a THIRD resolution beside approve/cancel for
  the same `awaiting_approval` escalation state. The VERIFIED / NOT_VERIFIED /
  MANUAL_VERIFY_REQUIRED verdict logic is untouched; v0.7.1 only changes what
  the escalation `update_task` stores (adds output_summary) and adds the accept
  path.
- v0.5.0-mvp2 `/approve` `/cancel`: untouched; `/accept` is a sibling verb in
  the same regex + the same `?source=telegram` audit pattern.
- v0.6.5 `/yes` `/no`: the reply-router strict-match for `/accept` follows the
  same shortcut shape.
- v0.6.7 budgets / v0.2.0 cap: accept spawns nothing, so spend is unaffected.
- Risk gate (run_once ~600) + autonomy gate (~650): their `awaiting_approval`
  tasks have `review_verdict IS NULL`, so the accept guard correctly refuses
  them — risk/autonomy approvals keep using `/approve` exactly as before.

---

End of amendment. Apply against current `main` HEAD (post-v0.7.0, `233acf8`).
Quality bar matches v0.7.0 — but it is a patch: one column, one endpoint, one
verb, one button, plus the load-bearing escalation output-preservation that
makes accept meaningful. Accept never re-runs, never spawns, always audits.
