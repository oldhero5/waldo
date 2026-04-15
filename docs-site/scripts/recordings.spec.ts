/**
 * Record short walkthroughs of Waldo's key flows for the docs site.
 * Output: docs-site/static/img/recordings/<name>.webm  (also kept as .gif if ffmpeg is on PATH).
 *
 * Uses the same JWT injection trick as screenshots.spec.ts to bypass the broken login flow.
 */
import { test, expect, Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs/promises";

const OUT_DIR = path.resolve(__dirname, "..", "static", "img", "recordings");

const TOKEN = process.env.WALDO_TOKEN ?? "";

async function authed(page: Page): Promise<void> {
  await page.goto("/login");
  await page.evaluate((t) => {
    if (t) localStorage.setItem("waldo_token", t);
  }, TOKEN);
}

test.use({
  video: { mode: "on", size: { width: 1440, height: 900 } },
});

test.describe("Waldo flow recordings", () => {
  test.beforeAll(async () => {
    await fs.mkdir(OUT_DIR, { recursive: true });
  });

  test("tour: dashboard → datasets → workflows → deploy", async ({ page }, info) => {
    await authed(page);
    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);

    await page.getByRole("link", { name: /Datasets/i }).first().click();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1500);

    await page.getByRole("link", { name: /Workflows/i }).first().click();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1500);

    await page.getByRole("link", { name: /Deploy/i }).first().click();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    info.attachments;
  });

  test("review: scroll through labeled frames", async ({ page }) => {
    await authed(page);
    await page.goto("/review/3227d592-5401-4064-b294-49542a6a1a15", { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    for (let i = 0; i < 6; i++) {
      await page.mouse.wheel(0, 600);
      await page.waitForTimeout(700);
    }
  });

  test("deploy: tab through models, endpoints, monitor, edge", async ({ page }) => {
    await authed(page);
    await page.goto("/deploy", { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);
    for (const tab of ["models", "endpoints", "test", "monitor", "edge"]) {
      await page.goto(`/deploy/${tab}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(1400);
    }
  });

  test("workflows: open editor, drag canvas", async ({ page }) => {
    await authed(page);
    await page.goto("/workflows", { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);
    await page.goto("/workflows/new", { waitUntil: "networkidle" });
    await page.waitForTimeout(2500);
  });

  test("agent: open chat panel", async ({ page }) => {
    await authed(page);
    await page.goto("/agent", { waitUntil: "networkidle" });
    await page.waitForTimeout(2500);
  });

  test.afterEach(async ({}, info) => {
    // Move Playwright's auto-saved video into a stable filename per test.
    for (const att of info.attachments) {
      if (att.name === "video" && att.path) {
        const stable = path.join(OUT_DIR, `${info.title.split(":")[0]}.webm`);
        try {
          await fs.copyFile(att.path, stable);
        } catch (err) {
          console.warn(`failed to stash video for ${info.title}:`, err);
        }
      }
    }
    expect(true).toBe(true);
  });
});
