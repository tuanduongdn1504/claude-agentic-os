# Changelog

## v0.7.3 — tasks run in the project, not the install dir (hotfix)

Bug A, found while validating v0.7.2: the dispatcher spawned claude with **no
`cwd`**, so children inherited the launchd agent's `WorkingDirectory`
(`~/.command-centre`) instead of `CC_PROJECT_ROOT`. The project root was set in
the env but **never used** — so the operator's chosen project was ignored and
tasks ran (and wrote files) in the install dir. The v0.7.2 BANANA task landed
at `~/.command-centre/tmp/cc_phone_test.txt` instead of the project.

Fix: a `_task_cwd()` helper returns `CC_PROJECT_ROOT` when it's an existing dir
(else `None` + a loud stderr warning — a stale root like a deleted worktree
degrades to the install dir rather than crashing every task), passed as `cwd=`
to all three child `Popen` calls (`_run_classic` / `_run_stream` /
`_run_review`).

Verified: deployed dispatcher resolves cwd to the project root; smokes green
(v0.7.0 69/69, v0.7.1 43/43 — subprocess stubbed).

## v0.7.2 — dispatcher actually executes (hotfix)

The autonomous dispatcher had **never successfully run a real task** — every
feature was built against *stubbed* claude in smoke tests, so the broken live
invocation went unnoticed until the first real `/run` from a phone returned
`rc=1`. Three compounding bugs, all in the dispatch→claude→execute path:

1. **Wrong claude binary under launchd.** launchd agents don't load the login
   profile, so the plist PATH resolved a **broken global** `/usr/local/bin/claude`
   ("native binary not installed") instead of the operator's working nvm claude.
   Fix: `install.sh` resolves the operator's claude via their login shell and
   bakes its absolute path into the mission-control plist as `CLAUDE_CLI_OVERRIDE`,
   and prepends its bin dir to the plist PATH so claude's `node` resolves.
2. **Missing `--verbose`.** `_run_stream` ran `claude -p … --output-format
   stream-json`, which the CLI **rejects** without `--verbose`. Every task
   defaults to stream mode, so every task died here. Fix: add `--verbose`.
3. **Headless permission wall.** `claude -p` denies tool permissions by default,
   so a task would exit 0 having written nothing (`permission_denials:[Write]`),
   silently no-op'ing while reporting success. Fix: `--dangerously-skip-permissions`
   on `_run_classic` / `_run_stream` / `_run_review` — the operator's pre-dispatch
   gates (risk gate / autonomy / approval) are the safety layer; once a task is
   cleared to run, the agent gets full tools.

Validated: the exact corrected stream invocation now writes the file
(`exit 0`, `is_error:false`, `permission_denials:[]`). Both smoke suites still
green (v0.7.0 69/69, v0.7.1 43/43 — they stub the subprocess, so unaffected).

Also folds in `ui/package-lock.json` regenerated for **arm64** — the prior lock
pinned rollup's native dep to x64, breaking `npm run build` (hence UI deploys)
on Apple Silicon.

Deferred (v0.7.3+): the dispatcher still marks success on `returncode` alone;
it should also inspect the stream `result` line's `is_error` so a genuine
mid-task error can't read as "done". Lower priority now that #3 removes the
silent-no-op path.

## v0.7.1 — accept review output as-is

> Spec: `observability/(C) build-your-own-dashboard-prompt-v0.7.1-amendment.md`.
> Shipped on branch `claude/v0.7.1`. Built on `main` HEAD `191cb78` (v0.7.0
> `233acf8` + the v0.7.1 DRAFT spec).

A **patch** over v0.7.0 (no new architectural concept — extends the existing
escalation resolution). v0.7.0 escalates a review-failed task to
`awaiting_approval`, where `/approve` **re-runs** and `/cancel` **drops** — but
there's no way to say *"the reviewer was wrong, the output is fine, just mark
it done."* The first reviewer false-negative forces a wasted re-run or lost
work. v0.7.1 adds **accept**: complete the task with the output the
implementer already produced, no re-run.

### The dependency it fixes (the load-bearing part)

v0.7.0's escalation branch **discarded the implementer's `output_summary`**
(only the VERIFIED arm called `complete_task`), so an escalated task had
`output_summary = NULL` and accept would have had nothing to accept. v0.7.1
first preserves `output_summary` / `session_id` / `duration_ms` at escalation
(`dispatcher.run_once`, the NOT_VERIFIED-after-retry / MANUAL_VERIFY_REQUIRED
`else:` branch) — which also lets the escalated TaskBoard card finally show the
output the operator is judging. Without this, accept is hollow.

### Surfaces (built)

- **Schema:** `ops_tasks.review_overridden INTEGER NOT NULL DEFAULT 0` via
  `_migrate_add_column` (mirrors the v0.7.0 block; idempotent, existing rows
  read 0). `1` = completed by accepting over a non-VERIFIED verdict; the
  `review_verdict` is **kept** so the audit trail shows WHY it escalated.
- **Dispatcher:** the escalation `update_task` now also stores
  `output_summary=summary_head`, `session_id`, `duration_ms` (the gotcha fix).
  The VERIFIED / NOT_VERIFIED-retry / verdict logic is otherwise untouched.
- **API:** `POST /api/tasks/{id}/accept` (mirrors `/approve` + `/cancel`) —
  guarded to **review escalations only** (`status='awaiting_approval'` AND
  `review_verdict IS NOT NULL`). A risk/autonomy-gated task (verdict NULL,
  never ran) is refused `400 … not a review escalation … use /approve`; wrong
  states `400`. On accept: `status='done'`, `review_overridden=1`,
  `completed_at=now`, keeping the preserved output + verdict + feedback. Writes
  an `activities` `task_review_overridden` row (`prior_verdict`,
  `review_feedback_first_120`, `source`). **No dispatch, no `claude -p` spawn —
  zero added cost.** (`GET /api/tasks` now also returns `review_overridden`.)
- **Telegram:** `/accept <id>` (verb added to `_CMD_WITH_ID_RE`; `_handle_accept`
  mirrors `_handle_approve` → `POST .../accept?source=telegram`). The
  review-escalation reply router gains a strict-match `/accept` / bare `accept`
  shortcut (v0.6.5 `/yes` `/no` shape); any longer reply still falls through to
  `/approve` (re-run). The escalation ping now offers accept / approve / cancel;
  `/help` gains a line.
- **UI (TaskBoard):** the review-escalated card now renders the preserved
  `output_summary` and offers **Accept output / approve / cancel**; after
  accept a distinct **amber "✓ done · accepted over review"** badge renders
  (`status='done' && review_overridden=1`), visually separate from a clean
  green ✓ VERIFIED so the override is never invisible.

### Verified

- `command-centre/scripts/dev/smoke_v0_7_1.py` — **43 / 43**, all 12 stop
  conditions: output preserved at escalation (NOT_VERIFIED + MANUAL_VERIFY);
  accept happy path (done · `review_overridden=1` · output + verdict kept · no
  re-run — session_id/cost unchanged, `today_cost_usd` stays $0); accept
  refuses non-review awaiting_approval (verdict NULL → "use /approve"); accept
  refuses pending/running/done (400) + missing (404); the audit row; approve
  still re-dispatches (`review_overridden` stays 0); cancel still drops;
  VERIFIED keeps `review_overridden=0` + risk `/approve` untouched;
  `GET /api/tasks` exposes `review_overridden`; Telegram `/accept <id>` slash +
  reply-`/accept` + bare `accept` (strict) + the longer-reply fall-through to
  approve.
- `command-centre/ui/tests/e2e/v0.7.1.spec.ts` — **2 / 2** (escalated card
  Accept-output button → `POST /accept` → preserved output + "done · accepted
  over review" badge; badge distinct from a clean ✓ VERIFIED). `tsc --noEmit`
  clean; `vite build` clean.
- Backward compat (stop 11): smoke v0.7.0 **69/69** · v0.6.1 **20/20** ·
  v0.6.2 **39/39** · v0.6.4 **45/45** · v0.6.5 **43/43** · v0.6.7 **35/35** ·
  v0.6.8 **26/26** — all unchanged. e2e: v0.7.0 TaskBoard verdict-badge +
  v0.6.6/v0.6.7/v0.6.8 green. (One pre-existing **environment-only** e2e
  failure — v0.7.0's SkillLauncher `review_mode` toggle, a `check({force})` on
  an `sr-only` checkbox — reproduces identically against the **unmodified main
  dist**, so it is not a v0.7.1 regression; the unchanged SkillLauncher was not
  touched.)

### Implementation notes (deviations from the spec, flagged)

1. **`POST /accept` returns the full updated task row**, not the small
   `{accepted: true}` shape that `/approve` + `/cancel` return — following the
   spec's explicit "API delta → step 5: *Return the updated task*" over the
   mirror's return shape. The UI's `useAcceptTask` and the smoke both consume
   the returned `review_overridden` directly.
2. **A Cancel button was added to the escalated card** (with `cancelTask` /
   `useCancelTask`). The spec's UI delta says "beside the existing Approve
   (re-run) and **Cancel** (drop)" and stop condition 10 wants the escalated
   card to show "Accept/Approve/Cancel" — but the v0.7.0 TaskBoard had no
   Cancel button (only Approve/Rerun/Delete). Cancel is scoped to
   review-escalated cards; risk/autonomy-gated `awaiting_approval` cards keep
   the single Approve, exactly as in v0.7.0.

### Deferred

Bulk accept · auto-accept policy (the `task_review_overridden` audit rows are
its future data source) · overridden-today rollup + AttentionBar tile ·
re-review on accept.

---

## v0.7.0 — adversarial review gate

> Spec: `observability/(C) build-your-own-dashboard-prompt-v0.7.0-amendment.md`.
> Shipped on branch `claude/v0.7.0`. The spec + this DRAFT predate v0.6.8;
> built on the actual `main` HEAD `5ce116e` (post v0.6.8). v0.6.8 ("expose
> historical ranges") touches only `helpers/timerange.py` + the range-picker
> UI — no overlap with the review-gate surfaces — so it composes cleanly, and
> the dispatcher line references the spec named (run_once 674, _run_classic
> 271, _run_stream 303, _build_prompt 260) still land exactly.

The first MINOR bump since the project went patch-only at v0.2.0. Earns it:
unlike v0.6.7 (a mechanical extension of the v0.2.0 cap shape — explicitly
kept as a patch for that reason), v0.7.0 adds a **new execution path** (a
second `claude -p` reviewer child per reviewed task) and **new task-lifecycle
semantics** (a verdict gate between "ran ok" and "done"). It is the milestone
the v0.6.x arc kept deferring to "v0.7+".

Distilled from the Storm Bear wiki — Pattern #76 Adversarial Subagent Review
Architecture (`gotalab/cc-sdd` v61) + Pattern #74 EARS-Format Requirements +
Pattern #21 SDD Methodology. (Reciprocal to the 2026-05-29 wiki contribution
that registered Command Center's cost-discipline architecture as an
Observation-Track — wiki→product this time.)

### Why this release

A task is marked `done` today purely on its own `output_summary` (the agent
grades its own homework). v0.7.0 adds an **independent reviewer**: for skills
opted into `review_mode`, after the implementer exits ok the dispatcher spawns
a reviewer `claude -p` child in the same workspace (so it re-reads the actual
files — fresh evidence, not self-report) that returns `VERIFIED` /
`NOT_VERIFIED` / `MANUAL_VERIFY_REQUIRED`. `VERIFIED` completes as today;
anything else routes to the existing `awaiting_approval` state with the
reviewer's reason attached. Opt-in per skill, **off by default**, because it
~doubles the per-task API cost.

### Surfaces (built)

- **Schema:** `skills.review_mode` + `ops_tasks.{success_criteria,
  review_verdict, review_count, review_feedback}` via `_migrate_add_column`.
- **Dispatcher:** `_skill_review_mode` + `_run_review` (verdict-marker scan,
  fail-safe to `MANUAL_VERIFY_REQUIRED` on no-verdict/crash/timeout) + a gate
  in the `run_once` completion block; one automatic retry (feedback prepended)
  then escalation to `awaiting_approval`.
- **Reuse, not rebuild:** escalations land in `awaiting_approval` — resolved
  by the existing dashboard approve/reject + Telegram `/approve` `/cancel`
  (v0.5.0-mvp2). No new decision wiring, near-zero Telegram work.
- **API:** `PATCH /api/skills/{name}/review` + `review_mode` / `success_criteria`
  / verdict fields on skill + task shapes + dispatcher rollup + `/api/attention`
  `review_escalated` warning.
- **UI:** SkillLauncher editor toggle, TaskBoard verdict badge, optional
  `success_criteria` field, AttentionBar fold-in.
- **Cost-honest:** reviewer spend is `api_pool`, counts toward the global cap
  (v0.2.0) + per-skill budget (v0.6.7) post-hoc; `CC_REVIEW_MODEL` allows a
  cheaper review tier.

### Verified

- `command-centre/scripts/dev/smoke_v0_7_0.py` — **69 / 69**, all 12 stop
  conditions: VERIFIED happy path; NOT_VERIFIED → one auto-retry → escalate;
  MANUAL_VERIFY_REQUIRED immediate escalation; no-verdict + crash + timeout
  fail-safes (real `_run_review` driven through a stubbed `subprocess.Popen`);
  verdict-parser fixtures (`—` / `-` / `--` separators, case-sensitivity,
  last-match-wins); `review_mode`-off byte-identical completion; dry-run skip;
  `success_criteria` reaches the reviewer prompt; cost attribution (~2× a
  non-reviewed task); review PID marker `mode="review"` + sweep coverage;
  dispatcher `review` rollup + `/api/attention` `review_escalated` (warning);
  approve re-dispatch; and the Telegram `review_gated` notification.
- `command-centre/ui/tests/e2e/v0.7.0.spec.ts` — **3 / 3** (editor review
  toggle → PATCH `.../review`; TaskBoard ✓ / ✗ / ? verdict badges +
  reviewer-reason line). `tsc --noEmit` clean; `vite build` clean.
- Backward compat (stop 12): smoke_mvp2 24/24 · v0.6.1 20/20 · v0.6.2 39/39 ·
  v0.6.4 45/45 · v0.6.5 43/43 · v0.6.7 35/35 · v0.6.8 26/26; e2e
  v0.6.6/v0.6.7/v0.6.8 **11 / 11** — all unchanged.

### Implementation notes (deviations from the spec, flagged)

Two refinements of the spec's *illustrative* pseudocode, both required to make a
stop condition true and both documented in `dispatcher._run_review`:

1. **Reviewer runs `claude -p --output-format json`, not plain.** The spec says
   "same shape as `_run_classic`" (plain stdout) and `_run_review` returns
   `{verdict, reason}`. But plain `claude -p` emits no cost, and **stop
   condition 10** requires the reviewer's spend to count toward the per-skill
   budget + global cap (a reviewed task moves `today_cost_usd` by ~2×). So the
   reviewer uses `--output-format json` (same family as `_run_stream`'s
   stream-json); the VERDICT scan runs on the parsed `result` text (spec step
   4) with a raw-stdout fallback, and `total_cost_usd` is captured. It stays
   classic-shaped otherwise (blocking, stdin DEVNULL, timeout-capped, PID-marked
   `mode="review"`, no streaming / decision / inbox machinery).
2. **`_run_review` returns `cost_usd`; the gate adds it to the task.** Required
   by (1): the completion block does an additive `cost_usd = COALESCE(cost_usd,
   0) + reviewer_cost` on the same task id (no separate row), *inside the review
   branch only* — the non-review path stays byte-identical (stop 7).

Minor: the spec's "API delta" lists `GET /api/tasks/{id}`, which doesn't exist
(only the `GET /api/tasks` list) — the review fields were added to the list
SELECT (what TaskBoard consumes) and `success_criteria` to `POST /api/tasks`.
The AttentionBar "click scrolls to the awaiting-approval tasks" was kept
describe-only to match every existing attention issue (none are click-navigable).

### Deferred

"Accept output as-is" action · per-task review override · multi-reviewer
N-vote panel · per-skill review-model routing · EARS parser · reviewer cost
pre-gating · review lineage rows. See the amendment's "Not in this release".

---

## v0.6.8 — expose historical ranges

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.8-amendment.md`,
applied on top of current `main` HEAD (post v0.6.7). Smallest release of
the v0.6.x arc — one backend helper, the `Range` type, and the one
shared `RANGES` array.

Surfaces data that is **already in the database** but currently
unreachable from the UI. The dashboard's range toggle caps at 30
days — but nothing prunes old data (no retention job, prune, purge,
or vacuum exists anywhere), so every session / token / outcome row
ever ingested is still there. This release adds `90d` / `1y` / `all`
to the range selector so operators can see their full history. The
operator-facing headline: **your data was never deleted — you just
couldn't see past 30 days.**

Numbered `v0.6.8` (patch over v0.6.7). No schema change, no new
endpoint, no operator config. The backend already supports an `all`
range (`helpers/timerange.py` maps it to `1=1`); this adds the `90d`
+ `1y` predicates and exposes the wider windows in the UI's single
shared `RangePicker`.

### Why this release

The operator's own usage history is in the DB but the dashboard can't
show more than 30 days of it — a one-character product gap. The
backend already computes every window; the UI just never offered the
wider ones. Read-only exposure of existing data; explicitly NOT a
retention or cleanup feature.

### What ships

- **`helpers/timerange.py`** — `90d` (`-90 days`) + `1y` (`-1 year`,
  SQLite-native + leap-safe) predicates added to `ALLOWED` +
  `sql_predicate`; `all` (`1=1`) was already there. The dead-but-latent
  `days_in_range` dict gains `90d` / `1y` keys (90 / 365) so it can't
  `KeyError` once those ranges are valid — defensive, nothing calls it
  yet.
- **`ui/src/lib/api.ts`** — `Range` widened to
  `'today' | '7d' | '30d' | '90d' | '1y' | 'all'`. Additive union
  member; every `(range: Range = '7d')` signature and the `qs()` URL
  builder pass the string straight through.
- **`ui/src/components/panels/TokenUsageCard.tsx`** — the shared
  `RANGES` array extended to the six values, and each picker button now
  carries a `data-range` attribute (additive, for deterministic test
  selection — matches the existing `data-*` idiom). ~14 panels +
  SessionsTable + SessionsPage inherit the new options via the one
  shared `RangePicker`; no per-panel change.
- Defaults unchanged (`7d` / `30d`) — wider windows are opt-in via the
  toggle, so initial load doesn't widen to `all`.
- **README** — one line under the dashboard section: the toggle now
  offers `90d` / `1y` / `all`, and nothing prunes old data so `all` is
  the operator's full history.

### API delta

None. Every range-aware endpoint already routes `?range=` through
`sql_predicate` / `normalize`; the new values became valid the moment
`ALLOWED` grew. The full set that immediately gained `90d` / `1y`:
`usage/tokens`, `usage/cache`, `sessions/outcomes`, `tools/latency`,
`hooks/activity`, `sessions/by-project`, `tools/agent-fanout`,
`tools/edit-decisions`, `activity/productivity`, `activity/heatmap`,
`sessions` (+ `sessions/failures`), `skills/economics`, `mcp`.

### Schema delta

None. Read-only over existing rows.

### Verified

Backend + endpoints: `command-centre/scripts/dev/smoke_v0_6_8.py` boots
a real server over a temp SQLite DB seeded with multi-month fixtures (10
distinct days spanning the 30d / 90d / 1y windows + one >1-year-old
row), startup sync neutralised (`CC_CLAUDE_PROJECTS_DIR` /
`CC_COWORK_DIR` → missing dirs) so the DB holds only seeded rows.
**26 / 26 checks** — amendment stop conditions 1–6 + 9.

- **Stop 1 — predicates.** `sql_predicate('90d')` → `… >=
  datetime('now','-90 days')`; `'1y'` → the `-1 year` fragment; `'all'`
  → `1=1` (unchanged). `normalize` passes `90d` / `1y` / `all` through,
  coerces `bogus` → `7d`.
- **Stop 2 — `days_in_range`.** No `KeyError` on any `ALLOWED` value;
  `90d`→90, `1y`→365, `all`→365.
- **Stop 5 — empty DB.** `?range=all` on a fresh DB returns
  `daily: []` + all-zero totals (not null) on `usage/tokens`,
  `sessions/outcomes`, and `sessions` — no crash.
- **Stop 3 — wider windows ⊇ narrower.** Seeded date-sets chain
  `30d ⊆ 90d ⊆ 1y ⊆ all`, counts 3 / 6 / 9 / 10 on both `usage/tokens`
  and `sessions/outcomes`; `90d` surfaces days `30d` omits.
- **Stop 4 — full history.** The >1-year-old row appears under `all`
  and is absent from `1y` and `30d` (both endpoints).
- **Stop 6 — pagination.** `GET /api/sessions?range=all&limit=10`
  returns 10 rows (not the whole table), `total` = 310, `offset=10`
  walks to a disjoint older page, ordered `started_at DESC`.
- **Stop 9 — performance.** `?range=all` on `usage/tokens`,
  `sessions/outcomes`, and `sessions` each complete in single-digit ms
  with a few hundred seeded rows (index-backed).

UI: `command-centre/ui/tests/e2e/v0.6.8.spec.ts` — **3 / 3 specs**
against `page.route` fixtures (stops 7 + 8 on TokenUsageCard,
SessionsPage, and the SkillCost panel).

- **Stop 7 — picker + widening.** The shared `RangePicker` renders six
  options; clicking `1Y` / `ALL` fires the widened request (`?range=1y`,
  `?range=all`) and re-renders with no console error.
- **Stop 8 — defaults unchanged.** First load still requests each
  panel's prior default — TokenUsageCard `7d`, SessionsPage list `30d`,
  SkillCost economics `30d` — never `all`, verified via the initial
  request's `range` param.
- **Stop 10 — backward compat.** `tsc --noEmit` clean with the widened
  `Range` union; the v0.6.6 / v0.6.7 specs pass unchanged. (Two
  unrelated v0.6.0 interaction specs — preset-reload round-trip and the
  cost-source filter — are flaky in the local preview harness; verified
  they fail identically on the pre-v0.6.8 build, so they're
  environmental, not a regression from this change.)

### Operator flow

```bash
cc restart                       # no migration; UI ships in ui/dist
open http://127.0.0.1:8765/      # range toggle now: TODAY 7D 30D 90D 1Y ALL
# Pick 1Y or ALL on any panel to see older history. Defaults are
# unchanged, so a fresh load looks exactly like v0.6.7.
```

Nothing prunes old data, so `ALL` is your complete ingested history. If
the DB ever grows uncomfortably, `cc doctor` reports `db_size_bytes`; an
actual retention/vacuum knob is a v0.7+ feature (see below).

### Tunables

None this release. The new ranges are fixed preset buckets on the
existing toggle — no env var, no config.

### Not in this release (deferred)

- **Retention / vacuum knob.** The *opposite* feature — actually
  deleting old rows + reclaiming SQLite space on a schedule or by an
  operator-set age (`MISSION_CONTROL_RETENTION_DAYS` + nightly prune +
  `VACUUM`). v0.6.8 only *exposes* old data; deletion carries its own
  irreversibility risks and is a separate v0.7+ concern.
- **Custom date-range picker** (arbitrary from/to). MVP is the fixed
  bucket toggle; a calendar range is a bigger UI surface.
- **Weekly / monthly downsampling for long ranges.** `1y` / `all`
  render up to ~365 daily bars — dense but functional. Bucketing to
  weekly points is a charting concern.
- **Uniform gap-filled date axis.** Days with no activity are simply
  absent (no zero-fill); over long sparse ranges the x-axis collapses
  gaps. Gap-fill touches every daily-series builder.
- **CSV / JSON export of a range.** A natural sibling ("give me all my
  data as a file") but a distinct feature.
- **Per-range default persistence.** Remembering the operator's last
  picked range per panel (localStorage). Nice-to-have.

---

## v0.6.7 — per-skill daily cost budgets

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.7-amendment.md`,
applied on top of current `main` HEAD (post v0.6.6). Largest multi-
surface release since v0.6.0 — schema + dispatcher + API + UI +
Telegram outbound + Playwright — but each surface mirrors an
existing v0.2.0 (global cap) or v0.6.0 (api_pool semantics +
SkillLauncher panel) pattern. Additive — no breaking changes to
existing endpoints, idempotent migration, NULL-budget rows behave
identically to v0.6.6.

Numbered `v0.6.7` (patch over v0.6.6). Cadence consistent with
v0.6.1–v0.6.6. Alternative `v0.7.0` defensible (multi-surface
feature with schema + dispatcher + UI changes); kept the patch
cadence because the pattern is a mechanical extension of v0.2.0's
existing cap shape, not a new architectural concept.

### Why this release

`MISSION_CONTROL_DAILY_COST_CAP_USD` is a single number that
protects the operator's total wallet but can't distinguish between
an expensive deep-research skill burning $4/day legitimately and a
misconfigured morning-brief skill burning $4/day by accident. When
the global cap fires, the operator doesn't know which skill ate
the budget. Per-skill budgets add a second axis — each
`user_invocable=1` skill optionally gets a `daily_budget_usd`;
dispatcher refuses to claim tasks for that skill once today's
api_pool spend reaches the budget. Composes cleanly with the
global cap: both apply, whichever triggers first wins. Skill
budget is the per-skill axis, global cap is the safety floor.

### What ships

- **`skills.daily_budget_usd REAL NULL`** column via additive
  `_migrate_add_column` next to v0.6.0's `preset_json` /
  `last_launched_at` / `launch_count`. Idempotent — re-running
  `apply_migrations()` is a no-op. Three semantic values:
  `NULL` = unlimited (current behaviour for every existing skill,
  no migration shock); `0` = blocked, refuses every claim (operator
  temp-disable without deleting); `> 0` = post-hoc cap.
- **`PATCH /api/skills/{name}/budget`** new endpoint. Body
  `{daily_budget_usd: float | null}`. `null` clears; `0` is valid;
  negative numbers and non-numeric values are 400. Mirrors the
  v0.6.0 `PATCH .../autonomy` shape exactly — column-update, no
  JSON blob.
- **`GET /api/skills`** rows now include `daily_budget_usd` and
  `today_cost_usd` next to v0.6.0's `avg_cost_usd_30d`. Today's
  spend uses the same
  `cost_source='api_pool' AND DATE(completed_at,'localtime')=today`
  predicate the global cap reads (v0.6.0 cap rewrite), so the
  card-level math agrees with the dispatcher's refusal math down
  to the cent.
- **`GET /api/system/dispatcher`** gains two rollup fields —
  `skills_with_budget` (count of non-NULL budgets) and
  `skills_at_budget` (count where `today_cost_usd >= budget`).
  AttentionBar and DispatcherStrip consume these instead of
  fanning out one query per skill.
- **Dispatcher pre-claim check** in `dispatcher.run_once()`,
  inserted between the global cap (top-level early-return,
  unchanged) and the per-task autonomy gate. Post-hoc shape —
  matches the v0.2.0 global cap; refuses only when today's spend
  already meets or exceeds the budget. Operator can over-spend by
  at most one task's cost beyond the budget. When a claim is
  refused: task reverts from `running` back to `pending`,
  `stats.skill_budget_capped` increments, and an
  `dispatcher_skill_budget_capped` activity row is logged with
  full metadata (`{task_id, skill, today_cost_usd, daily_budget_usd}`).
  Order of checks remains: emergency stop → stale PID sweep →
  back-pressure → hard risk gate → global daily cap → **per-skill
  budget (NEW)** → per-task autonomy.
- **`/api/attention` aggregator** — new `skill_budget_capped` issue
  type with `severity: "warning"` (yellow), NOT `error` (red).
  Different from `cost_capped`: operator-tuneable, expected to
  trigger more often. Single issue covers N capped skills with
  `{count, skill, today_cost_usd, daily_budget_usd, title, message}`
  — first skill's spend renders for context; `(+N more)` suffix
  when more than one skill is over budget.
- **`AttentionBar` rendering.** New `skill_budget_capped` case
  renders the count + first skill's spend / budget. The whole
  banner now tones to amber when only warning-severity issues are
  present (preserves the existing red treatment when any `error`
  issue — `failed_task`, `cost_capped` — sits in the feed).
  Issues are sorted error-then-warning, keeping `cost_capped`
  above `skill_budget_capped` per the amendment spec.
- **`SkillLauncher` card 3-tier visual state.** Hidden entirely
  when `daily_budget_usd` is `NULL` (v0.6.6 layout preserved). Dim
  monospace `today $X.XX / $Y.YY` when `today < 0.8 × budget`;
  amber when `0.8 × budget ≤ today < budget`; red + **Launch**
  button disabled with a tooltip showing exact values
  (`"Daily budget reached ($1.50 / $1.00) — resets at midnight
  local"`) when `today ≥ budget`. Tier is derived in render code
  from the GET /api/skills payload; no extra endpoint needed. The
  red-tier `data-budget-blocked="1"` attribute and
  `data-budget-tier` on the budget line are Playwright-stable.
- **`SkillLauncher` inline preset editor — Budget (optional)
  section** at the bottom of the form, visually separated from the
  9 preset launch-default fields by a kicker, a divider, and a
  trailing `today $X.XX` chip. Number input; blank = NULL
  (unlimited). On save: preset goes through the existing
  `PATCH .../preset` call, then a **separate**
  `PATCH .../budget` call fires only if the budget changed —
  matching the amendment's "preset = launch defaults, budget =
  spending policy" carve-out so a preset edit never silently
  rewrites the budget.
- **Telegram outbound `skill_budget_exceeded` event type.** Fourth
  poll source in `_outbound_tick`, sitting after task_complete and
  risk_gated. First-fire push per skill per day; dedupe key is
  `{skill_name}:{today_local_date}` (uses the v0.6.1 `_today_local_date`
  helper) so the same skill same day stays silent, but tomorrow's
  first exceed triggers a fresh notification on the rolled-over
  date. `_format_skill_budget_exceeded` renders Markdown V1 in the
  v0.5.0-mvp1 style:
  > 🔒 **Skill budget reached**
  >
  > skill-name hit $1.50 / $1.00 today (HH:MM GMT+7).
  >
  > Dispatcher refusing new claims for this skill until midnight local.
  > Other skills + manual /run tasks continue normally.
- **`cc doctor`** gains `skill budgets` check: warns when any
  skill has `daily_budget_usd < $0.01` (likely cents-vs-dollars
  typo); green when every set budget is `>= $0.01`; skips on
  pre-v0.6.7 databases.

### Schema delta

One additive column, idempotent against any v0.x install:

| Table | Column | Type | Default |
|---|---|---|---|
| `skills` | `daily_budget_usd` | REAL | `NULL` |

### Verified

- **Smoke harness** —
  `command-centre/scripts/dev/smoke_v0_6_7.py` drives the 13
  amendment stop conditions end-to-end against a real FastAPI
  server and a real `dispatcher.run_once()` loop (claude-CLI spawn
  stubbed for hermeticity, Telegram `_tg` stubbed for capture).
  Seeds three fixture skills (no budget / under budget / over
  budget) plus auxiliary `skill-zero` / `skill-unlimited` /
  `skill-null` rows; asserts the migration is idempotent, the new
  PATCH endpoint accepts valid payloads and rejects negative +
  non-numeric inputs, the GET augmentation surfaces the new
  fields, the dispatcher refuses at-budget and over-budget claims
  while NULL budgets stay unlimited and zero budgets block
  everything, the global cap still wins when both could trigger,
  the Telegram dedupe fires first time and re-fires on the next
  local day, the AttentionBar count drops the moment the operator
  lifts one skill's budget, the 3-tier derivation lines up with
  the API values, every existing v0.6.6 skill field stays present
  with the same semantics, and `cc doctor`'s new check warns on a
  sub-cent budget. **35 / 35 checks pass.**
- **Playwright** —
  `command-centre/ui/tests/e2e/v0.6.7.spec.ts` exercises the UI
  surfaces against `page.route` fixtures (no real backend
  required): four-skill grid renders dim / amber / red / hidden
  tiers correctly; Launch button is disabled with
  `data-budget-blocked="1"` only on the red tier; AttentionBar
  tones to amber when only `skill_budget_capped` (warning) issues
  are present; AttentionBar keeps the skill row visible and
  ordered below `cost_capped` (error) when both fire at once.
- `tsc --noEmit` clean. `vite build` 531 KB / 158 KB gzipped.
- `python3 -m py_compile` clean across all touched Python files.

### Operator flow

```bash
# Existing v0.6.x install — just restart, migration runs in lifespan.
cc restart
cc doctor                  # `skill budgets` should be ok or skip

# Set a budget through the UI:
#   open http://127.0.0.1:8765
#   Skill launcher → pencil icon on a card
#   scroll to the new "Budget (optional)" section at the bottom
#   enter a number (or 0 to temp-disable; blank to clear)
#   save preset

# Or via curl:
curl -X PATCH http://127.0.0.1:8765/api/skills/morning-brief/budget \
     -H 'Content-Type: application/json' \
     -d '{"daily_budget_usd": 0.50}'
```

When today's api_pool spend on that skill reaches the budget:

1. The dispatcher refuses to claim new tasks for the skill and
   logs `dispatcher_skill_budget_capped`.
2. The SkillLauncher card flips red, **Launch** disables with a
   tooltip, and the AttentionBar surfaces a `skill_budget_capped`
   warning.
3. Telegram (if configured) pushes a one-time
   🔒 *Skill budget reached* message — dedupes within the same
   local day, re-fires after midnight if the operator hasn't
   lifted the budget.

### Tunables (env vars or .env)

No new env vars. The new policy lives entirely on the per-skill
column.

| Var | Default | Behaviour in v0.6.7 |
|---|---|---|
| `MISSION_CONTROL_DAILY_COST_CAP_USD` | unset = off | Unchanged. Global safety floor; fires before the per-skill check. |
| `TELEGRAM_NOTIFY_INTERVAL_S` | 30 | Unchanged. Sets the cadence at which `skill_budget_exceeded` polls. |

### Not in this release (deferred)

- **Per-account-per-skill budgets.** v0.6.3 added the `account_id`
  axis; combining "skill X has $5/day for account_a but $20/day
  for account_b" is a v0.7+ matrix concept. v0.6.7 sums across
  all accounts for the skill. The per-skill cost predicates in
  `dispatcher._skill_budget_state` and `routers/skills._today_cost_usd`
  deliberately omit `account_id` filtering — a v0.7+ matrix must add
  account scoping behind explicit config, not by filtering these
  queries (which would change budget semantics for single-account
  operators).
- **Soft-warning + hard-cap pair** (two budgets per skill). v0.6.7
  uses a single hard cap + 80% amber threshold. Two-tier policies
  add config surface; defer until usage demands.
- **Budget templates / classes** ("deep-research skills get $5,
  brief skills get $0.50"). Operator can set per-skill manually;
  templates are syntactic sugar.
- **Non-daily time windows** (weekly, monthly). MVP is daily-only.
- **Preemptive hard-reject** (refuse a task that would push *over*
  the budget, not at-budget). Would need per-task cost estimation;
  speculative. v0.6.7 matches the v0.2.0 post-hoc cap shape.
- **`cc setup skill-budgets` wizard.** Operator sets budgets via
  the SkillLauncher inline editor; a wizard for bulk setup is
  v0.7+ if signals support.

---

## v0.6.6 — Obsidian embed mode (`?embed=1`)

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.6-amendment.md`,
applied on top of current `main` HEAD (post v0.6.5). First non-
Telegram release since v0.6.0. Closes the loop on this arc's original
framing (Chase AI's Obsidian command-centre video) without shipping
an Obsidian companion plugin: when `?embed=1` is set, the dashboard
strips its chrome (Nav, header, command palette, emergency-stop
banner, footer) so operators can paste the URL into Obsidian's
web-viewer plugin and get the panels rendered next to their notes.
No Obsidian SDK, no plugin manifest, no custom integration to
maintain — the iframe is the contract.

Numbered `v0.6.6` (patch over v0.6.5). Alternative `v0.7.0`
defensible (first product-surface expansion since v0.6.0); kept the
patch cadence because there's no schema, no new endpoint, no operator
config — `?embed=1` is invisible to every existing feature.

### Why this release

Dashboard is a standalone localhost web app at `127.0.0.1:8765`.
Operators who use Obsidian as their daily knowledge base have to
context-switch between vault and a separate browser tab. `?embed=1`
is the minimum-viable bridge: dashboard stays standalone, but
operators who want eye-level co-presence install Obsidian's
web-viewer plugin and paste the embed URL into a pane. No new
dependency burden on either side.

### What ships

Frontend only — three TypeScript files + one stylesheet + README +
this CHANGELOG entry. No schema change, no new endpoint, no
backend code change (the FastAPI server already ships no
`X-Frame-Options` and no `Content-Security-Policy`, so the
Obsidian iframe loads without adjustment — see the README section
for the `curl -I` verification).

- **TanStack Router root-search type** (`ui/src/router.tsx`).
  `rootRoute.validateSearch` now accepts an optional
  `embed?: string`, declared inline as a `RootSearch` type so
  `useSearch({ from: '__root__' })` returns the flag with
  TypeScript happiness. Strings only (`'1'`, `'true'`, anything
  else); the truthiness check lives in AppShell. Empty object when
  the param is absent.
- **AppShell conditional render** (`ui/src/components/layout/AppShell.tsx`).
  Reads the root `embed` param; when it's `'1'` or `'true'` the
  component early-returns a stripped layout — just
  `<div class="app-shell embedded">` → `<main class="main embedded">`
  → `<Outlet />`. Header (Command Centre branding + Nav + StatePill
  + ⌘K trigger), `EmergencyStopBanner`, footer (`local · 127.0.0.1
  …`), and `CommandPalette` are dropped from the tree entirely
  (conditional render, not CSS `display:none` — keeps the DOM
  clean for Obsidian's narrow iframe). Non-embed path is byte-
  identical to v0.6.5.
- **`AttentionBar` stays visible.** It's rendered inside
  `CommandPage` (and any future page that opts in), not in
  AppShell, so the chrome strip leaves it intact. Stuck loops,
  failed tasks, dispatcher silence, `cost_capped`, back-pressure —
  all still surface inside the embedded pane. This was the
  critical-signal-carve-out call from the amendment: keep the bar,
  drop the banner.
- **Embed-mode CSS** (`ui/src/styles.css`). Vanilla CSS rules
  scoped under `.app-shell.embedded`, no new Tailwind utilities:
  zero outer padding, 16px main padding (was Tailwind `px-6 py-8`
  = 24/32px), tighter 12px gap between Command-page sections
  (overrides Tailwind's `space-y-6` = 24px when nested under
  `.embedded`), and a `@media (max-width: 600px)` rule shrinking
  body font to 13px so KPI labels and card titles don't wrap in a
  narrow Obsidian pane. Single block, ~20 lines.
- **Internal link preservation.** `Nav.tsx`'s `<Link>` and every
  `useNavigate({ to })` call in `CommandPalette.tsx` now pass
  `search: (prev) => prev`, the canonical TanStack Router idiom for
  carrying the current search params across navigation. Today the
  Nav and palette are hidden in embed mode so the change is dormant;
  the value is the convention — any future panel-internal `<Link>`
  inherits the embed-flag-preservation behaviour by example, and
  Playwright stop 3 covers the cross-route case directly.
- **README — "Embed in Obsidian (optional, v0.6.6+)" section.**
  Step-by-step plugin install + paste + pane-drag flow, an
  explicit callout that EmergencyStopBanner is hidden by design and
  the operator's recovery path is to open
  `http://127.0.0.1:8765/` (without `?embed=1`) in a regular
  browser, plus a host-local `curl -I` snippet to confirm no iframe-
  blocking headers exist.

### Schema delta

None. UI + docs only. The `embed` flag lives purely in the URL
query string and is read by `AppShell` at render time; no
persistence, no API contract change.

### Verified

`command-centre/ui/tests/e2e/v0.6.6.spec.ts` covers amendment stop
conditions 1–4 against a deterministic API fixture (mocked the same
way as `v0.6.spec.ts` so the suite doesn't need real seed data).
**4 / 4 specs.**

- **Stop 1 — embed=1 hides chrome.** `GET /?embed=1` renders
  CommandPage without `Command Centre` branding, without any
  `Activity` / `Sessions` Nav links, without the red Emergency-stop
  button, without the `⌘K` palette trigger, and without the
  `no cloud · no account · no outbound telemetry` footer line. The
  `.app-shell.embedded` wrapper class is present so the CSS rules
  engage.
- **Stop 2 — no embed unchanged.** `GET /` renders all five
  surfaces (branding, Nav, EmergencyStopBanner, ⌘K, footer)
  unchanged. `.app-shell.embedded` is absent. No visual diff vs
  v0.6.5 — existing `smoke.spec.ts` and `v0.6.spec.ts` continue to
  pass without modification.
- **Stop 3 — every route honours the flag.** Loops over
  `/?embed=1`, `/activity?embed=1`, `/skills?embed=1`,
  `/sessions?embed=1`, `/decisions?embed=1` and asserts the embed
  wrapper is present and the chrome (branding + emergency-stop) is
  absent on each. This is the proxy test for cross-route
  preservation: if the operator clicks an in-app link that uses
  `search: (prev) => prev`, the URL stays `…?embed=1` and the same
  AppShell branch fires — exactly what the loop verifies.
- **Stop 4 — AttentionBar survives in embed.** Mocks
  `/api/attention` with one `dispatcher_stale` issue, loads
  `/?embed=1`, asserts both the red `Needs attention` header and
  the issue text (`dispatcher silent 180s`) are visible. Confirms
  the critical-signal carve-out: chrome is gone but the in-page
  attention surface is not.
- **Stop 6 — no iframe-blocking header.**
  `grep -n 'middleware\|X-Frame\|frame_options\|Content-Security'
  command-centre/scripts/server.py` returns no matches; FastAPI
  does not add `X-Frame-Options` by default, so localhost iframe
  embedding works without backend change. The README documents the
  `curl -I` repro for operators on non-default proxy setups.
- **Stop 9 — backward compat.** The existing
  `tests/e2e/smoke.spec.ts` and `tests/e2e/v0.6.spec.ts` keep
  passing unchanged; embed mode is gated on the search param and
  the non-embed path is byte-identical to v0.6.5. `tsc --noEmit`
  is clean.

Stops 5 (critical-signal carve-out documented) and 7 (manual
Obsidian smoke) are operator-side. Stop 5 is satisfied by the
README's explicit hidden-by-design callout. Stop 7 needs a real
Obsidian install + Web Viewer plugin and is intentionally
deferred to the operator's first post-deploy run:

1. Install the Web Viewer community plugin in Obsidian, enable
   it, restart Obsidian.
2. Run `cc start` if the dashboard isn't already up.
3. Command palette → **Web Viewer: Open web page in new tab** →
   paste `http://127.0.0.1:8765/?embed=1` → Enter.
4. Drag the tab into a side pane.
5. Verify: chrome is stripped, panels render, clicking a
   SkillLauncher card's **Launch** button fires the task (toast
   should show `↗ Task #N`).
6. Trigger a dispatcher stall (or wait for natural attention) and
   confirm the red `Needs attention` bar appears inside the
   embedded pane.

If step 5 or 6 fails, capture the iframe DevTools console and
file against this CHANGELOG entry.

### Operator flow

```bash
cc restart                       # no migration; UI ships in ui/dist
# Browser, regular tab:
open http://127.0.0.1:8765/      # full dashboard, as before
# Obsidian, with Web Viewer plugin installed:
#   ⌘P → Web Viewer: Open web page in new tab
#   paste: http://127.0.0.1:8765/?embed=1
#   drag the tab to the right sidebar
# Optional — verify no header blocks the iframe:
curl -I http://127.0.0.1:8765/?embed=1 | grep -iE 'x-frame|content-security'
# (no output expected)
```

### Tunables

None this release. The embed mode is a single boolean toggle.
Alternative pane-sized modes (`?embed=compact`, `?embed=tab`),
theme matching (`?theme=light|dark`), and a compact inline
emergency-stop variant are all on the v0.7+ list — see "Not in
this release" below.

### Not in this release (deferred)

- **Compact inline `EmergencyStopBanner` for embed mode.** The
  amendment floats a one-line red-dot variant that injects above
  `<Outlet />` only when `emergency_stop` is engaged. Deferred:
  needs a small `GET /api/system/state` poll inside the embed
  branch and a separate visual treatment, and the MVP carve-out
  (hide entirely, operator falls back to the non-embed tab) is
  honest and unambiguous. Revisit once at least one operator
  reports the round-trip as friction.
- **CSP `frame-ancestors` hardening.** Adding
  `Content-Security-Policy: frame-ancestors 'self' app://obsidian.md`
  would lock embedding to Obsidian + same-origin and refuse other
  frames. Skipped until real Obsidian iframe testing has confirmed
  the actual `origin` it presents (`app://obsidian.md` is the
  documented value; verify before committing to it as a CSP
  allowlist entry).
- **Theme matching (`?theme=light|dark`).** Dashboard is dark-only.
  Obsidian's default dark theme is close enough that most operators
  won't notice; a real light-theme port is a v0.8+ design surface,
  not a v0.6.x patch.
- **`?embed=compact|tab|wide` pane-sized variants.** MVP is one
  embed mode. Add variants when usage data shows operators
  consistently using narrow vs wide panes — premature today.
- **Obsidian companion plugin.** Out of strategy. The iframe path
  keeps the dashboard standalone and avoids tying releases to
  Obsidian's plugin-store cadence.

---

## v0.6.5 — Telegram `/yes <id>` + `/no <id>` slash commands

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.5-amendment.md`,
applied on top of current `main` HEAD (post v0.6.4). Fourth Phase 2
feature from the Telegram Remote Trigger PRD —  no-heuristic
alternative to the deferred inline keyboard `[Yes] [No]`. Bridge-only
— single file change to `telegram_bridge.py` plus this CHANGELOG entry.
No schema changes, no new endpoints, no breaking changes. Smallest
scope in the v0.6.x patch sequence.

Numbered `v0.6.5` (patch over v0.6.4) — same Telegram-bridge
subsystem, cadence consistent with v0.6.1 / v0.6.2 / v0.6.3 /
v0.6.4.

### Why this release

Inline keyboard for binary DECISIONS needs a "what's binary?"
heuristic — defer until 10+ real examples accumulate.
`/yes <id>` and `/no <id>` skip the heuristic: operator opts in
explicitly per decision, types two keystrokes on phone instead of
"yes" or "no" as free-text. Same API payload, shorter input. Once
inline keyboard ships in v0.7+, `/yes`/`/no` stays as the typed
fallback and the slash-command path becomes the data source for the
heuristic (every `/yes`/`/no` answer in `ops_decisions` is a
labelled binary example).

### What ships

All changes in `command-centre/scripts/telegram_bridge.py` (+72 / -4)
plus a dev smoke script (`scripts/dev/smoke_v0_6_5.py`). No new
dependencies, stdlib only.

- **`/yes <id>` and `/no <id>` slash commands.** Extends
  `_CMD_WITH_ID_RE` from `(answer|reply|approve|cancel|snooze)` to
  `(answer|reply|approve|cancel|snooze|yes|no)` — the new verbs
  keep the optional body group (the regex's `(?:\s+(.+))?`
  trailer carries over from `/snooze`'s grammar; for `yes` / `no`
  the body is ignored if anyone supplies one). Two new verb arms
  in `_handle_message`'s with-ID branch route both to
  `_handle_yes_no`.
- **`_handle_yes_no(chat_id, message_id, decision_id, answer)`** —
  thin POST wrapper around the existing
  `POST /api/decisions/{id}/answer` endpoint. Normalizes the
  answer string to lowercase `"yes"` or `"no"` before sending, so
  `/YES 42` and `/Yes 42` both record `answer='yes'`. Replies
  `✅ decision #{id} answered: yes` on 200, `decision #{id}
  already answered` when the API returns 200 with
  `{"already": true}` (the real shape for an already-answered
  decision — the spec said 400, but the existing
  `/api/decisions/{id}/answer` returns 200 + flag, which the
  handler surfaces as a distinct branch so the operator hint is
  precise), `decision #{id} not found` on 404, and a verbatim
  `decision #{id} · {detail}` on 400. NFR11: every error branch
  emits a reply and returns; the long-poll loop never raises.
- **Reply-to-decision shortcut.** Reply to a `❓ DECISION`
  notification with the exact text `/yes`, `/no`, `yes`, or `no`
  (case-insensitive, after `.strip()`) → resolves the decision_id
  via the existing `_lookup_by_tg_message` → calls
  `_handle_yes_no` with the normalized lowercase answer. The check
  is scoped to `event_type == 'decision'` and placed BEFORE the
  v0.6.2 `/snooze` reply-branch and the v0.3.0 verbatim
  `_route_reply` fallback. A reply of `/yes` to a task-complete
  notification routes through the v0.6.4 follow-up branch (which
  returns earlier in the same function), not through this
  shortcut — task-complete replies remain follow-up bodies.
- **Strict whole-message match.** Longer replies like
  `Yes, do it` or `No — defer` are NOT collapsed: they fall
  through to the existing verbatim `_route_reply('decision', ...)`
  so the operator's nuance lands in `ops_decisions.answer`
  unchanged. Only the four literals trigger the shortcut.
- **No `activities` audit row.** Matches the existing `/answer`
  slash pattern from v0.3.0 — the decisions answer endpoint is
  the audit source, not the bridge. Deliberate departure from
  mvp2's audit-everything-from-Telegram pattern for state-changing
  commands because `/yes` and `/no` route through the same code
  path as `/answer`, which itself doesn't audit. The smoke
  verifies `activities` row count does not change after `/yes`,
  `/no`, or `/answer`.
- **`/help`** extended with `/yes <decision_id>` ·
  `/no <decision_id>` and the reply-to-DECISION shortcut hint.

### Schema delta

None. Read-only data flow via the existing
`POST /api/decisions/{id}/answer` endpoint.

### Verified

`command-centre/scripts/dev/smoke_v0_6_5.py` walks all 9 amendment
stop conditions plus the regex fixture pre-check against a real
FastAPI server on `127.0.0.1:8869` (spawned in a temp
`$CC_INSTALL_DIR`), with a stubbed `_tg` capture so no real Bot API
call is made. **43 / 43 checks pass.**

- **R — regex fixtures.** `/yes 42` and `/no 42` parse with
  `(verb, '42', None)`; `/YES 42` / `/Yes 42` parse
  case-insensitively; `/answer 42 body` and `/snooze 42 30m` still
  carry the optional body group; `/yes abc` / `/yes` / `/yesno 42`
  do not match (regex enforces `\d+` and exact verb alternation).
- **S1 — `/yes <id>` happy path.** `/yes 1` flips `ops_decisions.id=1`
  to `status='answered'`, `answer='yes'`; bridge replies
  `✅ decision #1 answered: yes`; `activities` row count is
  unchanged from before the call.
- **S2 — `/no <id>` happy path + no-audit.** `/no 2` flips to
  `answer='no'` (lowercase); reply confirms; `activities` count
  unchanged. `/YES 42` answers with lowercase `yes` (normalization
  verified).
- **S3 — reply-to-msg `/yes` shortcut.** Reply with body `/yes` to
  a notification whose `notification_log` row points at decision
  `#N` → `ops_decisions.id=N` flips to `answer='yes'` (NOT
  `/yes` — the bridge strips the slash before POSTing).
- **S4 — reply-to-msg bare `yes` / `no` / `/No`.** All three case-
  variants resolve the decision via `_lookup_by_tg_message` and
  record the lowercase answer. Mixed-case `/No` also normalizes.
- **S5 — verbatim fallback preserved.** Reply with `Yes, do it`
  → `ops_decisions.answer='Yes, do it'` (verbatim, NOT collapsed
  to `yes`). Same for `No — defer`. Operator nuance survives.
- **S6 — already-answered decision.** Answer #10 with `/yes 10`,
  then re-send `/yes 10` → bridge replies
  `decision #10 already answered`. The API returns 200 with
  `{"already": true}`, not 400 — the handler surfaces that branch
  directly. No state change.
- **S7 — non-existent decision.** `/yes 9999` →
  `decision #9999 not found` (the API's 404 → 404 branch).
- **S8 — malformed input (NFR11).** `/yes abc`, `/yes`, `/no` all
  fail the regex (no `\d+`) and fall through; no DB mutation, no
  crash. Long-poll continues.
- **S9 — backward compat.** All existing verbs still parse
  (`/answer`, `/reply`, `/approve`, `/cancel`, `/snooze`, `/run`,
  `/status`); end-to-end `/answer 42 option A` still flips
  `ops_decisions.status='answered'` with `answer='option A'`;
  `/help` still renders and now mentions both `/yes` and `/no`.

### Operator flow

```bash
cc restart                    # no migration this release; bridge restarts.
# In Telegram, on a pending ❓ DECISION `#42`:
/yes 42                       # → ✅ decision `#42` answered: yes
# Or, replying to the DECISION push directly:
yes                           # bare yes (or /yes, /no, no) — strict match
# Nuanced replies still work verbatim:
Yes, but only after the EAS build finishes
# → recorded as ops_decisions.answer='Yes, but only after the EAS build finishes'
```

### Tunables

None. The shortcut grammar is intentionally the four literals
`/yes` / `/no` / `yes` / `no` — adding `y` / `n` or `yep` / `nope`
expands the surface without clear benefit; operators who want
nuance type free text and get verbatim capture.

### Not in this release (deferred)

- **Inline keyboard `[Yes] [No]`** — still needs the binary
  heuristic. The `/yes` and `/no` commands now feed the heuristic
  designer real data: every binary answer recorded in
  `ops_decisions` via this slash path is a labelled binary
  example. Build inline keyboard once 10+ examples accumulate.
- **`/maybe` or other multi-option presets** — operators can use
  `/answer <id> <text>` for nuanced answers. Don't multiply slash
  verbs for every imaginable choice.
- **Decision categorisation in the dashboard** — surfacing which
  decisions historically had binary yes/no answers vs free-text.
  Useful for inline-keyboard heuristic design later but out of
  scope.

---

## v0.6.4 — Telegram reply-to-task-complete → follow-up `/run`

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.4-amendment.md`,
applied on top of current `main` HEAD (post v0.6.3). Third Phase 2
feature from the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Bridge-only — single
file change to `telegram_bridge.py` plus this CHANGELOG entry. No
schema changes, no new endpoints, no breaking changes.

Numbered `v0.6.4` (patch over v0.6.3) — same Telegram-bridge
subsystem, cadence consistent with v0.6.1 / v0.6.2 / v0.6.3.

### Why this release

Today `/run` launches one task. Chaining workflows means retyping
context — `✅ task #42 done · Saved to docs/release-notes-v2.4.md`
followed by `/run write PR description from those notes` is two
disconnected operations. v0.6.4 collapses that to one: reply to the
task-complete notification with the follow-up instruction; bridge
composes a new task with the previous task's title + output summary
as context, dispatched immediately.

Closes Journey 1 deeper — "EAS build wait" becomes
"EAS build wait → chain three tasks from phone while waiting" instead
of "one task per build wait."

### What ships

All changes in `command-centre/scripts/telegram_bridge.py` (+182 / -2)
plus a dev smoke script (`scripts/dev/smoke_v0_6_4.py`). No new
dependencies, stdlib only.

- **Reply-to-task-complete routing.** Extends the `_handle_message`
  reply-to-msg branch (the mvp2 extension point) with a
  `task_complete` case — `_lookup_by_tg_message` already returns the
  event_type for any row in `notification_log`, so no SQL change was
  needed; only the calling branch grew a new dispatch arm. Mirrors
  mvp2's reply-to-RISK-GATED → `/approve` shape.
- **`_handle_task_followup(chat_id, message_id, prev_task_id, body)`** —
  fetches prev task via a direct `ops_tasks` read (same pattern as
  v0.6.2 `_handle_snooze`'s `ops_decisions` read — no GET endpoint
  exists for a single task and adding one would violate the
  bridge-only constraint); refuses on `status in ('failed',
  'cancelled')` with a clear "chain on a successful task" hint;
  refuses on a missing row with "task #N not found — notification
  may reference a deleted task"; composes the new task description
  with prev title (truncated 80 chars + ellipsis) + prev
  output_summary (truncated 300 chars + ellipsis; literal
  `(no output summary recorded)` when None / empty) + `---`
  separator + operator's reply; `POST /api/tasks` with
  `execution_mode='classic'`, `quadrant='do'`, `risk_level='low'`,
  `created_at_source='telegram'`; INSERTs an `activities` row tagged
  `event_type='task_followup_created'`,
  `detail={prev_task_id, new_task_id, prev_title,
  operator_reply_first_60, source: 'telegram'}` (FR19, matches
  mvp2 + v0.6.2 audit-everything-from-Telegram pattern); triggers
  the dispatcher inline via `POST /api/dispatcher/trigger` so the
  new task transitions within ~1s instead of waiting for the 120s
  heartbeat (same pattern as v0.6.0's `SkillLauncher.launch`);
  replies `✅ task #M queued · follow-up to #N`.
- **Title composition:** `Follow-up: {operator_reply[:60]}` with
  ellipsis if the reply is longer than 60 chars and newlines
  flattened so TaskBoard cards render one-line.
- **No skill inheritance.** The new task is created without
  `assigned_skill` or `model` — the existing dispatcher
  `skill_router.py` re-picks based on the operator's new reply,
  which may want a different skill than the original. Operators who
  want skill continuity use `SkillLauncher` for the follow-up
  explicitly.
- **No `parent_task_id` schema column.** Context lives entirely in
  the new task's `description` text; lineage is a v0.7+ UI concern
  for a TaskBoard chain visualization, not an MVP need.
- **Multi-hop is natural.** Reply to a follow-up's completion ping →
  triggers another follow-up referencing the immediate parent (e.g.
  `#43`), which itself was a follow-up of `#42`. No special multi-
  hop detection — the description chain breaks at one level deep on
  each reply.
- **NFR11.** Whitespace-only reply text is filtered before reply-to-
  msg routing by the existing bridge guard; empty reply bodies after
  strip reply with `cannot create follow-up from empty reply`;
  5000-char reply bodies create a task whose title is capped at
  `Follow-up:` + 60 chars + ellipsis. Audit-row INSERT failures and
  dispatcher-trigger failures log to stderr but do not block the
  operator reply (the task is already created; worst-case the
  dispatcher picks it up on the next 120s heartbeat).
- **`/help` text** extended with the new pattern:
  `Reply to a ✅ task-complete notification with a follow-up
  instruction to chain a new task with the previous task's output
  as context.`

### Schema delta

None. All composition lives in the new task's `description` field;
no new columns, no new endpoints.

### Verified

`command-centre/scripts/dev/smoke_v0_6_4.py` walks all 10 amendment
stop conditions against a real FastAPI server on `127.0.0.1:8868`
(spawned in a temp `$CC_INSTALL_DIR`), with a no-op `heartbeat.py`
seeded so `/api/dispatcher/trigger` can write its activities row, and
a stubbed `_tg` capture so no real Bot API call is made. **45 / 45
checks pass.**

- **S1 — happy path.** Seed a done task with title
  `write hello to /tmp/hello.txt` + summary
  `Wrote /tmp/hello.txt (1 line, 5 bytes).`. Reply to its
  notification with `now write goodbye to /tmp/goodbye.txt`. New
  task `#M` exists with title
  `Follow-up: now write goodbye to /tmp/goodbye.txt`,
  `created_at_source='telegram'`, `execution_mode='classic'`,
  `quadrant='do'`, `risk_level='low'`; description contains the prev
  `#N` reference + prev title + prev summary + `---` separator +
  operator's reply. Bridge replies `✅ task #M queued · follow-up
  to #N`.
- **S2 — empty summary.** Seed a done task with `output_summary=NULL`.
  New task's description carries the literal
  `(no output summary recorded)` slot; everything else renders
  correctly.
- **S3 — failed task refusal.** Reply to a `❌ task #N failed`
  notification → reply `task #N failed — chain on a successful task
  or queue a fresh /run`. No new `ops_tasks` row. No
  `task_followup_created` activities row. Cancelled-task parity
  case (S3.5) also refuses with the same hint.
- **S4 — deleted task.** Insert a fixture task, seed its
  notification_log row, `DELETE FROM ops_tasks WHERE id=N`, then
  reply to the (now-orphan) notification. Bridge replies
  `task #N not found — notification may reference a deleted task`.
- **S5 — truncation rules.** Seed a task with a 100-char title and a
  500-char output_summary. New task's description shows the title
  truncated at 80 chars + `…` (never 81+ T's) and the summary
  truncated at 300 chars + `…` (never 301+ S's).
- **S6 — multi-hop.** Run S1, then manually mark the new follow-up
  as `done` with its own summary and seed a fresh notification_log
  row. Reply to that notification with a third instruction. New
  task's description references the immediate parent (`#M`), not
  the grandparent (`#N`).
- **S7 — audit log.** Each successful follow-up writes exactly one
  `activities` row with `event_type='task_followup_created'` and
  detail containing `prev_task_id`, `new_task_id`, `prev_title`
  (full, not truncated), `operator_reply_first_60` (exactly 60
  chars), `source: 'telegram'`.
- **S8 — dispatcher triggered inline.** Within 2 s of the follow-up
  creation, an `activities` row with `event_type='dispatcher_trigger'`
  is written by `/api/dispatcher/trigger` (proving the bridge's
  inline call reached the endpoint and Popen'd the heartbeat).
- **S9 — backward compat.** Reply to a decision notification still
  flips `ops_decisions.status='answered'` with the typed answer.
  Reply to an inbox notification does not create a follow-up task.
  Reply to a 🛑 RISK-GATED notification still flips the task to
  `pending` and writes `task_risk_approved` (not
  `task_followup_created`). None of those paths create an
  `ops_tasks` row via the v0.6.4 surface.
- **S10 — `/help`.** Reply still renders the existing usage card and
  now mentions the task-complete follow-up pattern.
- **NFR11 — malformed input.** Whitespace-only text is filtered by
  the existing `_handle_message` guard before reply-to-msg routing
  (no crash, no task). Very-long (5000-char) reply still creates a
  task whose title is `Follow-up:` + 60 chars + `…`. The long-poll
  loop does not raise.

### Operator flow

```bash
cc restart                    # no migration this release; bridge restarts.
# In Telegram:
/run write release notes v2.4 to docs/release-notes-v2.4.md
# Wait for ✅ task #42 done · Saved to docs/release-notes-v2.4.md (~8 sections)
# Reply to that message with: now write the PR description from those notes
# Bridge: ✅ task `#43` queued · follow-up to `#42`
# Continue the chain — reply to the #43 completion with the next instruction.
```

### Tunables

No new env vars. The 80 / 300 / 60 truncation thresholds are
intentionally hard-coded — different operator preferences here is a
v0.7+ concern, not Phase 2.

### Not in this release (deferred)

- **`parent_task_id` column** on `ops_tasks` for proper lineage
  tracking. Context-in-description is sufficient for MVP; lineage
  becomes useful only if TaskBoard gains a chain visualization
  (v0.7+ UI work).
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

---

## v0.6.3 — multi-account port + FSEvents opt-in + wip cleanup

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.3-amendment.md`,
applied on top of current `main` HEAD (post v0.6.2). Clean-up release —
not new feature work. Three wip commits (`c877468`, `9e51c76`,
`b1f5650`) landed on main between the v0.6.1 spec and the v0.6.1 ship
without CHANGELOG mention. This release labels them, completes the
half-shipped pieces (multi-account UI was rendering an empty state
because the backend never made the round-trip from the install), and
gates the experimental watcher behind opt-in.

Numbered `v0.6.3` (patch over v0.6.2) — consistent with v0.6.1 /
v0.6.2 patch-cadence. Alternative `v0.7.0` was defensible (multi-
account is a real feature surfacing for the first time end-to-end) and
the installed copy's `db.py` actually labels the migration "v0.7.0 —
multi-account tagging"; chose `v0.6.3` because the work that USED to
be v0.7.0 is finishing rather than starting fresh.

### What ships

- **GMT+7 finish (`c877468`) — production.** Already-clean threading
  of `fmtDateTimeUTC7` / `fmtTimeUTC7` through `AttentionBar`,
  `DecisionsCard`, `InboxCard`, `LiveSessionsCard`, `SchedulesCard`,
  `SkillsRegistry`, `SystemHealthStrip`, `DecisionsPage`. Continuation
  of `a505d24`. Already shipped in `main` — labelled here for the
  full wip-cleanup attribution.
- **Per-event daily token attribution (part of `9e51c76`) —
  production.** Sessions that span midnight now split tokens across
  calendar days correctly via per-event `daily_usage` keyed by
  `(date, model, source)`. Already shipped in `main` — labelled here.
- **Multi-account backend port (NEW IN v0.6.3) — completes the UI
  half from `9e51c76`.** Adds `scripts/helpers/accounts.py` (106-line
  verbatim port from install: hook-hint > entrypoint-proxy resolver,
  `lru_cache`-backed config reload, `label_for` / `all_account_ids` /
  `reload_config` public symbols) and
  `scripts/hooks/session_start_account_snapshot.py` (91-line
  verbatim port; stdlib only; never blocks session start; stderr-only
  logging). Adds `sessions.account_id` TEXT column + idempotent
  `idx_sessions_account` index via `_migrate_add_column`, additive
  next to v0.6.0's `cost_source` and v0.5.0-mvp2's `created_at_source`
  (three additive columns coexist on the same table). `sync_sessions.py`
  stamps `account_id` per row right after the existing
  `_derive_cost_source` call, using the hook-hint > entrypoint-proxy
  chain — safe when no `accounts.json` is configured because the
  helper returns the configured `fallback` (default `"unknown"`).
  `server.py`'s `/api/summary` gains the `by_account` block (one row
  per account today + a `setdefault` pass to surface every configured
  account even at zero usage). Pre-shipped UI in
  `AccountBreakdownCard.tsx` now renders real rows instead of the
  empty state.
- **FSEvents opt-in (NEW IN v0.6.3) — `CC_USE_FSEVENTS=1` gate.**
  Wraps the `watchdog` import in an env-gate; restores the proven
  120-second polling loop as the unconditional default branch (the
  spec called this the "pre-experiment polling path"). Matches the
  install copy's already-reverted-to-polling behaviour. `watchdog`
  stays in `requirements.txt` with an inline comment marking it
  optional so the single-file install path keeps working — the gate
  ensures the experimental code isn't exercised unless explicitly
  opted in. Includes graceful degradation: if an operator sets
  `CC_USE_FSEVENTS=1` without `watchdog` installed, the server logs
  the missing import once and falls back to the polling loop instead
  of crashing.

### What didn't ship

(Nothing — all four wip-derived bits now have explicit handling. No
deferrals.)

### Schema delta

One column, additive next to v0.6.0's `cost_source` and
v0.5.0-mvp2's `created_at_source`:

| Table | Column | Type | Default |
|---|---|---|---|
| `sessions` | `account_id` | TEXT | NULL |

Plus `CREATE INDEX IF NOT EXISTS idx_sessions_account ON
sessions(account_id)`. Both idempotent — running `apply_migrations()`
three times in succession is a no-op after the first.

### Verified

All 8 stop conditions from the amendment pass against a tempdir
`CC_INSTALL_DIR`:

- **S1 — migration idempotent.** `apply_migrations()` 3× in a row;
  `sessions.account_id` + `idx_sessions_account` present once.
- **S2 — `/api/summary.by_account` shape.** No-config → single
  `unknown` row with today's session totals. Valid `accounts.json`
  with personal + work → both keys present, work surfaces at zero
  usage via the `setdefault` path, labels mapped via `label_for`.
  Direct HTTP via `TestClient(server.app)` returns the exact dict
  shape `AccountBreakdownCard` consumes (`{label, sessions, tokens,
  cost_usd}` per account).
- **S3 — `AccountBreakdownCard` renders.** UI type contract
  (`AccountSummaryRow` in `types.ts`) matches server response 1:1;
  no UI code change required.
- **S4 — hook smoke.** `session_start_account_snapshot.py` writes
  `data/account-hints/<sid>.json` with full record (session_id +
  account_uuid + email + organization_uuid + display_name +
  captured_at) within 1s of receiving stdin JSON. Malformed
  `~/.claude.json` does NOT block — hook still exits 0, logs the
  failure to stderr, and writes a hint with `account_uuid=None`.
- **S5 — FSEvents opt-out default.** `CC_USE_FSEVENTS` unset →
  `server.USE_FSEVENTS=False`; sync loop log reads
  `using 120s polling loop`. No `Observer` instantiated.
- **S6 — FSEvents opt-in.** `CC_USE_FSEVENTS=1` + `watchdog`
  installed → `server.USE_FSEVENTS=True`; sync loop log reads
  `[sync_loop] FSEvents watching ~/.claude/projects`. With env set
  but `watchdog` missing, server logs the ImportError once and
  degrades to the polling loop instead of crashing.
- **S7 — backward compat (no config + no env).** Helper returns
  `"unknown"` fallback; `all_account_ids() == []`; sessions with no
  `account_id` surface as the single `unknown` row in `by_account`.
  All v0.6.2 features (Telegram `/run`, `/status`, `/snooze`)
  untouched.
- **S8 — idempotent on re-install.** Schema + helpers + hook land in
  place; running `sync_sessions.run_sync()` twice in succession on a
  seeded JSONL leaves `account_id='work'` for the test entrypoint
  (`claude-desktop`) without re-querying or drift.

### Operator flow

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
# 2. Add the SessionStart hook to ~/.claude/settings.json (README has
#    the snippet).
# 3. Restart Claude Code so the hook fires for new sessions.
# 4. Wait one sync cycle. The Account split card populates.
```

For FSEvents (optional):

```bash
echo "CC_USE_FSEVENTS=1" >> ~/.command-centre/.env
cc restart
# Verify in logs: "[sync_loop] FSEvents watching ~/.claude/projects"
```

### Not in this release

- **Auto-registering the SessionStart hook** in `~/.claude/settings.json`.
  Operator-configured. Install.sh could prompt interactively, but
  operators who don't want multi-account shouldn't be hassled. README
  documents the manual step.
- **`cc doctor` checks for multi-account / FSEvents config** —
  defaults are zero-config so the doctor has nothing useful to say.
  Add when an operator actually trips on a misconfiguration.
- **Backfill on existing closed sessions** beyond the
  entrypoint-proxy path. Hook hints only apply to sessions that
  started after hook registration. Acceptable — `cc sync` re-runs
  the proxy on every cycle.

---

## v0.6.2 — Telegram `/snooze <decision_id> [duration]`

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.2-amendment.md`,
applied on top of current `main` HEAD (post v0.6.1). Second Phase 2
feature from the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Bridge-only — single
file change to `telegram_bridge.py` plus this CHANGELOG entry. No
schema changes (column `notification_log.snoozed_until` already exists,
has been unused since v0.3.0), no new endpoints, no breaking changes.

Numbered `v0.6.2` (patch over v0.6.1) — even tighter scope, same
Telegram-bridge subsystem.

### Why this release

A decision pings, operator can't answer now (meeting, call, asleep),
outbound tick re-fires every 30s. Today: answer half-mind or mute the
whole chat. `/snooze 42 30m` says "remind me in 30 min" — notification
suppressed during the window, re-fires once after elapse, then default
dedupe resumes. Matches the phone "snooze button" UX, then revert.

The wire was designed in v0.3.0 (`snoozed_until` column + intent noted
in the original CHANGELOG) but never built. This release finishes that
work.

### What ships

All changes in `command-centre/scripts/telegram_bridge.py` (+170 / -6)
plus a dev smoke script (`scripts/dev/smoke_v0_6_2.py`). No new
dependencies, stdlib only.

- **`/snooze <decision_id> [duration]`** slash command. Extends
  `_CMD_WITH_ID_RE` from `(answer|reply|approve|cancel)` to
  `(answer|reply|approve|cancel|snooze)`. The body group is already
  optional, so `/snooze 42` (no duration) parses cleanly and defaults
  to 30m inside the handler.
- **`_handle_snooze(chat_id, message_id, decision_id, duration_str)`** —
  parse duration via the strict `^(\d+)([mhd])$` grammar, verify the
  decision exists and is still pending (direct `ops_decisions` query),
  verify a `notification_log` row exists for this decision + chat,
  `UPDATE notification_log SET snoozed_until=?` to `now_utc + duration`,
  and insert an `activities(event_type='decision_snoozed', detail=…)`
  row with `source='telegram'` — matches mvp2's FR19 audit-everything-
  from-Telegram pattern for state-changing commands. Reply with
  `✅ decision #{id} snoozed for {duration} — next re-fire {HH:MM GMT+7}`
  using a new `_future_local_str(seconds_ahead)` helper alongside
  v0.6.1's `_now_local_str`.
- **`_now_utc_iso()`** sibling to `_now_local_str()` — returns
  `YYYY-MM-DD HH:MM:SS` UTC matching SQLite's `datetime('now')` so
  `notification_log.snoozed_until` comparisons stay lexicographic.
- **Fix `_already_notified`** to respect `snoozed_until`. Previously
  blocked unconditionally on any matching row; now returns False when
  `snoozed_until <= now_utc_iso()`, letting the outbound tick re-fire
  once after the window elapses. Behaviour change is invisible to
  operators until they actually use `/snooze` — the column has been
  NULL on every existing row since v0.3.0, so `_already_notified`
  still returns True for those (NULL branch).
- **Fix `_record_notify`** to clear `snoozed_until` back to NULL after
  a re-fire. The existing `INSERT OR IGNORE` is a no-op for an existing
  row, so without this fix the past `snoozed_until` would persist and
  the next tick would re-fire forever. New trailing `UPDATE … SET
  snoozed_until=NULL WHERE … AND snoozed_until <= ?` makes the
  semantics: snooze → wait → re-fire ONCE → permanent dedupe (unless
  operator re-snoozes).
- **Reply-to-msg snooze** — reply to a decision notification with
  `/snooze 30m` (no ID) and the bridge resolves the decision_id via
  `_lookup_by_tg_message`. New `_CMD_SNOOZE_REPLY_RE` matches the
  `/snooze` or `/snooze <duration>` shorthand inside the reply-to-msg
  branch. Mirrors mvp2's reply-to-RISK-GATED → `/approve` pattern.
  Non-decision targets reject with `snooze is decisions-only — this
  is a {event_type} notification`.
- **Scope: decisions only.** Inbox / task-complete / risk-gated snooze
  deferred — different UX shapes (inbox rarely re-fires; task-complete
  fires once; risk-gated has `/approve` + `/cancel` as the natural
  resolution). Generalisation waits for usage signal.
- **NFR11.** Malformed input (`/snooze`, `/snooze abc`, `/snooze 42
  wat`, `/snooze 42 0m`, `/snooze 42 -1m`, `/snooze 42 30s`) reply
  with the usage hint and never raise. Duration parsing is strict;
  no natural-language fallback.
- **24h cap.** `^(\d+)([mhd])$` matches, but the parser clamps to
  86400 s and the reply prefixes `⚠️ max 24h — capped · ` so the
  operator sees both the cap notice and the success confirmation in a
  single message. Operator can re-snooze for longer chains if they
  genuinely want.
- **`/help` text** extended with the new verb and a `Reply to a ❓
  DECISION notification with /snooze [duration] to defer it.`
  footer line.

### Schema delta

None. `notification_log.snoozed_until` exists since v0.3.0; this
release wires it up. Pre-existing rows on every install have
`snoozed_until IS NULL`, so the `_already_notified` semantic change
is invisible until the first `/snooze` writes a non-null value.

### Verified

`command-centre/scripts/dev/smoke_v0_6_2.py` walks all 12 amendment
stop conditions against a real FastAPI server on `127.0.0.1:8866`
(spawned in a temp `$CC_INSTALL_DIR` so the migration runs fresh) and
a stubbed `_tg` capture so no real Bot API call is made. **39 / 39
checks pass.**

- **S1 — happy path with default duration.** `/snooze 42` (no body)
  sets `snoozed_until ≈ now + 30m`, replies
  `✅ decision #42 snoozed for 30m — next re-fire HH:MM GMT+7`, and
  `_already_notified('decision', '42')` flips to True.
- **S2 — explicit duration.** `/snooze 42 2h` sets `snoozed_until ≈
  now + 7200s`, reply mentions `2h`.
- **S3 — re-snooze.** Two consecutive snoozes (30m then 2h) advance
  the timer; second `snoozed_until` is later than the first by ≈ 5400s
  net (90 minutes minus the 1s sleep between the calls).
- **S4 — re-fire after elapse.** Hand-UPDATE `snoozed_until` to 30s in
  the past; `_already_notified` returns False; calling `_record_notify`
  (what the outbound tick would call after a re-send) clears
  `snoozed_until` back to NULL; the next `_already_notified` returns
  True. No re-fire loop.
- **S5 — reply-to-msg snooze.** Synthetic `reply_to_message` pointing
  at the seeded `telegram_message_id` resolves the decision via
  `_lookup_by_tg_message` and snoozes correctly. Bare `/snooze` (no
  duration) in the reply branch uses the 30m default.
- **S6 — malformed duration.** `wat` / `42 wat` / `0m` / `-1m` /
  `30s` all reply with the usage hint and leave `snoozed_until`
  untouched. The long-poll loop does not raise.
- **S7 — cap at 24h.** `/snooze 42 7d` clamps to `now + 86400s`,
  reply leads with `⚠️ max 24h — capped`.
- **S8 — non-existent decision.** `/snooze 9999` replies `decision
  #9999 not found`.
- **S9 — already-answered decision.** Manually flip `ops_decisions.
  status='answered'`, then `/snooze` replies `decision #{id} already
  answered`. `snoozed_until` stays NULL.
- **S10 — not-yet-notified decision.** Pending decision in DB but no
  `notification_log` row → reply `decision #{id} not yet notified —
  cannot snooze`.
- **S11 — audit log.** Each successful `/snooze` writes
  `activities(event_type='decision_snoozed', detail={decision_id,
  duration, snoozed_until, source: 'telegram'})`.
- **S12 — backward compat.** All existing regexes (`_CMD_WITH_ID_RE`,
  `_CMD_RUN_RE`, `_CMD_STATUS_RE`) continue to match `/answer`,
  `/reply`, `/approve`, `/cancel`, `/run`, `/status`. End-to-end
  `/answer` still flips a decision to `'answered'`. `/help` renders
  the usage card and now includes the `/snooze` line.

### Operator flow

```bash
cc restart                    # no migration this release; bridge restarts.
# In Telegram (operator chat):
/snooze 42                    # default 30m — outbound tick stops re-firing
/snooze 42 2h                 # custom window
/snooze 42 7d                 # ⚠️ max 24h — capped (re-snooze for longer)
# Reply to a ❓ DECISION ping with `/snooze 30m` — no ID needed.
```

### Tunables

No new env vars. The 24h cap and 30m default are intentionally hard-
coded — different operators wanting different defaults is a v0.7+
concern, not Phase 2.

### Not in this release (deferred — other Phase 2 features)

- **Generalised snooze** for inbox / risk_gated / task_complete event
  types — defer until concrete usage signals demand it. Different UX
  shapes; risk_gated already has `/approve` + `/cancel` as the natural
  resolution.
- **`/snoozes` listing command** showing active snoozes with wake
  times. Adding `N snoozed` to `/status` (v0.6.1) is a v0.7
  enhancement.
- **`/unsnooze 42`** to clear the timer early — operator workaround is
  `/answer 42 …` which clears the dedupe row anyway.
- **Per-event-type snooze caps** (e.g., risk_gated cannot be snoozed
  > 1h) — premature; single 24h cap for now.

---

## v0.6.1 — Telegram `/status` snapshot

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.1-amendment.md`,
applied on top of current `main` HEAD (post v0.5.0-mvp2). First Phase 2
feature from the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Bridge-only — single
file change to `telegram_bridge.py` plus this CHANGELOG entry. No
schema changes, no new endpoints, no breaking changes.

Numbered `v0.6.1` (patch-level over v0.6.0) — small additive feature
confined to the Telegram bridge subsystem.

### Why this release

mvp1 + mvp2 shipped the read/write Telegram loop. The remaining gap was
**passive awareness**: between active operations, the operator's most
common question is "What's my agent doing right now?" — and the answer
required opening the dashboard. `/status` collapses the
KpiRow + DispatcherStrip + AttentionBar + live-sessions view into a
single Telegram reply.

This is the first feature in the corpus designed for glanceable
telemetry, not active control. Expected use pattern is multiple times
per day — morning check, lunch break, post-meeting, before bed.

### What ships

All changes in `command-centre/scripts/telegram_bridge.py` (+222 / -4)
plus two dev smoke scripts (`scripts/dev/smoke_status_format.py`,
`scripts/dev/smoke_v0_6_1.py`). No new dependencies, stdlib only.

- **Inbound `/status` parser.** New `_CMD_STATUS_RE` (third sibling to
  `_CMD_WITH_ID_RE` and `_CMD_RUN_RE`) — strict whole-message match
  `^/status\s*$`, no args. `/status foo` does not match the regex and
  falls through to the `/help` branch (NFR11).
- **`_handle_status(chat_id)`** — fetches 5 read-only GET endpoints
  in sequence with per-call try/except:
  - `GET /api/system/dispatcher` — running, max_concurrent, free_slots,
    `today_cost_api_pool_usd`, `today_cost_max_sub_usd`,
    `daily_cost_cap_usd`, `cost_capped`, `back_pressure`,
    `hard_risk_gate`
  - `GET /api/decisions?status=pending` — count via `len(items)`
  - `GET /api/sessions/live` — count + longest-running age computed
    from `started_at` (parsed as UTC, age in seconds since
    `datetime.now(timezone.utc)`)
  - `GET /api/tasks?status=done|failed|awaiting_approval` — three
    calls, today-bucketed client-side (`completed_at` for done/failed,
    `created_at` for awaiting_approval; timestamps parsed as UTC and
    projected into local-tz `YYYY-MM-DD` before comparison)
  - `GET /api/system/state` — checks
    `state.emergency_stop.value == "1"`
- **`_format_status(metrics)`** — Markdown V1, mobile-readable.
  Steady state 5–6 lines:

  ```
  *STATUS*  _14:32 GMT+7_

  ⚙️  Dispatcher  *2/3* running · *1* free
  💰 Today        api *$3.42* · max *$1.18* · cap $10.00
  📨 Decisions    *2* pending          ← omitted when count = 0
  🟢 Live         *1* session · 4m active   ← omitted when count = 0
  ✅ Tasks today  5 done · 1 failed · 0 risk-gated   ← always shown
  ```

  Conditional alerts appended only when their state is active:

  ```
  🛑 *Emergency stop ON* — dispatcher refusing new work
  ⚠️  *Cost cap reached* (api_pool $10.00 / $10.00)
  ⚠️  *Back-pressure active* — 3/3 slots full
  ```

- **Time helpers** — `_now_local()` / `_now_local_str()` /
  `_today_local_date()` pinned to `Asia/Ho_Chi_Minh` via stdlib
  `zoneinfo` (Python 3.9+; falls back to naive system local time if
  zoneinfo isn't available — the `GMT+7` suffix drops but the format
  otherwise renders). Matches the v0.5.0-mvp1 timezone-fix convention
  (commit `a505d24`).
- **No audit-log row.** Deliberate departure from mvp2's audit-
  everything-from-Telegram pattern. `/status` is read-only telemetry,
  not a state-changing operator action — no `activities` insert.
- **Partial-failure resilience.** Failed endpoints render `?` for
  their metric and a footer line `_some metrics unavailable — see
  logs_`. Total server failure (every endpoint refused or timed out)
  short-circuits to `⚠️ status check failed — dashboard server may
  be down. Try `cc status` or `cc restart`.` and never crashes the
  inbound long-poll loop (NFR11).
- **`/help` text** extended with the new verb.

### Schema delta

None. All data sourced from existing endpoints; only the bridge
subsystem changes.

### Verified

`command-centre/scripts/dev/smoke_v0_6_1.py` walks all 6 amendment
stop conditions against a real FastAPI server on `127.0.0.1:8765`
(with the freshly-migrated v0.6.0 DB seeded with one pending
decision + one done task + one risk-gated task + one live session)
and a stubbed `_send_message`. 20 checks pass.

- **S1 — happy path.** `/status` returns the four steady-state lines
  (Dispatcher, Today, Decisions, Live, Tasks today) plus the italic
  `*STATUS*  _HH:MM GMT+7_` header.
- **S2 — cost-cap alert.** Fixture with `cost_capped=true` renders the
  `⚠️ Cost cap reached (api_pool $X.XX / $Y.YY)` line; clearing the
  flag removes it.
- **S3 — emergency stop.** `POST /api/system/emergency-stop` →
  `/status` reply includes `🛑 Emergency stop ON`. `POST
  /api/system/emergency-resume` → alert line gone.
- **S4 — partial failure.** Pointing the bridge at a closed port (no
  server) renders the fallback `⚠️ status check failed — dashboard
  server may be down. Try `cc status` or `cc restart`.` and the
  inbound loop continues without exception.
- **S5 — mobile render.** Worst-case line length is 54 chars (happy +
  alerts fixtures); under the 60-char ceiling that keeps the
  "table-ish" alignment readable on 390 px iPhone before Telegram's
  client wraps. No horizontal scroll under any fixture (NFR4
  satisfied — mobile clients always vertical-wrap text).
- **S6 — backward compat.** `_CMD_WITH_ID_RE` and `_CMD_RUN_RE`
  unchanged. `/answer`, `/reply`, `/run`, `/approve`, `/cancel`,
  `/help`, `/start` all still route correctly. `/status foo` and
  `/statusfoo` fall through to `/help` as required by NFR11.

A parallel fixture-only smoke
(`command-centre/scripts/dev/smoke_status_format.py`) renders five
named scenarios (happy / idle / all-alerts / partial / dispatcher-only)
against `_format_status` directly with no HTTP, useful for locking
template changes without a running server.

### Operator flow

```bash
cc restart                    # no migration this release; bridge restarts.
# In Telegram (operator chat):
/status                       # → 5-6 line snapshot, optional alerts
```

### Tunables

No new env vars. The bridge uses the existing
`CC_DASHBOARD_URL` / `CC_HOST` / `CC_PORT` resolution from earlier
releases. Local-tz handling honours `Asia/Ho_Chi_Minh` via stdlib
zoneinfo — no `TZ` env var or `tzdata` package needed on macOS.

### Not in this release (deferred — other Phase 2 features)

- **`/snooze <decision_id> <duration>`** wires up the existing
  `notification_log.snoozed_until` column. Spec separately if signal
  supports it after `/status` lands.
- **Inline keyboard `[Yes] [No]` for binary DECISIONS** — needs a
  "what's binary?" heuristic; defer until concrete examples
  accumulate.
- **`/schedule "<cron>" <prompt>`** creates `ops_schedules` rows from
  TG — dashboard `ScheduleComposer` handles this well, TG-only use
  case is rare.
- **Reply-to-task-complete starts a follow-up `/run`** — chained
  workflows; defer until usage signals demand it.

---

## v0.6.0 — SkillLauncher + cost-source disambiguation

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.6.0-amendment.md`,
applied on top of the v0.5.0-mvp1 working tree. Additive — no breaking
changes to existing endpoints, no row drops, idempotent migration.
Re-running `install.sh` against an existing v0.5.x install upgrades the
schema in place and preserves every row.

### What ships

- **`SkillLauncher` panel** on the Command page (between Token usage and
  Observability, inside `CollapsibleSection id="launcher"`). Renders
  `user_invocable=1` skills as a 3-col grid, sorted by `launch_count`
  desc → `last_launched_at` desc → name. Each card shows the skill name,
  preset title, 30-day avg cost, last-launched age, a primary **Launch**
  button (optimistic spinner → `↗ Task #N` chip for 2.5s), and a pencil
  that expands an inline preset editor in-place — not a Sheet, not a
  Modal. Esc cancels. Empty state teaches the operator how to mark a
  skill `user_invocable: true` and run `cc sync`.
- **New endpoints** —
  `POST /api/skills/{name}/launch` (one-click run; defaults via the
  preset → frontmatter → hardcoded-default fallback chain; cost_source
  hard-coded to `'api_pool'`; pokes the dispatcher inline so the task
  starts within ~1s instead of waiting for the next 120s heartbeat) and
  `PATCH /api/skills/{name}/preset` (replace, not merge — `null` clears).
  `GET /api/skills` rows now include `preset`, `last_launched_at`,
  `launch_count`, `avg_cost_usd_30d`.
- **`cost_source` enum** (`api_pool` | `max_sub` | `unknown`,
  `codex_api` slot reserved for a future backend swap and accepted by
  the validator from day one) on `ops_tasks` + `sessions`. Dispatcher
  and launcher write `api_pool` (belt-and-braces UPDATE in `run_once`
  for pre-migration pending rows); `sync_sessions._derive_cost_source`
  pins `max_sub` for interactive REPL/IDE sessions via a left-join
  against `ops_tasks`. Race covered by a two-line back-fill in
  `task_tracker.update_task` whenever the dispatcher stashes a
  `session_id`.
- **Cost-split endpoints, backwards-compatible.** Every cost-bearing
  endpoint keeps its existing top-level `cost_usd` total and adds a
  `cost_by_source` sibling `{api_pool, max_sub, unknown}`:
  - `GET /api/summary` — today rollup
  - `GET /api/usage/tokens` — per daily row + window total
  - `GET /api/system/dispatcher` — adds
    `today_cost_api_pool_usd`, `today_cost_max_sub_usd`,
    `today_cost_unknown_usd`
  - `GET /api/sessions` — each row gains `cost_source`; new
    `?cost_source=` filter param
  - `GET /api/tasks` — each row gains `cost_source`
- **`MISSION_CONTROL_DAILY_COST_CAP_USD` reads api_pool only.** Both
  the dispatcher's `_today_cost_usd()` and the `AttentionBar`
  `cost_capped` issue derive cap state from
  `cost_source='api_pool' AND DATE(completed_at,'localtime')=today`.
  Max-sub spend (Pro/Max-subsidised interactive sessions) is no longer
  rolled into the cap math — capping on it would surprise users who
  don't pay per-token interactively. Issue copy: "API-pool spend reached
  cap ($X.XX of $Y.YY today). Max-sub usage continues."
- **UI splits** —
  - **KpiRow** cost tile gains a second line `api $X.XX · max $Y.YY`
    in JetBrains Mono, dim; an amber `? $Z.ZZ` appended when unknown
    spend exists today. Line 2 is omitted entirely when the day total
    is zero (no visual noise on an empty tile).
  - **DispatcherStrip** cost segment reads `today api $X.XX / cap $Y.YY`
    (was `today $cost / cap`). Tone tracking unchanged.
  - **TokenUsageCard** gains a thin per-day cost band beneath the
    token stacks: two stacked mini-bars per day (cyan = api_pool,
    grey = max_sub), ~6px tall, hover tooltip showing the per-day
    triplet. Band hides when both subtotals are zero for the full window.
  - **Sessions list** (`SessionsPage` at `/sessions` + the legacy
    `SessionsTable` on `/activity`) gains a **Source** column between
    Model and Tokens with `api` (cyan) / `max` (grey) / `?` (amber)
    pills, plus an `all/api/max/?` filter toolbar.
  - **TaskBoard** cards gain a small source pill next to the risk
    pill. Hidden when the task is `pending`/`awaiting_approval` AND
    `cost_usd IS NULL` (don't clutter cards with placeholder pills).
- **`cc doctor`** gains `cost_source backfill` check: PASS when zero
  ended sessions still read `'unknown'`; WARN (non-fatal) when pre-v0.6
  rows exist — they surface as amber `?` tags in the UI and stay
  unguessed. Manual recourse documented in HANDOVER.md.

### Schema delta

Five columns total across three tables, all through the existing
`_migrate_add_column` helper. Re-runnable; existing rows on all three
tables get `'unknown'` / `NULL` / `0` on first boot.

| Table | Column | Type | Default |
|---|---|---|---|
| `skills` | `preset_json` | TEXT | NULL |
| `skills` | `last_launched_at` | TEXT | NULL |
| `skills` | `launch_count` | INTEGER NOT NULL | `0` |
| `ops_tasks` | `cost_source` | TEXT NOT NULL | `'unknown'` |
| `sessions` | `cost_source` | TEXT NOT NULL | `'unknown'` |

### Verified

- Migration: fresh DB → `init_db()` + two consecutive `apply_migrations()`
  calls clean, all 5 columns present, defaults applied.
- `tsc --noEmit` clean. `vite build` 524KB / 156KB gzipped.
- `cc doctor`: PASS path renders `cost-source backfill complete — 0 rows
  pending`; WARN path renders the pre-v0.6 row count with the manual-
  recourse pointer.
- Playwright (`tests/e2e/v0.6.spec.ts`, 7 specs against
  `page.route` fixtures): KpiRow two-source readout renders + hides on
  zero-total; SkillLauncher sorts by launch_count and filters out
  non-invocable skills; one-click launch POSTs to
  `/api/skills/{name}/launch` and surfaces the `↗ Task #N` chip;
  preset edit round-trips across a page reload; sessions-list filter
  narrows the request `cost_source` param and re-renders.

### Operator flow

```bash
# Existing v0.5.x install — just restart, migration runs in lifespan.
cc restart
cc doctor                  # cost-source backfill should be ok or warn

# Mark a skill invocable.
# Add `user_invocable: true` to its frontmatter, then:
cc sync

# Open the dashboard → "Skill launcher" section now shows the skill.
# Click Launch — task transitions pending → running → done without
# ever opening TaskComposer.
```

### Tunables (env vars or .env)

No new env vars. The cap variable + default-model variable are unchanged
from v0.2.0:

| Var | Default | What changed in v0.6.0 |
|---|---|---|
| `MISSION_CONTROL_DAILY_COST_CAP_USD` | unset = off | Now reads `api_pool` cost only |
| `MISSION_CONTROL_DEFAULT_MODEL` | unset | Used as launcher's final fallback when preset has no model |

### Not in this release (deferred)

- **Codex backend.** `cost_source='codex_api'` is reserved in the
  validator; backend pluggability is a future change with its own brief.
- **Per-skill cost budgets.** Different concept from the daily cap.
- **Obsidian embed mode** (`/embed` route).
- **Automatic backfill heuristics** for pre-v0.6 `unknown` rows.

---

## v0.5.0-mvp2 — Telegram bridge inbound `/run`, `/approve`, `/cancel`

Built against the amendment in
`observability/(C) build-your-own-dashboard-prompt-v0.5.0-mvp2-amendment.md`,
applied on top of current `main` HEAD (post-v0.6.0 merge). MVP commit 2
of 2 per the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Closes Journey 4 —
operator's inbound counterpart to mvp1's outbound `task_complete` +
`risk_gated` push.

Additive. No breaking changes to existing endpoints, no row drops,
idempotent migration. Re-running the installer against any v0.5.x
or v0.6.x install upgrades the schema in place and preserves every row.

### Why this release

mvp1 closed the outbound feedback loop. mvp2 closes inbound: operator
can launch tasks (`/run <prompt>`), approve risk-gated tasks
(`/approve <id>`), and cancel pending tasks (`/cancel <id>`) entirely
from Telegram. Without mvp2, Journey 4 (late-night risk-gate intercept)
had no phone-side recovery — operator had to walk to the desk and open
the dashboard.

### What ships

All changes in `command-centre/scripts/telegram_bridge.py`,
`command-centre/scripts/routers/tasks.py`, and
`command-centre/scripts/db.py`. Plus a smoke harness at
`command-centre/scripts/dev/smoke_mvp2.py`.

- **Inbound `/run <prompt>` parser** (FR1-FR4). `_CMD_RE` split into
  `_CMD_WITH_ID_RE` (for `answer|reply|approve|cancel <int_id>`) and
  `_CMD_RUN_RE` (free-text remainder, multi-line via DOTALL). Title is
  first 80 chars of the prompt (line breaks collapsed); description
  carries the full prompt up to 3000 chars. Over-cap → usage hint with
  the actual length; empty → usage hint. POSTs to `/api/tasks` with
  `created_at_source='telegram'`.
- **Inbound `/approve <task_id>`** (FR16) and **`/cancel <task_id>`**
  (FR18). Thin wrappers around the API endpoints — no business logic
  in the bridge. Each passes `?source=telegram` so the activities row
  the backend writes is tagged for FR19 audit. 404 / 4xx error bodies
  are surfaced to the operator verbatim (truncated to 500 chars,
  `_md_safe`-scrubbed).
- **Reply-to-msg on 🛑 RISK-GATED notifications** routes to
  `_handle_approve(task_id)` (FR17). Reply-to-cancel is NOT supported —
  explicit `/cancel <id>` only, to avoid accidental cancels from
  casual replies.
- **New endpoint `POST /api/tasks/{task_id}/cancel`** mirroring the
  existing `/approve` pattern in `routers/tasks.py`. Accepts a prior
  status of `pending` or `awaiting_approval`; returns 400 with an
  explicit message for `running` (points to `/api/system/emergency-stop`)
  or any terminal state. Writes `activities` row
  `event_type='task_cancelled'` with `detail={task_id, prior_status,
  source}` for forensic reconstruction.
- **`POST /api/tasks/{task_id}/approve`** extended with `?source=`
  query parameter and the matching `activities` row
  (`event_type='task_risk_approved'`) — closes the FR19 audit gap.
  Default `source='api'` keeps existing dashboard callers working
  unchanged (FR24); bridge sends `'telegram'`.
- **`POST /api/tasks`** body validator accepts an optional
  `created_at_source` field (must be a non-empty string when present;
  defaults to `'dashboard'`). Coexists with v0.6.0's hardcoded
  `cost_source='api_pool'` — different columns, different concerns.
- **`/help` text** extended with the three new verbs and a note that
  replying to a 🛑 RISK-GATED message approves the task.

### Schema delta

One column, orthogonal with v0.6.0's `cost_source` migration:

| Table | Column | Type | Default |
|---|---|---|---|
| `ops_tasks` | `created_at_source` | TEXT NOT NULL | `'dashboard'` |

Through the existing `_migrate_add_column` helper. Existing rows
backfill to `'dashboard'`. Re-runnable.

### Pre-impl spike findings

- `_CMD_RE` at `telegram_bridge.py:360` matched `(answer|reply)` only;
  not extensible to `/run` (free-text, no leading int) without a split.
  Two patterns now coexist.
- `/api/tasks/{id}/approve` already existed (`tasks.py:135`) and used
  HTTP 409 for status conflicts; mvp2 keeps that to avoid breaking
  dashboard callers and adds the audit-row INSERT it was missing.
  `/cancel` is new and uses 400 per amendment. The bridge handles both
  uniformly — operator sees the error body either way.
- `_lookup_by_tg_message` already accepted any `event_type` string —
  `notification_log` rows tagged `risk_gated` by mvp1's outbound tick
  resolved without DB changes. Only the routing branch in
  `_handle_message` is new.
- `ops_tasks.status` enum already included `'cancelled'` from v0.1.0.
  No enum extension.

### Verified

`command-centre/scripts/dev/smoke_mvp2.py` walks all 6 stop conditions
end-to-end against a stdlib mock Bot API on port 8767 and a real
FastAPI server (with the freshly-migrated DB) on port 8866.

- **S1 — `/run` happy path.** Row exists with `created_at_source=
  'telegram'`, title is first-80-chars, description carries full
  prompt, bot replied with `✅ task #1 queued · dispatcher pending`.
- **S2 — `/cancel` happy path.** Status flips to `'cancelled'`,
  `activities` row `task_cancelled` with `detail.source='telegram'`
  and `detail.prior_status='pending'`.
- **S3 — `/approve` via reply-to-msg on `risk_gated`.** Task transitions
  `awaiting_approval → pending`; `activities` row `task_risk_approved`
  with `detail.source='telegram'`.
- **S4 — backward compat.** `/answer` flips `ops_decisions.status`,
  `/reply` inserts `direction='user_to_agent'`, `/help` returns the
  usage card (FR23).
- **S5 — NFR11 malformed `/run`.** Empty, whitespace-only, and 3100-char
  prompts each return a usage hint; the inbound loop does not crash.
- **S6 — NFR11 non-integer id.** `/cancel abc` and `/approve xyz` each
  return a usage hint; no API call.

Plus the NFR5 grep audit: bot token absent from every DB row and from
the server's full stdout/stderr capture.

24/24 checks pass.

### Operator flow

```bash
cc restart                            # lifespan runs the new migration
# In Telegram (operator chat):
/run draft release notes for v2.4     # → ✅ task #N queued
/approve 62                           # → ✅ task #62 approved
/cancel 63                            # → ✅ task #63 cancelled
# Or, reply to a 🛑 RISK-GATED notification with any text → approves.
```

### Not in this release (deferred to mvp2-growth / Phase 2)

- **`/status` snapshot** (live sessions, pending decisions, today cost,
  free slots).
- **Inline keyboard `[Yes] [No]`** for DECISIONS.
- **`/snooze <decision_id> <duration>`** wires up
  `notification_log.snoozed_until`.
- **`/schedule "<cron>" <prompt>`** creates `ops_schedules` rows from TG.
- **Reply-to-task-complete starts follow-up `/run`** — Growth feature.
- Topic-per-task in TG supergroup, inline-button risk approval,
  voice-memo → STT, multi-operator allowlist, mobile dashboard surface
  — Phase 3 vision; no commitment.

---

## v0.5.0-mvp1 — Telegram bridge: outbound task_complete + risk_gated push

MVP commit 1 of 2 per the Resource Risk mitigation in the Telegram Remote
Trigger PRD (`command-centre/docs/prd-telegram-remote.md`, commit
`3fd6e35`). Closes the feedback loop for FR11 (task-complete push) and
FR12 (risk-gated push). Backfilled into CHANGELOG retroactively at v0.6.0
spec time — original commit was `1936cdf`.

### What ships

All changes in `command-centre/scripts/telegram_bridge.py` (+107 / -3).

- `_outbound_tick()` now polls `/api/tasks?status=done|failed` and
  `/api/tasks?status=awaiting_approval`, pushing each new transition once
  via `notification_log` dedupe (`event_type='task_complete'` or
  `'risk_gated'`).
- `_format_task_complete()` renders done/failed task summaries with title,
  duration, cost, session, plus `error_message` (failed) or
  `output_summary` (done).
- `_format_risk_gated()` renders gated-task notifications with title,
  `risk_level`, and inline `/approve <id>` + `/cancel <id>` recovery
  hints.
- `_md_safe()` sanitises Markdown V1 control chars (`_ * \` [ ]`) in
  user-content fields to prevent Telegram parser 400s.
- `_outbound_loop()` log line extended: `notified d= i= tc= rg= err=`.

### Pre-impl spike findings

- `/api/tasks/{id}/approve` already exists (`tasks.py:132`). No work
  needed for FR16.
- State name confirmed: `'awaiting_approval'` (PRD assumption correct).
- `/api/tasks/{id}/cancel` does NOT exist yet — deferred to mvp2 (~30
  min estimate).

### Verified

Synthetic `done` task → 30s tick → TG message id=13 delivered,
`notification_log` row written, dedupe verified on re-tick.

### Not in this release (deferred to mvp2)

- Inbound `/run`, `/approve`, `/cancel` parsers (Journey 4 closure).

---

## v0.4.0 — operator UI: Sessions Explorer, Decisions Queue, Telegram bridge status

Three new operator surfaces on the dashboard. UI-heavy release with a
single new health endpoint — no schema changes, no dispatcher behaviour
changes. Backfilled into CHANGELOG retroactively at v0.6.0 spec time —
original commit was `5d0a72b`.

### What ships

- **`/sessions`** — Sessions Explorer. Two-panel layout (projects →
  timeline); client-side grouping over `/api/sessions` (limit=500), no new
  backend route.
- **`/decisions`** — HITL hub combining the existing `DecisionsCard` and
  `InboxCard` plus answered history. Pending count badge in nav (polls
  every 5s).
- **`/api/system/telegram`** — Telegram bridge health endpoint: pgrep
  liveness probe, `notification_log` stats over 24h, stderr-tail of the
  last error. Surfaced as a status row at the top of `/decisions`.

### Frontend

- New pages: `SessionsPage.tsx` (+226 lines), `DecisionsPage.tsx` (+131).
- New panel: `TelegramBridgeStatus.tsx` (+92).
- Nav, router, types, hooks, and api wrappers extended (~57 lines
  combined).

### Backend

- `command-centre/scripts/routers/system.py` (+93 lines) — adds the
  `/api/system/telegram` endpoint.

Total diff: 9 files, +595 insertions, -4 deletions.

---

## v0.3.2 — fix: `cc` via `~/.local/bin` symlink

Bug fix. When `install.sh` symlinks `~/.local/bin/cc` →
`~/.command-centre/bin/cc`, invoking `cc setup telegram` (or any
subcommand that resolves a script under `$INSTALL_DIR/scripts/`)
failed with:

```
can't open file '/Users/<you>/.local/bin/scripts/setup_telegram.py': [Errno 2]
```

Root cause: the shim computed `INSTALL_DIR` from `$(dirname "$0")`
without resolving symlinks, so it picked up `~/.local/bin` instead of
`~/.command-centre`. macOS `readlink` is BSD (no `-f`), so the fix
walks the symlink chain by hand.

### What ships

- **`cc`** shim now resolves `$0` through any symlink chain before
  computing `INSTALL_DIR`. Works whether you call `cc` directly,
  via `~/.local/bin/cc`, or via any other symlink.

No data, schema, or config changes. Drop-in replacement.

## v0.3.1 — weekly backup + cc backup subcommand

Long-term data hygiene. The SQLite DB at `data/command-centre.db`
holds your full session/task/decision/inbox history; without a
backup, one bad `rm -rf` or disk failure wipes months of telemetry.

### What ships

- **`cc-backup`** script (alongside `cc` shim). Online-safe: uses
  `sqlite3 .backup` (the proper SQLite backup API), so the daemon
  can keep writing during the snapshot — no need to stop the server.
  Tars `data/command-centre.db` + `.env` + `config` + the Telegram
  offset state into `~/Backups/command-centre/cc-runtime-<stamp>.tar.gz`.
  Prunes to the most recent `CC_BACKUP_RETENTION=12` snapshots
  (default = 3 months at weekly cadence).
- **`cc backup`** subcommand on the shim — same as calling
  `cc-backup` directly, just discoverable from `cc help`.
- **`com.commandcentre.weekly-backup.plist.template`** — picked up
  by `install.sh`'s existing template loop. Fires every Sunday 03:00
  local time. `launchd` catches up missed runs if the Mac was asleep.
  `RunAtLoad=false` so install doesn't immediately fire one.
- **`install.sh`**: copies `cc-backup` into `$INSTALL_DIR/bin/`
  alongside `cc`; adds `{{HOME}}` substitution for plist templates
  (the backup template needs it for `~/Backups/`).

### Restore flow

```bash
# 1. New machine: install fresh.
git clone <repo> && cd <repo>
bash command-centre/install.sh --no-otel --no-launchd --no-start --yes

# 2. Stop the server before swapping DBs.
~/.command-centre/bin/cc stop

# 3. Untar the backup.
tar xzf ~/Backups/command-centre/cc-runtime-YYYYMMDD-HHMMSS.tar.gz \
  -C ~/.command-centre --strip-components=1
# (extracted layout: command-centre/{data,.env,config,...} → ~/.command-centre/{data,.env,...})

# 4. Bring the server back up.
~/.command-centre/bin/cc start
```

### Tunables (env vars or .env)

| Var | Default | What |
|---|---|---|
| `CC_BACKUP_DIR` | `~/Backups/command-centre` | Where tarballs land |
| `CC_BACKUP_RETENTION` | `12` | Keep this many tarballs; older are pruned by mtime |

---

## v0.3.0 — Telegram bridge

The last item from the original deferred list. Forwards pending
`DECISION:` and unread `INBOX:` from Claude Code sessions to your
Telegram chat; routes your replies back to the dashboard.

### What it does

- **Outbound** (every `TELEGRAM_NOTIFY_INTERVAL_S=30s`): polls
  `/api/decisions?status=pending` and `/api/inbox?unread=1`, sends a
  Markdown-formatted notification to `TELEGRAM_DASH_CHAT_ID` for any
  item not already in `notification_log`. Stores the resulting
  Telegram `message_id` for reply lookup.
- **Inbound** (Telegram long-poll, 30s): receives messages, routes
  three ways:
  - **Reply-to-message** → look up the original notification, post to
    `/api/decisions/{id}/answer` or `/api/inbox/{id}/reply`.
  - **`/answer <id> <text>`** or **`/reply <id> <text>`** — escape
    hatch for clients without reply-to.
  - **`/help`** / **`/start`** — short usage card.
- Each successful route gets an `✅ recorded` ack in the chat.

### New files

- **`scripts/telegram_bridge.py`** — single-file daemon, stdlib only
  (no `python-telegram-bot` dep, just `urllib`). Sources `.env`
  itself since launchd doesn't.
- **`scripts/setup_telegram.py`** — interactive wizard. Prompts for
  bot token, verifies via `getMe`, captures `chat_id` by long-polling
  for the next message you send, writes to `$INSTALL_DIR/.env` with a
  timestamped backup. Supports `--token`/`--chat`/`--dry-run`.
- **`templates/launchd/com.commandcentre.telegram-bridge.plist.template`**
  — picked up by `install.sh`'s existing template loop, no code
  changes needed.

### Smoke-tested end-to-end

Drove the bridge against a stdlib mock Telegram API (port 8767):
- 3 outbound notifications forwarded (2 decisions + 1 inbox);
  re-running same tick is a no-op (dedupe via `notification_log`).
- Reply-to-msg → `/api/decisions/11/answer` → status flips to
  `answered`, ack sent back to chat as a reply to the original.
- `/reply 10 got it thanks` → `/api/inbox/10/reply` → user_to_agent
  row inserted, original marked `read=1`.
- `/help` returns a friendly usage card.

### Operator flow

```bash
cc setup telegram                   # wizard: token + chat capture
cc setup telegram --foreground      # run bridge inline for debugging
launchctl load ~/Library/LaunchAgents/com.commandcentre.telegram-bridge.plist
```

The bridge is a no-op (sleeps 60s/cycle) when `TELEGRAM_BOT_TOKEN` or
`TELEGRAM_DASH_CHAT_ID` is unset, so launchd can stay loaded without
churn even before you run the wizard.

---

## v0.2.0 — dispatcher hardening

Three guards land between `claim_pending` and the actual `claude -p`
invocation. Without these, a runaway loop or a misconfigured high-risk
task could rack up real money or fire-and-forget on production
infrastructure. With them, the dispatcher fails closed.

### Backend

- **Back-pressure** (`MISSION_CONTROL_MAX_CONCURRENT`, default `3`).
  `run_once` counts `ops_tasks WHERE status='running'` first; new claims
  are deferred when the slot count is full. Logs `dispatcher_back_pressure`
  to `activities`. AttentionBar surfaces a `back_pressure` issue.
- **Daily cost cap** (`MISSION_CONTROL_DAILY_COST_CAP_USD`, default
  unset = off). Sums `cost_usd` across today's tasks and refuses to
  dispatch when the cap is reached. Logs `dispatcher_cost_capped`.
  AttentionBar surfaces a `cost_capped` issue (severity `error`).
- **Hard risk gate** (`MISSION_CONTROL_HARD_RISK_GATE`, default on).
  Tasks created with `risk_level='high'` AND `requires_approval=False`
  are auto-promoted to `awaiting_approval` at claim time and never
  auto-dispatch. Treats the misconfiguration as operator error, not
  intent. Logs `task_risk_gated`.

### New endpoint

- **`GET /api/system/dispatcher`** — live snapshot:
  ```
  { max_concurrent, running, free_slots, back_pressure,
    daily_cost_cap_usd, today_cost_usd, cost_capped,
    hard_risk_gate, risk_gated_today }
  ```

### Frontend

- **`DispatcherStrip.tsx`** — one-line strip above the TaskBoard
  showing `slots running/max`, `today $cost / cap $cap` (or "no cap"),
  and `risk gate on/off · N gated today`. Color tones: idle / info /
  warn / error track the underlying state. Polls every 10s.

### Verified

All three guards smoke-tested end-to-end against a real fake-claude:
- Risk gate: `risk=high, requires_approval=false` → task #11 promoted
  to `awaiting_approval`, `risk_gated_today: 1` increments.
- Back-pressure: 3 phantom running rows → `claimed=0, back_pressure=1`,
  AttentionBar issue surfaced.
- Cost cap: $5 spent today, cap=$4.99 → `claimed=0, cost_capped=1`,
  AttentionBar shows red `cost_capped` issue.

Stats payload from `run_once` now includes per-guard counters so launchd
logs make it obvious why a tick was a no-op.

---

## v0.1.1 — cosmetic finish (the three deferred panels)

Closes the three panels the prompt called for that phase 1 shipped without.
Pure additions — no schema changes, no breaking changes to existing endpoints.

### Backend

- **`GET /api/summary/sparklines`** — 24 hourly buckets each for
  `sessions / tokens / cost_usd / errors`, slot-aligned to local-time hour
  boundaries. Powers the inline charts in the four KPI tiles.
- **`GET /api/activity/heatmap?range={today,7d,30d}`** — 7×24 grid
  (weekday × hour-of-day) of session counts plus `peak` for color scaling.

### Frontend

- **`Sparkline.tsx`** — pure-SVG primitive, no library. Configurable
  stroke + area fill, scaled to viewport, handles all-zero series.
- **`KpiRow.tsx`** — now embeds an 84×28 sparkline next to each KPI
  value, color-matched to tile tone (blue / purple / green / amber).
- **`HeatmapGrid.tsx`** — 7×24 grid panel with a 5-step blue color
  scale, per-cell hover tooltip, scrollable on narrow viewports,
  "less → more" legend. Wired into a new "Observability · rhythm"
  section on Command page.
- **`TopSkillsCard.tsx`** — leaderboard with cost / tokens / runs sort
  picker, gradient bar visualization, top-10 cap. Sourced from the
  existing `/api/skills/economics`. Wired beside `SkillCostCard` on
  Skills page.

### Verified

`tsc --noEmit` clean. `vite build` 494KB / 149KB gzipped (+6KB from
v0.1.0). 6 Playwright screenshot tests pass. Real DB renders 16
sessions over 30d distributed across Tue/Wed/Thu/Fri afternoons —
matches API output.

---

## v0.1.0-phase1 — initial release

End-to-end working command centre: dashboard, dispatcher, installer, smoke
tests. Built milestone-by-milestone against `observability/build-your-own-dashboard-prompt.md`.

### Milestones (in order shipped)

1. **Schema recon.** Read every JSONL + OTEL row from a real install,
   documented 5 deltas from the prompt's data assumptions in
   `command-centre/docs/00-schema-recon.md`. Locked the data layer before
   committing to 85+ files of API + UI on top.

2. **18-table SQLite schema.** WAL mode. New tables `system_events`
   (PressurePanel input) and `session_titles`. Added `entrypoint`,
   `service_tier`, `is_error_any` to `sessions`; `caller`, `is_subagent`,
   `parent_uuid` to `tool_calls`. Hardcoded model price table for cost
   derivation in interactive sessions (no `result` envelope).

3. **API surface.** 10 routers, ~45 endpoints. OTLP/HTTP JSON ingest,
   JSONL scraper with subagent tracking, derived cost / duration /
   `is_error_any`, tool-call duration pairing via outer-envelope timestamps.

4. **UI shell + first panels.** Vite + React 18 + TS + Tailwind + TanStack
   Router (code-based). KpiRow, AttentionBar, SystemHealthStrip,
   TokenUsage, CacheEfficiency, SessionOutcomes, ToolLatency.

5. **Mission Control.** `subprocess.Popen(start_new_session=True)` +
   per-PID marker files for emergency-stop targeting (macOS 12+ hides env
   from `ps eww`). Atomic claim via `BEGIN IMMEDIATE`. Fence-aware
   `DECISION:` / `INBOX:` line scanner with stdin-mailbox reply loop.
   `skill_router` with strict >1 keyword threshold so unmatched tasks stay
   on auto autonomy. NL→cron parser for schedules.

6. **install.sh + cc shim.** Python detection chain, scoped
   `rsync --delete`, OTEL wizard that preserves user values, sed-templated
   launchd plists, deterministic `cc doctor`. Idempotent — re-running
   never touches `data/`, `logs/`, or `.env`.

7. **Panel build-out (~25 panels).** TaskBoard (3-col with approve/rerun/
   delete), TaskComposer Sheet, SchedulesCard + ScheduleComposer with
   live cron preview, DecisionsCard with answer Modal, InboxCard with
   inline reply, LiveSessionsCard + LiveSessionDetail drawer (tool
   timeline + follow-up input), HookActivity, ProjectBreakdown,
   AgentFanout, plus cluster wire-up across CommandPage / ActivityPage /
   SkillsPage.

8. **Live verification.** 24/24 GET endpoints return valid JSON. 8/8
   Playwright tests pass; screenshots captured for every page + every
   interactive flow.

9. **Install smoke test.** Fresh `install.sh` into scratch, `cc doctor`
   green, dispatcher claims fake-claude task end-to-end (queue → claim →
   `DECISION:` posted → operator answer → consumed → `INBOX:` captured →
   done), OTEL wizard preserves existing user keys + creates backup,
   emergency-stop kills only marked dispatcher children + spares decoy.

### Bugs found + fixed during smoke

- **Dispatcher hardcoded port 8765** for decisions/inbox POSTs when
  `CC_DASHBOARD_URL` was unset — silently lost every marker on non-default
  ports. Now derives from `CC_HOST` / `CC_PORT`; `install.sh` writes
  `CC_DASHBOARD_URL` to `config`.
- **`cc start` race**: hardcoded 0.5s sleep before status check. Now polls
  `/api/health` up to 6s, exits early on server crash.
- **Emergency-stop zombie race**: a dispatcher child killed+reaped between
  liveness probe and `ps -p` left `<defunct>` as argv, failing the
  `claude in argv` check. Now treats defunct as dead, drops the marker.
- **Doctor display**: `otel=Nones`, `sync=68.46400201320648s`. Now
  formatted as `otel=never`, `sync=68s`.
- Plus ~6 dev-only fixes called out in commit history (Python 3.9 union
  syntax in FastAPI signatures, `_scalar()` for non-scalar SQLite binds,
  macOS `ru_maxrss` unit, `tsconfig` `composite + noEmit` conflict).

### Not in this release (deferred)

- **Telegram bridge.** DB columns + endpoints are wired; the poller and
  webhook handlers are not. `cc setup telegram` reports as not wired.
- **3 cosmetic panels.** HeatmapGrid (hour × weekday), TopSkills
  leaderboard, KPI sparklines. Cluster scaffolding ships without them.
- **Dispatcher hardening.** Daily cost cap, hard-reject for
  `risk=high + requires_approval=false`, back-pressure when >N running.
  Endpoint shape exists; enforcement is open.
- **Documentation.** Per-script README, architecture deep-dive, deploy
  guide. The prompt + this CHANGELOG are the authoritative source for
  now.

### Acknowledgements

Built against the spec in `observability/build-your-own-dashboard-prompt.md`
and the reference design in `observability/build-your-own-dashboard-guide.html`,
both kept in this repo for context.
