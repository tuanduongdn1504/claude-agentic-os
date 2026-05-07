# Changelog

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
