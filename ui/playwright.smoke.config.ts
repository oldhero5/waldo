import { defineConfig } from "@playwright/test";

// Deterministic client smoke tests: no database, JWT secret, or inference service.
export default defineConfig({
  testDir: "./e2e",
  testMatch: ["ui-cleanup.spec.ts", "job-coverage.spec.ts", "video-overlay.spec.ts", "media-renewal.spec.ts"],
  timeout: 30_000,
  reporter: process.env.CI ? [
    ["dot"], ["github"],
    ["junit", { outputFile: "test-results/junit.xml" }],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173", headless: true, reducedMotion: "reduce",
    screenshot: "only-on-failure", trace: "retain-on-failure",
    launchOptions: process.env.WALDO_CHROMIUM_PATH ? { executablePath: process.env.WALDO_CHROMIUM_PATH } : {},
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
});
