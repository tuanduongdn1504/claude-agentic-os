# Changelog

## v0.6.0 — DRAFT spec: SkillLauncher + cost-source disambiguation

Spec only — not yet built. Full build directive in
`observability/(C) build-your-own-dashboard-prompt-v0.6.0-amendment.md`,
applied on top of the v0.5.0-mvp1 working tree (current `main` HEAD).

Note: v0.4.0 (operator UI — Sessions Explorer, Decisions Queue, Telegram
bridge status) and v0.5.0-mvp1 (Telegram outbound: task_complete +
risk_gated push) shipped in git after v0.3.2 but were not backfilled into
this CHANGELOG. Commit messages are the current record; backfill is
queued alongside the v0.6.0 build.

### Why this release

Two gaps in the current build.

**Workflow.** Queuing a skill today means opening TaskComposer and filling
eight fields, every time. Skills you run daily (morning brief, deep
research, inbox triage) should be one click — same task, sensible defaults,
fire-and-forget.

**Observability.** As of Anthropic's recent billing change, headless
`claude -p` no longer draws from Pro/Max — it pulls from a separate ~$200/mo
API pool at full API rates (~10× the Max-subsidised cost). The dashboard
mixes both streams into a single `cost_usd` today, which makes the v0.2.0
daily cost cap dollar-blind.

### What's planned

- **`SkillLauncher` panel** on the Command page. One-click launch per
  `user_invocable` skill with stored presets, last-launched timestamp,
  30-day avg cost, inline preset editor. Fires via new endpoint
  `POST /api/skills/{name}/launch` → optimistic UI → dispatcher triggered
  inline (no 120s wait for next heartbeat).
- **`cost_source` enum** (`api_pool` / `max_sub` / `unknown`, with
  `codex_api` slot reserved for v0.5) on `ops_tasks` + `sessions`.
  Dispatcher and launcher write `api_pool`; `sync_sessions.py` derives
  `max_sub` for interactive REPL/IDE sessions via a left-join against
  `ops_tasks`. Race covered by a two-line back-fill in
  `task_tracker.claim_pending`.
- **`MISSION_CONTROL_DAILY_COST_CAP_USD` re-pointed to api_pool only.**
  Max-sub cost is notional for Pro/Max operators; capping on it would
  surprise users. The cap exists to protect real-dollar spend.
- **UI splits** — KpiRow cost tile shows `api $X · max $Y`; DispatcherStrip
  reads `today api / cap`; SessionsTable gains a Source column + filter;
  TaskBoard cards gain a small source pill; TokenUsageCard gains a per-day
  cost band beneath the token stacks.
- **Pre-v0.6 rows** surface as amber `?` tags everywhere. `cc doctor`
  reports a non-fatal warning. Manual recourse documented in HANDOVER.md.

### Schema delta

Five columns total across three tables, all through the existing
`_migrate_add_column` helper:

| Table | Column | Type | Default |
|---|---|---|---|
| `skills` | `preset_json` | TEXT | NULL |
| `skills` | `last_launched_at` | TEXT | NULL |
| `skills` | `launch_count` | INTEGER | `0` |
| `ops_tasks` | `cost_source` | TEXT | `'unknown'` |
| `sessions` | `cost_source` | TEXT | `'unknown'` |

Idempotent. Re-running `install.sh` against v0.5.x upgrades in place,
preserves all rows.

### Status

- [x] Spec drafted
- [ ] Reviewed
- [ ] Built
- [ ] Smoke-tested

Review before kicking off the build. Open design questions called out
inline in the amendment file (preset JSON blob vs nine columns, launcher
default `classic` vs composer default `stream`, cap reads api_pool only,
`codex_api` slot reserved for v0.5).

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
