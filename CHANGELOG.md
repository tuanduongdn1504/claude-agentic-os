# Changelog

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

## v0.5.0-mvp2 — DRAFT spec: Telegram bridge inbound `/run`, `/approve`, `/cancel`

Spec only — not yet built. Full build directive in
`observability/(C) build-your-own-dashboard-prompt-v0.5.0-mvp2-amendment.md`,
applied on top of current `main` HEAD (post-v0.6.0 merge).

mvp2 of the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Closes Journey 4 —
operator's inbound counterpart to mvp1's outbound `task_complete` +
`risk_gated` push. Numbered `v0.5.0-mvp2` per PRD's "MVP commit 1 of 2"
framing; lands chronologically after v0.6.0 (version-history zigzag
accepted).

### Why this release

mvp1 closed the outbound feedback loop. mvp2 closes inbound: operator
can launch tasks (`/run <prompt>`), approve risk-gated tasks
(`/approve <id>`), and cancel pending tasks (`/cancel <id>`) entirely
from Telegram. Without mvp2, Journey 4 (late-night risk-gate intercept)
has no phone-side recovery — operator must walk to the desk and open
the dashboard.

### What's planned

- **Inbound `/run`, `/approve`, `/cancel` parsers** in
  `telegram_bridge.py`. Splits the existing `_CMD_RE` into two patterns:
  with-ID for `(answer|reply|approve|cancel)`, no-ID free-text for
  `/run`. Three new handler functions; reuses `_md_safe` from mvp1.
- **New endpoint `POST /api/tasks/{id}/cancel`** mirroring the
  existing `/approve` pattern at `tasks.py:132`. Audit-log row to
  `activities` tagged `source='telegram'` on success.
- **`POST /api/tasks`** body extended with optional `created_at_source`
  field (default `'dashboard'`). Non-breaking — existing dashboard
  POSTs continue to work.
- **`POST /api/tasks/{id}/approve`** extended with `?source=` query
  param + audit-log row insert (FR19). Adds the activities INSERT if
  not already present.
- **`_lookup_by_tg_message` extension** — reply-to-msg on 🛑 RISK-GATED
  notifications routes to `/approve` (FR17). Reply-to-cancel NOT
  supported — explicit `/cancel <id>` only to avoid accidental cancels.
- **`created_at_source` column** on `ops_tasks` for the "Async hours
  shifted" success metric. Orthogonal with v0.6.0's `cost_source` —
  different column, different code paths.

### Schema delta

One column, orthogonal with v0.6.0's `cost_source` migration:

| Table | Column | Type | Default |
|---|---|---|---|
| `ops_tasks` | `created_at_source` | TEXT NOT NULL | `'dashboard'` |

Idempotent. Order-independent with v0.6.0 (both additive `TEXT NOT NULL
DEFAULT` columns on `ops_tasks`).

### Status

- [x] Spec drafted
- [ ] Reviewed
- [ ] Built
- [ ] Smoke-tested

Estimate: 3-4h per PRD (revised from earlier 1-2h — `/cancel` endpoint
must be added from scratch). PRD's `/api/tasks/{id}/approve` already
exists (`tasks.py:132`); status enum already includes `'cancelled'`.

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
