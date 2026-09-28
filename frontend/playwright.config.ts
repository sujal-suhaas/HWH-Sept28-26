import { defineConfig, devices } from '@playwright/test'

/**
 * The happy path runs against the deterministic harness in
 * `scripts/serve_e2e.py`, not the live Hindsight/Groq stack: real routes, real
 * contract, real trace log, but a fake model and a fake memory provider so CI
 * needs no API keys and the run is reproducible.
 *
 * The live path is exercised by the demo; see docs/demo/script.md.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'uv run python scripts/serve_e2e.py --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
    },
    {
      // Pinned to 127.0.0.1: Vite otherwise binds localhost over IPv6 only, and
      // the IPv4 health check never connects. The API base is pinned for the
      // same reason — `localhost` can resolve to ::1 and miss the backend.
      command:
        'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      env: { VITE_API_BASE: 'http://127.0.0.1:8000' },
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
    },
  ],
})
