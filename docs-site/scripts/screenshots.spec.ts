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

type PageSpec = {
  name: string;
  route: string;
  waitFor?: string;
  postLoad?: (page: Page) => Promise<void>;
};

// Wait until the review page has actually painted bounding-box overlays into
// at least one canvas. The review canvases are absolutely positioned over each
// frame thumbnail; we poll until one has non-transparent pixels.
async function waitForBoxesPainted(page: Page) {
  await page.waitForFunction(
    () => {
      const canvases = document.querySelectorAll("canvas");
      for (const c of Array.from(canvases) as HTMLCanvasElement[]) {
        if (c.width === 0 || c.height === 0) continue;
        const ctx = c.getContext("2d");
        if (!ctx) continue;
        try {
          const data = ctx.getImageData(0, 0, c.width, c.height).data;
          for (let i = 3; i < data.length; i += 4 * 64) {
            if (data[i] > 0) return true;
          }
        } catch {
          return true; // tainted canvas — treat as painted
        }
      }
      return false;
    },
    null,
    { timeout: 15_000, polling: 250 }
  );
}

const PAGES: Array<PageSpec> = [
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
  {
    name: "review",
    route: "/review/3227d592-5401-4064-b294-49542a6a1a15",
    // The list-view canvases race against <img loading="lazy"> and often paint
    // at h=0 (known UI bug). Click into the first frame to open the inspect
    // modal, which mounts a fresh AnnotationCanvas with proper sizing.
    postLoad: async (page) => {
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(1500);
      const thumb = page.locator('img[src*="/download/frames/"]').first();
      await thumb.waitFor({ state: "visible", timeout: 15_000 });
      // Click the parent div (which has the onClick), not the image itself.
      await thumb.locator("..").click({ force: true });
      await page.waitForTimeout(2500);
      try {
        await waitForBoxesPainted(page);
      } catch {}
      await page.waitForTimeout(800);
    },
  },
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
      test.setTimeout(120_000);
      try {
        if (p.name !== "login") await login(page);
        await page.goto(p.route, { waitUntil: "networkidle" });
        if (p.waitFor) await page.waitForSelector(p.waitFor, { timeout: 10_000 });
        if (p.postLoad) {
          await p.postLoad(page);
        } else {
          await page.waitForTimeout(1500);
        }
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
