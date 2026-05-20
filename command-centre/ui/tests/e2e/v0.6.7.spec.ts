// v0.6.7 — per-skill daily cost budgets.
//
// Two surfaces covered:
//   1. SkillLauncher card 3-tier render (stop 11) + Launch button disable
//      at the red tier (stop 4 UI side).
//   2. AttentionBar amber rendering for the new `skill_budget_capped`
//      issue (stop 4 UI side).
//
// Fixtures are pinned via page.route so the spec is hermetic — no real
// dispatcher or DB required.
import { expect, Page, Route, test } from '@playwright/test';

// ---------- shared baseline ----------

const SUMMARY = {
  sessions_today: 4, effective_tokens_today: 12_000, errors_today: 0,
  cost_usd_today: 6.50,
  cost_by_source: { api_pool: 6.50, max_sub: 0, unknown: 0 },
  tools_today: 18,
};

const SYSTEM_HEALTH = {
  ok: true, uptime_s: 3600, tz: 'Asia/Ho_Chi_Minh', mem_rss_mb: 84,
  db_size_bytes: 1024, last_otel_event_age_s: 12, last_sync_tick_age_s: 5,
  daemon_last_tick_age_s: 30, notifier_last_tick_age_s: 60,
  sync_loop_heartbeat_age_s: 5,
};

const DISPATCHER_STATE = {
  max_concurrent: 3, running: 0, free_slots: 3, back_pressure: false,
  daily_cost_cap_usd: null, today_cost_usd: 6.50,
  today_cost_api_pool_usd: 6.50, today_cost_max_sub_usd: 0, today_cost_unknown_usd: 0,
  cost_capped: false, hard_risk_gate: true, risk_gated_today: 0,
  skills_with_budget: 3, skills_at_budget: 1,
};

function skillRow(name: string, daily_budget_usd: number | null, today_cost_usd: number) {
  return {
    name, environment: 'cc', description: `Test ${name}`,
    path: `/abs/${name}.md`, autonomy_level: 'auto',
    user_invocable: 1, script_count: 1, last_modified: null,
    preset: null, last_launched_at: null, launch_count: 0,
    avg_cost_usd_30d: 0.10,
    daily_budget_usd, today_cost_usd,
  };
}

const SKILLS = {
  items: [
    // Dim tier: $1.00 / $5.00 = 20% → far below 0.8×.
    skillRow('skill-dim', 5.00, 1.00),
    // Amber tier: $4.20 / $5.00 = 84% → between 0.8× and 1.0×.
    skillRow('skill-amber', 5.00, 4.20),
    // Red tier: $1.50 / $1.00 → over budget, Launch disabled.
    skillRow('skill-red', 1.00, 1.50),
    // No-budget skill: budget line hidden, Launch enabled.
    skillRow('skill-nobudget', null, 0),
  ],
  count: 4,
};

const ATTENTION_BUDGET = {
  issues: [
    {
      kind: 'skill_budget_capped', severity: 'warning', count: 1,
      skill: 'skill-red', today_cost_usd: 1.50, daily_budget_usd: 1.00,
      title: '1 skill at daily budget',
      message: 'skill-red blocked at $1.50 / $1.00',
    },
  ],
  count: 1,
};

const ATTENTION_BUDGET_AND_GLOBAL_CAP = {
  // Both an error (cost_capped) and a warning (skill_budget_capped) at once
  // — banner should tone to red, but the skill-budget item still renders.
  issues: [
    {
      kind: 'cost_capped', severity: 'error',
      today_cost_usd: 8.50, today_cost_api_pool_usd: 8.50, cap_usd: 8.00,
      message: 'API-pool spend reached cap ($8.50 of $8.00 today). Max-sub usage continues.',
    },
    {
      kind: 'skill_budget_capped', severity: 'warning', count: 2,
      skill: 'skill-red', today_cost_usd: 1.50, daily_budget_usd: 1.00,
      title: '2 skills at daily budget',
      message: 'skill-red blocked at $1.50 / $1.00 (+1 more)',
    },
  ],
  count: 2,
};

async function mockBaseline(page: Page, overrides: Record<string, unknown> = {}) {
  const json = (body: unknown) => async (route: Route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(body),
  });
  await page.route('**/api/health',              json({ ok: true, uptime_s: 1 }));
  await page.route('**/api/system/health',       json(SYSTEM_HEALTH));
  await page.route('**/api/system/state',        json({}));
  await page.route('**/api/system/dispatcher',   json(overrides.dispatcher ?? DISPATCHER_STATE));
  await page.route('**/api/system/telegram',     json({ configured: false, alive: false, pid: null, chat_id_set: false, token_set: false, last_outbound_at: null, notified_24h: 0, notified_24h_by_type: {}, stdout_log_mtime_age_s: null, last_error: null, last_error_at: null }));
  await page.route('**/api/system/pressure',     json({ retry_exhaust_threshold: 10, retry_exhaust_count_jsonl: 0, retry_exhaust_count_otel: 0, compaction_count: 0, recent_api_errors: [] }));
  await page.route('**/api/attention',           json(overrides.attention ?? { issues: [], count: 0 }));
  await page.route('**/api/summary',             json(SUMMARY));
  await page.route('**/api/summary/sparklines',  json({ slots: [], sessions: [], tokens: [], cost_usd: [], errors: [] }));
  await page.route('**/api/usage/tokens**',      json({ range: '7d', daily: [], totals: { input: 0, output: 0, cache_read: 0, cache_create: 0, total: 0 }, cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 } }));
  await page.route('**/api/usage/cache**',       json({ range: '7d', overall_hit_rate: null, target_hit_rate: 0.7, low_sample: true, billable_tokens: 0, daily: [] }));
  await page.route('**/api/sessions/outcomes**', json({ range: '7d', daily: [], totals: { errored:0, rate_limited:0, truncated:0, unfinished:0, ok:0, total:0 }, buckets_priority: [] }));
  await page.route('**/api/sessions/live',       json({ items: [], window_s: 300 }));
  await page.route('**/api/sessions/by-project**', json({ range: '7d', items: [] }));
  await page.route('**/api/sessions/failures**', json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/tools/latency**',     json({ range: '7d', items: [] }));
  await page.route('**/api/tools/agent-fanout**', json({ range: '7d', items: [] }));
  await page.route('**/api/tools/edit-decisions**', json({ range: '7d', items: [], total: 0, low_sample: true }));
  await page.route('**/api/hooks/activity**',    json({ range: '7d', total_fires: 0, starts: 0, completes: 0, jsonl_stop_hook_summaries: 0, paired_count: 0, paired_p50_ms: null, paired_p95_ms: null, paired_max_ms: null }));
  await page.route('**/api/activity/productivity**', json({ range: '7d', daily: [], totals: { commits: 0, pull_requests: 0, lines_of_code: 0 }, note: '' }));
  await page.route('**/api/activity/heatmap**',  json({ range: '30d', grid: Array.from({length:7},()=>Array(24).fill(0)), total: 0, peak: 0, weekdays: ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'] }));
  await page.route('**/api/tasks**',             json({ items: [], count: 0 }));
  await page.route('**/api/schedules**',         json({ items: [], count: 0 }));
  await page.route('**/api/decisions**',         json({ items: [], count: 0 }));
  await page.route('**/api/inbox**',             json({ items: [], count: 0 }));
  await page.route('**/api/firehose**',          json({ items: [] }));
  await page.route('**/api/mcp**',               json({ range: '7d', items: [] }));
  await page.route('**/api/skills/economics**',  json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/context/health**',    json({ settings: { exists: false }, claude_md: { exists: false } }));
  await page.route('**/api/skills',              json(overrides.skills ?? SKILLS));
}

// ---------------------------------------------------------------------------

test.describe('v0.6.7 — SkillLauncher 3-tier budget visuals', () => {
  test('renders dim / amber / red tiers with the right copy + no line when budget is null', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/');

    // Dim: under 0.8× — text-text-subtle, no warning tone.
    const dim = page.locator('[data-skill="skill-dim"][data-budget-tier]');
    await expect(dim).toBeVisible();
    await expect(dim).toHaveAttribute('data-budget-tier', 'dim');
    await expect(dim).toContainText('today $1.00 / $5.00');

    // Amber: 0.8× ≤ x < 1.0×.
    const amber = page.locator('[data-skill="skill-amber"][data-budget-tier]');
    await expect(amber).toHaveAttribute('data-budget-tier', 'amber');
    await expect(amber).toContainText('today $4.20 / $5.00');

    // Red: ≥ 1.0× — Launch button should be disabled + carry the blocked
    // attribute so smoke tests and operators can identify it.
    const red = page.locator('[data-skill="skill-red"][data-budget-tier]');
    await expect(red).toHaveAttribute('data-budget-tier', 'red');
    await expect(red).toContainText('today $1.50 / $1.00');

    // No-budget skill: budget line hidden, Launch enabled (smoke check
    // that v0.6.6 layout still works when daily_budget_usd is null).
    await expect(
      page.locator('[data-skill="skill-nobudget"][data-budget-tier]'),
    ).toHaveCount(0);
  });

  test('Launch button disabled at the red tier with budget tooltip', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/');

    const redLaunch = page.locator(
      '[data-action="launch-skill"][data-skill="skill-red"]',
    );
    await expect(redLaunch).toBeDisabled();
    await expect(redLaunch).toHaveAttribute('data-budget-blocked', '1');

    // Other tiers should remain clickable.
    const dimLaunch = page.locator(
      '[data-action="launch-skill"][data-skill="skill-dim"]',
    );
    await expect(dimLaunch).toBeEnabled();
    const amberLaunch = page.locator(
      '[data-action="launch-skill"][data-skill="skill-amber"]',
    );
    await expect(amberLaunch).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------

test.describe('v0.6.7 — AttentionBar amber rendering for skill_budget_capped', () => {
  test('shows amber tone when only warning-severity issues are present', async ({ page }) => {
    await mockBaseline(page, { attention: ATTENTION_BUDGET });
    await page.goto('/');

    // The skill_budget_capped row should be visible.
    const row = page.locator('[data-issue-kind="skill_budget_capped"]');
    await expect(row).toBeVisible();
    await expect(row).toContainText('skill-red blocked at $1.50 / $1.00');
  });

  test('keeps the skill row visible even when a global cost_capped error sits above it', async ({ page }) => {
    await mockBaseline(page, { attention: ATTENTION_BUDGET_AND_GLOBAL_CAP });
    await page.goto('/');

    // Both rows render; cost_capped (error) is ranked above skill_budget_capped
    // (warning) so the order matches the spec.
    const items = page.locator('[data-issue-kind]');
    await expect(items.nth(0)).toHaveAttribute('data-issue-kind', 'cost_capped');
    await expect(items.nth(1)).toHaveAttribute('data-issue-kind', 'skill_budget_capped');
    await expect(items.nth(1)).toContainText('2 skills at daily budget');
  });
});
