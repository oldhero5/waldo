/**
 * Capture screenshots of every Waldo UI page for the docs site.
 *
 * Usage (host):       npm run screenshots
 * Usage (Docker):     docker compose -f ../docker-compose.docs.yml run --rm docs-screenshots
 * Required env:       WALDO_BASE_URL (default http://localhost:8000)
 *                     WALDO_USER, WALDO_PASSWORD (for login)
 *
 * Output goes to docs-site/static/img/screenshots/<page>.png and is referenced
 * from MDX docs via ![alt](/img/screenshots/<page>.png).
 *
 * The runner is intentionally tolerant: a page that fails to load logs and
 * skips, so a partial Waldo deployment doesn't break the docs build.
 */
import { test, expect, Page } from "@playwright/test";
import * as path from "path";

const PAGES: Array<{ name: string; route: string; waitFor?: string }> = [
  { name: "dashboard", route: "/dashboard" },
  { name: "datasets", route: "/datasets" },
  { name: "label", route: "/label" },
  { name: "review", route: "/review" },
  { name: "train", route: "/train" },
  { name: "experiments", route: "/experiments" },
  { name: "deploy", route: "/deploy" },
  { name: "agent", route: "/agent" },
  { name: "workflows", route: "/workflows" },
  { name: "settings", route: "/settings" },
];

const OUT_DIR = path.resolve(__dirname, "..", "static", "img", "screenshots");

async function login(page: Page): Promise<void> {
  const user = process.env.WALDO_USER;
  const pass = process.env.WALDO_PASSWORD;
  if (!user || !pass) {
    console.warn("WALDO_USER / WALDO_PASSWORD not set — skipping login");
    return;
  }
  await page.goto("/login");
  await page.fill('input[type="email"]', user);
  await page.fill('input[type="password"]', pass);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10_000 });
}

test.describe("Waldo screenshot capture", () => {
  test.beforeAll(async () => {
    const fs = await import("fs/promises");
    await fs.mkdir(OUT_DIR, { recursive: true });
  });

  for (const p of PAGES) {
    test(`capture ${p.name}`, async ({ page }) => {
      try {
        await login(page);
        await page.goto(p.route, { waitUntil: "networkidle" });
        if (p.waitFor) await page.waitForSelector(p.waitFor, { timeout: 10_000 });
        await page.waitForTimeout(800); // let animations settle
        await page.screenshot({
          path: path.join(OUT_DIR, `${p.name}.png`),
          fullPage: true,
        });
        expect(true).toBe(true);
      } catch (err) {
        console.warn(`screenshot ${p.name} failed:`, err);
        test.skip();
      }
    });
  }
});
