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
  { name: "dashboard", route: "/" },
  { name: "upload", route: "/upload" },
  { name: "collections", route: "/collections" },
  { name: "datasets", route: "/datasets" },
  { name: "jobs", route: "/jobs" },
  { name: "experiments", route: "/experiments" },
  { name: "workflows", route: "/workflows" },
  { name: "workflows-editor", route: "/workflows/new" },
  { name: "deploy", route: "/deploy" },
  { name: "deploy-models", route: "/deploy/models" },
  { name: "deploy-endpoints", route: "/deploy/endpoints" },
  { name: "deploy-test", route: "/deploy/test" },
  { name: "deploy-monitor", route: "/deploy/monitor" },
  { name: "deploy-edge", route: "/deploy/edge" },
  { name: "agent", route: "/agent" },
  { name: "settings", route: "/settings" },
  { name: "login", route: "/login", waitFor: 'input[type="email"]' },
  { name: "review", route: "/review/3227d592-5401-4064-b294-49542a6a1a15" },
  { name: "train", route: "/train/3227d592-5401-4064-b294-49542a6a1a15" },
  { name: "label", route: "/label/collection/c711d261-e7ef-4f5b-a2d4-7e4267ca3551" },
];

const OUT_DIR = path.resolve(__dirname, "..", "static", "img", "screenshots");

async function login(page: Page): Promise<void> {
  // Inject a pre-minted admin JWT into localStorage so the SPA boots authenticated.
  // Cleaner than the email/password flow (and works around the bcrypt/passlib init bug).
  const token = process.env.WALDO_TOKEN;
  if (!token) {
    console.warn("WALDO_TOKEN not set — skipping login");
    return;
  }
  await page.goto("/login");
  await page.evaluate((t) => {
    localStorage.setItem("waldo_token", t);
  }, token);
}

test.describe("Waldo screenshot capture", () => {
  test.beforeAll(async () => {
    const fs = await import("fs/promises");
    await fs.mkdir(OUT_DIR, { recursive: true });
  });

  for (const p of PAGES) {
    test(`capture ${p.name}`, async ({ page }) => {
      try {
        if (p.name !== "login") await login(page);
        await page.goto(p.route, { waitUntil: "networkidle" });
        if (p.waitFor) await page.waitForSelector(p.waitFor, { timeout: 10_000 });
        await page.waitForTimeout(1500); // let animations + lazy data settle
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
