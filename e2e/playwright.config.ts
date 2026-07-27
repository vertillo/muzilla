import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: false,
  // Each test manages its own scratch library/DB and starts the app
  // fresh (see tests/fixtures.ts) — parallel workers would need
  // separate ports and separate scratch dirs to not collide, and this
  // suite is small enough that serial execution costs little.
  workers: 1,
  reporter: [['list']],
  use: {
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
