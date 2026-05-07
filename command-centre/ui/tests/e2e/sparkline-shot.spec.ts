import { test } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, '../../.screenshots');
fs.mkdirSync(OUT, { recursive: true });

test('KPI row close-up', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(2500);
  const kicker = page.getByText('Sessions · today').first();
  await kicker.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const row = kicker.locator('xpath=ancestor::*[contains(@class,"grid")][1]');
  await row.screenshot({ path: path.join(OUT, '09-kpi-sparklines.png') });
});

test('Heatmap close-up', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(2500);
  // Find the heatmap header by text and screenshot its parent section.
  const heatmap = page.getByText(/Sessions by weekday/i).first();
  await heatmap.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  const card = heatmap.locator('xpath=ancestor::*[contains(@class,"bg-surface")][1]');
  await card.screenshot({ path: path.join(OUT, '10-heatmap.png') });
});

test('Top skills close-up', async ({ page }) => {
  await page.goto('/skills');
  await page.waitForTimeout(2500);
  const top = page.getByText(/Invocations · tokens · cost per skill/i).first();
  await top.scrollIntoViewIfNeeded();
  const card = top.locator('xpath=ancestor::*[contains(@class,"bg-surface")][1]');
  await card.screenshot({ path: path.join(OUT, '11-top-skills.png') });
});
