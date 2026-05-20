# Changelog

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
