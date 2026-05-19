import { expect, test } from '@playwright/test';

test.describe('Command Centre shell', () => {
  test('Command page loads and shows KPI row', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Command Centre')).toBeVisible();
    // The four KPI kickers render.
    for (const k of ['Sessions · today', 'Tokens · today', 'Cost · today', 'Errors · today']) {
      await expect(page.getByText(k)).toBeVisible();
    }
  });

  test('navigates to Activity and Skills pages', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: /activity/i }).click();
    await expect(page).toHaveURL(/\/activity$/);
    // Scope to the section's toggle button — both `All sessions` and
    // `MCP servers` appear inside CollapsibleSections, and `All sessions`
    // also renders as a CardTitle on /activity (causing strict-mode
    // collisions). The button is the canonical, single-element handle.
    await expect(page.getByRole('button', { name: /All sessions/i })).toBeVisible();
    await page.getByRole('link', { name: /skills/i }).click();
    await expect(page).toHaveURL(/\/skills$/);
    await expect(page.getByRole('button', { name: /MCP servers/i })).toBeVisible();
  });

  test('Command palette opens with ⌘K', async ({ page }) => {
    await page.goto('/');
    const isMac = process.platform === 'darwin';
    await page.keyboard.press(isMac ? 'Meta+K' : 'Control+K');
    const input = page.getByPlaceholder(/Jump to, run action/i);
    await expect(input).toBeVisible();
    await input.fill('activity');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/activity$/);
  });

  test('CollapsibleSection toggle persists', async ({ page }) => {
    await page.goto('/');
    const trigger = page.locator('[aria-expanded]').first();
    await trigger.click();
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await page.reload();
    await expect(page.locator('[aria-expanded]').first()).toHaveAttribute('aria-expanded', 'false');
  });

  test('Emergency stop confirm sheet opens + closes', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /emergency stop/i }).click();
    await expect(page.getByText(/Confirm emergency stop/i)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByText(/Confirm emergency stop/i)).toBeHidden();
  });
});
