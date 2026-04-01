/**
 * Screenshot walkthrough: uploads Target.mp4, labels "person", captures every screen.
 * Run: npx playwright test e2e/screenshots.spec.ts
 */
import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const TARGET_VIDEO = path.resolve(__dirname, "../../Target.mp4");
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots");

test.describe("Screenshot walkthrough", () => {
  test("full pipeline with Target.mp4", async ({ page, request }) => {
    test.setTimeout(300_000);

    // ── 1. Upload page ──────────────────────────────────────
    await page.goto("/upload");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/01-upload-page.png`, fullPage: true });

    // Upload via API (reliable for large file)
    const uploadRes = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "Target.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TARGET_VIDEO),
        },
      },
    });
    expect(uploadRes.status()).toBe(201);
    const { video_id } = await uploadRes.json();

    // ── 2. Label page ───────────────────────────────────────
    await page.goto(`/label/${video_id}`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/02-label-page.png`, fullPage: true });

    // Fill in search and submit
    const searchInput = page.locator('input[placeholder*="Describe"]');
    await searchInput.fill("person");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/03-label-search-filled.png`, fullPage: true });

    await page.getByRole("button", { name: "Search", exact: true }).click();

    // Wait for the job to start showing progress
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/04-label-in-progress.png`, fullPage: true });

    // Poll until completed
    await expect(page.locator("text=Review Results")).toBeVisible({ timeout: 180_000 });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/05-label-completed.png`, fullPage: true });

    // Get the job_id from the page URL or API
    const jobsRes = await request.get(`/api/v1/status?video_id=${video_id}`);
    const jobs = await jobsRes.json();
    const completedJob = jobs.find((j: any) => j.status === "completed");
    const job_id = completedJob.job_id;

    // ── 3. Review page ──────────────────────────────────────
    await page.click("text=Review Results");
    await page.waitForURL(/\/review\//);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1500); // let images load
    await page.screenshot({ path: `${SCREENSHOT_DIR}/06-review-page.png`, fullPage: true });

    // ── 4. Jobs page ────────────────────────────────────────
    await page.goto("/jobs");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/07-jobs-page.png`, fullPage: true });

    // ── 5. Click mode view ──────────────────────────────────
    await page.goto(`/label/${video_id}`);
    await page.waitForLoadState("networkidle");
    await page.click("text=Click Mode");
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/08-click-mode.png`, fullPage: true });

    // ── 6. Train page ───────────────────────────────────────
    await page.goto(`/train/${job_id}`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/09-train-page.png`, fullPage: true });

    // ── 7. Different task types ──────────────────────────────
    await page.goto(`/label/${video_id}`);
    await page.waitForLoadState("networkidle");
    // Select Detection task type
    const taskSelect = page.locator("select").first();
    await taskSelect.selectOption("detect");
    await searchInput.fill("car");
    await page.screenshot({ path: `${SCREENSHOT_DIR}/10-detection-mode.png`, fullPage: true });
  });
});
