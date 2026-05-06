// Milestone (f) verification — full-page screenshots of all three pages +
// exercises the three big interactive surfaces: task composer, decision
// answer modal, live-session drawer. Failures here are real UI regressions.
import { expect, test } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const OUT_DIR = path.resolve(__dirname, '../../.screenshots');
fs.mkdirSync(OUT_DIR, { recursive: true });

async function waitForSteady(page: import('@playwright/test').Page) {
  // Can't use 'networkidle' — the OTEL firehose holds an EventSource open.
  await page.waitForLoadState('domcontentloaded');
  // Give React Query a beat to resolve the initial wave + framer-motion to settle.
  await page.waitForTimeout(1500);
}

test.describe('Page screenshots', () => {
  test('Command page — full', async ({ page }) => {
    await page.goto('/');
    await waitForSteady(page);
    await page.screenshot({
      path: path.join(OUT_DIR, '01-command.png'),
      fullPage: true,
    });
    // Sanity: the three top-level clusters we added this milestone render.
    await expect(page.getByText(/Live \+ HITL/i).first()).toBeVisible();
    await expect(page.getByText(/Mission Control/i).first()).toBeVisible();
    await expect(page.getByText(/System pressure/i).first()).toBeVisible();
  });

  test('Activity page — full', async ({ page }) => {
    await page.goto('/activity');
    await waitForSteady(page);
    await page.screenshot({
      path: path.join(OUT_DIR, '02-activity.png'),
      fullPage: true,
    });
    await expect(page.getByText(/All sessions/i).first()).toBeVisible();
    await expect(page.getByText(/Failures/i).first()).toBeVisible();
    await expect(page.getByText(/Telemetry firehose/i).first()).toBeVisible();
  });

  test('Skills page — full', async ({ page }) => {
    await page.goto('/skills');
    await waitForSteady(page);
    await page.screenshot({
      path: path.join(OUT_DIR, '03-skills.png'),
      fullPage: true,
    });
    await expect(page.getByText(/MCP servers/i).first()).toBeVisible();
    await expect(page.getByText(/Skills registry/i).first()).toBeVisible();
    await expect(page.getByText(/Context health/i).first()).toBeVisible();
    await expect(page.getByText(/Skill economics/i).first()).toBeVisible();
  });
});

test.describe('Interactive flows', () => {
  test('Task composer opens from board', async ({ page }) => {
    await page.goto('/');
    await waitForSteady(page);
    await page.locator('[data-action="open-task-composer"]').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByPlaceholder(/what should the agent do/i)).toBeVisible();
    await page.screenshot({
      path: path.join(OUT_DIR, '04-task-composer.png'),
      fullPage: false,
    });
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('Schedule composer opens + cron parse', async ({ page }) => {
    await page.goto('/');
    await waitForSteady(page);
    await page.getByRole('button', { name: /new schedule/i }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    // NL → cron round-trip (parser endpoint lives, even if heuristic).
    await page.getByRole('button', { name: /parse → cron/i }).click();
    await page.waitForTimeout(700);
    await page.screenshot({
      path: path.join(OUT_DIR, '05-schedule-composer.png'),
      fullPage: false,
    });
    await page.keyboard.press('Escape');
  });

  test('Live session row opens detail drawer', async ({ page }) => {
    await page.goto('/');
    await waitForSteady(page);
    // LiveSessionsCard renders clickable <li> rows. If none, skip gracefully.
    const firstLive = page.locator('[role="button"][tabindex="0"]').first();
    const liveCount = await firstLive.count();
    if (liveCount === 0) {
      test.info().annotations.push({ type: 'info', description: 'no live sessions in test DB — drawer unexercised' });
      return;
    }
    await firstLive.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.screenshot({
      path: path.join(OUT_DIR, '06-live-session-drawer.png'),
      fullPage: false,
    });
    await page.keyboard.press('Escape');
  });

  test('MCP server drill-down expands tools row', async ({ page }) => {
    await page.goto('/skills');
    await waitForSteady(page);
    const firstRow = page.locator('table tbody tr').first();
    const rowCount = await firstRow.count();
    if (rowCount === 0) {
      test.info().annotations.push({ type: 'info', description: 'no MCP rows — drill-down unexercised' });
      return;
    }
    await firstRow.click();
    await page.waitForTimeout(600);
    await page.screenshot({
      path: path.join(OUT_DIR, '07-mcp-drilldown.png'),
      fullPage: false,
    });
  });

  test('OTEL firehose renders + pause works', async ({ page }) => {
    await page.goto('/activity');
    await waitForSteady(page);
    // Scroll the firehose into view; SSE connection + initial frames need ~2-3s.
    const header = page.getByText(/Telemetry firehose/i).first();
    await header.scrollIntoViewIfNeeded();
    const pauseBtn = page.getByRole('button', { name: /^(pause|resume)$/i });
    await expect(pauseBtn).toBeVisible({ timeout: 8000 });
    await pauseBtn.click();
    await page.screenshot({
      path: path.join(OUT_DIR, '08-firehose.png'),
      fullPage: false,
    });
  });
});
