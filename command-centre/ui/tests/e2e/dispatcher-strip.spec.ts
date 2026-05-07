import { test } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, '../../.screenshots');
fs.mkdirSync(OUT, { recursive: true });

test('DispatcherStrip close-up', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(2500);
  // The strip lives in the Mission Control section, just above the TaskBoard.
  const strip = page.locator('text=/slots\\s+\\d+/').first();
  await strip.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const container = strip.locator('xpath=ancestor::*[contains(@class,"flex") and contains(@class,"items-center")][1]');
  await container.screenshot({ path: path.join(OUT, '12-dispatcher-strip.png') });
});
