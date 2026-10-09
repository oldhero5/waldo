import { expect, test, type Page, type Route } from "@playwright/test";
const json = (route: Route, data: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
const summary = {
  backend: "mlx-image-iou-tracker", coverage: "sampled", timestamp_method: "source_frame_index/fps",
  requested_sample_fps: 2, score_threshold: 0.2,
  videos: [
    { video_id: "clip-a", status: "completed", sampled_frames: 2, assessed_timestamps_s: [0, 1] },
    { video_id: "clip-b", status: "failed", error: "Decode stopped at source frame 25" },
  ],
};
const job = (status: string, processing_summary: unknown = summary) => ({
  job_id: "coverage-job", name: "Road footage", text_prompt: "camera", video_id: "clip-a",
  status, progress: 1, processed_frames: 2, total_frames: 2, annotation_count: 1,
  class_count: 1, version: 1, parent_id: null, result_url: status === "completed" ? "/exported-dataset.zip" : null,
  error_message: status === "failed" ? "Decoder failed" : null, processing_summary,
});
const annotation = {
  id: "sighting", frame_id: "frame-a", class_name: "camera", class_index: 0,
  polygon: [], bbox: [0.5, 0.5, 0.2, 0.2], confidence: 0.9, status: "accepted",
  frame_url: null, track_id: 7, source_video_id: "clip-a", timestamp_s: null,
};
async function mock(page: Page, status: string, processingSummary: unknown = summary) {
  let statusRequests = 0;
  await page.addInitScript(() => localStorage.setItem("waldo_token", "test"));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return json(route, { id: "user", email: "user@test.local", display_name: "Test", workspace_id: "workspace", workspace_name: "Test workspace", role: "admin", avatar_url: null });
    if (path.endsWith("/label")) return json(route, { job_id: "coverage-job", status: "queued", celery_task_id: "task" });
    if (path.endsWith("/status/coverage-job")) { statusRequests++; return json(route, job(status, processingSummary)); }
    if (path.endsWith("/status")) return json(route, [job(status, processingSummary)]);
    if (path.endsWith("/annotations")) return json(route, [annotation]);
    if (path.endsWith("/stats")) return json(route, { total_annotations: 1, total_frames: 1, annotated_frames: 1, empty_frames: 0, by_class: [{ name: "camera", count: 1 }], by_status: { accepted: 1 }, annotation_density: 1 });
    if (path.endsWith("/overview")) return json(route, { job_id: "coverage-job", name: "Road footage", prompt: "camera", status, total_frames: 2, labeled_frames: 1, total_annotations: 1, accepted: 1, rejected: 0, pending: 0, classes: ["camera"], sample_frames: [], dataset_url: null, feedback_count: 0, labeling_in_progress: 0, in_progress_classes: [], in_progress_details: [] });
    return json(route, []);
  });
  return () => statusRequests;
}

test("partial labeling stops polling, explains sample coverage, and allows evidence review", async ({ page }) => {
  const requests = await mock(page, "partial");
  await page.goto("/label/clip-a");
  await page.getByPlaceholder('e.g. "car, sedan, SUV"').fill("camera");
  await page.getByRole("button", { name: "Find Objects", exact: true }).click();
  await expect(page.getByText("partial", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Review available evidence", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Find Objects", exact: true })).toBeEnabled();
  await expect(page.getByText(/does not establish full-video coverage/)).toBeVisible();
  await expect(page.getByText(/Decode stopped at source frame 25/)).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Processing progress" })).toHaveAttribute("aria-valuenow", "50");
  const stoppedAt = requests();
  await page.waitForTimeout(2300); // Beyond the labeling poll interval.
  expect(requests()).toBe(stoppedAt);
  await page.getByRole("button", { name: "Review available evidence", exact: true }).click();
  await expect(page).toHaveURL(/\/review\/coverage-job$/);
});

for (const status of ["partial", "failed"]) {
  test(`${status} evidence is discoverable in datasets and never gets a training shortcut`, async ({ page }) => {
    await mock(page, status);
    await page.goto("/datasets");
    await expect(page.getByText("Road footage", { exact: true })).toBeVisible();
    await page.getByText("Road footage", { exact: true }).click();
    await expect(page.getByRole("link", { name: "View Annotations", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Train New Model", exact: true })).toHaveCount(0);
    await page.getByRole("link", { name: "View Annotations", exact: true }).click();
    await expect(page.getByRole("link", { name: "Train Model", exact: true })).toHaveCount(0);
    await expect(page.getByText(/Track IDs are local to this job and source video/)).toBeVisible();
    await expect(page.getByText("Time unknown", { exact: true })).toBeVisible();
  });
}

test("legacy completed jobs show unknown coverage without inventing assessed timestamps", async ({ page }) => {
  await mock(page, "completed", null);
  await page.goto("/review/coverage-job");
  await expect(page.getByText("Processing coverage is unknown for this job.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Train Model", exact: true }).first()).toBeVisible();
  await expect(page.getByText(/full-video coverage/)).toHaveCount(0);
});

async function recordSockets(page: Page) {
  await page.addInitScript(() => {
    const records: { url: string; protocols?: string | string[] }[] = [];
    (window as Window & { socketRequests?: typeof records }).socketRequests = records;
    class FakeSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      readyState = 1;
      constructor(url: string, protocols?: string | string[]) { records.push({ url, protocols }); }
      close() { this.readyState = 3; }
    }
    window.WebSocket = FakeSocket as unknown as typeof WebSocket;
  });
}

test("training metrics authenticate with the negotiated subprotocol without a URL token", async ({ page }) => {
  await mock(page, "completed", null);
  await recordSockets(page);
  await page.route("**/api/v1/train/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/variants")) return json(route, { variants: {}, defaults: {} });
    return json(route, {
      run_id: "training-run", job_id: "coverage-job", name: "Training", status: "training",
      task_type: "detect", model_variant: "yolo26n", epoch_current: 0, total_epochs: 1,
      metrics: {}, best_metrics: {}, hyperparameters: {}, loss_history: [], metric_history: [],
      weights_url: null, error_message: null, tags: [], notes: null, started_at: null, completed_at: null,
    });
  });
  await page.goto("/train/training-run");
  await expect.poll(() => page.evaluate(() => (window as Window & { socketRequests?: unknown[] }).socketRequests?.length)).toBeGreaterThan(0);
  const sockets = await page.evaluate(() => (window as Window & { socketRequests?: { url: string; protocols: string[] }[] }).socketRequests || []);
  expect(sockets[0].url).toMatch(/\/ws\/training\/training-run$/);
  expect(sockets[0].url).not.toContain("token");
  expect(sockets[0].protocols).toEqual(["waldo", "bearer.test"]);
});

test("video prediction sends the displayed model and authenticates its stream", async ({ page }) => {
  await mock(page, "completed", null);
  await recordSockets(page);
  let selectedModel: string | null = null;
  await page.route("**/api/v1/serve/status", (route) => json(route, { loaded: true, model_id: "selected-model", model_name: "Selected", class_names: ["camera"], task_type: "detect", model_variant: "yolo26n", device: "test" }));
  await page.route("**/api/v1/predict/video**", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ status: "running", session_id: "video-session" }) });
    selectedModel = new URL(route.request().url()).searchParams.get("model_id");
    return json(route, { session_id: "video-session", celery_task_id: "task", frame_count: 1 });
  });
  await page.goto("/deploy/test");
  await page.getByRole("button", { name: "Video", exact: true }).click();
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles({ name: "source.mp4", mimeType: "video/mp4", buffer: Buffer.from("synthetic-test-upload") });
  await page.getByRole("button", { name: "Track Objects", exact: true }).click();
  await expect.poll(() => selectedModel).toBe("selected-model");
  await expect.poll(() => page.evaluate(() => (window as Window & { socketRequests?: unknown[] }).socketRequests?.length)).toBeGreaterThan(0);
  const sockets = await page.evaluate(() => (window as Window & { socketRequests?: { url: string; protocols: string[] }[] }).socketRequests || []);
  expect(sockets[0].url).toMatch(/\/ws\/predict\/video-session$/);
  expect(sockets[0].url).not.toContain("token");
  expect(sockets[0].protocols).toEqual(["waldo", "bearer.test"]);
});

test("a partial preview job stops generic polling and reports the returned failure", async ({ page }) => {
  await mock(page, "completed", null);
  let polls = 0;
  await page.route("**/api/v1/label/preview", (route) => json(route, { job_id: "partial-preview" }));
  await page.route("**/api/v1/job/partial-preview", (route) => { polls++; return json(route, { job_id: "partial-preview", status: "partial", error: "One preview clip failed" }); });
  await page.goto("/label/clip-a");
  await page.getByPlaceholder('e.g. "car, sedan, SUV"').fill("camera");
  await page.getByRole("button", { name: "Test Prompts", exact: true }).click();
  await expect(page.getByText("One preview clip failed", { exact: true })).toBeVisible();
  expect(polls).toBe(1);
});


test("completed native evidence requires export before offering training", async ({ page }) => {
  await mock(page, "completed");
  await page.route("**/api/v1/status/coverage-job", (route) => json(route, { ...job("completed"), result_url: null }));
  await page.goto("/review/coverage-job");
  await expect(page.getByText("Export dataset before training.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Train Model", exact: true })).toHaveCount(0);
});


test("exported artifact offers an actionable download when popups are blocked and unlocks training", async ({ page }) => {
  await mock(page, "completed", null);
  let exported = false, exportRequests = 0;
  await page.route("**/api/v1/status", (route) => json(route, [{ ...job("completed", null), result_url: exported ? "/ready.zip" : null }]));
  await page.route("**/api/v1/jobs/coverage-job/export**", (route) => { exported = true; exportRequests++; return json(route, { download_url: "/ready.zip" }); });
  await page.route("**/ready.zip", (route) => route.fulfill({ contentType: "application/zip", headers: { "Content-Disposition": 'attachment; filename="ready-segment.zip"' }, body: "fixture archive" }));
  await page.addInitScript(() => { window.open = () => null; });
  await page.goto("/datasets");
  await page.getByText("Road footage", { exact: true }).click();
  await expect(page.getByRole("link", { name: "Train New Model", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("button", { name: /YOLO Segment/ }).click();
  await expect(page.getByRole("link", { name: "Train New Model", exact: true })).toBeVisible();
  const link = page.getByRole("link", { name: "Download YOLO Segment export", exact: true });
  await expect(link).toHaveAttribute("href", "/ready.zip");
  const downloading = page.waitForEvent("download");
  await link.click();
  expect((await downloading).suggestedFilename()).toBe("ready.zip");
  expect(exportRequests).toBe(1);
});

test("classification and centroid pose exports request their own formats and offer downloads", async ({ page }) => {
  await mock(page, "completed", null);
  const formats: string[] = [];
  await page.route("**/api/v1/jobs/coverage-job/export**", (route) => {
    const format = route.request().postDataJSON().format;
    formats.push(format);
    return json(route, { download_url: `/ready-${format}.zip` });
  });
  await page.route("**/ready-*.zip", (route) => route.fulfill({ contentType: "application/zip", body: "fixture archive" }));
  await page.goto("/datasets");
  await page.getByText("Road footage", { exact: true }).click();
  for (const { format, label, description } of [
    { format: "classify", label: "YOLO Classify", description: "Padded crops of saved objects, grouped by source video" },
    { format: "pose", label: "YOLO Pose", description: "One centroid keypoint per saved object" },
  ]) {
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await expect(page.getByText(description, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: new RegExp(label) }).click();
    const link = page.getByRole("link", { name: `Download ${label} export`, exact: true });
    await expect(link).toHaveAttribute("href", `/ready-${format}.zip`);
    const downloading = page.waitForEvent("download");
    await link.click();
    expect((await downloading).suggestedFilename()).toBe(`ready-${format}.zip`);
  }
  expect(formats).toEqual(["classify", "pose"]);
});

test("task descriptions describe object crops and one centroid rather than whole frames or skeletons", async ({ page }) => {
  await mock(page, "completed");
  await page.goto("/label/clip-a");
  const task = page.getByText("Output format", { exact: true }).locator("..").locator("select");
  await task.selectOption("classify");
  await expect(page.getByText("Padded object crops labeled by class", { exact: true })).toBeVisible();
  await task.selectOption("pose");
  await expect(page.getByText("One centroid keypoint per detected object", { exact: true })).toBeVisible();
});


test("training uses the exported detection format and current patience setting", async ({ page }) => {
  await mock(page, "completed", null);
  let payload: { task_type?: string; hyperparameters?: { patience?: number } } | null = null;
  await page.route("**/api/v1/status/coverage-job", (route) => json(route, { ...job("completed", null), task_type: "detect" }));
  await page.route("**/api/v1/train/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/variants")) return json(route, { variants: { yolo26n: "model.pt" }, defaults: { detect: "yolo26n", segment: "yolo26n-seg" }, hyperparams: {} });
    if (path.includes("dataset-stats")) return json(route, null);
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"Run not found"}' });
  });
  await page.route("**/api/v1/train", (route) => {
    if (route.request().method() === "GET") return json(route, []);
    payload = route.request().postDataJSON();
    return json(route, { run_id: "new-run", status: "pending", celery_task_id: "task" });
  });
  await page.goto("/train/coverage-job");
  await expect(page.getByText("Dataset output: detect", { exact: true })).toBeVisible();
  const patience = page.locator("label").filter({ hasText: /^Patience/ }).locator("..").locator('input[type="number"]');
  await patience.fill("7");
  await page.getByRole("button", { name: "Start Training", exact: true }).click();
  await expect.poll(() => payload?.task_type).toBe("detect");
  expect(payload?.hyperparameters?.patience).toBe(7);
});

test("review uses chronological source evidence and named annotation controls", async ({ page }) => {
  await mock(page, "completed", null);
  const image = `data:image/svg+xml;base64,${Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="gray"/></svg>').toString("base64")}`;
  await page.route("**/api/v1/jobs/coverage-job/annotations**", (route) => json(route, [
    { ...annotation, id: "later", frame_id: "frame-later", timestamp_s: 2, timestamp_method: "frame_index/fps", frame_url: image },
    { ...annotation, id: "unknown", frame_id: "frame-unknown", timestamp_s: null, timestamp_method: "unknown", frame_url: image },
    { ...annotation, id: "earlier", frame_id: "frame-earlier", timestamp_s: 0.12, timestamp_method: "source_pts", frame_url: image },
  ]));
  await page.goto("/review/coverage-job");
  await expect(page.getByText("0.12s", { exact: true })).toBeVisible();
  const times = page.locator("span").filter({ hasText: /^(?:Approx\. )?\d+\.\d+s$|^Time unknown$/ });
  await expect(times).toHaveText(["0.12s", "Approx. 2.00s", "Time unknown"]);
  await page.getByRole("img", { name: "Frame preview", exact: true }).first().locator("..").click();
  for (const name of ["Zoom in", "Zoom out", "Reset zoom and pan", "Zoom to selected annotation", "Close annotation editor"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: "Close annotation editor", exact: true }).click();
  await expect(page.getByRole("button", { name: "Close annotation editor", exact: true })).toHaveCount(0);
});

test("dataset counts refer to frames and Newest preserves server creation order", async ({ page }) => {
  await mock(page, "completed", null);
  const sampled = (count: number) => ({ ...summary, videos: [{ video_id: "clip-a", status: "completed", sampled_frames: count, assessed_timestamps_s: [0, 0.5] }] });
  await page.route("**/api/v1/status", (route) => json(route, [
    { ...job("completed", sampled(10)), job_id: "newer", name: "Newer ten-frame dataset", total_frames: 10, processed_frames: 10, created_at: "2026-10-07T12:00:00Z" },
    { ...job("completed", sampled(20)), job_id: "older", name: "Older twenty-frame dataset", total_frames: 20, processed_frames: 20, created_at: "2026-10-06T12:00:00Z" },
  ]));
  await page.goto("/datasets");
  await expect(page.locator("h3")).toHaveText(["Newer ten-frame dataset", "Older twenty-frame dataset"]);
  await expect(page.getByText("10 sampled frames · 10 processed", { exact: true })).toBeVisible();
  await expect(page.getByText("20 sampled frames · 20 processed", { exact: true })).toBeVisible();
  await expect(page.getByText(/\d+ videos · \d+ processed/)).toHaveCount(0);
  await page.getByRole("combobox").selectOption({ label: "Most frames" });
  await expect(page.locator("h3")).toHaveText(["Older twenty-frame dataset", "Newer ten-frame dataset"]);
  await page.getByRole("combobox").selectOption({ label: "Newest" });
  await expect(page.locator("h3")).toHaveText(["Newer ten-frame dataset", "Older twenty-frame dataset"]);
});

test("bulk exports use each dataset format and retain individual download links", async ({ page }) => {
  await mock(page, "completed", null);
  await page.addInitScript(() => { window.open = () => null; });
  await page.route("**/api/v1/status", (route) => json(route, [
    { ...job("completed", null), job_id: "newer", name: "Newer dataset", total_frames: 10, task_type: "classify" },
    { ...job("completed", null), job_id: "older", name: "Older dataset", total_frames: 20, task_type: "pose" },
    { ...job("completed", null), job_id: "legacy", name: "Legacy dataset", total_frames: 15 },
    { ...job("completed", null), job_id: "transformer", name: "Transformer dataset", total_frames: 12, task_type: "detect_transformer" },
  ]));
  const exports: { id: string; format: string }[] = [];
  await page.route("**/api/v1/jobs/*/export**", (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2)!;
    exports.push({ id, format: route.request().postDataJSON().format });
    return json(route, { download_url: `/ready-${id}.zip` });
  });
  await page.goto("/datasets");
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByRole("checkbox", { name: "Select Newer dataset", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select Older dataset", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select Legacy dataset", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select Transformer dataset", exact: true }).check();
  await page.getByRole("button", { name: "Export All", exact: true }).click();
  await expect(page.getByRole("link", { name: "Download Newer dataset export", exact: true })).toHaveAttribute("href", "/ready-newer.zip");
  await expect(page.getByRole("link", { name: "Download Older dataset export", exact: true })).toHaveAttribute("href", "/ready-older.zip");
  await expect(page.getByRole("link", { name: "Download Legacy dataset export", exact: true })).toHaveAttribute("href", "/ready-legacy.zip");
  await expect(page.getByRole("link", { name: "Download Transformer dataset export", exact: true })).toHaveAttribute("href", "/ready-transformer.zip");
  expect(exports).toEqual([
    { id: "newer", format: "classify" },
    { id: "older", format: "pose" },
    { id: "legacy", format: "segment" },
    { id: "transformer", format: "detect" },
  ]);
});
