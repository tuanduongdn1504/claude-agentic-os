// v0.6.0 — SkillLauncher + cost-source disambiguation.
//
// Specs are deterministic against API fixtures (page.route) so they don't
// need real seed data on the host. Covers the four amendment §Order of
// operations §8 checks: one-click launch, preset edit persistence,
// KpiRow two-source readout, sessions-list filter.
import { expect, Page, Route, test } from '@playwright/test';

// ---------- fixtures ----------

const SUMMARY = {
  sessions_today: 12,
  effective_tokens_today: 158_000,
  errors_today: 0,
  cost_usd_today: 7.42,
  cost_by_source: { api_pool: 4.12, max_sub: 3.10, unknown: 0.20 },
  tools_today: 64,
};

const SYSTEM_HEALTH = {
  ok: true, uptime_s: 3600, tz: 'Asia/Ho_Chi_Minh', mem_rss_mb: 84,
  db_size_bytes: 1024, last_otel_event_age_s: 12, last_sync_tick_age_s: 5,
  daemon_last_tick_age_s: 30, notifier_last_tick_age_s: 60,
  sync_loop_heartbeat_age_s: 5,
};

const ATTENTION = { issues: [], count: 0 };

const SKILLS_INITIAL = {
  items: [
    {
      name: 'morning-brief', environment: 'cc', description: 'Daily morning briefing skill',
      path: '/abs/morning-brief.md', autonomy_level: 'auto',
      user_invocable: 1, script_count: 1, last_modified: '2026-04-30T08:00:00Z',
      preset: { title: 'Morning brief', model: 'claude-sonnet-4-5', execution_mode: 'classic' },
      last_launched_at: '2026-05-18 07:30:00',
      launch_count: 12,
      avg_cost_usd_30d: 0.0421,
    },
    {
      name: 'inbox-triage', environment: 'cc', description: 'Triage inbox to actionable list',
      path: '/abs/inbox-triage.md', autonomy_level: 'review',
      user_invocable: 1, script_count: 1, last_modified: '2026-04-22T18:00:00Z',
      preset: null,
      last_launched_at: null,
      launch_count: 0,
      avg_cost_usd_30d: null,
    },
    {
      name: 'background-skill', environment: 'cc', description: 'Not user invocable',
      path: '/abs/bg.md', autonomy_level: 'auto',
      user_invocable: 0, script_count: 1, last_modified: null,
      preset: null, last_launched_at: null, launch_count: 0, avg_cost_usd_30d: null,
    },
  ],
  count: 3,
};

const EMPTY_TASKS = { items: [], count: 0 };
const EMPTY_LIVE = { items: [], window_s: 300 };
const EMPTY_SCHEDULES = { items: [], count: 0 };
const EMPTY_DECISIONS = { items: [], count: 0 };
const EMPTY_INBOX = { items: [], count: 0 };

const DISPATCHER_STATE = {
  max_concurrent: 3, running: 0, free_slots: 3, back_pressure: false,
  daily_cost_cap_usd: null, today_cost_usd: 7.42,
  today_cost_api_pool_usd: 4.12, today_cost_max_sub_usd: 3.10, today_cost_unknown_usd: 0.20,
  cost_capped: false, hard_risk_gate: true, risk_gated_today: 0,
};

const EMPTY_USAGE = {
  range: '7d', daily: [],
  totals: { input: 0, output: 0, cache_read: 0, cache_create: 0, total: 0 },
  cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 },
};

const SESSIONS_ALL = {
  items: [
    sessionRow('sess-api-1', 'api_pool', 'API task 1'),
    sessionRow('sess-api-2', 'api_pool', 'API task 2'),
    sessionRow('sess-max-1', 'max_sub',  'Interactive 1'),
    sessionRow('sess-unk-1', 'unknown',  'Pre-v0.6 row'),
  ],
  total: 4, offset: 0, limit: 100,
};

function sessionRow(id: string, source: string, title: string) {
  return {
    session_id: id, source: 'ide', entrypoint: 'claude-cli',
    cwd: '/Users/me/proj', git_branch: 'main',
    model: 'claude-sonnet-4-5', title,
    started_at: '2026-05-18T10:00:00Z', ended_at: '2026-05-18T10:05:00Z',
    duration_ms: 300_000, input_tokens: 1000, output_tokens: 2000,
    cache_read_tokens: 0, cache_create_tokens: 0, total_tokens: 3000,
    effective_tokens: 3000, cost_usd: 0.12, cost_source: source,
    error_count: 0, is_error_any: 0, rate_limit_hit: 0, stop_reason: null,
    service_tier: 'standard', jsonl_mtime: 0,
  };
}

// ---------- helpers ----------

async function mockBaseline(page: Page, overrides: Record<string, unknown> = {}) {
  const json = (body: unknown) => async (route: Route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(body),
  });
  await page.route('**/api/health',              json({ ok: true, uptime_s: 1 }));
  await page.route('**/api/system/health',       json(SYSTEM_HEALTH));
  await page.route('**/api/system/state',        json({}));
  await page.route('**/api/system/dispatcher',   json(DISPATCHER_STATE));
  await page.route('**/api/system/telegram',     json({ configured: false, alive: false, pid: null, chat_id_set: false, token_set: false, last_outbound_at: null, notified_24h: 0, notified_24h_by_type: {}, stdout_log_mtime_age_s: null, last_error: null, last_error_at: null }));
  await page.route('**/api/system/pressure',     json({ retry_exhaust_threshold: 10, retry_exhaust_count_jsonl: 0, retry_exhaust_count_otel: 0, compaction_count: 0, recent_api_errors: [] }));
  await page.route('**/api/attention',           json(ATTENTION));
  await page.route('**/api/summary',             json(overrides.summary ?? SUMMARY));
  await page.route('**/api/summary/sparklines',  json({ slots: [], sessions: [], tokens: [], cost_usd: [], errors: [] }));
  await page.route('**/api/usage/tokens**',      json(overrides.usage ?? EMPTY_USAGE));
  await page.route('**/api/usage/cache**',       json({ range: '7d', overall_hit_rate: null, target_hit_rate: 0.7, low_sample: true, billable_tokens: 0, daily: [] }));
  await page.route('**/api/sessions/outcomes**', json({ range: '7d', daily: [], totals: { errored:0, rate_limited:0, truncated:0, unfinished:0, ok:0, total:0 }, buckets_priority: [] }));
  await page.route('**/api/sessions/live',       json(EMPTY_LIVE));
  await page.route('**/api/sessions/by-project**', json({ range: '7d', items: [] }));
  await page.route('**/api/sessions/failures**', json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/tools/latency**',     json({ range: '7d', items: [] }));
  await page.route('**/api/tools/agent-fanout**', json({ range: '7d', items: [] }));
  await page.route('**/api/tools/edit-decisions**', json({ range: '7d', items: [], total: 0, low_sample: true }));
  await page.route('**/api/hooks/activity**',    json({ range: '7d', total_fires: 0, starts: 0, completes: 0, jsonl_stop_hook_summaries: 0, paired_count: 0, paired_p50_ms: null, paired_p95_ms: null, paired_max_ms: null }));
  await page.route('**/api/activity/productivity**', json({ range: '7d', daily: [], totals: { commits: 0, pull_requests: 0, lines_of_code: 0 }, note: '' }));
  await page.route('**/api/activity/heatmap**',  json({ range: '30d', grid: Array.from({length:7},()=>Array(24).fill(0)), total: 0, peak: 0, weekdays: ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'] }));
  await page.route('**/api/tasks**',             json(EMPTY_TASKS));
  await page.route('**/api/schedules**',         json(EMPTY_SCHEDULES));
  await page.route('**/api/decisions**',         json(EMPTY_DECISIONS));
  await page.route('**/api/inbox**',             json(EMPTY_INBOX));
  await page.route('**/api/firehose**',          json({ items: [] }));
  await page.route('**/api/mcp**',               json({ range: '7d', items: [] }));
  await page.route('**/api/skills/economics**',  json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/context/health**',    json({ settings: { exists: false }, claude_md: { exists: false } }));
  await page.route('**/api/skills',              json(overrides.skills ?? SKILLS_INITIAL));
}

// ---------------------------------------------------------------------------

test.describe('v0.6.0 — KpiRow cost-by-source readout', () => {
  test('shows api $ and max $ when both nonzero, plus amber ? for unknown', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/');
    const sub = page.getByTestId('cost-by-source');
    await expect(sub).toBeVisible();
    // Total = 4.12 + 3.10 + 0.20 = 7.42.
    await expect(sub).toContainText(/api/i);
    await expect(sub).toContainText('$4.12');
    await expect(sub).toContainText(/max/i);
    await expect(sub).toContainText('$3.10');
    // Amber ? appended when unknown > 0.
    await expect(sub).toContainText('?');
    await expect(sub).toContainText('$0.20');
  });

  test('hides the sub-line entirely when today total is zero', async ({ page }) => {
    await mockBaseline(page, {
      summary: { ...SUMMARY, cost_usd_today: 0, cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 } },
    });
    await page.goto('/');
    await expect(page.getByTestId('cost-by-source')).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------

test.describe('v0.6.0 — SkillLauncher', () => {
  test('renders only user_invocable skills, sorted by launch_count', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/');
    // Two invocable skills should render.
    await expect(page.getByText('morning-brief', { exact: true })).toBeVisible();
    await expect(page.getByText('inbox-triage',  { exact: true })).toBeVisible();
    await expect(page.getByText('background-skill', { exact: true })).toHaveCount(0);
    // morning-brief (launch_count=12) sorts before inbox-triage (0).
    const cardOrder = await page.locator('[data-action="launch-skill"]').evaluateAll(
      btns => btns.map(b => (b as HTMLElement).getAttribute('data-skill')),
    );
    expect(cardOrder.indexOf('morning-brief')).toBeLessThan(cardOrder.indexOf('inbox-triage'));
  });

  test('one-click launch posts to /api/skills/{name}/launch and shows task chip', async ({ page }) => {
    await mockBaseline(page);
    let launchedBody: any = null;
    await page.route('**/api/skills/morning-brief/launch', async route => {
      launchedBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ task_id: 42, status: 'pending', skill: 'morning-brief' }),
      });
    });
    await page.goto('/');
    await page.locator('[data-action="launch-skill"][data-skill="morning-brief"]').click();
    await expect(page.getByText(/↗ Task #42/)).toBeVisible({ timeout: 4000 });
    expect(launchedBody).not.toBeNull();
  });

  test('preset edit persists across reload (round-trip mock)', async ({ page }) => {
    // Initial state — preset has model=claude-sonnet-4-5.
    let currentSkills = JSON.parse(JSON.stringify(SKILLS_INITIAL));
    await mockBaseline(page, { skills: currentSkills });
    await page.route('**/api/skills', async route => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(currentSkills) });
    });
    await page.route('**/api/skills/morning-brief/preset', async route => {
      const sent = JSON.parse(route.request().postData() || 'null');
      // Persist sent preset onto the in-memory fixture.
      const target = currentSkills.items.find((s: { name: string }) => s.name === 'morning-brief');
      target.preset = sent;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(target) });
    });

    await page.goto('/');
    await page.locator('[aria-label="Edit preset for morning-brief"]').click();
    // Title input is autofocused inside the editor — change model.
    const editor = page.locator('[data-testid="preset-editor"][data-skill="morning-brief"]');
    await expect(editor).toBeVisible();
    const modelSelect = editor.locator('select').nth(0);
    await modelSelect.selectOption('claude-opus-4-5');
    await editor.getByRole('button', { name: /save preset/i }).click();
    await expect(editor).toHaveCount(0, { timeout: 4000 });

    // Reload — fixture now returns the persisted preset.
    await page.reload();
    await page.locator('[aria-label="Edit preset for morning-brief"]').click();
    const editor2 = page.locator('[data-testid="preset-editor"][data-skill="morning-brief"]');
    await expect(editor2).toBeVisible();
    await expect(editor2.locator('select').nth(0)).toHaveValue('claude-opus-4-5');
  });

  test('empty state when no invocable skills exist', async ({ page }) => {
    await mockBaseline(page, {
      skills: {
        items: [SKILLS_INITIAL.items[2]], // only the non-invocable one
        count: 1,
      },
    });
    await page.goto('/');
    await expect(page.getByText(/No invocable skills yet/i)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------

test.describe('v0.6.0 — sessions-list cost-source filter', () => {
  test('filter dropdown narrows the request param', async ({ page }) => {
    const seen: string[] = [];
    const json = (body: unknown) => async (route: Route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(body),
    });
    await mockBaseline(page);
    // Override /api/sessions to capture the cost_source query param.
    await page.route('**/api/sessions**', async route => {
      const u = new URL(route.request().url());
      seen.push(u.searchParams.get('cost_source') ?? '<none>');
      const filtered = u.searchParams.get('cost_source')
        ? { ...SESSIONS_ALL, items: SESSIONS_ALL.items.filter(s => s.cost_source === u.searchParams.get('cost_source')) }
        : SESSIONS_ALL;
      filtered.total = filtered.items.length;
      await json(filtered)(route);
    });

    await page.goto('/sessions');
    // Default = all → first call has no cost_source param.
    await page.waitForResponse(r => r.url().includes('/api/sessions') && r.status() === 200);
    await expect(page.getByText('API task 1')).toBeVisible();
    await expect(page.getByText('Interactive 1')).toBeVisible();
    await expect(page.getByText('Pre-v0.6 row')).toBeVisible();

    // Click "api" filter → request goes with cost_source=api_pool and only API rows render.
    await page.getByTestId('cost-source-filter-api_pool').click();
    await page.waitForResponse(r => r.url().includes('/api/sessions') && r.url().includes('cost_source=api_pool'));
    await expect(page.getByText('API task 1')).toBeVisible();
    await expect(page.getByText('Interactive 1')).toHaveCount(0);
    await expect(page.getByText('Pre-v0.6 row')).toHaveCount(0);

    // Click "?" → only unknown rows.
    await page.getByTestId('cost-source-filter-unknown').click();
    await page.waitForResponse(r => r.url().includes('/api/sessions') && r.url().includes('cost_source=unknown'));
    await expect(page.getByText('Pre-v0.6 row')).toBeVisible();
    await expect(page.getByText('API task 1')).toHaveCount(0);

    // Source pills render with the correct data-source value.
    const pill = page.locator('[data-testid="cost-source-pill"]').first();
    await expect(pill).toHaveAttribute('data-source', 'unknown');
  });
});
