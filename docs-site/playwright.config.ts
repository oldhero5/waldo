import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./scripts",
  fullyParallel: false,
  reporter: "list",
  timeout: 60_000,
  use: {
    baseURL: process.env.WALDO_BASE_URL ?? "http://localhost:8000",
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
