// v0.7.0 — adversarial review gate.
//
// Two UI surfaces covered (per the amendment's smoke step 7):
//   1. SkillLauncher inline editor — the review_mode toggle in its own row,
//      firing its own PATCH /api/skills/{name}/review on save.
//   2. TaskBoard card — the verdict badge (✓ VERIFIED / ✗ NOT_VERIFIED /
//      ? MANUAL_VERIFY_REQUIRED) + the reviewer-reason line on escalated cards.
//
// Fixtures are pinned via page.route so the spec is hermetic — no real
// dispatcher or DB required. Mirrors v0.6.7.spec.ts's shape.
import { expect, Page, Route, test } from '@playwright/test';

// ---------- shared baseline ----------

const SYSTEM_HEALTH = {
  ok: true, uptime_s: 3600, tz: 'Asia/Ho_Chi_Minh', mem_rss_mb: 84,
  db_size_bytes: 1024, last_otel_event_age_s: 12, last_sync_tick_age_s: 5,
  daemon_last_tick_age_s: 30, notifier_last_tick_age_s: 60,
  sync_loop_heartbeat_age_s: 5,
};

const DISPATCHER_STATE = {
  max_concurrent: 3, running: 0, free_slots: 3, back_pressure: false,
  daily_cost_cap_usd: null, today_cost_usd: 0,
  today_cost_api_pool_usd: 0, today_cost_max_sub_usd: 0, today_cost_unknown_usd: 0,
  cost_capped: false, hard_risk_gate: true, risk_gated_today: 0,
  skills_with_budget: 0, skills_at_budget: 0,
  // v0.7.0 — review rollup.
  skills_with_review: 1, tasks_awaiting_review_approval: 1,
};

function skillRow(name: string, review_mode: number) {
  return {
    name, environment: 'cc', description: `Test ${name}`,
    path: `/abs/${name}.md`, autonomy_level: 'auto',
    user_invocable: 1, script_count: 1, last_modified: null,
    preset: null, last_launched_at: null, launch_count: 0,
    avg_cost_usd_30d: 0.10, daily_budget_usd: null, today_cost_usd: 0,
    review_mode,
  };
}

function taskRow(over: Record<string, unknown>) {
  return {
    id: 1, title: 'a task', description: 'do the thing', status: 'done',
    priority: 0, assigned_skill: 'rev-skill', model: null,
    execution_mode: 'stream', scheduled_for: null, requires_approval: 0,
    risk_level: 'low', dry_run: 0, quadrant: 'do', approved_at: null,
    session_id: null, started_at: null, completed_at: null, duration_ms: 1200,
    cost_usd: 0.2, cost_source: 'api_pool', output_summary: null,
    error_message: null, consecutive_failures: 0, created_at: '2026-05-30 10:00:00',
    success_criteria: null, review_verdict: null, review_count: 0,
    review_feedback: null,
    ...over,
  };
}

const SKILLS_REVIEW_OFF = { items: [skillRow('rev-skill', 0)], count: 1 };

const TASKS_WITH_VERDICTS = {
  items: [
    taskRow({ id: 11, title: 'verified task', status: 'done',
              review_verdict: 'VERIFIED', review_count: 0 }),
    taskRow({ id: 12, title: 'rejected task', status: 'awaiting_approval',
              review_verdict: 'NOT_VERIFIED', review_count: 1,
              review_feedback: 'the file /tmp/x.txt was never written',
              cost_usd: null }),
    taskRow({ id: 13, title: 'unverifiable task', status: 'awaiting_approval',
              review_verdict: 'MANUAL_VERIFY_REQUIRED', review_count: 0,
              review_feedback: 'reviewer produced no parseable verdict',
              cost_usd: null }),
  ],
  count: 3,
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
  await page.route('**/api/summary',             json({ sessions_today: 0, effective_tokens_today: 0, errors_today: 0, cost_usd_today: 0, cost_by_source: { api_pool: 0, max_sub: 0, unknown: 0 }, tools_today: 0 }));
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
  await page.route('**/api/tasks**',             json(overrides.tasks ?? { items: [], count: 0 }));
  await page.route('**/api/schedules**',         json({ items: [], count: 0 }));
  await page.route('**/api/decisions**',         json({ items: [], count: 0 }));
  await page.route('**/api/inbox**',             json({ items: [], count: 0 }));
  await page.route('**/api/firehose**',          json({ items: [] }));
  await page.route('**/api/mcp**',               json({ range: '7d', items: [] }));
  await page.route('**/api/skills/economics**',  json({ range: '30d', items: [], count: 0 }));
  await page.route('**/api/context/health**',    json({ settings: { exists: false }, claude_md: { exists: false } }));
  await page.route('**/api/skills',              json(overrides.skills ?? SKILLS_REVIEW_OFF));
}

// ---------------------------------------------------------------------------

test.describe('v0.7.0 — SkillLauncher review toggle', () => {
  test('editor shows the review row and PATCHes .../review on save', async ({ page }) => {
    await mockBaseline(page);

    // Capture the review PATCH so we can assert the toggle wires to its own
    // endpoint (separate from preset + budget).
    let reviewPatchBody: Record<string, unknown> | null = null;
    await page.route('**/api/skills/rev-skill/review', async (route: Route) => {
      reviewPatchBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ ...skillRow('rev-skill', 1) }),
      });
    });
    // The preset PATCH always fires on save; stub it so save completes.
    await page.route('**/api/skills/rev-skill/preset', async (route: Route) =>
      route.fulfill({ status: 200, contentType: 'application/json',
                      body: JSON.stringify(skillRow('rev-skill', 0)) }));

    await page.goto('/');

    // Open the inline preset editor.
    await page.locator('[aria-label="Edit preset for rev-skill"]').click();
    const reviewSection = page.locator('[data-testid="preset-editor-review"]');
    await expect(reviewSection).toBeVisible();
    await expect(reviewSection).toContainText('Adversarial review');
    await expect(reviewSection).toHaveAttribute('data-review-mode', '0');

    // Toggle it on (the switch checkbox is sr-only — force-check it).
    await reviewSection.getByRole('checkbox').check({ force: true });
    await expect(reviewSection).toHaveAttribute('data-review-mode', '1');

    // Save → the review PATCH must fire with review_mode=1.
    await page.getByRole('button', { name: 'save preset' }).click();
    await expect.poll(() => reviewPatchBody).not.toBeNull();
    expect(reviewPatchBody).toEqual({ review_mode: 1 });
  });
});

// ---------------------------------------------------------------------------

test.describe('v0.7.0 — TaskBoard verdict badges', () => {
  test('renders ✓ / ✗ / ? badges and the reviewer-reason line', async ({ page }) => {
    await mockBaseline(page, { tasks: TASKS_WITH_VERDICTS });
    await page.goto('/');

    // VERIFIED badge (green ✓).
    const verified = page.locator('[data-review-verdict="VERIFIED"]');
    await expect(verified).toBeVisible();
    await expect(verified).toContainText('✓');

    // NOT_VERIFIED badge (red ✗) + reviewer-reason line on the escalated card.
    const notVerified = page.locator('[data-review-verdict="NOT_VERIFIED"]');
    await expect(notVerified).toBeVisible();
    await expect(notVerified).toContainText('✗');
    await expect(
      page.locator('[data-review-feedback]', { hasText: 'the file /tmp/x.txt was never written' }),
    ).toBeVisible();

    // MANUAL_VERIFY_REQUIRED badge (amber ?).
    const manual = page.locator('[data-review-verdict="MANUAL_VERIFY_REQUIRED"]');
    await expect(manual).toBeVisible();
    await expect(manual).toContainText('?');
  });

  test('no verdict badge on a task that has not been reviewed', async ({ page }) => {
    await mockBaseline(page, {
      tasks: { items: [taskRow({ id: 20, title: 'plain task', status: 'done' })], count: 1 },
    });
    await page.goto('/');
    await expect(page.locator('[data-review-verdict]')).toHaveCount(0);
  });
});
