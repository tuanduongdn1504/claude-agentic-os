# v0.6.7 Amendment — per-skill daily cost budgets

Apply on top of current `main` HEAD (post v0.6.6). Multi-surface
release — schema + dispatcher + API + UI + Telegram outbound — but
each surface mirrors an existing pattern from v0.2.0 (global cap)
and v0.6.0 (api_pool-only semantics + SkillLauncher panel). Lowest-
risk way to expand spending discipline from the global cap to a
per-skill axis.

**Numbered `v0.6.7`** — patch over v0.6.6. Cadence consistent with
v0.6.1 – v0.6.6. Alternative `v0.7.0` defensible (multi-surface
feature with schema + dispatcher + UI changes); recommendation
`v0.6.7` since the pattern is a mechanical extension of v0.2.0's
existing cap shape rather than a new architectural concept.

**Verified against current `main` HEAD**:
- `skills` table already has `preset_json`, `last_launched_at`,
  `launch_count` (v0.6.0) + `daily_budget_usd` slot NOT yet present.
- `MISSION_CONTROL_DAILY_COST_CAP_USD` reads `api_pool` cost only
  (v0.6.0 cap rewrite) — per-skill budgets reuse the same
  `cost_source='api_pool'` predicate so semantics stay consistent.
- `PATCH /api/skills/{name}/autonomy` exists as the column-update
  precedent; v0.6.7 follows the same shape with
  `PATCH /api/skills/{name}/budget`.
- `GET /api/skills` already returns `avg_cost_usd_30d` (v0.6.0);
  add `daily_budget_usd` + `today_cost_usd` siblings.
- `AttentionBar` already surfaces `cost_capped` issue (v0.2.0);
  add `skill_budget_capped` as a new issue type.
- `SkillLauncher` panel + inline preset editor (v0.6.0) are the UI
  surfaces extended.
- Telegram `notification_log` UNIQUE constraint
  `(event_type, event_key, chat_id)` (v0.3.0) gives natural dedupe
  for the new `skill_budget_exceeded` event type.
- No collision with v0.6.1 – v0.6.6 surfaces.

---

## Why this release

The global `MISSION_CONTROL_DAILY_COST_CAP_USD` is a single number
that protects the operator's total wallet. It cannot distinguish
between an expensive deep-research skill burning $4/day legitimately
and a misconfigured morning-brief skill burning $4/day by accident.
When the operator hits the cap, they don't know which skill
ate the budget.

Per-skill budgets add a second axis: each `user_invocable=true`
skill optionally gets a `daily_budget_usd`. Dispatcher refuses to
claim tasks for skills that have hit their budget. Operator gets a
clear signal — "morning-brief blocked at $0.85 today, deep-research
still has $3.20 of its $5.00 budget" — instead of opaque global cap
exhaustion.

Composes cleanly with the global cap: both apply, whichever
triggers first wins. Skill budget is the per-skill axis, global cap
is the safety floor.

## Schema delta

One additive column on the `skills` table through the existing
`_migrate_add_column` helper.

| Table | Column | Type | Default |
|---|---|---|---|
| `skills` | `daily_budget_usd` | REAL | NULL |

Semantics:
- `NULL` → no budget; skill runs without per-skill cap (current
  behaviour for every existing skill).
- `0` → blocked; any task assignment to this skill refuses to
  claim. Useful for "disable this skill temporarily without
  removing it from the registry."
- `> 0` → cap. Dispatcher refuses to claim when today's
  `SUM(cost_usd)` for tasks assigned to this skill exceeds the
  value.

Idempotent. Existing rows get `NULL` on migration — no behaviour
change until operator sets a budget.

## API delta

### `PATCH /api/skills/{name}/budget` — new endpoint

Body: `{daily_budget_usd: float | null}`. Validation:
- `null` clears the budget.
- `0` is valid (blocks all claims).
- Negative values rejected with 400.
- Non-numeric rejected with 400 via Pydantic.

Returns the updated skill row.

Pattern mirrors `PATCH /api/skills/{name}/autonomy` — column-update
endpoint, no JSON blob.

### `GET /api/skills` — augmented response

Each skill row gains two fields next to existing `avg_cost_usd_30d`:

- `daily_budget_usd: float | null` — the stored budget value.
- `today_cost_usd: float` — computed:
  ```sql
  SELECT COALESCE(SUM(cost_usd), 0)
  FROM ops_tasks
  WHERE assigned_skill = ?
    AND cost_source = 'api_pool'
    AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
  ```

`today_cost_usd` is always returned (zero when no spend today). The
UI uses both values to render the budget state.

### `GET /api/system/dispatcher` — augmented response

Add two health-summary fields next to the existing
`cost_capped` / `back_pressure` block:

- `skills_with_budget: int` — count of skills where
  `daily_budget_usd IS NOT NULL`.
- `skills_at_budget: int` — count where
  `today_cost_usd >= daily_budget_usd`.

Powers the AttentionBar issue surfacing and DispatcherStrip badge
without requiring the UI to fan out one query per skill.

## Dispatcher delta — `dispatcher.py`

### Pre-claim budget check

Inside `run_once()`, after the existing global-cap check and before
the per-task autonomy check, add a per-skill budget check:

```python
if task.assigned_skill:
    skill = _get_skill(conn, task.assigned_skill)
    if skill and skill["daily_budget_usd"] is not None:
        today_spend = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)
            FROM ops_tasks
            WHERE assigned_skill = ?
              AND cost_source = 'api_pool'
              AND DATE(completed_at, 'localtime')
                  = DATE('now', 'localtime')
            """,
            (task.assigned_skill,),
        ).fetchone()[0]
        if today_spend >= skill["daily_budget_usd"]:
            conn.execute(
                """INSERT INTO activities
                   (event_type, detail, metadata)
                   VALUES ('dispatcher_skill_budget_capped', ?, ?)""",
                (
                    f"skill={task.assigned_skill} "
                    f"today=${today_spend:.4f} "
                    f"budget=${skill['daily_budget_usd']:.2f}",
                    json.dumps({
                        "task_id": task.id,
                        "skill": task.assigned_skill,
                        "today_cost_usd": today_spend,
                        "daily_budget_usd": skill["daily_budget_usd"],
                    }),
                ),
            )
            continue  # skip this task; try the next pending one
```

Post-hoc check (matches v0.2.0 global cap shape): does NOT
preemptively reject tasks that would push over the budget; refuses
ONLY when the existing spend already meets or exceeds the budget.
Operator can over-spend by at most one task's cost beyond the
budget — same trade-off as the global cap.

Order of checks in `run_once()` (top to bottom):
1. Emergency stop → early return
2. Sweep stale PIDs
3. Back-pressure → defer
4. Hard risk gate → promote to awaiting_approval
5. Global daily cost cap → refuse
6. **Per-skill budget check (NEW)** → refuse
7. Per-task autonomy → run / promote

Skill budget check sits between global cap (safety floor) and
per-task autonomy (skill-by-skill discipline).

### AttentionBar issue type — `skill_budget_capped`

Add a new issue producer in the `/api/attention` aggregator. When
any skill has `today_cost_usd >= daily_budget_usd`, emit:

```json
{
  "severity": "warning",
  "type": "skill_budget_capped",
  "title": "{N} skill(s) at daily budget",
  "detail": "{first_skill_name} blocked at ${today_cost} / ${budget}",
  "count": N
}
```

Severity `warning` (yellow), NOT `error` (red) — the global cap
firing is `error`; per-skill is operator-tuneable and expected to
trigger more often.

## UI delta

### `SkillLauncher` card — budget state visuals

Each card already shows `avg cost (30d)`. Add a second numeric:

- **No budget set** (`daily_budget_usd IS NULL`): hide the budget
  line. Card looks identical to v0.6.6.
- **Budget set, `today_cost_usd < 0.8 × budget`**: show
  `today $X.XX / $Y.YY` in dim mono. No visual emphasis.
- **Budget set, `0.8 × budget <= today_cost_usd < budget`**: show
  same string in amber mono.
- **Budget set, `today_cost_usd >= budget`**: show in red mono.
  Launch button disabled with tooltip `"Daily budget reached
  ($X.XX / $Y.YY) — resets at midnight local"`.

Implementation: derive state in render code from
`daily_budget_usd` + `today_cost_usd` returned by `GET /api/skills`.
No new endpoint needed.

### `SkillLauncher` inline preset editor — budget field

The inline editor (v0.6.0) currently has 9 preset fields. Add a
10th: `daily_budget_usd` (number input, optional, allow empty for
NULL). Save → `PATCH /api/skills/{name}/budget` separately from
the existing `PATCH /api/skills/{name}/preset` call.

Field placement: at the bottom of the editor in its own section
labelled "Budget (optional)" — visually separated from preset
launch-defaults to signal they're different concerns (preset =
launch defaults, budget = spending policy).

### `AttentionBar` rendering — new issue type

Render `skill_budget_capped` issues with the existing amber-warning
style. Sort below `cost_capped` (error severity) and above
generic info-level issues.

## Telegram outbound delta — `telegram_bridge.py`

### New event type — `skill_budget_exceeded`

In `_outbound_tick`, add a fourth poll source after
v0.5.0-mvp1's task_complete + risk_gated queries:

```python
# v0.6.7 — first-fire push per skill per day for budget exhaustion.
skill_caps = _http_get_json(f"{API_BASE}/api/skills")
for skill in skill_caps:
    budget = skill.get("daily_budget_usd")
    today = skill.get("today_cost_usd", 0)
    if budget is None or today < budget:
        continue
    event_key = f"{skill['name']}:{_today_local_date()}"
    if _already_notified("skill_budget_exceeded", event_key):
        continue
    text = _format_skill_budget_exceeded(skill)
    msg_id = _send_message(text)
    _record_notify("skill_budget_exceeded", event_key, msg_id)
```

`event_key` includes today's local date so the dedupe fires once
per skill per day — next day's first claim that exceeds budget
triggers a fresh notification.

### `_format_skill_budget_exceeded(skill)` — new helper

```
🔒 *Skill budget reached*

{skill_name} hit ${today_cost_usd:.2f} / ${daily_budget_usd:.2f}
today ({HH:MM GMT+7}).

Dispatcher refusing new claims for this skill until midnight local.
Other skills + manual /run tasks continue normally.
```

Markdown V1 (matches mvp1/v0.6.2 style). `_md_safe` for skill name
(operator-controllable string).

## Stop conditions

1. **Migration idempotent.** Fresh DB → `init_db()` + two
   `apply_migrations()` calls in succession; `skills.daily_budget_usd`
   exists once. Existing rows have NULL.
2. **`PATCH .../budget` accepts valid payload.** Set budget = 5.0
   on a skill via POST → row updates → `GET /api/skills` reflects
   the new value.
3. **`PATCH .../budget` rejects invalid.** Negative number → 400.
   Non-numeric string → 400. `null` → clears budget (sets to NULL).
4. **Pre-claim refusal — at budget.** Skill X has budget $1.00 and
   today_cost = $1.00. Queue a new task assigned to X. Dispatcher
   tick → task stays in `pending` → `activities` has a
   `dispatcher_skill_budget_capped` row → AttentionBar shows
   `skill_budget_capped` issue. Other skills' tasks continue to
   dispatch normally.
5. **Pre-claim refusal — over budget.** Skill X has budget $1.00
   and today_cost = $1.50 (operator lowered budget mid-day). Same
   refusal behaviour.
6. **NULL budget = unlimited.** Skill Y has `daily_budget_usd =
   NULL`. Tasks dispatch regardless of any `today_cost` value.
7. **Zero budget = blocked.** Skill Z has `daily_budget_usd = 0`.
   First task assigned to Z refuses claim immediately (today_cost
   = 0 >= 0).
8. **Global cap still fires.** With one skill at $5 budget +
   $4 already spent today + global cap at $8 + total today
   = $7.50: the next task pushing total to $8.20 should be
   refused by GLOBAL cap (not skill budget). Order of checks
   verified — global cap fires before per-skill.
9. **Telegram push fires once per skill per day.** Skill X hits
   budget at 14:32. TG message arrives within 30s next tick. Same
   tick again at 14:33 → dedupe, no re-send. Manually re-test at
   00:01 next day → fresh notification (new event_key).
10. **AttentionBar count accuracy.** Two skills at budget today →
    AttentionBar shows "2 skills at daily budget". Lower one
    skill's budget such that it's no longer over → count drops to
    1 within the next poll.
11. **UI card states.** Test all 3 budget tiers on the
    SkillLauncher card: < 0.8 → dim, 0.8–1.0 → amber, >= 1.0 →
    red + disabled Launch. Card renders correctly at each tier.
12. **Backward compat.** Skills without budget render identically
    to v0.6.6. All v0.6.0 SkillLauncher behaviour (launch, preset
    edit, autonomy controls) unchanged.
13. **`cc doctor`.** Existing checks pass. Optional new check
    (defer or include): warn if any skill has
    `daily_budget_usd < 0.01` (likely typo).

## Order of operations

1. **Schema migration.** Add `skills.daily_budget_usd REAL NULL` via
   `_migrate_add_column`.
2. **`PATCH /api/skills/{name}/budget`** endpoint. Validation +
   row update + return.
3. **`GET /api/skills`** augmentation — add `daily_budget_usd` +
   `today_cost_usd` per row. Verify the SQL aggregate is fast
   (single query per skill should suffice).
4. **`GET /api/system/dispatcher`** augmentation —
   `skills_with_budget` + `skills_at_budget` counts.
5. **Dispatcher pre-claim check** in `run_once()`. Insert between
   global cap and per-task autonomy.
6. **`/api/attention` aggregator** — add `skill_budget_capped`
   issue producer.
7. **SkillLauncher card** — 3-tier visual state, Launch button
   disable at red tier.
8. **Inline preset editor** — Budget field in its own section,
   separate PATCH call.
9. **AttentionBar** rendering for the new issue type.
10. **Telegram outbound** — new event type + format helper +
    dedupe by `{skill_name}:{today_local_date}`.
11. **Smoke tests** — all 13 stop conditions end-to-end. Seed
    fixtures: 3 skills (one no budget, one under budget, one over
    budget), real FastAPI server, dispatcher tick driver.
12. **Playwright** — add spec for the 3-tier UI states (stop 11)
    plus the disable-Launch behaviour (stop 4 UI side).
13. **CHANGELOG `v0.6.7` entry** flipped from DRAFT to shipped.
    Match v0.6.0 / v0.6.6 multi-surface style.

## Not in this release (deferred)

- **Per-account-per-skill budgets** — v0.6.3 multi-account adds
  the `account_id` axis. Combining "skill X has $5/day for account_a
  but $20/day for account_b" is a v0.7+ matrix concept. MVP: skill-
  level only, summing across all accounts.
- **Soft warning + hard cap pair** (two budgets per skill) — MVP
  uses single hard cap + 80% amber threshold. Two-tier policies
  add config surface; defer until usage demands.
- **Budget templates / classes** (e.g., "deep-research skills get
  $5, brief skills get $0.50") — operator can set per-skill
  manually. Templates are syntactic sugar; defer.
- **Budget time windows other than daily** (weekly, monthly) — MVP
  daily only. Most operator spend patterns are daily-scoped.
- **Hard reject preemptively** (refuse task that would push OVER
  budget, not at-budget) — would need cost estimation per task;
  speculative. MVP matches v0.2.0 cap shape (post-hoc).
- **`cc setup skill-budgets` wizard** — operator can set budgets
  through SkillLauncher's inline editor. Wizard for bulk setup is
  v0.7+ if signals support.

## Estimate

~3-4h:
- Schema + endpoint + augmented GETs: ~45 min
- Dispatcher check + activities log + AttentionBar: ~45 min
- SkillLauncher UI 3-tier visuals + disable + editor field: ~60-75 min
- Telegram outbound new event type + format helper: ~30 min
- Smoke tests (13 conditions) + Playwright (2-3 specs): ~45-60 min
- CHANGELOG flip: ~10 min

Largest scope since v0.6.0 because of multi-surface (schema +
dispatcher + UI + Telegram + Playwright). All surfaces mirror
existing patterns so risk stays low; effort is in the breadth.

## Compat note with v0.6.0 – v0.6.6

No collisions:
- v0.6.0 SkillLauncher panel + preset_json + cost_source + global
  cap rewrite all sit ALONGSIDE per-skill budgets. The card
  surface extends without changing the existing render path.
- v0.5.0-mvp2 + v0.6.4 + v0.6.5 Telegram bridge work untouched
  except the new outbound `skill_budget_exceeded` event slots
  naturally into `_outbound_tick`.
- v0.6.1 `/status` could optionally surface `skills_at_budget`
  count in the Today line — defer to v0.7+ as a separate sub-spec.
- v0.6.2 `/snooze` semantics untouched.
- v0.6.3 multi-account `account_id` stays orthogonal; budget
  sums across accounts for the skill (per-account budgets is the
  deferred v0.7+ matrix).
- v0.6.6 `?embed=1` mode renders the new budget states
  identically — UI conditional render doesn't change.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.6).
Quality bar matches v0.6.0 — multi-surface but each surface
mirrors a proven pattern. Estimate ~3-4h.
