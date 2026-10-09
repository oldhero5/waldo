import { test, expect, type Page, type Route } from "@playwright/test";

const user = (id: string) => ({
  id, email: `${id}@example.test`, display_name: id, avatar_url: null,
  workspace_id: `workspace-${id}`, workspace_name: `${id} workspace`, role: "admin",
});
const job = (id: string) => ({
  job_id: id, name: `${id} private footage`, text_prompt: "car", status: "completed",
  annotation_count: 12, total_frames: 30,
});
async function json(route: Route, data: unknown) {
  await route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
}
async function signIn(page: Page, id: string) {
  await page.locator('input[type="email"]').fill(`${id}@example.test`);
  await page.locator('input[type="password"]').fill("test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Workspace overview" })).toBeVisible();
  await expect(page.getByText(`${id} workspace`, { exact: true })).toBeVisible();
}

async function mockApi(page: Page, status?: (route: Route, id: string) => Promise<void>) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const id = route.request().headers().authorization?.replace("Bearer ", "") || "A";
    if (path.endsWith("/auth/login")) {
      const loginId = route.request().postDataJSON().email.split("@")[0];
      return json(route, { access_token: loginId, refresh_token: `refresh-${loginId}` });
    }
    if (path.endsWith("/auth/me")) return json(route, user(id));
    if (path.endsWith("/status")) return status ? status(route, id) : json(route, [job(id)]);
    if (path.endsWith("/serve/status")) return json(route, { loaded: false, class_names: [], backend: "test" });
    return json(route, []);
  });
}

test("neutral controls, truthful workspace, active navigation, and reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/login");
  const button = page.getByRole("button", { name: "Sign in", exact: true });
  await expect(button).toHaveCSS("background-color", "rgb(237, 237, 237)");
  await expect(button).toHaveCSS("color", "rgb(24, 24, 24)");
  await button.hover();
  await expect(button).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect(button).toHaveCSS("color", "rgb(24, 24, 24)");
  await signIn(page, "A");
  await expect(page.getByText("A private footage", { exact: true })).toBeVisible();
  await expect(page.locator('aside a[aria-current="page"]')).toHaveText("Home");
  await expect(page.locator("canvas")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "A workspace" })).toHaveCount(0);
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(24, 24, 24)");
  const metadata = page.getByText("Review your footage, labeled datasets, and model activity.");
  await expect(metadata).toHaveCSS("color", "rgb(188, 188, 188)");
  // A CSS-driven activity indicator is static when reduced motion is requested.
  await page.evaluate(() => {
    const indicator = document.createElement("div");
    indicator.id = "motion-probe";
    indicator.className = "animate-spin";
    document.body.append(indicator);
  });
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  await expect(page.locator("#motion-probe")).toHaveCSS("animation-name", "none");
  await page.locator("#motion-probe").evaluate((el) => el.remove());
  if (process.env.WALDO_SCREENSHOT_DIR) {
    await page.screenshot({ path: `${process.env.WALDO_SCREENSHOT_DIR}/waldo-neutral-dashboard.png`, fullPage: true });
    await page.setViewportSize({ width: 1024, height: 900 });
    await page.getByRole("link", { name: "Playground new" }).click();
    await expect(page.getByRole("heading", { name: "Video prompt preview", exact: true })).toBeVisible();
    await page.screenshot({ path: `${process.env.WALDO_SCREENSHOT_DIR}/waldo-neutral-playground.png`, fullPage: true });
    await page.goto("/login");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${process.env.WALDO_SCREENSHOT_DIR}/waldo-neutral-login-mobile.png`, fullPage: true });
  }
});

test("logout isolates cached data and late account A responses from account B", async ({ page }) => {
  let callsA = 0;
  let releaseA!: () => void;
  let releaseB!: () => void;
  let startedA!: () => void;
  let startedB!: () => void;
  const pendingA = new Promise<void>((resolve) => { releaseA = resolve; });
  const pendingB = new Promise<void>((resolve) => { releaseB = resolve; });
  const requestA = new Promise<void>((resolve) => { startedA = resolve; });
  const requestB = new Promise<void>((resolve) => { startedB = resolve; });
  await mockApi(page, async (route, id) => {
    if (id === "A" && ++callsA > 1) { startedA(); await pendingA; }
    if (id === "B") { startedB(); await pendingB; }
    await json(route, [job(id)]);
  });
  await page.goto("/login");
  await signIn(page, "A");
  await expect(page.getByText("A private footage", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await requestA; // A's background refetch stays in flight across logout.
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Sign Out" }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await signIn(page, "B");
  await requestB;
  await expect(page.getByText("A private footage", { exact: true })).toHaveCount(0);
  const lateA = page.waitForResponse((response) => response.url().endsWith("/api/v1/status") && response.request().headers().authorization === "Bearer A");
  releaseA();
  await (await lateA).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve(null))));
  await expect(page.getByText("A private footage", { exact: true })).toHaveCount(0);
  releaseB();
  await expect(page.getByText("B private footage", { exact: true })).toBeVisible();
  await expect(page.getByText("A private footage", { exact: true })).toHaveCount(0);
});

const previewResult = (label = "car", timestamp = 3) => ({
  frames: [{ frame_idx: 0, image_b64: "", timestamp_s: timestamp, width: 640, height: 360,
    detections: [{ bbox: [10, 10, 40, 40], score: 0.9, label, polygon: null, track_id: 1 }] }],
  total_detections: 1, unique_track_count: 1, fps: 30, video_duration_s: 30, mode: "window",
});
async function openPreview(page: Page) {
  await mockApi(page);
  await page.route("**/api/v1/projects**", async (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/videos")) {
      return json(route, [
        { id: "video-a", filename: "Source A.mp4", duration_s: 30, width: 640, height: 360, fps: 30, url: "/mock/video-a.mp4" },
        { id: "video-b", filename: "Source B.mp4", duration_s: 20, width: 640, height: 360, fps: 30, url: "/mock/video-b.mp4" },
      ]);
    }
    return json(route, [{ id: "collection-a", name: "Test footage", video_count: 2 }]);
  });
  await page.route("**/mock/*.mp4", (route) => route.fulfill({ status: 200, contentType: "video/mp4", body: "" }));
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await page.goto("/playground");
  await expect(page.locator("select").nth(1)).toHaveValue("video-a");
  await page.getByPlaceholder("pothole", { exact: true }).fill("car");
  const sliders = page.locator('input[type="range"]');
  await sliders.nth(1).fill("3");
  await sliders.nth(2).fill("4");
}

test("delayed preview keeps source and playback window from its request after draft edits", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await openPreview(page);
  await page.route("**/api/v1/label/preview", (route) => json(route, { job_id: "preview-a", result_url: "/api/v1/job/preview-a" }));
  await page.route("**/api/v1/job/preview-a", async (route) => {
    await pending;
    await json(route, { job_id: "preview-a", status: "completed", result: previewResult() });
  });
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await page.locator("select").nth(1).selectOption("video-b");
  await page.getByPlaceholder("pothole", { exact: true }).fill("pothole");
  await page.locator('input[type="range"]').nth(1).fill("8");
  await page.locator('input[type="range"]').nth(2).fill("2");
  release();
  await expect(page.locator("video")).toHaveAttribute("src", "/mock/video-a.mp4");
  await expect(page.getByRole("slider", { name: "Preview playback time" })).toHaveAttribute("min", "3");
  await expect(page.getByRole("slider", { name: "Preview playback time" })).toHaveAttribute("max", "7");
  await expect(page.getByText("Draft changes are not included in this preview.", { exact: true })).toBeVisible();
});

test("promotion uses displayed preview source and prompts and cannot be submitted twice", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const promotions: unknown[] = [];
  await openPreview(page);
  await page.route("**/api/v1/label/preview", (route) => json(route, previewResult()));
  await page.route("**/api/v1/label", async (route) => {
    promotions.push(route.request().postDataJSON());
    await pending;
    await json(route, { job_id: "new-job", status: "queued", celery_task_id: "task" });
  });
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("video")).toHaveAttribute("src", "/mock/video-a.mp4");
  await page.locator("select").nth(1).selectOption("video-b");
  await page.getByPlaceholder("pothole", { exact: true }).fill("pothole");
  await page.getByRole("slider", { name: "Confidence threshold" }).fill("0.8");
  await page.getByRole("slider", { name: "Start time", exact: true }).fill("8");
  await page.getByRole("slider", { name: "Window duration" }).fill("2");
  await page.getByRole("button", { name: "8 fps", exact: true }).click();
  await expect(page.getByText(/The preview time window does not limit the full job/)).toBeVisible();
  await page.getByRole("button", { name: "Start full job", exact: true }).click();
  await expect.poll(() => promotions.length).toBe(1);
  expect(promotions[0]).toMatchObject({ video_id: "video-a", class_prompts: [{ name: "car", prompt: "car" }], task_type: "segment", threshold: 0.35, fps: 4 });
  expect(promotions[0]).not.toHaveProperty("start_sec");
  expect(promotions[0]).not.toHaveProperty("duration_sec");
  await expect(page.getByRole("button", { name: "Starting job…", exact: true })).toBeDisabled();
  release();
  await page.waitForURL("**/review/new-job");
  expect(promotions).toHaveLength(1);
});


test("discarded preview cannot replace a newer preview when its server job finishes late", async ({ page }) => {
  let releaseOld!: () => void;
  const oldJob = new Promise<void>((resolve) => { releaseOld = resolve; });
  await openPreview(page);
  await page.route("**/api/v1/label/preview", async (route) => {
    const request = route.request().postDataJSON();
    if (request.video_id === "video-a") {
      await oldJob;
      return json(route, previewResult("old-car", 3));
    }
    return json(route, previewResult("new-pothole", 8));
  });
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await page.getByRole("button", { name: "Discard preview", exact: true }).click();
  await page.getByLabel("Video", { exact: true }).selectOption("video-b");
  await page.getByRole("textbox", { name: "Prompt 1", exact: true }).fill("pothole");
  await page.getByRole("slider", { name: "Start time", exact: true }).fill("8");
  await page.getByRole("slider", { name: "Window duration" }).fill("2");
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("video")).toHaveAttribute("src", "/mock/video-b.mp4");
  await expect(page.getByRole("button", { name: /#1 new-pothole/ })).toBeVisible();
  const lateResponse = page.waitForResponse((response) => response.url().endsWith("/label/preview") && response.request().postDataJSON().video_id === "video-a");
  releaseOld();
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve(null))));
  await expect(page.locator("video")).toHaveAttribute("src", "/mock/video-b.mp4");
  await expect(page.getByRole("button", { name: /#1 new-pothole/ })).toBeVisible();
  await expect(page.getByText(/old-car/)).toHaveCount(0);
  const seek = page.getByRole("slider", { name: "Preview playback time" });
  await seek.focus();
  await seek.press("ArrowRight");
  await expect(seek).toHaveValue("8.01");
});

test("provider settings require cloud consent and never persist entered credentials", async ({ page }) => {
  await mockApi(page);
  const status = { provider: "ollama", model: "local-model", ok: true, source: "environment", cloud_text_enabled: false, connection_verified: false };
  let payload: Record<string, unknown> | null = null;
  await page.route("**/api/v1/agent/health", (route) => json(route, status));
  await page.route("**/api/v1/agent/provider", (route) => {
    payload = route.request().postDataJSON();
    return json(route, { ...status, provider: "openai", model: "chosen-model", source: "runtime", cloud_text_enabled: true, connection_verified: true });
  });
  await page.goto("/login"); await signIn(page, "A");
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "AI provider", exact: true }).click();
  await expect(page.getByText(/clear on restart/)).toBeVisible();
  await page.getByLabel("Provider", { exact: true }).selectOption("openai");
  await page.getByLabel("Model identifier", { exact: true }).fill("chosen-model");
  await page.getByLabel("API key", { exact: true }).fill("credential-do-not-store");
  const apply = page.getByRole("button", { name: "Test and apply temporary configuration" });
  await expect(apply).toBeDisabled();
  await page.getByRole("checkbox", { name: /I allow workspace/ }).check();
  await apply.click();
  await expect(page.getByText(/Connection test passed/)).toBeVisible();
  expect(payload).toEqual({ provider: "openai", model: "chosen-model", api_key: "credential-do-not-store", allow_cloud_text: true });
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
  expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain("credential-do-not-store");
  if (process.env.WALDO_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.WALDO_SCREENSHOT_DIR}/waldo-agent-provider-settings.png`, fullPage: true });
});

test("late provider health does not replace a configuration draft", async ({ page }) => {
  await mockApi(page);
  const status = { provider: "ollama", model: "local-model", ok: true, source: "environment", cloud_text_enabled: false, connection_verified: false };
  let releaseHealth!: () => void;
  let healthStarted!: () => void;
  const pendingHealth = new Promise<void>((resolve) => { releaseHealth = resolve; });
  const requestedHealth = new Promise<void>((resolve) => { healthStarted = resolve; });
  await page.route("**/api/v1/agent/health", async (route) => {
    healthStarted();
    await pendingHealth;
    await json(route, status);
  });
  await page.goto("/login"); await signIn(page, "A");
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "AI provider", exact: true }).click();
  await requestedHealth;
  await page.getByLabel("Provider", { exact: true }).selectOption("openai");
  await page.getByLabel("Model identifier", { exact: true }).fill("chosen-model");
  const healthResponse = page.waitForResponse("**/api/v1/agent/health");
  releaseHealth();
  await (await healthResponse).finished();
  await expect(page.getByText(/Current: ollama/)).toBeVisible();
  await expect(page.getByLabel("Provider", { exact: true })).toHaveValue("openai");
  await expect(page.getByLabel("Model identifier", { exact: true })).toHaveValue("chosen-model");
  await expect(page.getByLabel("API key", { exact: true })).toBeVisible();
});

test("training charts recover from unavailable values and keep hidden series restorable", async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await mockApi(page);
  let populated = false;
  let requests = 0;
  await page.route("**/api/v1/train/**", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/variants")) return json(route, { variants: {}, defaults: {} });
    requests++;
    return json(route, {
      run_id: "chart-run", job_id: "chart-job", name: "Chart test", status: "training",
      task_type: "detect", model_variant: "test", epoch_current: 2, total_epochs: 10,
      metrics: {}, best_metrics: {}, hyperparameters: {}, metric_history: [],
      loss_history: [
        { epoch: 1, "train/box_loss": populated ? 0.5 : null, "train/cls_loss": populated ? 0.3 : null },
        { epoch: 2, "train/box_loss": populated ? 0.4 : null, "train/cls_loss": populated ? 0.2 : null },
      ],
      weights_url: null, error_message: null, tags: [], notes: null, started_at: null, completed_at: null,
    });
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/train/chart-run");
  await expect(page.getByText("Loss Curves", { exact: true })).toBeVisible();
  const pollsBefore = requests;
  populated = true;
  await page.clock.runFor(3100);
  await expect.poll(() => requests).toBeGreaterThan(pollsBefore);
  const boxLegend = page.getByRole("button", { name: "Train box_loss", exact: true });
  await expect(boxLegend).toBeVisible();
  await expect(boxLegend.locator("..").locator("..").locator("svg path")).toHaveAttribute("d", /^M.*L/);
  await boxLegend.click();
  await expect(boxLegend).toBeVisible();
  await boxLegend.click();
  await expect(boxLegend).toHaveCSS("opacity", "1");
  const clipIds = await page.locator("clipPath").evaluateAll((elements) => elements.map((el) => el.id));
  expect(clipIds.length).toBe(2);
  expect(new Set(clipIds).size).toBe(clipIds.length);
  expect(errors).toEqual([]);
});

test("model test classes default to all and preserve None across polling", async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await mockApi(page);
  let modelId = "first-model";
  let requests = 0;
  await page.route("**/api/v1/serve/status", (route) => {
    requests++;
    return json(route, { loaded: true, model_id: modelId, model_name: modelId, class_names: ["car", "person"], task_type: "detect" });
  });
  await page.route("**/api/v1/predict/image**", (route) => json(route, {
    detections: [{ class_name: "car", confidence: 0.9, bbox: [10, 10, 50, 50], track_id: null }],
  }));
  await page.goto("/deploy/test");
  const car = page.getByRole("checkbox", { name: "car", exact: true });
  const person = page.getByRole("checkbox", { name: "person", exact: true });
  await expect(car).toBeChecked();
  await expect(person).toBeChecked();
  await page.locator('input[type="file"][accept="image/*"]').setInputFiles({
    name: "fixture.svg", mimeType: "image/svg+xml", buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="gray"/></svg>'),
  });
  const canvas = page.locator("canvas");
  await expect(canvas).toHaveAttribute("width", "100");
  await canvas.hover();
  await page.mouse.wheel(0, -100);
  await expect(page.getByRole("button", { name: "Reset", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Predict", exact: true }).click();
  await expect(page.getByText(/1 detections shown/)).toBeVisible();
  await page.getByRole("button", { name: "None", exact: true }).click();
  await expect(page.getByText(/0 detections shown/)).toBeVisible();
  const before = requests;
  await page.clock.runFor(5100);
  await expect.poll(() => requests).toBeGreaterThan(before);
  await expect(car).not.toBeChecked();
  await expect(person).not.toBeChecked();
  modelId = "second-model";
  await page.clock.runFor(5100);
  await expect(car).toBeChecked();
  await expect(person).toBeChecked();
});

test("workflow catalog and execution authenticate and display server errors", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await mockApi(page);
  const tokens: (string | undefined)[] = [];
  await page.route("**/api/v1/workflows/**", (route) => {
    tokens.push(route.request().headers().authorization);
    if (new URL(route.request().url()).pathname.endsWith("/blocks")) return json(route, { blocks: [{
      name: "test", display_name: "Test block", description: "Offline fixture", category: "general", inputs: [], outputs: [], config_schema: { prompt: { type: "text", default: "default prompt", label: "Prompt" } },
    }] });
    return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "Editor role required" }) });
  });
  await page.goto("/workflows/new");
  await page.getByRole("button", { name: /Test block/ }).click();
  await page.getByRole("button", { name: "Run Pipeline", exact: true }).click();
  await expect(page.getByText("Editor role required", { exact: true })).toBeVisible();
  expect(tokens.length).toBeGreaterThanOrEqual(2);
  expect(tokens.every((token) => token === "Bearer A")).toBe(true);
});

const workflowBlock = {
  name: "test", display_name: "Test block", description: "Offline fixture", category: "general",
  inputs: [], outputs: [], config_schema: { prompt: { type: "text", default: "default prompt", label: "Prompt" } },
};
const savedGraph = (text: string) => ({ nodes: [{ id: "saved-node", type: "test", config: { prompt: text }, position: { x: 120, y: 80 } }], edges: [] });

test("workflow palette adds distinct nodes when randomUUID is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("waldo_token", "A");
    // Match non-loopback HTTP origins, where getRandomValues exists but randomUUID does not.
    Object.defineProperty(crypto, "randomUUID", { value: undefined });
  });
  await mockApi(page);
  let graph = savedGraph("saved prompt");
  await page.route("**/api/v1/workflows/**", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/blocks")) return json(route, { blocks: [workflowBlock] });
    if (new URL(route.request().url()).pathname.endsWith("/deploy")) return json(route, { endpoint: "/api/v1/workflows/serve/owned", curl: "curl fixture" });
    if (route.request().method() === "PUT") graph = route.request().postDataJSON().graph;
    return json(route, { id: "owned-id", slug: "owned", name: "Owned workflow", description: "", graph, block_count: graph.nodes.length, is_deployed: false });
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/workflows/owned");
  const nodes = page.locator(".react-flow__node");
  await expect(nodes).toHaveCount(1);
  await page.getByRole("button", { name: /Test block/ }).click();
  await page.getByRole("button", { name: /Test block/ }).click();
  await expect(nodes).toHaveCount(3);
  const ids = await nodes.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-id")));
  expect(ids).toContain("saved-node");
  expect(new Set(ids).size).toBe(3);
  await page.getByRole("button", { name: "Save & Deploy", exact: true }).click();
  await expect(page.getByText("Workflow Deployed", { exact: true })).toBeVisible();
  expect(graph.nodes.map((node) => node.id)).toEqual(ids);
  await page.reload();
  await expect(nodes).toHaveCount(3);
  expect(await nodes.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-id")))).toEqual(ids);
  expect(errors).toEqual([]);
});

test("saved workflow reopens, edits, and updates the same record", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await mockApi(page);
  let graph = savedGraph("saved prompt");
  const mutations: { method: string; path: string }[] = [];
  await page.route("**/api/v1/workflows**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    expect(route.request().headers().authorization).toBe("Bearer A");
    if (path.endsWith("/blocks")) return json(route, { blocks: [workflowBlock] });
    if (path.endsWith("/deploy")) return json(route, { endpoint: "/api/v1/workflows/serve/owned", curl: "curl fixture" });
    if (method !== "GET") {
      mutations.push({ method, path });
      graph = route.request().postDataJSON().graph;
    }
    return json(route, { id: "owned-id", slug: "owned", name: "Owned workflow", description: "Preserved", graph, block_count: 1, is_deployed: true });
  });
  await page.goto("/workflows/owned");
  const node = page.locator('.react-flow__node[data-id="saved-node"]');
  await expect(node).toBeVisible();
  await node.click();
  await expect(page.locator("textarea")).toHaveValue("saved prompt");
  await page.locator("textarea").fill("edited prompt");
  await page.getByRole("button", { name: "Save & Deploy", exact: true }).click();
  await expect(page.getByText("Workflow Deployed", { exact: true })).toBeVisible();
  expect(mutations).toEqual([{ method: "PUT", path: "/api/v1/workflows/saved/owned" }]);
  expect(graph.nodes[0].config.prompt).toBe("edited prompt");
  expect(graph.nodes[0].position).toEqual({ x: 120, y: 80 });
  await page.reload();
  await expect(node).toBeVisible();
  await node.click();
  await expect(page.locator("textarea")).toHaveValue("edited prompt");
});

for (const identifier of ["missing", "foreign"]) {
  test(`${identifier} saved workflow shows a blocking missing state`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
    await mockApi(page);
    await page.route("**/api/v1/workflows/**", (route) => {
      if (new URL(route.request().url()).pathname.endsWith("/blocks")) return json(route, { blocks: [workflowBlock] });
      return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"Workflow not found"}' });
    });
    await page.goto(`/workflows/${identifier}`);
    await expect(page.getByRole("alert")).toContainText("Workflow not found");
    await expect(page.getByRole("button", { name: "Save & Deploy", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Back to workflows", exact: true })).toBeVisible();
  });
}

test("a late workflow response cannot replace edits after changing routes", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await mockApi(page);
  let delayed: Route | undefined;
  await page.route("**/api/v1/workflows/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/blocks")) return json(route, { blocks: [workflowBlock] });
    if (path.endsWith("/saved/slow")) { delayed = route; return; }
    if (path.endsWith("/saved")) return json(route, [{ id: "current-id", name: "Current workflow", slug: "current", block_count: 1, is_deployed: false }]);
    return json(route, { id: "current-id", name: "Current workflow", slug: "current", description: "", graph: savedGraph("current prompt"), is_deployed: false });
  });
  await page.goto("/workflows/slow");
  await expect.poll(() => Boolean(delayed)).toBe(true);
  await page.getByRole("link", { name: "Workflows beta", exact: true }).click();
  await page.getByRole("link", { name: /Current workflow/ }).click();
  await page.locator('.react-flow__node[data-id="saved-node"]').click();
  await page.locator("textarea").fill("unsaved current edit");
  await json(delayed!, { id: "slow-id", slug: "slow", name: "Slow workflow", graph: savedGraph("late stale prompt") }).catch(() => {});
  await expect(page.locator("textarea")).toHaveValue("unsaved current edit");
  await expect(page).toHaveURL(/\/workflows\/current$/);
});

test("retrying a saved workflow deployment updates without creating a duplicate", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  await mockApi(page);
  page.on("dialog", (dialog) => dialog.accept("Created workflow"));
  const methods: string[] = [];
  let deployments = 0;
  await page.route("**/api/v1/workflows**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/blocks")) return json(route, { blocks: [workflowBlock] });
    if (path.endsWith("/deploy")) {
      deployments++;
      if (deployments === 1) return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Deployment temporarily unavailable"}' });
      return json(route, { endpoint: "/api/v1/workflows/serve/created", curl: "curl fixture" });
    }
    methods.push(route.request().method());
    return json(route, { id: "created-id", slug: "created", name: "Created workflow", description: "", is_deployed: false });
  });
  await page.goto("/workflows/new");
  await page.getByRole("button", { name: /Test block/ }).click();
  await page.getByRole("button", { name: "Save & Deploy", exact: true }).click();
  await expect(page.getByText("Deployment temporarily unavailable", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Save & Deploy", exact: true }).click();
  await expect(page.getByText("Workflow Deployed", { exact: true })).toBeVisible();
  expect(methods).toEqual(["POST", "PUT"]);
});
