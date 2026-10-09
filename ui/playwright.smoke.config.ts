import { defineConfig } from "@playwright/test";

// Deterministic client smoke tests: no database, JWT secret, or inference service.
export default defineConfig({
  testDir: "./e2e",
  testMatch: ["ui-cleanup.spec.ts", "job-coverage.spec.ts", "video-overlay.spec.ts"],
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:4173", headless: true, reducedMotion: "reduce",
    launchOptions: process.env.WALDO_CHROMIUM_PATH ? { executablePath: process.env.WALDO_CHROMIUM_PATH } : {},
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
});
