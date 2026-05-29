// v0.6.8 — expose historical ranges (90d / 1y / all).
//
// Covers the two UI-side stop conditions from
// `observability/(C) build-your-own-dashboard-prompt-v0.6.8-amendment.md`:
//   stop 7 — the single shared RangePicker now renders SIX options, and
//            clicking 1Y / ALL issues the widened request + re-renders with
//            no console error (checked on TokenUsageCard, SessionsPage, and
//            the SkillCost "skill economics" panel).
//   stop 8 — defaults are unchanged: first load still requests each panel's
//            prior default (7d / 30d), NOT all. Verified via the initial
//            network request's `range` param.
//
// Fixtures are pinned via page.route so the spec is hermetic — no real
// backend or DB. Request `range` params are captured with page.on('request').
import { expect, Page, Route, test } from '@playwright/test';

const SIX_RANGES = ['today', '7d', '30d', '90d', '1y', 'all'] as const;

function rangeOf(url: string): string | null {
  return new URL(url).searchParams.get('range');
}

const EMPTY_USAGE = {
  range: '7d', daily: [],
  totals: { input: 0, output: 0, cache_read: 0, cache_create: 0, total: 0 },
  cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 },
};

// Mocks every /api/* surface the dashboard touches, 200-empty, so the only
// variable under test is the range param the UI chooses to send.
async function mockBaseline(page: Page) {
  const json = (body: unknown) => async (route: Route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(body),
  });
  await page.route('**/api/health',              json({ ok: true, uptime_s: 1 }));
  await page.route('**/api/system/health',       json({ ok: true, uptime_s: 3600, tz: 'Asia/Ho_Chi_Minh', mem_rss_mb: 84, db_size_bytes: 1024, last_otel_event_age_s: 12, last_sync_tick_age_s: 5, daemon_last_tick_age_s: 30, notifier_last_tick_age_s: 60, sync_loop_heartbeat_age_s: 5 }));
  await page.route('**/api/system/state',        json({}));
  await page.route('**/api/system/dispatcher',   json({ max_concurrent: 3, running: 0, free_slots: 3, back_pressure: false, daily_cost_cap_usd: null, today_cost_usd: 0, today_cost_api_pool_usd: 0, today_cost_max_sub_usd: 0, today_cost_unknown_usd: 0, cost_capped: false, hard_risk_gate: true, risk_gated_today: 0, skills_with_budget: 0, skills_at_budget: 0 }));
  await page.route('**/api/system/telegram',     json({ configured: false, alive: false, pid: null, chat_id_set: false, token_set: false, last_outbound_at: null, notified_24h: 0, notified_24h_by_type: {}, stdout_log_mtime_age_s: null, last_error: null, last_error_at: null }));
  await page.route('**/api/system/pressure',     json({ retry_exhaust_threshold: 10, retry_exhaust_count_jsonl: 0, retry_exhaust_count_otel: 0, compaction_count: 0, recent_api_errors: [] }));
  await page.route('**/api/attention',           json({ issues: [], count: 0 }));
  await page.route('**/api/summary',             json({ sessions_today: 0, effective_tokens_today: 0, errors_today: 0, cost_usd_today: 0, cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 }, tools_today: 0 }));
  await page.route('**/api/summary/sparklines',  json({ slots: [], sessions: [], tokens: [], cost_usd: [], errors: [] }));
  await page.route('**/api/usage/tokens**',      json(EMPTY_USAGE));
  await page.route('**/api/usage/cache**',       json({ range: '7d', overall_hit_rate: null, target_hit_rate: 0.7, low_sample: true, billable_tokens: 0, daily: [] }));
  await page.route('**/api/sessions/outcomes**', json({ range: '7d', daily: [], totals: { errored: 0, rate_limited: 0, truncated: 0, unfinished: 0, ok: 0, total: 0 }, buckets_priority: [] }));
  await page.route('**/api/sessions/live',       json({ items: [], window_s: 300 }));
  await page.route('**/api/sessions/by-project**', json({ range: '7d', items: [] }));
  await page.route('**/api/sessions/failures**', json({ range: '30d', items: [], count: 0 }));
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
  // Sessions LIST (regex so it only matches `/api/sessions?…`, not the
  // `/api/sessions/*` sub-routes above).
  await page.route(/\/api\/sessions\?/,          json({ items: [], total: 0, offset: 0, limit: 50 }));
  await page.route('**/api/skills',              json({ items: [], count: 0 }));
}

// Collect console-error + pageerror; assert empty so a widened range never
// throws in the panel render path.
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

// ---------------------------------------------------------------------------

test.describe('v0.6.8 — RangePicker exposes 90d / 1y / all', () => {
  test('TokenUsageCard: default stays 7d, picker offers 6, 1Y/ALL widen the fetch', async ({ page }) => {
    const tokenRanges: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/api/usage/tokens')) tokenRanges.push(rangeOf(r.url()) ?? '(none)');
    });
    const errors = collectErrors(page);
    await mockBaseline(page);

    const firstTokens = page.waitForRequest((u) => u.url().includes('/api/usage/tokens'));
    await page.goto('/');
    await firstTokens;

    // stop 8 — first request is the panel default (7d), not widened.
    expect(tokenRanges[0]).toBe('7d');
    const initial = [...tokenRanges];
    expect(initial).not.toContain('all');
    expect(initial).not.toContain('1y');
    expect(initial).not.toContain('90d');

    // stop 7 — the TokenUsageCard picker renders all six options. Scope to the
    // card's CardHeader via two explicit parent steps (h3 → title-group div →
    // CardHeader) so a sibling card's picker can't bleed into the count.
    const tokenPicker = page.getByRole('heading', { name: 'Daily token consumption' })
      .locator('xpath=../..');
    await expect(tokenPicker.locator('[data-range]')).toHaveCount(6);
    for (const r of SIX_RANGES) {
      await expect(tokenPicker.locator(`[data-range="${r}"]`)).toBeVisible();
    }

    // Clicking 1Y then ALL fires the widened request each time.
    const req1y = page.waitForRequest((u) => u.url().includes('/api/usage/tokens') && rangeOf(u.url()) === '1y');
    await tokenPicker.locator('[data-range="1y"]').click();
    await req1y;

    const reqAll = page.waitForRequest((u) => u.url().includes('/api/usage/tokens') && rangeOf(u.url()) === 'all');
    await tokenPicker.locator('[data-range="all"]').click();
    await reqAll;

    // Shared component → the new options propagate to every panel on the page.
    expect(await page.locator('[data-range="all"]').count()).toBeGreaterThanOrEqual(5);

    expect(errors).toEqual([]);
  });

  test('SessionsPage: default stays 30d, picker offers 6, ALL widens the list fetch', async ({ page }) => {
    const sessRanges: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/api/sessions?')) sessRanges.push(rangeOf(r.url()) ?? '(none)');
    });
    const errors = collectErrors(page);
    await mockBaseline(page);

    const firstList = page.waitForRequest((u) => u.url().includes('/api/sessions?'));
    await page.goto('/sessions');
    await firstList;

    // stop 8 — list defaults to 30d, not all.
    expect(sessRanges[0]).toBe('30d');
    expect([...sessRanges]).not.toContain('all');

    // stop 7 — single picker on this page, six options.
    await expect(page.locator('[data-range]')).toHaveCount(6);
    for (const r of SIX_RANGES) {
      await expect(page.locator(`[data-range="${r}"]`)).toBeVisible();
    }

    const reqAll = page.waitForRequest((u) => u.url().includes('/api/sessions?') && rangeOf(u.url()) === 'all');
    await page.locator('[data-range="all"]').click();
    await reqAll;

    expect(errors).toEqual([]);
  });

  test('SkillCost panel: default stays 30d, picker offers 6, 1Y widens the economics fetch', async ({ page }) => {
    const econRanges: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/api/skills/economics')) econRanges.push(rangeOf(r.url()) ?? '(none)');
    });
    const errors = collectErrors(page);
    await mockBaseline(page);

    const firstEcon = page.waitForRequest((u) => u.url().includes('/api/skills/economics'));
    await page.goto('/skills');
    await firstEcon;

    // stop 8 — skill economics defaults to 30d.
    expect(econRanges[0]).toBe('30d');
    expect([...econRanges]).not.toContain('all');

    // stop 7 — the SkillCost panel's picker offers six options. SkillCost and
    // TopSkills share the *same* CardTitle ("Invocations · tokens · cost per
    // skill"), so anchor on SkillCost's unique Kicker ("Skill economics") and
    // step up to its CardHeader (kicker → title-group div → CardHeader).
    const skillPicker = page.getByText('Skill economics', { exact: true })
      .locator('xpath=../..');
    await expect(skillPicker.locator('[data-range]')).toHaveCount(6);

    const req1y = page.waitForRequest((u) => u.url().includes('/api/skills/economics') && rangeOf(u.url()) === '1y');
    await skillPicker.locator('[data-range="1y"]').click();
    await req1y;

    expect(errors).toEqual([]);
  });
});
