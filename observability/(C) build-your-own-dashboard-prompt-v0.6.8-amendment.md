# v0.6.8 Amendment — expose historical ranges

Apply on top of current `main` HEAD (post v0.6.7). Small, low-risk
patch — one backend helper + one shared UI component + the `Range`
type. Surfaces data that is **already in the database** but currently
unreachable from the UI because the range selector caps at 30 days.

**Numbered `v0.6.8`** — patch over v0.6.7. Cadence consistent with
v0.6.1 – v0.6.7. No schema change, no new endpoint, no new operator
config. The backend already supports an `all` range; this release
adds `90d` + `1y` predicates and exposes `90d` / `1y` / `all` in the
UI. There is no architectural change to justify a minor bump.

**Verified against current `main` HEAD** (read 2026-05-29):
- **Nothing prunes old data.** No retention job, prune, purge,
  vacuum, or time-based `DELETE` exists anywhere in `scripts/`, the
  launchd plists, `install.sh`, or `.env.example`. The only
  `DELETE`s are per-session re-parse cleanup
  (`sync_sessions.py` drops a session's `tool_calls` /
  `system_events` before re-upsert) and explicit task / schedule
  deletes. `sessions`, `token_usage`, `otel_events`, `tool_calls`,
  `activities` rows accumulate indefinitely.
- **`<30 day` is a UI ceiling, not retention.** `helpers/timerange.py`
  already has `ALLOWED = ("today", "7d", "30d", "all")` and maps
  `all` → `1=1` (no time filter). Every observability endpoint that
  calls `sql_predicate` already honours `?range=all` today. The UI
  type `Range = 'today' | '7d' | '30d'` (`ui/src/lib/api.ts`) and the
  shared `RANGES = ['today', '7d', '30d']` array
  (`ui/src/components/panels/TokenUsageCard.tsx`) are the *only*
  reason older data is invisible.
- **Daily-series endpoints build `daily` straight from `GROUP BY`
  rows — there is NO zero-fill scaffold.** `usage/tokens`,
  `usage/cache`, `sessions/outcomes`, `activity/productivity` each
  return one row per day that actually has data. So a wide range does
  NOT explode into a fixed-length zero-filled array; it returns
  exactly the days present. (An earlier worry about a "365-day
  zero-fill cap" was wrong — no such cap exists in these endpoints.)
- **`days_in_range()` is dead code** (defined in `timerange.py`; no
  live caller — only a stale `.pyc` references it). It hard-codes
  `{"today":1, "7d":7, "30d":30, "all":365}[r]` and would `KeyError`
  on `90d` / `1y` once those become valid ranges. This release
  extends it defensively even though nothing calls it yet.
- **Single shared `RangePicker`.** Exported from `TokenUsageCard.tsx`;
  consumed by ~14 panels + `SessionsTable` + `SessionsPage`, all
  reading the one `RANGES` array. Extending that array propagates the
  new options to every range surface automatically.
- **Ingestion is not time-bounded.** `sync_sessions.run_sync` globs
  all `~/.claude/projects/**/*.jsonl` and re-derives any whose mtime
  changed. The upstream limit is purely whatever Claude Code itself
  retains on disk — rows already upserted into the DB persist
  regardless.

---

## Why this release

The operator's own usage history is in the database but the dashboard
can't show more than 30 days of it. That's a one-character product
gap: the backend already computes every window, the UI just never
offers the wider ones. Operators who want to see a quarter, a year, or
their full history of token spend / session outcomes / skill cost
should be able to pick it from the same range toggle they already use.

This is read-only exposure of existing data. It is explicitly NOT a
retention or cleanup feature — see "Not in this release."

## Backend delta — `helpers/timerange.py`

Two new predicates + the dead-code defensive fix. `all` is unchanged
(already present).

```python
ALLOWED = ("today", "7d", "30d", "90d", "1y", "all")   # + 90d, 1y

def sql_predicate(range_, column="started_at"):
    r = normalize(range_)
    if r == "today":
        return f"DATE({column}, 'localtime') = DATE('now', 'localtime')", []
    if r == "7d":
        return f"{column} >= datetime('now', '-7 days')", []
    if r == "30d":
        return f"{column} >= datetime('now', '-30 days')", []
    if r == "90d":                                          # NEW
        return f"{column} >= datetime('now', '-90 days')", []
    if r == "1y":                                           # NEW — SQLite
        return f"{column} >= datetime('now', '-1 year')", []  # native, leap-safe
    return "1=1", []                                        # all (unchanged)

def days_in_range(range_):
    r = normalize(range_)
    return {"today": 1, "7d": 7, "30d": 30,
            "90d": 90, "1y": 365, "all": 365}[r]            # + 90d, 1y keys
```

`normalize()` is unchanged — it already returns the input verbatim
when it's in `ALLOWED`, else falls back to `7d`. Adding `90d` / `1y`
to `ALLOWED` is what makes them pass through instead of being silently
coerced to `7d`.

`-1 year` is the SQLite native modifier (handles leap years); `-365
days` is an acceptable substitute if a contributor prefers the
day-bucket mental model. Pick one and keep it.

## API delta

**None.** Every range-aware endpoint already accepts an arbitrary
`?range=` string and routes it through `sql_predicate` /
`normalize`. The new values become valid the moment `ALLOWED` grows;
no endpoint signature changes.

The full set of endpoints that immediately gain `90d` / `1y` / `all`:
`/api/usage/tokens`, `/api/usage/cache`, `/api/sessions/outcomes`,
`/api/tools/latency`, `/api/hooks/activity`, `/api/sessions/by-project`,
`/api/tools/agent-fanout`, `/api/tools/edit-decisions`,
`/api/activity/productivity`, `/api/activity/heatmap`,
`/api/sessions` (+ `/api/sessions/failures`), `/api/skills/economics`,
`/api/mcp`.

## UI delta

### `ui/src/lib/api.ts` — `Range` type

```ts
export type Range = 'today' | '7d' | '30d' | '90d' | '1y' | 'all';
```

Adding union members is backward-compatible: existing function
signatures (`(range: Range = '7d')`) and the `qs()` URL builder pass
the string straight through.

### `ui/src/components/panels/TokenUsageCard.tsx` — `RANGES` array

```ts
const RANGES: Range[] = ['today', '7d', '30d', '90d', '1y', 'all'];
```

The shared `RangePicker` renders one button per entry, label = the
raw key under an `uppercase` class → `TODAY` / `7D` / `30D` / `90D` /
`1Y` / `ALL`. Every consumer (TokenUsageCard, CacheEfficiencyCard,
SessionOutcomesCard, ToolLatencyCard, HookActivityCard,
ProjectBreakdownCard, AgentFanoutCard, EditAcceptanceCard,
ProductivityCard, HeatmapGrid, TopSkillsCard, SkillCostCard, MCPPanel,
SessionsTable, SessionsPage) inherits the new options with no
per-panel change.

### Defaults unchanged

Every panel keeps its current `useState<Range>('7d')` / `'30d'`
default so initial dashboard load does NOT widen to `all`. The new
options are opt-in via the toggle. Do not change defaults in this
release.

### Optional: 6-button width

Six buttons in the inline `RangePicker` is wider than the current
three. On a narrow Obsidian embed pane (v0.6.6 `?embed=1`) or a phone
this may wrap. Acceptable for MVP. If it looks bad in the embed pane,
the fallback is a `<select>` for the picker — defer unless the
operator reports it.

## Performance + payload notes

- **Index-backed.** `idx_sessions_started_at`, `idx_token_usage_date`,
  and `idx_otel_events_name_ts` already cover the `WHERE`/`GROUP BY`
  columns the wide ranges scan. `all` is a full-range scan but
  index-assisted.
- **Bounded payloads.** Daily-series endpoints return one row per
  (date[, model, source]); a year ≈ 365 × a few models × 3 sources ≈
  low thousands of rows max. The sessions list is bounded by its
  existing `limit` (default 50) + offset pagination — `all` widens the
  filter, not the page size.
- **Chart density.** `all` / `1y` can render up to ~365 daily bars on
  TokenUsageCard's stacked-bar strip — dense but functional. Weekly
  downsampling for long ranges is deferred (see below).
- **Non-uniform date axis.** Because there's no zero-fill, days with
  no activity are simply absent from `daily`; over long sparse ranges
  the x-axis collapses gaps. This is existing behaviour, just more
  visible. Uniform gap-filled axes are deferred.

## Stop conditions

1. **`timerange` predicates.** `sql_predicate('90d','started_at')` →
   `"started_at >= datetime('now', '-90 days')"`;
   `sql_predicate('1y','started_at')` → the `-1 year` fragment;
   `sql_predicate('all')` → `"1=1"`. `normalize('90d')=='90d'`,
   `normalize('1y')=='1y'`, `normalize('all')=='all'`,
   `normalize('bogus')=='7d'`.
2. **`days_in_range` no longer KeyErrors.** `days_in_range('90d')==90`,
   `days_in_range('1y')==365`, `days_in_range('all')==365` — no
   exception on any `ALLOWED` value.
3. **Endpoint returns wider window.** Seed sessions across ~120 days.
   `GET /api/usage/tokens?range=90d` returns days the `30d` query
   omits; `?range=1y` ⊇ `?range=90d`; `?range=all` ⊇ `?range=1y`. Row
   counts are monotonic non-decreasing as the window widens.
4. **`all` returns full history.** Seed a session dated >1 year ago.
   `?range=all` includes it; `?range=1y` excludes it; `?range=30d`
   excludes it.
5. **Empty / fresh DB.** `?range=all` on a DB with no rows returns
   `{daily: [], totals: {...zeros}}` cleanly — no crash, no null.
6. **Sessions list paginates under `all`.** `GET /api/sessions?range=all`
   respects `limit` (does not dump the whole table); `offset` walks
   older pages.
7. **UI type + picker.** `tsc --noEmit` clean with the widened `Range`
   union. The shared `RangePicker` renders 6 buttons; clicking `1Y`
   and `ALL` fires the fetch and re-renders without console error on
   TokenUsageCard, SessionsPage, and at least one skill panel.
8. **Defaults unchanged.** First dashboard load still requests the
   panel's prior default (`7d` / `30d`), not `all` — verify via the
   initial network request param.
9. **Performance sanity.** With a few hundred seeded sessions,
   `?range=all` on `/api/usage/tokens` and `/api/sessions/outcomes`
   completes well under 1 s locally.
10. **Backward compat.** `today` / `7d` / `30d` behave exactly as
    v0.6.7 on every endpoint; existing Playwright specs
    (`v0.6.spec.ts`, `v0.6.6.spec.ts`, `v0.6.7.spec.ts`) pass
    unchanged.

## Order of operations

1. **`helpers/timerange.py`** — add `90d` + `1y` to `ALLOWED`, the two
   predicates, and the two `days_in_range` keys.
2. **`ui/src/lib/api.ts`** — widen the `Range` union.
3. **`ui/src/components/panels/TokenUsageCard.tsx`** — extend `RANGES`.
4. **Smoke test** — `scripts/dev/smoke_v0_6_8.py`: stop conditions
   1–6 + 9 against a real server with multi-month seeded fixtures
   (incl. one >1-year-old session for stop 4). Pattern-match the
   v0.6.7 smoke harness.
5. **Playwright** — extend or add a spec for stop 7 + 8 (picker shows
   6 options; selecting `1y` / `all` issues the widened request;
   default request param is unchanged) against `page.route` fixtures.
6. **README** — one line under the dashboard section: the range toggle
   now offers `90d` / `1y` / `all`, and a note that nothing prunes old
   data so `all` is the operator's full history.
7. **CHANGELOG `v0.6.8` entry** — DRAFT → shipped on completion, in
   the v0.6.6/v0.6.7 style (What ships / Verified / Operator flow /
   Not in release). Include the retention clarification — it's the
   operator-facing headline ("your data was never deleted; you just
   couldn't see it").

## Not in this release (deferred)

- **Retention / vacuum knob.** The *opposite* feature — actually
  deleting old rows + reclaiming SQLite space on a schedule or by an
  operator-set age. v0.6.8 only *exposes* old data; an
  `MISSION_CONTROL_RETENTION_DAYS` + nightly prune + `VACUUM` is a
  separate v0.7+ concern with its own irreversibility risks. `cc
  doctor` already reports `db_size_bytes` if growth becomes a concern.
- **Custom date-range picker** (arbitrary from/to). MVP is the fixed
  bucket toggle. A calendar range is a bigger UI surface; defer until
  the preset buckets prove insufficient.
- **Weekly / monthly downsampling for long ranges.** `1y` / `all`
  render daily bars; bucketing to weekly points for readability +
  payload reduction is a charting concern, not a data-access one.
  Defer.
- **Uniform gap-filled date axis.** Zero-filling absent days so long
  sparse ranges show a continuous x-axis. Existing endpoints don't
  zero-fill; adding it touches every daily-series builder. Defer.
- **CSV / JSON export of a range.** "Give me all my data as a file"
  is a natural sibling but a distinct feature. Defer.
- **Per-range default persistence.** Remembering the operator's last
  picked range per panel (localStorage). Nice-to-have; defer.

## Estimate

~1–1.5h. Genuinely small:
- `timerange.py` + `Range` + `RANGES`: ~15 min (the actual feature).
- Smoke test with multi-month fixtures: ~30–40 min.
- Playwright + README + CHANGELOG: ~30 min.

The effort is in the seeded-fixture test (proving the wider windows
actually return older rows), not the change itself.

## Compat note with v0.6.0 – v0.6.7

No collisions. The range param is orthogonal to every prior feature:
cost-source splits (v0.6.0), `?embed=1` (v0.6.6), and per-skill
budgets (v0.6.7) all read the same widened windows without change.
`/api/system/dispatcher` and `/api/skills` `today_cost_usd` math is
day-scoped and untouched. The v0.6.7 cross-account budget-summing
invariant (recorded in `026f6bf`) is unaffected — budgets remain
daily, this only widens *reporting* windows.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.7).
Quality bar matches v0.6.6 — single-surface, mirrors the existing
range-param plumbing. Estimate ~1–1.5h.
