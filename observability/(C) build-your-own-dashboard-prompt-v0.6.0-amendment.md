# v0.6.0 Amendment — SkillLauncher + cost-source disambiguation

Apply on top of the v0.5.0-mvp1 codebase (current `main` HEAD). Built
originally from `build-your-own-dashboard-prompt.md`. Intermediate releases
v0.4.0 (operator UI — Sessions Explorer, Decisions Queue, Telegram bridge
status) and v0.5.0-mvp1 (Telegram outbound: task_complete + risk_gated push)
shipped in git but are not yet backfilled into `CHANGELOG.md`. This
amendment is additive — no breaking changes to existing endpoints, no row
drops, idempotent migration. Re-running the installer against an existing
v0.5.x install upgrades the schema in place.

**Verified compatibility against intermediate commits**: no schema
collisions (v0.4.0 and v0.5.0-mvp1 added zero columns); no endpoint
collisions (`POST /api/skills/{name}/launch`, `PATCH /api/skills/{name}/preset`
do not overlap with `/api/system/telegram` or the `/sessions`, `/decisions`
page routes).

---

## Why this release

Two gaps the current command centre has today.

**Workflow gap.** Queuing a skill means opening the TaskComposer Sheet and
filling eight fields, every time. Skills you run daily (morning brief, deep
research, inbox triage) should be one click — same task, sensible defaults,
fire-and-forget. The TaskComposer stays for ad-hoc work; the launcher handles
the regulars.

**Observability gap.** As of Anthropic's recent billing change, headless
`claude -p` no longer draws from Claude Pro/Max. Headless invocations now
pull from a separate ~$200/mo API pool, billed at full API rates (roughly 10×
the Max-subsidised rate). The dashboard today sums both streams into a single
`cost_usd`, which makes the v0.2.0 daily cost cap dollar-blind: an interactive
Max session that emits a notional $5 of token cost looks identical to a $5
headless run that actually leaves your wallet. Split them.

The two changes compose: the launcher only dispatches headless tasks, so
every launcher fire is an api_pool charge. Showing the api_pool subtotal next
to total cost makes the launcher's true price legible.

## Schema delta

All migrations through the existing `_migrate_add_column(conn, table, col,
type)` helper. Five columns total across three tables. Re-runnable.

- **`skills`** — add:
  - `preset_json` TEXT (nullable) — JSON blob holding launch defaults. Schema
    inside: `{title?, description?, model?, execution_mode?, priority?,
    quadrant?, risk_level?, requires_approval?, dry_run?}`. Fields mirror
    TaskComposer 1:1; any field can be absent (fallback chain documented
    below). Use a blob, not nine columns, because presets are read whole on
    launch and edited whole in the inline editor.
  - `last_launched_at` TEXT (nullable, ISO8601) — sort key for the launcher
    grid. Denormalised from `ops_tasks` for cheap reads.
  - `launch_count` INTEGER NOT NULL DEFAULT 0 — primary sort key.

- **`ops_tasks`** — add:
  - `cost_source` TEXT NOT NULL DEFAULT `'unknown'` — values: `'api_pool'` |
    `'max_sub'` | `'unknown'`. Dispatcher and launcher both write
    `'api_pool'`. UI-created tasks via TaskComposer default to `'api_pool'`
    too (every dispatched task runs headless). Reserve the slot for a future
    `'codex_api'` value when a Codex backend is bolted on (v0.5).

- **`sessions`** — add:
  - `cost_source` TEXT NOT NULL DEFAULT `'unknown'` — same enum. Derived by
    `sync_sessions.py` via a left-join against `ops_tasks` (see ingest delta).

Existing rows on all three tables get `'unknown'` on migration. They surface
as an amber `?` tag in the UI — a known data-quality issue, not a silent
bucket. Rule 12 of the project conventions: fail loud.

## Ingest delta

### `sync_sessions.py`

After the existing per-session upsert, derive cost_source:

```python
def _derive_cost_source(conn, session_id):
    row = conn.execute(
        "SELECT 1 FROM ops_tasks WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    source = "api_pool" if row else "max_sub"
    conn.execute(
        "UPDATE sessions SET cost_source = ? WHERE session_id = ?",
        (source, session_id),
    )
```

Run after every upsert. Cheap (indexed lookup on `ops_tasks.session_id`,
already an index from v0.1.0). Idempotent.

**Race.** A dispatcher-launched task's `system.init` event may land in the
JSONL after the sync loop has already processed earlier lines of the same
file — so the `ops_tasks.session_id` back-fill happens after
`_derive_cost_source` first runs, leaving the row marked `'max_sub'`
incorrectly. Defend with a one-line back-fill inside `task_tracker.py`
where `session_id` gets stashed:

```python
conn.execute(
    "UPDATE ops_tasks SET session_id=? WHERE id=?", (sid, task_id),
)
conn.execute(
    "UPDATE sessions SET cost_source='api_pool' WHERE session_id=?", (sid,),
)
```

No polling, no listener, no event bus.

### Dispatcher (`dispatcher.py`)

Belt-and-braces: when `claim_pending` returns a row, write
`cost_source='api_pool'` to that `ops_tasks` row before spawning. The column
default already covers new rows; this line covers pre-migration tasks that
sat in `pending` across the upgrade.

## API surface delta

### Skills (new + altered)

- **`GET /api/skills`** — augmented response. Each skill row now includes
  `preset` (the parsed JSON blob, or `null` if unset), `last_launched_at`,
  `launch_count`, and `avg_cost_usd_30d` (computed: `AVG(ops_tasks.cost_usd)
  WHERE ops_tasks.assigned_skill = ? AND ops_tasks.created_at >= now - 30
  days AND ops_tasks.cost_usd IS NOT NULL`, `null` if zero samples).
- **`PATCH /api/skills/{name}/preset`** — body is the full preset blob
  (replace, not merge — simpler to reason about). Validate the nine optional
  fields against the same enums TaskComposer uses. Body `null` clears the
  preset entirely. Returns the updated skill row.
- **`POST /api/skills/{name}/launch`** — body
  `{description_override?: string}`. Read the skill's preset, merge with
  fallbacks (see below), `INSERT` into `ops_tasks` with
  `status='pending'` (or `'awaiting_approval'` if `requires_approval=true`),
  `cost_source='api_pool'`, `assigned_skill=<name>`. Bump
  `last_launched_at=now`, `launch_count = launch_count + 1`. Trigger
  `dispatcher.run_once()` via `asyncio.to_thread` so the task starts within
  ~1s instead of waiting for the next 120s heartbeat. Return
  `{task_id, status}`.

**Preset fallback chain** (per field, first non-null wins):

1. Preset value
2. Skill frontmatter (for `description` only — pulled from `skills.description`)
3. Hardcoded launcher defaults: `title = f"Run {name}"`,
   `execution_mode = "classic"`, `priority = "normal"`, `quadrant = "do"`,
   `risk_level = "low"`, `requires_approval = false`, `dry_run = false`,
   `model = MISSION_CONTROL_DEFAULT_MODEL`

Note: launcher defaults to `execution_mode='classic'` — the launcher pattern
is fire-and-forget. TaskComposer's default (`'stream'`/Interactive) is
unchanged. The two entry points have different defaults on purpose; document
this in the README.

### Cost / observability (altered shapes, backwards-compatible)

Every cost-bearing endpoint keeps its existing top-level `cost_usd` total
(sum of all sources) and adds a `cost_by_source` sibling:
`{api_pool: float, max_sub: float, unknown: float}`. Existing clients keep
working; new UI reads the split.

- **`GET /api/summary`** — today rollup gains `cost_by_source`.
- **`GET /api/usage/tokens`** — each daily row gains `cost_by_source`.
- **`GET /api/system/dispatcher`** — add `today_cost_api_pool_usd`,
  `today_cost_max_sub_usd`, `today_cost_unknown_usd` next to existing
  `today_cost_usd`. The v0.2.0 cap (`MISSION_CONTROL_DAILY_COST_CAP_USD`)
  reads ONLY `today_cost_api_pool_usd` from now on (see Dispatcher delta).
- **`GET /api/sessions`** — each row gains `cost_source`.
- **`GET /api/tasks`** — each row gains `cost_source`.

## Panels delta

### New — `SkillLauncher` (Command page)

Slots into the Command page between **Token usage** (item 6 in the v0.1.0
panel list) and the **Observability section** (item 7). Wrapped in a
`CollapsibleSection`, default open, localStorage key `cc:section:launcher`.

- **Grid** of skill cards, 3 columns at ≥1024px, 2 at ≥640px, 1 below. Cards
  drawn from `GET /api/skills` filtered to `user_invocable=1`. Sort:
  `launch_count DESC, last_launched_at DESC NULLS LAST, name ASC`. Cap at
  12; expose `Show all (N)` link when there are more.
- **Card content**:
  - Top row: skill name (Inter 600, 15px); right-aligned avg cost in
    JetBrains Mono (`$0.04 avg`, dim; `—` when `avg_cost_usd_30d` is null).
  - Subtitle: `preset.title` (or `Run {name}` fallback), single line, dim.
  - Footer row: relative last-launched (`3h ago`, `never`) on the left;
    **Launch** primary button + **Edit** ghost-pencil icon on the right.
- **Launch flow**: `POST /api/skills/{name}/launch`. Optimistic UI — button
  swaps to a 16px spinner, then to `↗ Task #N` for 2500ms, then resets. On
  error, inline toast (red, dismissable, 6s auto-dismiss) with the API error
  message. Do NOT navigate away; the user should be able to fire three
  skills in a row without losing scroll position.
- **Edit flow**: pencil icon opens an inline editor — NOT a Sheet, NOT a
  Modal. A second card-sized panel that expands beneath the row using the
  same 220ms framer-motion height animation as `CollapsibleSection`. Nine
  fields, two columns. Save → `PATCH /api/skills/{name}/preset` → collapse →
  refresh skill row. Cancel → discard, collapse. Esc cancels.
- **Empty state** when no `user_invocable=1` skills exist: a single dim
  card-sized block reading "No invocable skills yet. Mark a skill
  `user_invocable: true` in its frontmatter and run `cc sync`." with a
  monospaced inline-code style on the snippet.

### Altered — `KpiRow` (Command page top strip)

Today's cost tile is the only one that changes. Add a two-line readout:

- **Line 1** (existing): total `$cost_usd` today, large Inter 700 numeric.
- **Line 2** (new): `api $X.XX · max $Y.YY` in JetBrains Mono 11px, dim.
  When `cost_by_source.unknown > 0`, append ` · ? $Z.ZZ` in amber. Hide line
  2 entirely when the total is zero (don't add visual noise to an
  empty-state tile).

The three other tiles (sessions / tokens / errors) are untouched.

### Altered — `DispatcherStrip` (above TaskBoard)

The cost segment reads `today api $X.XX / cap $Y.YY` (was `today $cost /
cap $cap`). Tone tracking unchanged. Max-sub cost no longer registers here —
the cap is now an api-pool-only signal, and this strip exists to surface cap
state.

### Altered — `TokenUsageCard`

Below the existing token stacks, add a thin per-day cost band: two stacked
mini-bars per day (cyan = api_pool, grey = max_sub). Same x-axis as the
token bars, ~6px tall, separated from the token stack by a 4px gap. Legend
gains two entries. Skip the band entirely when both subtotals are zero for
the full window — empty-state discipline.

### Altered — Sessions list surface

Targets the current sessions surface, which is `SessionsPage` at
`/sessions` (added in v0.4.0). If the legacy `SessionsTable` on the
Activity page still exists, apply the same change there too.

New column **Source** between **Model** and **Tokens**. Pill values:
`api` (cyan, `#06b6d4` background at 15% opacity), `max` (grey, surface-2),
`?` (amber, hover tooltip "Pre-v0.6 row — source not tracked"). Above the
table, a new filter dropdown mirrors the three values plus an "all"
default. Default filter: all.

### Altered — `TaskBoard` cards

Each task card gains a small pill next to the existing risk pill: `api` /
`max` / `?`. Same color scheme as the sessions-list pills. Hide entirely when the
task is `pending` or `awaiting_approval` AND `cost_usd IS NULL` (cost is
unknown until the task runs) — don't clutter cards with placeholder pills.

## Dispatcher delta

### Cap behaviour

`MISSION_CONTROL_DAILY_COST_CAP_USD` now sums **only**:

```sql
SELECT COALESCE(SUM(cost_usd), 0)
FROM ops_tasks
WHERE cost_source = 'api_pool'
  AND cost_usd IS NOT NULL
  AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
```

Max-sub cost is notional for solo operators on Pro/Max — capping on it
would surprise users who don't pay per-token interactively. The cap exists
to protect real-dollar spend; api_pool is the real-dollar stream.

`AttentionBar` `cost_capped` issue copy updated to read: "API-pool spend
reached cap ($X.XX of $Y.YY today). Max-sub usage continues."

### Forward-compat: codex backend slot

This release does NOT add a Codex backend. But the `cost_source` validator
must accept `'codex_api'` as a fourth enum value from the start so a v0.5
backend swap is purely a dispatcher change, no schema migration. Add it to
the validator now; reject all other values.

## Setup UX delta

### `install.sh`

No new wizard prompts. The migration is automatic on next start —
`_migrate_add_column` calls run inside the existing lifespan startup hook.
The launcher panel renders empty until a skill is marked invocable.

### `cc doctor`

Add a single check: "Cost-source backfill complete — N rows pending."
`PASS` when `SELECT COUNT(*) FROM sessions WHERE cost_source='unknown' AND
ended_at IS NOT NULL` returns 0 OR the install is fresh (no pre-v0.6 rows).
`WARN` (yellow, non-fatal) when pre-v0.6 rows exist with unknown source —
they'll surface in the UI as amber `?` tags and won't ever be back-filled
automatically (we don't know which were interactive vs dispatched). Doc the
manual recourse in HANDOVER.md: rows with no matching `ops_tasks` row are
safe to bulk-update to `'max_sub'`.

### `.env.example`

No new env vars. The cap variable and the default model variable stay as
documented in v0.2.0.

## Stop conditions delta

Append to the existing list:

10. **Launcher fires.** Mark a skill `user_invocable: true`, run `cc sync`,
    open the dashboard. SkillLauncher panel shows the skill as a card. Click
    Launch. `last_launched_at` updates within 2s. The dispatched task
    transitions pending → running → done without TaskComposer being opened.
    Card moves up the sort order on next render.
11. **Preset edits persist.** Open the inline editor, change model and
    risk_level, save, reload the page. Editor reflects the saved values.
    Next launch creates an `ops_tasks` row with those fields.
12. **KpiRow math.** Cost tile reads `total · api $X · max $Y`. With at
    least one of each source today: `api X + max Y + ? Z` sums to the total
    (±$0.01 rounding).
13. **Cap reads api_pool only.** Set
    `MISSION_CONTROL_DAILY_COST_CAP_USD=0.01`. Run one interactive Max-sub
    session that emits `cost_usd=0.50` — `cost_capped` stays `false`. Then
    dispatch one task that emits `cost_usd=0.02` — `cost_capped` flips to
    `true`. AttentionBar surfaces the api-pool wording.
14. **Migration is idempotent.** Run `install.sh` against a fresh directory:
    fresh build. Run it again immediately: no errors, no schema drift.
    Pre-existing rows have `cost_source='unknown'` and surface as `?` tags.

## Order of operations

1. **Schema migrations first.** Five columns. Ship them in a single
    lifespan-startup pass so partial states never reach the UI.
2. **Back-fill plumbing.** `_derive_cost_source` in `sync_sessions.py` +
    the two-line back-fill in `task_tracker.claim_pending`. Verify against
    a real JSONL dump that crosses an `ops_tasks.session_id` write.
3. **Cost-split endpoints.** `GET /api/summary`, `/api/usage/tokens`,
    `/api/system/dispatcher`, `/api/sessions`, `/api/tasks`. Validate
    `cost_by_source` math against a hand-crafted fixture with one row of
    each source before touching the UI.
4. **Cap behaviour.** Re-point `MISSION_CONTROL_DAILY_COST_CAP_USD` math
    to api_pool only. Update `AttentionBar` copy.
5. **Skills endpoints.** `GET /api/skills` augmentation,
    `PATCH .../preset`, `POST .../launch`. Read path before write path.
6. **SkillLauncher panel + inline editor.** Build the read view against
    real `GET /api/skills` data before wiring the launch button.
7. **Cosmetic deltas.** KpiRow tile, TokenUsageCard band, sessions-list
    column + filter, TaskBoard pills.
8. **Playwright specs.** One-click launch creates a task; preset edit
    persists across reload; KpiRow renders two-source readout against
    fixture data; sessions-list filter narrows correctly.
9. **CHANGELOG.md v0.6.0 entry** following the v0.3.x style: What ships /
    What it does / Verified / Operator flow / Tunables. Backfill
    v0.4.0 and v0.5.0-mvp1 entries in the same pass — both shipped without
    CHANGELOG updates; commit messages are the only current record.

## Not in this release (deferred)

- **Codex backend.** The `cost_source` validator reserves the
   `'codex_api'` slot; backend pluggability (env switch, argv check in
   emergency-stop, model resolution) is a v0.5 change with its own brief.
- **Per-skill cost budgets.** Different concept from the daily cap. v0.5+.
- **Obsidian embed mode** (`/embed` route stripping AppShell chrome for
   iframe use). A separate path if you decide to ship an Obsidian
   companion; not entangled with this release.
- **Backfill heuristics for pre-v0.6 `unknown` rows.** Documented manual
   recourse in HANDOVER.md is enough; no automatic guess.

---

End of amendment. Apply in the order above against a v0.5.0-mvp1 working
tree (current `main` HEAD). Quality bar matches the original spec: Linear /
Raycast / Vercel, production-grade polish, ship with intent.
