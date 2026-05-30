// v0.7.1 — accept review output as-is.
//
// TaskBoard surface (per the amendment's smoke step 6 + stop condition 10):
//   1. The review-escalated card shows Accept output / approve / cancel and
//      renders the preserved output_summary the operator is judging.
//   2. Clicking "Accept output" POSTs /api/tasks/{id}/accept and, after the
//      refetch, the card shows a distinct "done · accepted over review" badge.
//   3. That badge is visually separate from a clean "✓ VERIFIED" completion.
//
// Fixtures are pinned via page.route so the spec is hermetic — no real
// dispatcher or DB required. Mirrors v0.7.0.spec.ts's shape.
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
  skills_with_review: 1, tasks_awaiting_review_approval: 1,
};

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
    review_feedback: null, review_overridden: 0,
    ...over,
  };
}

// An escalated review card: awaiting_approval, NOT_VERIFIED, with the output
// preserved by the dispatcher (the v0.7.1 gotcha fix).
const ESCALATED = taskRow({
  id: 12, title: 'rejected task', status: 'awaiting_approval',
  review_verdict: 'NOT_VERIFIED', review_count: 1,
  review_feedback: 'the file /tmp/x.txt was never written',
  output_summary: 'wrote /tmp/x.txt and ran the suite — 12 passed, 0 failed',
  cost_usd: null,
});
// What /accept returns + what the list shows after acceptance.
const ACCEPTED = taskRow({
  ...ESCALATED, status: 'done', review_overridden: 1,
});

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
  await page.route('**/api/skills',              json(overrides.skills ?? { items: [], count: 0 }));
}

// ---------------------------------------------------------------------------

test.describe('v0.7.1 — TaskBoard accept-output', () => {
  test('escalated card → Accept output → POST /accept → done·accepted badge', async ({ page }) => {
    await mockBaseline(page);

    // Dynamic task list: escalated until /accept is called, then accepted.
    let accepted = false;
    let acceptCalls = 0;
    await page.route('**/api/tasks**', async (route: Route) => {
      // Let the accept POST fall through to its own (later-registered) route.
      if (route.request().url().includes('/accept')) return route.fallback();
      const body = accepted ? { items: [ACCEPTED], count: 1 }
                            : { items: [ESCALATED], count: 1 };
      await route.fulfill({ status: 200, contentType: 'application/json',
                            body: JSON.stringify(body) });
    });
    // The accept endpoint — registered last so it wins for the /accept path.
    await page.route('**/api/tasks/*/accept**', async (route: Route) => {
      acceptCalls++;
      accepted = true;
      await route.fulfill({ status: 200, contentType: 'application/json',
                            body: JSON.stringify(ACCEPTED) });
    });

    await page.goto('/');

    // The escalated card offers all three resolutions + the preserved output.
    const acceptBtn = page.locator('[data-action="accept-task"]');
    await expect(acceptBtn).toBeVisible();
    await expect(acceptBtn).toContainText('accept output');
    await expect(page.locator('[data-action="cancel-task"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'approve' })).toBeVisible();
    await expect(page.locator('[data-output-summary]'))
      .toContainText('wrote /tmp/x.txt and ran the suite');

    // Before acceptance there is no override badge.
    await expect(page.locator('[data-accepted-over-review]')).toHaveCount(0);

    // Accept → POST /accept fires, then the refetch flips the card.
    await acceptBtn.click();
    await expect.poll(() => acceptCalls).toBeGreaterThan(0);

    const badge = page.locator('[data-accepted-over-review]');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText('accepted over review');
    // The accept controls are gone once it's done.
    await expect(page.locator('[data-action="accept-task"]')).toHaveCount(0);
  });

  test('done·accepted-over-review badge is distinct from a clean ✓ VERIFIED', async ({ page }) => {
    await mockBaseline(page, {
      tasks: {
        items: [
          taskRow({ id: 11, title: 'verified task', status: 'done',
                    review_verdict: 'VERIFIED', review_overridden: 0 }),
          taskRow({ id: 12, title: 'accepted task', status: 'done',
                    review_verdict: 'NOT_VERIFIED', review_overridden: 1,
                    review_feedback: 'reviewer was wrong',
                    output_summary: 'the work is fine' }),
        ],
        count: 2,
      },
    });
    await page.goto('/');

    // The override badge renders, exactly once, and reads as an override.
    const overrideBadge = page.locator('[data-accepted-over-review]');
    await expect(overrideBadge).toHaveCount(1);
    await expect(overrideBadge).toContainText('accepted over review');

    // The clean VERIFIED completion is a separate, distinct badge — the
    // override is never laundered into a green ✓ VERIFIED.
    const verified = page.locator('[data-review-verdict="VERIFIED"]');
    await expect(verified).toHaveCount(1);
    await expect(verified).toContainText('✓');
    await expect(verified).not.toContainText('accepted over review');
  });
});
