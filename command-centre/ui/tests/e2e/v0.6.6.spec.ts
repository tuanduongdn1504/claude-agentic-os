// v0.6.6 — Obsidian embed mode (`?embed=1`).
//
// Covers amendment stop conditions 1, 2, 3, 4: embed=1 hides chrome,
// no-embed renders full chrome, embed flag preserved across routes,
// AttentionBar still visible in embed (rendered inside CommandPage, not
// AppShell, so it survives the chrome strip).
//
// Specs are deterministic against API fixtures (page.route) so they
// don't depend on real seed data on the host — same shape as v0.6.spec.
// Stop 7 (real Obsidian install + iframe load) is operator-side manual;
// documented in README, not in this file.
import { expect, Page, Route, test } from '@playwright/test';

const SYSTEM_HEALTH = {
  ok: true, uptime_s: 3600, tz: 'Asia/Ho_Chi_Minh', mem_rss_mb: 84,
  db_size_bytes: 1024, last_otel_event_age_s: 12, last_sync_tick_age_s: 5,
  daemon_last_tick_age_s: 30, notifier_last_tick_age_s: 60,
  sync_loop_heartbeat_age_s: 5,
};

const SUMMARY = {
  sessions_today: 0, effective_tokens_today: 0, errors_today: 0,
  cost_usd_today: 0, cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 },
  tools_today: 0,
};

const DISPATCHER_STATE = {
  max_concurrent: 3, running: 0, free_slots: 3, back_pressure: false,
  daily_cost_cap_usd: null, today_cost_usd: 0,
  today_cost_api_pool_usd: 0, today_cost_max_sub_usd: 0, today_cost_unknown_usd: 0,
  cost_capped: false, hard_risk_gate: true, risk_gated_today: 0,
};

const EMPTY_USAGE = {
  range: '7d', daily: [],
  totals: { input: 0, output: 0, cache_read: 0, cache_create: 0, total: 0 },
  cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 },
};

async function mockBaseline(page: Page, overrides: { attention?: unknown } = {}) {
  const json = (body: unknown) => async (route: Route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(body),
  });
  await page.route('**/api/health',              json({ ok: true, uptime_s: 1 }));
  await page.route('**/api/system/health',       json(SYSTEM_HEALTH));
  await page.route('**/api/system/state',        json({}));
  await page.route('**/api/system/dispatcher',   json(DISPATCHER_STATE));
  await page.route('**/api/system/telegram',     json({ configured: false, alive: false, pid: null, chat_id_set: false, token_set: false, last_outbound_at: null, notified_24h: 0, notified_24h_by_type: {}, stdout_log_mtime_age_s: null, last_error: null, last_error_at: null }));
  await page.route('**/api/system/pressure',     json({ retry_exhaust_threshold: 10, retry_exhaust_count_jsonl: 0, retry_exhaust_count_otel: 0, compaction_count: 0, recent_api_errors: [] }));
  await page.route('**/api/attention',           json(overrides.attention ?? { issues: [], count: 0 }));
  await page.route('**/api/summary',             json(SUMMARY));
  await page.route('**/api/summary/sparklines',  json({ slots: [], sessions: [], tokens: [], cost_usd: [], errors: [] }));
  await page.route('**/api/usage/tokens**',      json(EMPTY_USAGE));
  await page.route('**/api/usage/cache**',       json({ range: '7d', overall_hit_rate: null, target_hit_rate: 0.7, low_sample: true, billable_tokens: 0, daily: [] }));
  await page.route('**/api/sessions/outcomes**', json({ range: '7d', daily: [], totals: { errored: 0, rate_limited: 0, truncated: 0, unfinished: 0, ok: 0, total: 0 }, buckets_priority: [] }));
  await page.route('**/api/sessions/live',       json({ items: [], window_s: 300 }));
  await page.route('**/api/sessions/by-project**', json({ range: '7d', items: [] }));
  await page.route('**/api/sessions/failures**', json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/sessions**',          json({ items: [], total: 0, offset: 0, limit: 100 }));
  await page.route('**/api/tools/latency**',     json({ range: '7d', items: [] }));
  await page.route('**/api/tools/agent-fanout**', json({ range: '7d', items: [] }));
  await page.route('**/api/tools/edit-decisions**', json({ range: '7d', items: [], total: 0, low_sample: true }));
  await page.route('**/api/hooks/activity**',    json({ range: '7d', total_fires: 0, starts: 0, completes: 0, jsonl_stop_hook_summaries: 0, paired_count: 0, paired_p50_ms: null, paired_p95_ms: null, paired_max_ms: null }));
  await page.route('**/api/activity/productivity**', json({ range: '7d', daily: [], totals: { commits: 0, pull_requests: 0, lines_of_code: 0 }, note: '' }));
  await page.route('**/api/activity/heatmap**',  json({ range: '30d', grid: Array.from({ length: 7 }, () => Array(24).fill(0)), total: 0, peak: 0, weekdays: ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'] }));
  await page.route('**/api/tasks**',             json({ items: [], count: 0 }));
  await page.route('**/api/schedules**',         json({ items: [], count: 0 }));
  await page.route('**/api/decisions**',         json({ items: [], count: 0 }));
  await page.route('**/api/inbox**',             json({ items: [], count: 0 }));
  await page.route('**/api/firehose**',          json({ items: [] }));
  await page.route('**/api/mcp**',               json({ range: '7d', items: [] }));
  await page.route('**/api/skills/economics**',  json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/context/health**',    json({ settings: { exists: false }, claude_md: { exists: false } }));
  await page.route('**/api/skills',              json({ items: [], count: 0 }));
}

test.describe('v0.6.6 — Obsidian embed mode (?embed=1)', () => {
  // Stop 1: embed=1 strips chrome.
  test('embed=1 hides Nav, Header, EmergencyStopBanner, CommandPalette button, footer', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/?embed=1');

    // Branding text from <header>: "Command Centre" + "GMT+7 · up…".
    await expect(page.getByText('Command Centre', { exact: true })).toHaveCount(0);
    // Nav links (Activity / Sessions / Skills & MCP / Decisions / Command).
    await expect(page.getByRole('link', { name: /activity/i })).toHaveCount(0);
    await expect(page.getByRole('link', { name: /sessions/i })).toHaveCount(0);
    // EmergencyStopBanner's red button.
    await expect(page.getByRole('button', { name: /emergency stop/i })).toHaveCount(0);
    // ⌘K palette trigger.
    await expect(page.getByRole('button', { name: /open command palette/i })).toHaveCount(0);
    // Footer tagline.
    await expect(page.getByText(/no cloud · no account · no outbound telemetry/i)).toHaveCount(0);

    // Embed wrapper class is applied so the embed CSS rules engage.
    await expect(page.locator('.app-shell.embedded')).toHaveCount(1);
  });

  // Stop 2: no embed flag → identical to current main.
  test('no embed renders full chrome unchanged', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/');

    await expect(page.getByText('Command Centre', { exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: /activity/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /emergency stop/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /open command palette/i })).toBeVisible();
    await expect(page.getByText(/no cloud · no account · no outbound telemetry/i)).toBeVisible();

    // Embed wrapper class must NOT be present.
    await expect(page.locator('.app-shell.embedded')).toHaveCount(0);
  });

  // Stop 3: embed=1 works on every route (proxy for "preserved across
  // navigation" — TanStack Router's `search: (prev) => prev` keeps the
  // flag set on every in-app `<Link>`; if the AppShell reads it
  // correctly on each route, an operator clicking around an embedded
  // pane stays embedded).
  test('embed=1 strips chrome on every route', async ({ page }) => {
    await mockBaseline(page);

    for (const path of ['/?embed=1', '/activity?embed=1', '/skills?embed=1', '/sessions?embed=1', '/decisions?embed=1']) {
      await page.goto(path);
      await expect(page.locator('.app-shell.embedded'), `embed wrapper missing on ${path}`).toHaveCount(1);
      await expect(page.getByText('Command Centre', { exact: true }), `branding leaked on ${path}`).toHaveCount(0);
      await expect(page.getByRole('button', { name: /emergency stop/i }), `emergency stop leaked on ${path}`).toHaveCount(0);
    }
  });

  // Stop 4: AttentionBar is rendered inside CommandPage (not AppShell),
  // so the embed strip leaves it intact. Mock at least one attention
  // issue and verify the red "Needs attention" header surfaces inside
  // the embedded view.
  test('AttentionBar still visible in embed when issues exist', async ({ page }) => {
    await mockBaseline(page, {
      attention: {
        count: 1,
        issues: [
          { kind: 'dispatcher_stale', age_s: 180, message: 'dispatcher silent 180s' },
        ],
      },
    });
    await page.goto('/?embed=1');

    await expect(page.locator('.app-shell.embedded')).toHaveCount(1);
    // The bar's red header text.
    await expect(page.getByText(/Needs attention/i)).toBeVisible();
    await expect(page.getByText(/dispatcher silent 180s/i)).toBeVisible();
  });
});
