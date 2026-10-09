import { expect, test as base, type Page, type Route } from "@playwright/test";
import { createDownloadBoundary, syntheticArchive, type DownloadBoundary } from "./downloadBoundary";
import { readFile } from "node:fs/promises";
const test = base.extend<{ downloads: DownloadBoundary }>({
  downloads: async ({ browser }, runFixture) => {
    void browser;
    const boundary = await createDownloadBoundary();
    try { await runFixture(boundary); } finally { await boundary.close(); }
  },
});

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGPQqDiBFTEMLQkAAtVaAcSRe60AAAAASUVORK5CYII=", "base64");
const orange = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGM4EaCBFTEMLQkAaplQAc/OcKAAAAAASUVORK5CYII=", "base64");
const json = (route: Route, data: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
const capability = (token: string) => `/api/v1/download?token=${token}`;
const job = () => ({ job_id: "coverage-job", name: "Road footage", text_prompt: "camera", video_id: "clip-a", task_type: "segment", status: "completed", progress: 1, processed_frames: 12, total_frames: 12, annotation_count: 12, class_count: 1, version: 1, parent_id: null, result_url: capability("A"), error_message: null, celery_task_id: null, processing_summary: null });
const annotation = (i: number, url = `/media/old/frame-${i}`) => ({ id: `ann-${i}`, frame_id: `frame-${i}`, class_name: "camera", class_index: 0, polygon: [], bbox: null, confidence: 0.9, status: "pending", frame_url: url, track_id: null, source_video_id: "clip-a", timestamp_s: i, timestamp_method: "source_pts" });

async function fixture(page: Page, downloads: DownloadBoundary) {
  await page.clock.install();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => localStorage.setItem("waldo_token", "A"));
  const state = { expired: false, permanent: false, frameRequests: [] as string[], annotationRequests: 0, renewTokens: [] as string[], exportCalls: 0, current: "A", freshInspector: false, downloadError: 0, networkError: false, secondJob: false };
  await page.route("**/media/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (state.permanent || (state.expired && path.includes("/old/"))) return route.fulfill({ status: state.permanent ? 404 : 401, body: "Expired or missing" });
    return route.fulfill({ contentType: "image/png", body: path.endsWith("frame-1") ? orange : png });
  });
  await page.route("**/api/v1/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) {
      const id = route.request().headers().authorization?.slice(7) || "A";
      return json(route, { id, email: `${id}@example.test`, display_name: id, avatar_url: null, workspace_id: `workspace-${id}`, workspace_name: `${id} workspace`, role: "admin" });
    }
    if (path.endsWith("/auth/login")) return json(route, { access_token: "B", refresh_token: "refresh-B" });
    if (path.endsWith("/status")) return json(route, [{ ...job(), result_url: capability(state.current) }, ...(state.secondJob ? [{ ...job(), job_id: "second-job", name: "Second footage" }] : [])]);
    if (path.endsWith("/status/coverage-job")) return json(route, { ...job(), result_url: capability(state.current) });
    if (path.endsWith("/annotations")) {
      state.annotationRequests++;
      const frame = url.searchParams.get("frame_id");
      return json(route, frame ? [annotation(Number(frame.slice(6)), state.freshInspector ? `/media/new/${frame}` : undefined)] : Array.from({ length: 12 }, (_, i) => annotation(i)));
    }
    if (path.endsWith("/stats")) return json(route, { total_annotations: 12, total_frames: 12, annotated_frames: 12, empty_frames: 0, by_class: [{ name: "camera", count: 12 }], by_status: { pending: 12 }, annotation_density: 1 });
    if (path.endsWith("/overview")) return json(route, { job_id: "coverage-job", name: "Road footage", prompt: "camera", status: "completed", total_frames: 12, labeled_frames: 12, total_annotations: 12, accepted: 0, rejected: 0, pending: 12, classes: ["camera"], sample_frames: Array.from({ length: 12 }, (_, i) => ({ frame_id: `frame-${i}`, frame_number: i, annotation_count: 1, accepted: 0, rejected: 0, pending: 1, thumbnail_url: `/media/old/frame-${i}`, classes: ["camera"] })), dataset_url: capability(state.current), feedback_count: 0, labeling_in_progress: 0, in_progress_classes: [], in_progress_details: [] });
    if (path.includes("/frames/")) { const frame = path.split("/").pop()!; state.frameRequests.push(frame); return json(route, { image_url: `/media/new/${frame}?renewal=${state.frameRequests.length}` }); }
    if (path.endsWith("/download-url")) {
      state.renewTokens.push(url.searchParams.get("token")!);
      if (state.networkError) return route.abort("failed");
      if (state.downloadError) return route.fulfill({ status: state.downloadError, contentType: "application/json", body: '{"detail":"Download unavailable"}' });
      return json(route, { download_url: `${downloads.origin}/dataset.zip?token=renewed-${url.searchParams.get("token")}` });
    }
    if (path.endsWith("/download")) return route.fulfill({ contentType: "application/zip", headers: { "Content-Disposition": 'attachment; filename="dataset.zip"' }, body: "synthetic-export" });
    if (path.endsWith("/export")) { state.exportCalls++; return json(route, { download_url: capability("A") }); }
    if (path.endsWith("/segment-points")) return json(route, { polygons: [[0.4, 0.4, 0.6, 0.4, 0.6, 0.6]], scores: [0.9] });
    return json(route, []);
  });
  return state;
}
async function datasets(page: Page) { await page.goto("/datasets"); await page.getByText("Road footage", { exact: true }).click(); }
async function inspector(page: Page, kind: "review" | "dataset") {
  if (kind === "review") { await page.goto("/review/coverage-job"); await page.getByAltText("Frame preview").first().locator("..").click(); }
  else { await datasets(page); await page.getByAltText("Frame 0", { exact: true }).locator("..").click(); }
  await expect(page.getByRole("button", { name: "Close annotation editor" })).toBeVisible();
}
const editorCanvas = (page: Page) => page.locator("canvas").filter({ visible: true }).last();
async function canvasColor(page: Page) {
  return editorCanvas(page).evaluate((canvas) => { const c = canvas as HTMLCanvasElement; return Array.from(c.getContext("2d")!.getImageData(c.width / 2, c.height / 4, 1, 1).data).slice(0, 3); });
}

// Native Image remains responsible for decoding/rendering. Expose only image-event
// boundaries so tests can deliver a late load or a failed reload after editing.
async function imageEvents(page: Page) {
  await page.addInitScript(() => {
    const NativeImage = window.Image;
    const images: HTMLImageElement[] = [];
    Object.assign(window, { mediaImages: images });
    window.Image = function (...args: ConstructorParameters<typeof Image>) { const image = new NativeImage(...args); images.push(image); return image; } as typeof Image;
  });
}
async function failCurrentImage(page: Page) {
  await page.evaluate(() => { const images = (window as Window & { mediaImages: HTMLImageElement[] }).mediaImages; const img = images.at(-1)!; img.dispatchEvent(new Event("error")); });
}

test("unopened review page recovers expired cached media without annotation polling", async ({ page, downloads }) => {
  const state = await fixture(page, downloads);
  await page.goto("/review/coverage-job");
  await expect(page.getByAltText("Frame preview").first()).toHaveJSProperty("naturalWidth", 8);
  const annotations = state.annotationRequests;
  await page.clock.fastForward(16 * 60 * 1000); state.expired = true;
  await page.getByRole("button", { name: "Next page of frames" }).click();
  await expect(page.getByAltText("Frame preview").last()).toHaveJSProperty("naturalWidth", 8);
  expect(state.frameRequests.filter((id) => id === "frame-11")).toHaveLength(1);
  expect(state.annotationRequests).toBe(annotations);
});

test("dataset overview thumbnails recover after expiry", async ({ page, downloads }) => {
  const state = await fixture(page, downloads);
  await page.goto("/datasets"); await page.clock.fastForward(16 * 60 * 1000); state.expired = true;
  await page.getByText("Road footage", { exact: true }).click();
  await expect(page.getByAltText("Frame 0", { exact: true })).toHaveJSProperty("naturalWidth", 8);
  expect(state.frameRequests.filter((id) => id === "frame-0")).toHaveLength(1);
});

test("permanent missing image renews once and offers an explicit retry", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); state.permanent = true;
  await datasets(page);
  await expect(page.getByRole("button", { name: "Retry image" }).first()).toBeVisible();
  await page.clock.runFor(5000);
  expect(state.frameRequests.filter((id) => id === "frame-0")).toHaveLength(1);
  state.permanent = false;
  await page.getByRole("button", { name: "Retry image" }).first().click();
  await expect(page.getByAltText("Frame 0", { exact: true })).toHaveJSProperty("naturalWidth", 8);
  expect(state.frameRequests.filter((id) => id === "frame-0")).toHaveLength(2);
});

for (const kind of ["review", "dataset"] as const) {
  test(`${kind} inspector recovers and preserves zoom, unsaved points and selected class`, async ({ page, downloads }) => {
    const state = await fixture(page, downloads); await imageEvents(page); await inspector(page, kind);
    await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await page.getByRole("button", { name: "Annotate", exact: true }).click();
    await editorCanvas(page).click({ position: { x: 500, y: 400 } });
    await expect(page.getByText("1 point", { exact: true })).toBeVisible();
    await page.clock.fastForward(16 * 60 * 1000); state.expired = true;
    await failCurrentImage(page);
    await expect.poll(() => state.frameRequests.filter((id) => id === "frame-0").length).toBe(1);
    await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
    await expect(page.getByText("130%", { exact: true })).toBeVisible();
    await expect(page.getByText("1 point", { exact: true })).toBeVisible();
    await expect(page.locator("select").last()).toHaveValue("camera");
    await page.getByRole("button", { name: "Next frame", exact: true }).click();
    await expect.poll(() => canvasColor(page)).toEqual([200, 80, 40]);
    await expect(page.getByText("100%", { exact: true })).toBeVisible();
    await expect(page.getByText("1 point", { exact: true })).toHaveCount(0);
  });
}

test("dataset inspector uses freshly queried frame URL", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); state.freshInspector = true;
  const urls: string[] = []; page.on("request", (request) => { if (request.url().includes("/media/")) urls.push(request.url()); });
  await inspector(page, "dataset");
  await expect.poll(() => urls.some((url) => url.endsWith("/media/new/frame-0"))).toBe(true);
  await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
});

test("thumbnail and inspector errors share one in-flight renewal", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); await imageEvents(page);
  await inspector(page, "review");
  let pending: Route | undefined;
  await page.route("**/api/v1/frames/frame-0", (route) => { state.frameRequests.push("frame-0"); pending = route; });
  await failCurrentImage(page);
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByAltText("Frame preview").first().dispatchEvent("error");
  await page.clock.runFor(100);
  expect(state.frameRequests).toEqual(["frame-0"]);
  await json(pending!, { image_url: "/media/new/frame-0" });
  await expect(page.getByAltText("Frame preview").first()).toHaveJSProperty("naturalWidth", 8);
  await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
});

test("late renewal and old Image load cannot overwrite a different frame", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); await imageEvents(page); await inspector(page, "review");
  let pending: Route | undefined;
  await page.route("**/api/v1/frames/frame-0", (route) => { state.frameRequests.push("frame-0"); pending = route; });
  await page.evaluate(() => { const img = (window as Window & { mediaImages: HTMLImageElement[] }).mediaImages.at(-1)!; Object.assign(window, { oldMediaLoad: img.onload, oldMediaImage: img }); });
  await failCurrentImage(page); await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole("button", { name: "Next frame" }).click();
  await expect.poll(() => canvasColor(page)).toEqual([200, 80, 40]);
  await json(pending!, { image_url: "/media/new/frame-0" });
  await page.evaluate(() => { const w = window as Window & { oldMediaLoad: (event: Event) => void; oldMediaImage: HTMLImageElement }; w.oldMediaLoad.call(w.oldMediaImage, new Event("load")); });
  await page.clock.runFor(100);
  await expect.poll(() => canvasColor(page)).toEqual([200, 80, 40]);
});

test("account replacement ignores a delayed previous-account renewal", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); await imageEvents(page); await inspector(page, "review");
  let pending: Route | undefined;
  const identities: string[] = [];
  await page.route("**/api/v1/frames/frame-0", (route) => {
    const auth = route.request().headers().authorization!; identities.push(auth);
    if (auth === "Bearer B") return json(route, { image_url: "/media/new/frame-0" });
    pending = route;
  });
  await failCurrentImage(page); await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole("button", { name: "Close annotation editor" }).click();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Sign Out", exact: true }).click();
  await page.locator('input[type="email"]').fill("B@example.test");
  await page.locator('input[type="password"]').fill("test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  state.expired = true;
  await page.getByRole("link", { name: "Datasets", exact: true }).click();
  await page.getByText("Road footage", { exact: true }).click();
  await expect(page.getByAltText("Frame 0", { exact: true })).toHaveJSProperty("naturalWidth", 8);
  expect(identities).toEqual(["Bearer A", "Bearer B"]);
  const obsolete: string[] = []; page.on("request", (request) => { if (request.url().endsWith("/media/obsolete/frame-0")) obsolete.push(request.url()); });
  await json(pending!, { image_url: "/media/obsolete/frame-0" });
  await page.clock.runFor(200);
  expect(obsolete).toEqual([]);
  await expect(page.getByText("B workspace", { exact: true })).toBeVisible();
  await expect(page.getByAltText("Frame 0", { exact: true })).toHaveJSProperty("naturalWidth", 8);
});

for (const kind of ["Review", "Original", "single", "bulk"] as const) {
  test(`${kind} download renews original artifact A after current pointer moves to B`, async ({ page, downloads }) => {
    const state = await fixture(page, downloads);
    if (kind === "Review") await page.goto("/review/coverage-job");
    else if (kind === "bulk") {
      state.secondJob = true;
      await page.goto("/datasets"); await page.getByRole("button", { name: "Select", exact: true }).click();
      await page.getByRole("checkbox", { name: "Select Road footage" }).check(); await page.getByRole("button", { name: "Export All", exact: true }).click();
    } else {
      await datasets(page); await page.getByRole("button", { name: "Export", exact: true }).click();
      if (kind === "single") await page.getByRole("button", { name: /YOLO Segment/ }).click();
    }
    const link = page.getByRole("link", { name: kind === "Review" ? "Download" : kind === "Original" ? /Original/ : kind === "single" ? "Download YOLO Segment export" : "Download Road footage export", exact: kind !== "Original" });
    await expect(link).toBeVisible();
    state.current = "B"; const exports = state.exportCalls;
    await page.clock.fastForward(16 * 60 * 1000);
    const download = page.waitForEvent("download");
    await link.focus(); await link.press("Enter");
    const artifact = await download;
    expect(artifact.url()).toContain("token=renewed-A");
    expect(artifact.suggestedFilename()).toBe("dataset.zip");
    expect(await readFile((await artifact.path())!)).toEqual(syntheticArchive);
    expect(state.renewTokens).toEqual(["A"]);
    expect(downloads.requests).toEqual(["/dataset.zip?token=renewed-A"]);
    expect(state.exportCalls).toBe(exports);
  });
}

for (const failure of [401, 404, "network"] as const) {
  test(`download ${failure} error stays inline and is retryable`, async ({ page, downloads }) => {
    const state = await fixture(page, downloads); state.downloadError = typeof failure === "number" ? failure : 0; state.networkError = failure === "network";
    await page.goto("/review/coverage-job");
    const link = page.getByRole("link", { name: "Download", exact: true });
    await link.click();
    await expect(page.getByRole("alert")).toBeVisible(); await expect(page).toHaveURL(/\/review\/coverage-job$/);
    state.downloadError = 0; state.networkError = false;
    const download = page.waitForEvent("download"); await link.click(); expect((await download).url()).toContain("token=renewed-A");
    expect(state.exportCalls).toBe(0);
  });
}

test("pending download ignores duplicate activation and late result after logout", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); let pending: Route | undefined;
  await page.route("**/api/v1/jobs/coverage-job/download-url?*", (route) => { state.renewTokens.push("A"); pending = route; });
  await page.goto("/review/coverage-job");
  const link = page.getByRole("link", { name: "Download", exact: true });
  await link.click(); await expect.poll(() => Boolean(pending)).toBe(true); await link.dispatchEvent("click"); expect(state.renewTokens).toEqual(["A"]);
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Sign Out", exact: true }).click();
  const delivered: string[] = []; page.on("download", (download) => delivered.push(download.url()));
  await json(pending!, { download_url: capability("renewed-A") }); await page.clock.runFor(200);
  expect(delivered).toEqual([]); await expect(page).toHaveURL(/\/login$/);
});


test("changing an inspector's supplied URL does not replenish its consumed automatic budget", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); state.permanent = true;
  await page.goto("/review/coverage-job");
  // Use the dataset inspector and delay annotations until after its fallback
  // image and automatically renewed URL have both failed.
  await page.getByRole("link", { name: "Datasets", exact: true }).click();
  await page.getByText("Road footage", { exact: true }).click();
  let annotations: Route | undefined;
  await page.route("**/api/v1/jobs/coverage-job/annotations?*", (route) => { annotations = route; });
  await page.getByAltText("Frame 0", { exact: true }).locator("..").click();
  await expect.poll(() => Boolean(annotations)).toBe(true);
  await expect(page.getByRole("button", { name: "Retry image" }).last()).toBeVisible();
  const attempts = state.frameRequests.filter((id) => id === "frame-0").length;
  await json(annotations!, [annotation(0, "/media/another/frame-0")]);
  await page.clock.runFor(1000);
  await expect(page.getByRole("button", { name: "Retry image" }).last()).toBeVisible();
  expect(state.frameRequests.filter((id) => id === "frame-0")).toHaveLength(attempts);
});

test("image renewal retains the selected annotation", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); await imageEvents(page);
  await page.route("**/api/v1/jobs/coverage-job/annotations?*", (route) => json(route, [
    annotation(0), { ...annotation(0), id: "selected-person", class_name: "person", class_index: 1 },
    ...Array.from({ length: 11 }, (_, i) => annotation(i + 1)),
  ]));
  await inspector(page, "review");
  const selected = page.getByRole("button", { name: /person.*90%/ });
  await selected.click();
  await expect(selected).toHaveClass(/bg-blue-600/)
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await failCurrentImage(page);
  await expect.poll(() => state.frameRequests.filter((id) => id === "frame-0").length).toBe(1);
  await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("130%", { exact: true })).toBeVisible();
});

test("a download without a token fails locally and stays on the review page", async ({ page, downloads }) => {
  const state = await fixture(page, downloads);
  await page.route("**/api/v1/status/coverage-job", (route) => json(route, { ...job(), result_url: "/legacy-dataset.zip" }));
  await page.goto("/review/coverage-job");
  await page.getByRole("link", { name: "Download", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("no token");
  expect(state.renewTokens).toEqual([]);
  await expect(page).toHaveURL(/\/review\/coverage-job$/);
});

test("a new supplied URL during renewal ignores the late result and still offers retry", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); await imageEvents(page); await datasets(page);
  await expect(page.getByAltText("Frame 0", { exact: true })).toHaveJSProperty("naturalWidth", 8);
  let annotations: Route | undefined; let renewal: Route | undefined;
  await page.route("**/api/v1/jobs/coverage-job/annotations?*", (route) => { annotations = route; });
  await page.route("**/api/v1/frames/frame-0", (route) => { state.frameRequests.push("frame-0"); renewal = route; });
  await page.getByAltText("Frame 0", { exact: true }).locator("..").click();
  await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
  await failCurrentImage(page); await expect.poll(() => Boolean(renewal)).toBe(true);
  state.permanent = true;
  await json(annotations!, [annotation(0, "/media/replaced/frame-0")]);
  await expect(page.getByRole("button", { name: "Retry image" }).last()).toBeVisible();
  const obsolete: string[] = []; page.on("request", (request) => { if (request.url().includes("/media/obsolete/")) obsolete.push(request.url()); });
  await json(renewal!, { image_url: "/media/obsolete/frame-0" });
  await page.clock.runFor(200);
  expect(obsolete).toEqual([]);
  expect(state.frameRequests).toEqual(["frame-0"]);
});

test("switching to an unavailable frame clears the previous frame pixels", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); await inspector(page, "review");
  await expect.poll(() => canvasColor(page)).toEqual([40, 120, 200]);
  state.permanent = true;
  await page.getByRole("button", { name: "Next frame" }).click();
  await expect(page.getByRole("button", { name: "Retry image" }).last()).toBeVisible();
  await expect.poll(() => canvasColor(page)).toEqual([0, 0, 0]);
});

test("manual image Retry reloads even when the server returns the same capability URL", async ({ page, downloads }) => {
  const state = await fixture(page, downloads); state.permanent = true;
  await page.route("**/api/v1/frames/frame-0", (route) => {
    state.frameRequests.push("frame-0");
    return json(route, { image_url: "/media/new/frame-0" });
  });
  await datasets(page);
  await expect(page.getByRole("button", { name: "Retry image" }).first()).toBeVisible();
  expect(state.frameRequests.filter((id) => id === "frame-0")).toHaveLength(1);
  state.permanent = false;
  await page.getByRole("button", { name: "Retry image" }).first().click();
  await expect(page.getByAltText("Frame 0", { exact: true })).toHaveJSProperty("naturalWidth", 8);
  expect(state.frameRequests.filter((id) => id === "frame-0")).toHaveLength(2);
});
