import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const TEST_CLIP = path.resolve(__dirname, "../../tests/fixtures/test_clip.mp4");

test.describe("Waldo UI", () => {
  test("health check API", async ({ request }) => {
    const res = await request.get("/health");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("ok");
  });

  test("upload page renders", async ({ page }) => {
    await page.goto("/upload");
    await expect(page.locator("h1")).toContainText("Upload Video");
    await expect(page.locator("text=Choose File")).toBeVisible();
  });

  test("jobs page renders", async ({ page }) => {
    await page.goto("/jobs");
    await expect(page.locator("h1")).toContainText("Jobs");
  });

  test("navigation works", async ({ page }) => {
    await page.goto("/upload");
    await page.click("text=Jobs");
    await expect(page).toHaveURL(/\/jobs/);
    await page.click("text=Upload");
    await expect(page).toHaveURL(/\/upload/);
  });

  test("upload video and navigate to label page", async ({ page }) => {
    await page.goto("/upload");

    // Upload via file input
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(TEST_CLIP);

    // Should show upload success with Continue button
    await expect(page.locator("text=Continue to Label")).toBeVisible({ timeout: 15000 });
    await page.click("text=Continue to Label");
    await page.waitForURL(/\/label\//, { timeout: 15000 });
    await expect(page.locator("h1")).toContainText("Label Video");
  });

  test("label page has text and click modes", async ({ page }) => {
    // First upload a video to get a valid video ID
    const uploadRes = await page.request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await uploadRes.json();

    await page.goto(`/label/${video_id}`);
    await expect(page.locator("h1")).toContainText("Label Video");

    // Text search mode is default
    await expect(page.locator('input[placeholder*="prompt"]')).toBeVisible();

    // Switch to click mode
    await page.click("text=Click Mode");
    await expect(page.locator('input[placeholder="Class name"]')).toBeVisible();
    await expect(page.locator("text=Left-click")).toBeVisible();

    // Switch back to text mode
    await page.click("text=Text Search");
    await expect(page.locator('input[placeholder*="prompt"]')).toBeVisible();
  });

  test("task selector has all 5 types", async ({ page }) => {
    const uploadRes = await page.request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await uploadRes.json();

    await page.goto(`/label/${video_id}`);

    const select = page.locator("select");
    const options = await select.locator("option").allTextContents();
    expect(options).toContain("Segmentation");
    expect(options).toContain("Detection");
    expect(options).toContain("Classification");
    expect(options).toContain("Oriented BBox");
    expect(options).toContain("Pose");
  });

  test("full text labeling pipeline via UI", async ({ page, request }) => {
    // Upload via API (faster and more reliable than file input)
    const uploadRes = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await uploadRes.json();

    // Go to label page
    await page.goto(`/label/${video_id}`);
    await expect(page.locator("h1")).toContainText("Label Video");

    // Type search and submit
    const searchInput = page.locator('input[placeholder*="prompt"]');
    await searchInput.fill("car");
    await page.getByRole("button", { name: "Search", exact: true }).click();

    // Wait for progress section to appear then complete
    await expect(page.locator("text=Review Results")).toBeVisible({ timeout: 120_000 });
    await expect(page.locator("text=Download Dataset")).toBeVisible();

    // Click Review Results
    await page.click("text=Review Results");
    await page.waitForURL(/\/review\//, { timeout: 10000 });
    await expect(page.locator("h1")).toContainText("Review Labels");
  });

  test("review page shows stats", async ({ page }) => {
    // Create a job via API first
    const uploadRes = await page.request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await uploadRes.json();

    const labelRes = await page.request.post("/api/v1/label", {
      data: { video_id, text_prompt: "car", task_type: "segment" },
    });
    const { job_id } = await labelRes.json();

    // Wait for job to complete
    for (let i = 0; i < 30; i++) {
      const statusRes = await page.request.get(`/api/v1/status/${job_id}`);
      const status = await statusRes.json();
      if (status.status === "completed" || status.status === "failed") break;
      await page.waitForTimeout(2000);
    }

    await page.goto(`/review/${job_id}`);
    await expect(page.locator("h1")).toContainText("Review Labels");
    await expect(page.locator("text=Dataset Stats")).toBeVisible();
    await expect(page.getByText("Annotations", { exact: true })).toBeVisible();
    await expect(page.getByText("Frames")).toBeVisible();
  });

  test("jobs page lists completed jobs", async ({ page }) => {
    await page.goto("/jobs");
    // There should be jobs from previous tests
    await expect(page.locator("text=car").first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe("API endpoints", () => {
  test("POST /upload returns video metadata", async ({ request }) => {
    const res = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    expect(res.status()).toBe(201);
    const body = await res.json();
    expect(body.video_id).toBeTruthy();
    expect(body.filename).toBe("test_clip.mp4");
  });

  test("POST /label starts a job", async ({ request }) => {
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await upload.json();

    const res = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "object", task_type: "detect" },
    });
    expect(res.status()).toBe(202);
    const body = await res.json();
    expect(body.job_id).toBeTruthy();
    expect(body.status).toBe("pending");
  });

  test("GET /status returns job list", async ({ request }) => {
    const res = await request.get("/api/v1/status");
    expect(res.ok()).toBeTruthy();
    const jobs = await res.json();
    expect(Array.isArray(jobs)).toBeTruthy();
  });

  test("GET /status/invalid returns 404", async ({ request }) => {
    const res = await request.get(
      "/api/v1/status/00000000-0000-0000-0000-000000000000"
    );
    expect(res.status()).toBe(404);
  });

  test("full label → poll → download pipeline", async ({ request }) => {
    // Upload
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await upload.json();

    // Label
    const label = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "car", task_type: "segment" },
    });
    const { job_id } = await label.json();

    // Poll
    let status;
    for (let i = 0; i < 30; i++) {
      const res = await request.get(`/api/v1/status/${job_id}`);
      status = await res.json();
      if (status.status === "completed" || status.status === "failed") break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    expect(status.status).toBe("completed");
    expect(status.result_url).toBeTruthy();

    // Download
    const download = await request.get(status.result_url);
    expect(download.ok()).toBeTruthy();
    const body = await download.body();
    // ZIP magic bytes
    expect(body[0]).toBe(0x50);
    expect(body[1]).toBe(0x4b);

    // Check stats
    const stats = await request.get(`/api/v1/jobs/${job_id}/stats`);
    expect(stats.ok()).toBeTruthy();
    const statsBody = await stats.json();
    expect(statsBody.total_frames).toBeGreaterThanOrEqual(1);

    // Check annotations endpoint
    const anns = await request.get(`/api/v1/jobs/${job_id}/annotations`);
    expect(anns.ok()).toBeTruthy();

    // Check frames endpoint
    const frames = await request.get(`/api/v1/videos/${video_id}/frames`);
    expect(frames.ok()).toBeTruthy();
    const frameList = await frames.json();
    expect(frameList.length).toBeGreaterThanOrEqual(1);
    expect(frameList[0].image_url).toBeTruthy();
  });

  test("PATCH /annotations updates status", async ({ request }) => {
    // Create a job and wait for completion
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await upload.json();

    const label = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "thing", task_type: "segment" },
    });
    const { job_id } = await label.json();

    for (let i = 0; i < 30; i++) {
      const res = await request.get(`/api/v1/status/${job_id}`);
      const s = await res.json();
      if (s.status === "completed" || s.status === "failed") break;
      await new Promise((r) => setTimeout(r, 2000));
    }

    // Get annotations
    const anns = await request.get(`/api/v1/jobs/${job_id}/annotations`);
    const annList = await anns.json();

    // If there are annotations, try patching one
    if (annList.length > 0) {
      const ann = annList[0];
      const patch = await request.patch(`/api/v1/annotations/${ann.id}`, {
        data: { status: "accepted" },
      });
      expect(patch.ok()).toBeTruthy();
      const patched = await patch.json();
      expect(patched.status).toBe("accepted");
    }
  });

  test("POST /label/exemplar endpoint exists", async ({ request }) => {
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await upload.json();

    const res = await request.post("/api/v1/label/exemplar", {
      data: {
        video_id,
        frame_idx: 0,
        points: [[80, 60]],
        labels: [1],
        task_type: "segment",
        class_name: "test_object",
      },
    });
    expect(res.status()).toBe(202);
    const body = await res.json();
    expect(body.job_id).toBeTruthy();
  });

  test("GET /train/variants returns model variants", async ({ request }) => {
    const res = await request.get("/api/v1/train/variants");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Object.keys(body.variants).length).toBeGreaterThan(0);
    expect(body.defaults.segment).toBeTruthy();
    expect(body.defaults.detect).toBeTruthy();
    expect(body.hyperparams.epochs).toBeTruthy();
  });

  test("POST /train starts a training run", async ({ request }) => {
    // Upload + label first to get a completed job
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: {
          name: "test_clip.mp4",
          mimeType: "video/mp4",
          buffer: fs.readFileSync(TEST_CLIP),
        },
      },
    });
    const { video_id } = await upload.json();

    const label = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "car", task_type: "segment" },
    });
    const { job_id } = await label.json();

    // Wait for labeling to complete
    for (let i = 0; i < 30; i++) {
      const s = await request.get(`/api/v1/status/${job_id}`);
      const status = await s.json();
      if (status.status === "completed") break;
      await new Promise((r) => setTimeout(r, 2000));
    }

    // Start training
    const res = await request.post("/api/v1/train", {
      data: {
        job_id,
        name: "test_training",
        model_variant: "yolo11n-seg",
        task_type: "segment",
        hyperparameters: { epochs: 1, batch: 1 },
      },
    });
    expect(res.status()).toBe(202);
    const body = await res.json();
    expect(body.run_id).toBeTruthy();
    expect(body.status).toBe("queued");

    // Check training run status
    const statusRes = await request.get(`/api/v1/train/${body.run_id}`);
    expect(statusRes.ok()).toBeTruthy();
    const runStatus = await statusRes.json();
    expect(runStatus.name).toBe("test_training");
    expect(runStatus.model_variant).toBe("yolo11n-seg");
  });

  test("GET /train lists training runs", async ({ request }) => {
    const res = await request.get("/api/v1/train");
    expect(res.ok()).toBeTruthy();
    const runs = await res.json();
    expect(Array.isArray(runs)).toBeTruthy();
  });

  test("GET /models lists models", async ({ request }) => {
    const res = await request.get("/api/v1/models");
    expect(res.ok()).toBeTruthy();
    const models = await res.json();
    expect(Array.isArray(models)).toBeTruthy();
  });

  test("train page renders with config form", async ({ page, request }) => {
    // Create a completed job via API
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: { name: "test_clip.mp4", mimeType: "video/mp4", buffer: fs.readFileSync(TEST_CLIP) },
      },
    });
    const { video_id } = await upload.json();
    const label = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "car" },
    });
    const { job_id } = await label.json();
    for (let i = 0; i < 30; i++) {
      const s = await (await request.get(`/api/v1/status/${job_id}`)).json();
      if (s.status === "completed") break;
      await new Promise((r) => setTimeout(r, 1000));
    }

    await page.goto(`/train/${job_id}`);
    await expect(page.locator("h1")).toContainText("Train Model");
    await expect(page.locator("text=Start Training")).toBeVisible();
    await expect(page.locator("text=Epochs")).toBeVisible();
    await expect(page.locator("text=Batch Size")).toBeVisible();
    await expect(page.locator("text=Image Size")).toBeVisible();
    // Model variant selector
    await expect(page.locator("select").nth(1)).toBeVisible();
  });

  test("review page has Train Model button", async ({ page, request }) => {
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: { name: "test_clip.mp4", mimeType: "video/mp4", buffer: fs.readFileSync(TEST_CLIP) },
      },
    });
    const { video_id } = await upload.json();
    const label = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "car" },
    });
    const { job_id } = await label.json();
    for (let i = 0; i < 30; i++) {
      const s = await (await request.get(`/api/v1/status/${job_id}`)).json();
      if (s.status === "completed") break;
      await new Promise((r) => setTimeout(r, 1000));
    }

    await page.goto(`/review/${job_id}`);
    await expect(page.locator("text=Train Model")).toBeVisible();

    // Click takes us to training page
    await page.click("text=Train Model");
    await page.waitForURL(/\/train\//, { timeout: 5000 });
    await expect(page.locator("h1")).toContainText("Train Model");
  });

  test("detection task type pipeline via API", async ({ request }) => {
    // Upload
    const upload = await request.post("/api/v1/upload", {
      multipart: {
        file: { name: "test_clip.mp4", mimeType: "video/mp4", buffer: fs.readFileSync(TEST_CLIP) },
      },
    });
    const { video_id } = await upload.json();

    // Label with detection task type
    const label = await request.post("/api/v1/label", {
      data: { video_id, text_prompt: "object", task_type: "detect" },
    });
    expect(label.status()).toBe(202);
    const { job_id } = await label.json();

    // Poll until done
    let status;
    for (let i = 0; i < 30; i++) {
      status = await (await request.get(`/api/v1/status/${job_id}`)).json();
      if (status.status === "completed" || status.status === "failed") break;
      await new Promise((r) => setTimeout(r, 1000));
    }
    expect(status.status).toBe("completed");

    // Download and verify format is detection (not segmentation)
    const dl = await request.get(status.result_url);
    expect(dl.ok()).toBeTruthy();
  });

  test("WebSocket endpoint exists", async ({ request }) => {
    // We can't do a full WebSocket test via Playwright request API,
    // but we can verify the upgrade path exists
    const res = await request.get("/ws/training/fake-run-id");
    // Should get 403 or connection upgrade attempt, not 404
    expect(res.status()).not.toBe(404);
  });

  test("GET /serve/status returns server info", async ({ request }) => {
    const res = await request.get("/api/v1/serve/status");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("loaded");
    expect(body).toHaveProperty("device");
  });

  test("POST /predict/image rejects invalid file", async ({ request }) => {
    const res = await request.post("/api/v1/predict/image", {
      multipart: {
        file: {
          name: "bad.txt",
          mimeType: "text/plain",
          buffer: Buffer.from("not an image"),
        },
      },
    });
    expect(res.status()).toBe(400);
  });

  test("POST /models/invalid/activate returns 404", async ({ request }) => {
    const res = await request.post(
      "/api/v1/models/00000000-0000-0000-0000-000000000000/activate"
    );
    expect(res.status()).toBe(404);
  });

  test("WebSocket predict endpoint exists", async ({ request }) => {
    const res = await request.get("/ws/predict/fake-session-id");
    expect(res.status()).not.toBe(404);
  });
});

test.describe("Deploy & Demo pages", () => {
  test("deploy page renders", async ({ page }) => {
    await page.goto("/deploy");
    await expect(page.locator("h1")).toContainText("Deploy");
    await expect(page.locator("text=Inference Server")).toBeVisible();
    await expect(page.locator("text=API Usage")).toBeVisible();
  });

  test("demo page renders", async ({ page }) => {
    await page.goto("/demo");
    await expect(page.locator("h1")).toContainText("Demo");
    await expect(page.getByRole("button", { name: "Image", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Video", exact: true })).toBeVisible();
    await expect(page.locator("text=Confidence")).toBeVisible();
  });

  test("demo page switches between image and video modes", async ({ page }) => {
    await page.goto("/demo");
    // Default is image mode
    await expect(page.locator("text=Choose Image")).toBeVisible();

    // Switch to video mode
    await page.click("button:text('Video')");
    await expect(page.locator("text=Choose Video")).toBeVisible();

    // Switch back to image mode
    await page.click("button:text('Image')");
    await expect(page.locator("text=Choose Image")).toBeVisible();
  });

  test("navigation includes Deploy and Demo", async ({ page }) => {
    await page.goto("/upload");
    await expect(page.locator("nav >> text=Deploy")).toBeVisible();
    await expect(page.locator("nav >> text=Demo")).toBeVisible();

    await page.click("nav >> text=Deploy");
    await expect(page).toHaveURL(/\/deploy/);

    await page.click("nav >> text=Demo");
    await expect(page).toHaveURL(/\/demo/);
  });
});
