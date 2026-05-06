import { defineConfig, devices } from '@playwright/test';

// Assumes FastAPI is already running on 8765 serving ui/dist.
// Start it with `python scripts/server.py` from the install dir.
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 20_000,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.CC_BASE_URL || 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
