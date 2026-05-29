# v0.7.0 Amendment — Adversarial review gate (reviewer subagent + verdict → awaiting_approval)

Apply on top of current `main` HEAD (post v0.6.7, commit `026f6bf`). The
first MINOR bump in the project — every prior release since v0.2.0 was a
patch. Justified: this adds a **new execution path** (a second `claude -p`
child per reviewed task) + **new task-lifecycle semantics** (a verdict gate
between "ran ok" and "done") + schema. It is the milestone the whole v0.6.x
arc deferred to "v0.7+". Numbering it `v0.7.0` also retires the recurring
patch-vs-minor question for this class of change.

**Provenance:** distilled from the Storm Bear wiki — Pattern #76 (Adversarial
Subagent Review Architecture, from `gotalab/cc-sdd` v61) + Pattern #74
(EARS-Format Requirements) + Pattern #21 (SDD Methodology). cc-sdd's
`/kiro-impl` dispatches an Implementer + a Reviewer subagent, then a
fresh-evidence completion gate returning `VERIFIED / NOT_VERIFIED /
MANUAL_VERIFY_REQUIRED`. This amendment ports that gate onto Command Center's
dispatcher, mapping the verdict onto the existing `awaiting_approval` state.

**Verified against current `main` HEAD** (read before writing — Rule 8):
- Dispatcher spawns `claude -p` children via `_run_classic` (line 271) /
  `_run_stream` (line 303). A reviewer is a second child of the same shape.
- Completion block at `run_once` lines 674-681: `if result.get("ok"):
  task_tracker.complete_task(...)`. This is the insertion point.
- `awaiting_approval` is an existing state with existing resolution paths:
  the risk gate (lines 600-612) and autonomy gate (lines 648-658) both set
  it; operators resolve it via the dashboard + Telegram `/approve` `/cancel`
  (v0.5.0-mvp2). The review gate REUSES this — no new decision wiring.
- `skills.autonomy_level TEXT DEFAULT 'review'` (db.py:329, `auto|review|
  manual`) + `skills.daily_budget_usd REAL` (db.py:404) show the per-skill
  column + `_migrate_add_column` idempotent pattern to mirror.
- `ops_tasks`: `status` (db.py:217), `execution_mode` (221, `classic|
  stream`), `output_summary` (233). `_skill_autonomy` (dispatcher:178) /
  `_skill_budget_state` (191) show the per-skill lookup-helper pattern.
- `resolve_model` (167), `_build_env` (80), `_mark_child_pid` (96),
  `TASK_TIMEOUT_SECONDS`, `MAX_CONCURRENT` all reusable for the reviewer.
- No collision with v0.6.0–v0.6.7 surfaces.

---

## Why this release

Today a task is marked `done` on the strength of the agent's **own**
`output_summary` (run_once line 677 — last 20 lines of its stdout). The agent
grades its own homework. There is no independent check that the work actually
happened, that files were really written, that the stated intent was met. For
an autonomous dispatcher that the operator drives from their phone, that is
the single biggest trust gap.

v0.7.0 closes it: for skills opted into `review_mode`, after the implementer
exits successfully the dispatcher spawns an **independent reviewer** `claude
-p` child — same working directory + env, so it can re-read the files the
implementer touched (fresh evidence, not self-report) — and emits a verdict.
`VERIFIED` completes the task as today; anything else routes to the existing
`awaiting_approval` flow with the reviewer's reason attached, so the operator
decides via paths they already have (dashboard approve/reject, Telegram
`/approve` `/cancel`).

It is opt-in per skill (off by default) precisely because it ~doubles the
per-task API cost — see the Cost section.

## Schema delta — `db.py`

Five additive columns via `_migrate_add_column` (all idempotent, all
backward-compatible — existing rows read as the documented defaults). Add a
new migration block after the v0.6.7 `daily_budget_usd` line (db.py:404):

```python
# v0.7.0 — adversarial review gate.
_migrate_add_column(conn, "skills",    "review_mode",      "INTEGER NOT NULL DEFAULT 0")
_migrate_add_column(conn, "ops_tasks", "success_criteria", "TEXT")
_migrate_add_column(conn, "ops_tasks", "review_verdict",   "TEXT")
_migrate_add_column(conn, "ops_tasks", "review_count",     "INTEGER NOT NULL DEFAULT 0")
_migrate_add_column(conn, "ops_tasks", "review_feedback",  "TEXT")
```

Semantics:
- `skills.review_mode` — `0` = off (unchanged behaviour), `1` = review every
  successful run of a task assigned to this skill. Per-skill, mirrors
  `autonomy_level` / `daily_budget_usd`.
- `ops_tasks.success_criteria` — OPTIONAL operator-supplied criteria (EARS-ish
  encouraged: "WHEN <trigger>, THE system SHALL <response>"). Free text — the
  reviewer LLM reads it; **no parser** (Rule 5: don't use code where the model
  judges). `NULL` = reviewer judges against title + description only.
- `ops_tasks.review_verdict` — last verdict: `VERIFIED` / `NOT_VERIFIED` /
  `MANUAL_VERIFY_REQUIRED` / `NULL` (not yet reviewed). For UI + audit.
- `ops_tasks.review_count` — retry counter; caps auto-retry at 1.
- `ops_tasks.review_feedback` — the reviewer's one-line reason on a non-VERIFIED
  verdict. Fed into the retry prompt + shown in the UI.

## Dispatcher delta — `dispatcher.py`

### `_skill_review_mode(skill_name) -> bool` — new helper

Mirror `_skill_autonomy` (line 178). `SELECT review_mode FROM skills WHERE
name = ?`; return `bool(row and row["review_mode"])`. `None`/missing skill →
`False`.

### `_run_review(task, impl_result) -> dict` — new

A second `claude -p` child, same shape as `_run_classic` but with the review
prompt and a verdict-marker scan. Returns `{"verdict": str, "reason": str |
None}`.

1. Build the reviewer prompt:

   ```
   You are an INDEPENDENT reviewer. Do NOT trust the implementer's
   self-report — verify against the actual workspace.

   TASK: {task title}
   INTENT: {task description or "(none)"}
   SUCCESS CRITERIA: {task.success_criteria or
       "(none specified — judge against INTENT)"}

   IMPLEMENTER OUTPUT (tail of its run):
   {impl_result stdout tail, reuse the same summary_head slice}

   Independently check whether the intent + criteria were ACTUALLY met.
   Re-read any files involved. Be skeptical: look for claimed-but-absent
   work, partial completion, hallucinated success, unaddressed criteria.

   End with EXACTLY one line, nothing after it:
   VERDICT: VERIFIED
   VERDICT: NOT_VERIFIED — <one-line reason>
   VERDICT: MANUAL_VERIFY_REQUIRED — <one-line reason>
   ```

2. Spawn `claude -p <review_prompt>` with `_build_env()`, `start_new_session=
   True`, same cwd, `--model` from `resolve_model(task)` unless
   `CC_REVIEW_MODEL` env is set (lets the operator point review at a cheaper
   tier — see Cost). Reuse `_mark_child_pid(pid, task["id"], "review")` so the
   reviewer shows in the PID sweep + emergency-stop argv verification.
3. Cap with `TASK_TIMEOUT_SECONDS` exactly like `_run_classic`.
4. Parse stdout for the LAST line matching
   `^\s*VERDICT:\s*(VERIFIED|NOT_VERIFIED|MANUAL_VERIFY_REQUIRED)\s*(?:—|-{1,2})?\s*(.*)$`
   (case-sensitive verdict tokens). Return that verdict + captured reason.
5. **Fail-safe (Rule 12 — never silently complete):** if the reviewer
   times out, crashes (`returncode != 0`), or emits no parseable `VERDICT:`
   line → return `{"verdict": "MANUAL_VERIFY_REQUIRED", "reason": "reviewer
   produced no parseable verdict"}`. An unverifiable review escalates to a
   human; it never auto-completes.

### `run_once` completion block — gate insertion

Replace the current success branch (lines 674-681). dry-run tasks and
non-review skills keep the exact current behaviour:

```python
if result.get("ok"):
    review_on = (
        not task.get("dry_run")
        and _skill_review_mode(task.get("assigned_skill"))
    )
    if not review_on:
        task_tracker.complete_task(task["id"], output_summary=summary_head,
                                   session_id=result.get("session_id"),
                                   duration_ms=elapsed_ms)
        stats["succeeded"] += 1
    else:
        verdict = _run_review(task, result)
        v, reason = verdict["verdict"], verdict.get("reason")
        task_tracker.update_task(task["id"], review_verdict=v,
                                 review_feedback=reason)
        task_tracker.log_activity(
            "task_review_verdict",
            f"task_id={task['id']} verdict={v}",
            metadata={"task_id": task["id"], "verdict": v, "reason": reason},
        )

        if v == "VERIFIED":
            task_tracker.complete_task(task["id"], output_summary=summary_head,
                                       session_id=result.get("session_id"),
                                       duration_ms=elapsed_ms)
            stats["review_verified"] += 1

        elif v == "NOT_VERIFIED" and (task.get("review_count") or 0) == 0:
            # One automatic retry. Re-queue; next sweep re-runs with feedback.
            task_tracker.update_task(task["id"], status="pending",
                                     started_at=None, review_count=1)
            stats["review_retried"] += 1

        else:
            # NOT_VERIFIED after a retry, OR MANUAL_VERIFY_REQUIRED → human.
            task_tracker.update_task(task["id"], status="awaiting_approval",
                                     started_at=None)
            stats["review_escalated"] += 1
            task_tracker.log_activity(
                "task_review_escalated",
                f"task_id={task['id']} verdict={v} reason={reason}",
                metadata={"task_id": task["id"], "verdict": v},
            )
```

New `stats` keys to add to the dict at line 535: `review_verified`,
`review_retried`, `review_escalated`.

### Retry prompt — `_build_prompt` extension (line 260)

When `task.get("review_count")` > 0 and `review_feedback` is set, append the
prior reviewer feedback so the retry addresses it:

```python
if (task.get("review_count") or 0) > 0 and task.get("review_feedback"):
    parts.append("")
    parts.append("PRIOR REVIEW REJECTED THIS WORK — address it:")
    parts.append(str(task["review_feedback"]))
```

This mirrors the v0.6.4 follow-up composition. The retry re-runs the SAME
task (same id) with feedback prepended; it does not create a new task.

### Resolution semantics (reuse, don't build)

A review-escalated task sits in `awaiting_approval` exactly like a risk-gated
one. **Approving re-dispatches it** (existing approve semantics → status back
to claimable, runs again with `review_feedback` in the prompt); **cancelling
drops it** (existing `/cancel`). No new endpoint, no new Telegram verb. The
operator sees the reviewer's reason via `review_feedback` (surfaced in the UI
+ optionally the approval notification).

## API delta

Minimal — extend existing shapes, mirror existing endpoints.

- **`PATCH /api/skills/{name}/review`** — new, mirrors the v0.6.7
  `/api/skills/{name}/budget` + the autonomy endpoint. Body `{"review_mode":
  0|1}`. Validates the skill exists (404 otherwise). Returns the updated skill.
- **`GET /api/skills`** + skill detail (skills.py) — add `review_mode` to the
  `SELECT` and response (alongside `daily_budget_usd` from v0.6.7).
- **`POST /api/tasks`** + **`GET /api/tasks/{id}`** — accept/return
  `success_criteria` (writable on create), and return `review_verdict`,
  `review_count`, `review_feedback` (read-only, dispatcher-owned).
- **`GET /api/system/dispatcher`** — add a `review` rollup:
  `{skills_with_review, tasks_awaiting_review_approval}` (count of
  `awaiting_approval` rows whose `review_verdict` is non-NULL).
- **`/api/attention`** — emit `review_escalated` (severity `warning`, reusing
  the v0.6.7 AttentionBar warning tone) when any task is in
  `awaiting_approval` with a non-NULL `review_verdict`. Distinct from the
  risk-gated approval issue so the operator knows WHY approval is needed.

## UI delta

- **SkillLauncher inline editor** — a `review_mode` toggle in its own row,
  with its own `PATCH /api/skills/{name}/review` call (separate from the
  v0.6.7 budget field, separate from preset_json). Label: "Adversarial review
  — verify every successful run with an independent agent (≈2× cost)."
- **TaskBoard card** — a verdict badge when `review_verdict` is set: ✓
  `VERIFIED` (green), ✗ `NOT_VERIFIED` (red), ? `MANUAL_VERIFY_REQUIRED`
  (amber). On an escalated card, show `review_feedback` as the reason line.
- **Task create form** — an optional `success_criteria` textarea (placeholder
  shows the EARS shape). Free text; no client-side validation.
- **AttentionBar** — fold `review_escalated` into the existing warning-tone
  count from v0.6.7. Clicking scrolls to the awaiting-approval tasks.

## Telegram delta — `telegram_bridge.py`

**Near-zero by design.** Review escalations land in `awaiting_approval`,
already handled by `/approve` / `/cancel` (v0.5.0-mvp2). Two small touches:

- The approval-needed outbound notification (the existing `risk_gated` /
  approval path) should include `review_feedback` when present so the operator
  reads the reviewer's reason on their phone before `/approve`-ing. If the
  outbound formatter distinguishes event types, add a `review_gated` variant
  that renders `❓ task #N needs review approval · {verdict} · {reason}`;
  otherwise reuse the approval notification with the reason appended.
- `/help` — one line: "Tasks under adversarial review that fail verification
  land in approval — `/approve <id>` to re-run, `/cancel <id>` to drop."

No new parsers, no new verbs.

## Cost — the honest tension (ties to the v0.2.0/v0.6.7 cost-discipline work)

`review_mode` spawns a **second `claude -p` per reviewed task** → roughly
**doubles** that task's API cost. This interacts directly with the existing
cost-discipline stack:

- The reviewer's spend IS `api_pool` and counts toward the global cap
  (v0.2.0) and the per-skill budget (v0.6.7) — but **post-hoc**: the
  pre-claim gates (run_once guards 2 + 4) fire BEFORE the implementer runs, so
  the review cost lands after the gate. A reviewed task can push spend past
  the cap by up to the reviewer's cost — the SAME "over-spend by at most one
  task" shape the budget already documents. Consistent, not new.
- Mitigation: `review_mode` is **opt-in per skill, off by default**. Turn it
  on for high-stakes skills (deploys, writes to shared state), leave it off
  for cheap/idempotent ones.
- Mitigation: `CC_REVIEW_MODEL` lets the reviewer run on a cheaper tier (a
  review is a narrower task than implementation). Defaults to the task's model
  if unset. Per-skill review-model + auto-cheap-tier routing is deferred.
- Throughput: the reviewer is a second blocking child, so a reviewed task
  holds its `MAX_CONCURRENT` slot ~2× longer. Acceptable under back-pressure;
  noted, not optimised.

## Stop conditions

1. **VERIFIED happy path.** Skill with `review_mode=1`. `/run` a task that
   genuinely succeeds. Implementer exits ok → reviewer runs → emits `VERDICT:
   VERIFIED` → task `done`, `review_verdict='VERIFIED'`. Card shows ✓.
2. **NOT_VERIFIED → one auto-retry.** Seed a task whose implementer claims
   success but doesn't meet criteria (e.g., "write /tmp/x.txt" but the file is
   absent). First review → `NOT_VERIFIED`; task returns to `pending`,
   `review_count=1`, `review_feedback` stored. Next sweep re-runs with
   "PRIOR REVIEW REJECTED…" in the prompt.
3. **NOT_VERIFIED after retry → escalate.** Same task fails review twice →
   `awaiting_approval`, `review_count=1`, `review_verdict='NOT_VERIFIED'`,
   `review_feedback` set. `/approve` re-runs it; `/cancel` drops it.
4. **MANUAL_VERIFY_REQUIRED → immediate escalation (no auto-retry).** Reviewer
   emits `MANUAL_VERIFY_REQUIRED` → task straight to `awaiting_approval`,
   `review_count` unchanged at 0.
5. **Reviewer no-verdict fail-safe.** Stub the reviewer to print no `VERDICT:`
   line → treated as `MANUAL_VERIFY_REQUIRED`; task escalates, never
   auto-completes.
6. **Reviewer timeout/crash fail-safe.** Reviewer exits non-zero or times out
   → `MANUAL_VERIFY_REQUIRED`; task escalates.
7. **review_mode off = unchanged.** Skill with `review_mode=0` → completion is
   byte-identical to pre-v0.7.0 (`complete_task`, no reviewer spawned, no cost
   doubling). Verify no second child appears in PID markers.
8. **dry_run skips review.** A `dry_run` task on a `review_mode=1` skill →
   completes without spawning a reviewer (nothing executed to verify).
9. **success_criteria reaches the reviewer.** Task with `success_criteria` set
   → the reviewer prompt contains it verbatim. Task without → prompt shows
   "(none specified — judge against INTENT)".
10. **Cost attribution.** A reviewed task's total api_pool cost reflects BOTH
    children and counts toward the per-skill budget (v0.6.7) + global cap
    (v0.2.0). Confirm a reviewed task moves the skill's `today_cost_usd` by
    ~2× a non-reviewed equivalent.
11. **PID sweep + emergency-stop.** A running reviewer has a PID marker
    (`mode="review"`); `_sweep_stale_pids` + emergency-stop argv verification
    cover it exactly like implementer children.
12. **Backward compat.** v0.6.0–v0.6.7 surfaces unaffected: SkillLauncher
    launch, cost_source, `/status`, `/snooze`, multi-account, follow-up chain,
    `/yes` `/no`, embed mode, per-skill budgets all still pass their smoke
    tests.

## Order of operations

1. **Schema migration** (5 columns) — verify idempotency on an existing DB +
   a fresh DB.
2. **`_skill_review_mode` + `_run_review`** — unit-smoke `_run_review`'s
   verdict parser against fixtures (each verdict token, missing line, crash,
   `—` vs `-` vs `--` separators) with a stubbed subprocess before wiring.
3. **`run_once` completion-block gate** + new stats keys + `_build_prompt`
   retry extension.
4. **API**: `PATCH /api/skills/{name}/review`, `GET /api/skills` +
   task shapes + dispatcher rollup + `/api/attention` issue.
5. **UI**: editor toggle → TaskBoard verdict badge → success_criteria field →
   AttentionBar fold-in.
6. **Telegram**: `review_feedback` in the approval notification + `/help`.
7. **Smoke** — `scripts/dev/smoke_v0_7_0.py`, all 12 stop conditions, real
   FastAPI server + a stubbed reviewer (`_run_review` monkeypatched to return
   each verdict). Match the `smoke_v0_6_7.py` shape (35 assertions). +
   Playwright spec for the editor toggle + verdict badge.
8. **CHANGELOG `v0.7.0`** flipped DRAFT → shipped. This is a MINOR bump —
   note it explicitly as the first since v0.x began patch-only at v0.2.0.

## Not in this release (deferred)

- **"Accept output as-is" action.** Approving a review-escalated task RE-RUNS
  it (existing approve semantics). Accepting the rejected output without
  re-running needs a distinct action/endpoint — defer to v0.7.1 until usage
  shows operators want it.
- **Per-task `review_mode` override.** v0.7.0 triggers review per-SKILL only.
  Per-task force-review / force-skip → defer.
- **Multi-reviewer panel / N-vote majority** (Pattern #76's deeper form —
  several skeptics, kill on majority-refute). MVP is single-reviewer. Defer
  until single-reviewer catch-rate is measured.
- **Per-skill review-model + auto-cheap-tier routing.** MVP: global
  `CC_REVIEW_MODEL` env or task model. Per-skill review model → defer.
- **EARS parser / structured criteria validation.** Criteria stay free text
  the reviewer reads. No code parser (Rule 5). Defer indefinitely unless a
  machine-checkable subset proves valuable.
- **Reviewer cost pre-gating.** Refusing to START a reviewable task when the
  reviewer's projected cost would breach the cap needs cost estimation;
  speculative. MVP matches the existing post-hoc cap shape.
- **Review lineage / `parent_task_id`.** Retries reuse the same task id; no
  separate lineage rows. Defer with the v0.6.4 `parent_task_id` deferral.

## Estimate

~4-6h — the largest in the arc (first multi-surface release that also adds an
execution path):
- Schema + helpers + `_run_review` + verdict parser: ~75-90 min
- `run_once` gate + retry prompt + stats: ~45 min
- API (review endpoint + shapes + rollup + attention): ~45 min
- UI (toggle + badge + criteria field + attention): ~75-90 min
- Telegram (notification reason + help): ~20 min
- Smoke (12 conditions, stubbed reviewer) + Playwright: ~60-75 min
- CHANGELOG: ~10 min

Risk is concentrated in `_run_review` (a new subprocess path) and the
completion-block gate (touches the hottest line in the dispatcher). Everything
else mirrors an existing pattern.

## Compat note with v0.6.0 – v0.6.7

No collisions:
- v0.2.0 global cap + v0.6.7 per-skill budget: the reviewer's spend flows
  through both, post-hoc (see Cost). No gate logic changes.
- v0.6.0 SkillLauncher + cost_source: untouched; the editor gains a toggle
  beside the budget field.
- v0.5.0-mvp2 `/approve` `/cancel`: the resolution path for escalated reviews
  — reused verbatim, not modified.
- v0.6.1 `/status`: could surface `tasks_awaiting_review_approval` in a later
  release; out of scope here.
- v0.6.2 `/snooze`, v0.6.3 multi-account, v0.6.4 follow-up chain, v0.6.5
  `/yes` `/no`, v0.6.6 embed mode: all untouched.
- The cross-account budget-summing invariant (v0.6.7 doc, commit `026f6bf`)
  is unaffected — review adds cost to the same api_pool sum, still summed
  across accounts.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.7, `026f6bf`).
Quality bar matches v0.6.7 — multi-surface but every surface mirrors an
existing pattern; the one genuinely new thing (the reviewer child + verdict
gate) fails safe to a human on every ambiguity. Opt-in, off by default,
cost-honest.
