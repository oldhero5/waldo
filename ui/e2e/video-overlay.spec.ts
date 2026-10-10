import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { seekDecodedVideo } from "../src/lib/videoPresentation";
import ts from "typescript";
const clip = fileURLToPath(new URL("./fixtures/moving-object.webm", import.meta.url));
const json = (route: Route, value: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
const frames = (coordinateScale = 1) => Array.from({ length: 7 }, (_, i) => {
  const index = i * 9, x = 20 + index * 4, s = coordinateScale;
  return { frame_index: index, timestamp_s: index / 30, frame_duration_s: 1 / 30, source_width: 320 * s, source_height: 180 * s, detections: [{
    class_name: "object", class_index: 0, confidence: 1, track_id: 1,
    bbox: [x * s, 90 * s, (x + 30) * s, 120 * s], mask: [[x * s, 90 * s], [(x + 30) * s, 90 * s], [(x + 30) * s, 120 * s], [x * s, 120 * s]],
  }] };
});
type OverlaySample = { objectX: number; maskX: number; time: number };
async function setup(page: Page, coordinateScale = 1, metadata: { timestamp_method?: string; source_width?: number; source_height?: number } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem("waldo_token", "fixture");
    const samples: { objectX: number; maskX: number; time: number }[] = [];
    (window as Window & { overlaySamples?: typeof samples }).overlaySamples = samples;
    const source = new WeakMap<CanvasRenderingContext2D, { x: number; time: number }>();
    const points = new WeakMap<CanvasRenderingContext2D, { x: number; y: number }[]>();
    const prototype = CanvasRenderingContext2D.prototype;
    const drawImage = prototype.drawImage, beginPath = prototype.beginPath, moveTo = prototype.moveTo, lineTo = prototype.lineTo, fill = prototype.fill;
    prototype.drawImage = function (...args: Parameters<typeof drawImage>) {
      drawImage.apply(this, args);
      if (args[0] instanceof HTMLVideoElement) {
        const data = this.getImageData(0, 0, this.canvas.width, this.canvas.height).data;
        let minX = Infinity;
        for (let y = 0; y < this.canvas.height; y++) for (let x = 0; x < this.canvas.width; x++) {
          const offset = (y * this.canvas.width + x) * 4;
          if (data[offset] > 240 && data[offset + 1] > 240 && data[offset + 2] > 240) minX = Math.min(minX, x);
        }
        source.set(this, { x: minX, time: args[0].currentTime });
      }
    };
    prototype.beginPath = function () { points.set(this, []); beginPath.call(this); };
    prototype.moveTo = function (x, y) { points.get(this)?.push(this.getTransform().transformPoint({ x, y })); moveTo.call(this, x, y); };
    prototype.lineTo = function (x, y) { points.get(this)?.push(this.getTransform().transformPoint({ x, y })); lineTo.call(this, x, y); };
    prototype.fill = function (...args: Parameters<typeof fill>) {
      const polygon = points.get(this), image = source.get(this);
      if (polygon && polygon.length >= 3 && image && Number.isFinite(image.x)) {
        samples.push({ objectX: image.x, maskX: Math.max(0, Math.min(...polygon.map((point) => point.x))), time: image.time });
      }
      fill.apply(this, args);
    };
  });
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return json(route, { id: "fixture", email: "test@example.local", display_name: "Test", workspace_id: "workspace", workspace_name: "Test workspace", role: "admin" });
    if (path.endsWith("/serve/status")) return json(route, { loaded: true, model_id: "fixture-model", model_name: "Fixture", class_names: ["object"], task_type: "segment" });
    if (path.endsWith("/predict/video")) return json(route, { frames: frames(coordinateScale).map((frame) => ({ ...frame, ...metadata })), total_frames: 7, model_id: "fixture-model" });
    return json(route, []);
  });
  await page.goto("/deploy/test");
  await page.getByRole("button", { name: "Video", exact: true }).click();
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles(clip);
  await page.getByRole("button", { name: "Track Objects", exact: true }).click();
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
  await expect(page.locator("canvas")).toHaveAttribute("width", "320");
}
const samples = (page: Page) => page.evaluate(() => (window as Window & { overlaySamples?: OverlaySample[] }).overlaySamples || []);

// Control the native decoder boundary while exercising the real seek helper.
class ControlledVideo extends EventTarget {
  currentSrc = "fixture.webm";
  readyState = 2;
  seeking = false;
  duration = 2;
  onSeek: (() => void) | null = null;
  private time = 0;
  private nextCallback = 0;
  readonly callbacks = new Map<number, VideoFrameRequestCallback>();
  get currentTime() { return this.time; }
  set currentTime(value: number) { this.time = value; this.seeking = true; this.onSeek?.(); }
  requestVideoFrameCallback(callback: VideoFrameRequestCallback) {
    const id = ++this.nextCallback;
    this.callbacks.set(id, callback);
    return id;
  }
  cancelVideoFrameCallback(id: number) { this.callbacks.delete(id); }
  seeked() { this.seeking = false; this.dispatchEvent(new Event("seeked")); }
  present(mediaTime: number) {
    const pending = [...this.callbacks];
    this.callbacks.clear();
    for (const [, callback] of pending) callback(0, { mediaTime } as VideoFrameCallbackMetadata);
  }
}

for (const first of ["seeked", "presentation"] as const) {
  test(`decoded seek waits for the requested presentation when ${first} arrives first`, async () => {
    const video = new ControlledVideo();
    const controller = new AbortController();
    let complete = false;
    const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, controller.signal, 0.6).then(() => { complete = true; });
    if (first === "seeked") video.seeked(); else video.present(0.6);
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(complete).toBe(false);
    if (first === "seeked") {
      video.present(0.3); // A queued callback for the preceding frame is insufficient.
      await new Promise<void>((resolve) => setImmediate(resolve));
      expect(complete).toBe(false);
      video.present(0.6);
    } else video.seeked();
    await pending;
    expect(complete).toBe(true);
  });
}

test("caller cancellation preserves bounded decoder observation for a same-frame retry", async () => {
  const video = new ControlledVideo();
  const controller = new AbortController();
  const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, controller.signal, 0.6);
  expect(video.callbacks.size).toBe(1);
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  expect(video.callbacks.size).toBe(1);
  video.seeked();
  video.present(0.6);
  await seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6);
  expect(video.callbacks.size).toBe(0);
});

test("caller cancellation after native confirmation keeps the confirmed retry available", async () => {
  const video = new ControlledVideo();
  const controller = new AbortController();
  const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, controller.signal, 0.6);
  video.seeked(); video.present(0.6);
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  await seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6);
  expect(video.callbacks.size).toBe(0);
});

test("retrying an aborted seek at the same time still waits for presentation", async () => {
  const video = new ControlledVideo();
  const controller = new AbortController();
  const aborted = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, controller.signal, 0.6);
  controller.abort();
  await expect(aborted).rejects.toMatchObject({ name: "AbortError" });
  video.seeked(); // The decoder completes after the first caller stopped listening.
  let complete = false;
  const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6).then(() => { complete = true; });
  await new Promise<void>((resolve) => setImmediate(resolve));
  expect(complete).toBe(false);
  video.seeked();
  video.present(0.6);
  await pending;
});

test("an unconfirmed paused seek recovers when the same frame produces no callback", async () => {
  const video = new ControlledVideo();
  const controller = new AbortController();
  const aborted = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, controller.signal, 0.6);
  controller.abort();
  await expect(aborted).rejects.toMatchObject({ name: "AbortError" });
  video.seeked();
  video.present(0.6); // Original presentation finishes after its caller aborts.
  let displayedTime = 0.605;
  video.onSeek = () => {
    const time = video.currentTime;
    queueMicrotask(() => {
      video.seeked();
      if (displayedTime !== time) {
        displayedTime = time;
        video.present(time === 0.605 ? 0.6 : time);
      }
    });
  };
  const recovery = new AbortController();
  let complete = false;
  const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, recovery.signal, 0.6).then(() => { complete = true; });
  try {
    await expect.poll(() => complete).toBe(true);
    expect(video.currentTime).toBe(0.605);
  } finally { recovery.abort(); await pending.catch(() => {}); }
});

test("a different seek cancels the old presentation observer and rejects stale metadata", async () => {
  const video = new ControlledVideo();
  const old = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.305, new AbortController().signal, 0.3);
  const oldResult = expect(old).rejects.toMatchObject({ name: "AbortError" });
  let complete = false;
  const current = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6).then(() => { complete = true; });
  await oldResult;
  expect(video.callbacks.size).toBe(1);
  video.seeked();
  video.present(0.3);
  await new Promise<void>((resolve) => setImmediate(resolve));
  expect(complete).toBe(false);
  video.present(0.6);
  await current;
});

test("presentation confirmation never crosses a video source change", async () => {
  const video = new ControlledVideo();
  const first = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6);
  video.seeked(); video.present(0.6); await first;
  video.currentSrc = "other.webm";
  video.seeking = true;
  let complete = false;
  const next = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6).then(() => { complete = true; });
  await new Promise<void>((resolve) => setImmediate(resolve));
  expect(complete).toBe(false);
  video.seeked(); video.present(0.6); await next;
});

for (const first of ["seeked", "presentation"] as const) {
  test(`approximate seek supports ${first} first without claiming exact source PTS`, async () => {
    const video = new ControlledVideo();
    let complete = false;
    const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, null).then(() => { complete = true; });
    if (first === "seeked") video.seeked(); else video.present(0.58);
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(complete).toBe(false);
    if (first === "seeked") video.present(0.58); else video.seeked();
    await pending;
  });
}

test("a decoded seek to the current time needs no new presentation", async () => {
  const video = new ControlledVideo();
  await seekDecodedVideo(video as unknown as HTMLVideoElement, 0, new AbortController().signal, 0);
  expect(video.callbacks.size).toBe(0);
});

test("decoded seeking supports browsers without presentation callbacks", async () => {
  const video = new ControlledVideo();
  Object.defineProperty(video, "requestVideoFrameCallback", { value: undefined });
  const pending = seekDecodedVideo(video as unknown as HTMLVideoElement, 0.605, new AbortController().signal, 0.6);
  video.seeked();
  await pending;
});

test("a real paused decoder can retry an aborted seek at the same time", async ({ page }) => {
  await setup(page);
  const source = readFileSync(fileURLToPath(new URL("../src/lib/videoPresentation.ts", import.meta.url)), "utf8");
  await page.route("**/fixture-video-presentation.js", (route) => route.fulfill({
    contentType: "text/javascript", body: ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText,
  }));
  const result = await page.evaluate(async () => {
    const moduleUrl = "/fixture-video-presentation.js";
    const { seekDecodedVideo: seek } = await import(moduleUrl);
    const video = document.querySelector("video")!;
    const events: { event: string; time: number; mediaTime?: number; seeking: boolean }[] = [];
    for (const event of ["seeking", "seeked"]) video.addEventListener(event, () => events.push({ event, time: video.currentTime, seeking: video.seeking }));
    const requestPresentation = video.requestVideoFrameCallback.bind(video);
    video.requestVideoFrameCallback = (callback) => requestPresentation((now, metadata) => {
      events.push({ event: "presentation", time: video.currentTime, mediaTime: metadata.mediaTime, seeking: video.seeking });
      callback(now, metadata);
    });
    const controller = new AbortController();
    const settled = new Promise<void>((resolve) => video.addEventListener("seeked", () => resolve(), { once: true }));
    const pending = seek(video, 0.605, controller.signal, 0.6);
    controller.abort();
    const error = await pending.catch((value: Error) => value.name);
    await settled;
    try { await seek(video, 0.605, new AbortController().signal, 0.6); }
    catch (error) { throw new Error(`${error}: ${JSON.stringify(events)}`); }
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 180;
    const context = canvas.getContext("2d")!;
    context.drawImage(video, 0, 0);
    const row = context.getImageData(0, 100, 320, 1).data;
    let objectX = -1;
    for (let x = 0; x < 320; x++) if (row[x * 4] > 240 && row[x * 4 + 1] > 240) { objectX = x; break; }
    return { error, objectX };
  });
  expect(result).toEqual({ error: "AbortError", objectX: 92 });
});

test("sampled masks stay on the moving object during real decoded playback", async ({ page }) => {
  await setup(page);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.ended), { timeout: 6000 }).toBe(true);
  const painted = await samples(page);
  expect(painted.length).toBeGreaterThan(3);
  expect(Math.max(...painted.map((sample) => Math.abs(sample.maskX - sample.objectX)))).toBeLessThanOrEqual(2);
  await expect(page.getByText(/Masks appear only on assessed frames/)).toBeVisible();
});

test("rapid seeking never paints an earlier request's mask over the latest decoded frame", async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { const data = (window as Window & { overlaySamples?: OverlaySample[] }).overlaySamples; if (data) data.length = 0; });
  const slider = page.locator('input[type="range"][max="6"]');
  await slider.evaluate((element: HTMLInputElement) => {
    for (const value of [5, 1, 4]) { element.value = String(value); element.dispatchEvent(new Event("input", { bubbles: true })); }
  });
  await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.seeking)).toBe(false);
  await page.waitForTimeout(350); // Exceeds the old premature-draw timeout.
  const painted = await samples(page);
  expect(painted.length).toBeGreaterThan(0);
  expect(Math.max(...painted.map((sample) => Math.abs(sample.maskX - sample.objectX)))).toBeLessThanOrEqual(2);
  if (process.env.WALDO_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.WALDO_SCREENSHOT_DIR}/waldo-decoded-mask-alignment.png`, fullPage: true });
});

test("inference pixel coordinates scale with source dimensions through resize and zoom", async ({ page }) => {
  await setup(page, 2);
  await page.setViewportSize({ width: 700, height: 800 });
  await page.locator("canvas").hover();
  await page.mouse.wheel(0, -100);
  await expect(page.getByRole("button", { name: "Reset", exact: true })).toBeVisible();
  const painted = await samples(page);
  expect(painted.length).toBeGreaterThan(0);
  expect(Math.max(...painted.map((sample) => Math.abs(sample.maskX - sample.objectX)))).toBeLessThanOrEqual(2);
});

test("panning clears video and mask pixels from the exposed canvas edge", async ({ page }) => {
  await setup(page);
  const canvas = page.locator("canvas");
  // The paused fixture has its white object and red mask at x=20..50, y=90..120.
  await expect.poll(() => canvas.evaluate((element: HTMLCanvasElement) => {
    const [red, green, , alpha] = element.getContext("2d")!.getImageData(30, 100, 1, 1).data;
    return alpha === 255 && red > green;
  })).toBe(true);
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + box.width / 4, box.y + box.height / 2);
  await page.mouse.up();
  // Panning by 80 source pixels exposes x=0..79. It must contain no old image or mask.
  await expect.poll(() => canvas.evaluate((element: HTMLCanvasElement) => {
    const pixels = element.getContext("2d")!.getImageData(0, 0, 79, element.height).data;
    let opaque = 0;
    for (let offset = 3; offset < pixels.length; offset += 4) if (pixels[offset] !== 0) opaque++;
    return opaque;
  })).toBe(0);
});

test("feedback snapshots retain the inference coordinate dimensions", async ({ page }) => {
  await setup(page, 2);
  let submitted: { model_id?: string; bbox: number[]; frame_image_b64: string; source_filename: string }[] = [];
  await page.route("**/api/v1/feedback/batch", async (route) => {
    submitted = route.request().postDataJSON().items;
    await json(route, []);
  });
  await page.getByText("Flag", { exact: true }).click();
  await page.getByRole("button", { name: "Submit for Retraining", exact: true }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0].model_id).toBe("fixture-model");
  expect(submitted[0].source_filename).toBe("moving-object.webm");
  expect(submitted[0].bbox).toEqual([40, 180, 100, 240]);
  const size = await page.evaluate(async (b64) => {
    const image = new Image();
    image.src = `data:image/jpeg;base64,${b64}`;
    await image.decode();
    return [image.naturalWidth, image.naturalHeight];
  }, submitted[0].frame_image_b64);
  expect(size).toEqual([640, 360]);
});

test("approximate timing never invents aligned masks", async ({ page }) => {
  await setup(page, 1, { timestamp_method: "frame_index/fps" });
  await expect(page.getByText(/Source timestamps are approximate/)).toBeVisible();
  expect(await samples(page)).toHaveLength(0);
});

test("incompatible rotated coordinate dimensions block the mask", async ({ page }) => {
  await setup(page, 1, { source_width: 180, source_height: 320 });
  await expect(page.getByRole("alert")).toContainText("Overlay dimensions do not match");
  expect(await samples(page)).toHaveLength(0);
});

for (const approximate of [false, true]) {
  test(approximate ? "approximate comparison navigation never invents aligned masks" : "comparison never borrows masks from a different model's assessment time", async ({ page }) => {
    await setup(page);
    const dense = frames().map((frame) => approximate ? { ...frame, timestamp_s: frame.timestamp_s + 0.02, timestamp_method: "frame_index/fps" } : frame);
    const sparse = dense.filter((_, index) => index % 2 === 0);
    await page.route("**/api/v1/comparisons/**", (route) => {
      if (new URL(route.request().url()).pathname.endsWith("/run")) return json(route, { session_id: "fixture-compare" });
      return json(route, { status: "completed", session_id: "fixture-compare", results: {
        a: { dets: [], frames: dense, latency: 1, error: null }, b: { dets: [], frames: sparse, latency: 1, error: null },
      } });
    });
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    if (approximate) await page.evaluate(() => { (window as Window & { overlaySamples?: OverlaySample[] }).overlaySamples!.length = 0; });
    await page.getByRole("combobox").first().selectOption("__sam3.1__");
    await page.locator('input[type="file"][accept="image/*,video/*"]').setInputFiles(clip);
    await page.getByRole("button", { name: "Compare Models", exact: true }).click();
    await expect(page.locator("canvas")).toHaveCount(2);
    const slider = page.locator('input[type="range"][max="6"]');
    await slider.evaluate((element: HTMLInputElement) => { element.value = "1"; element.dispatchEvent(new Event("input", { bubbles: true })); });
    await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.3);
    await expect(page.getByText(/0 dets/)).toHaveCount(approximate ? 2 : 1);
    await page.getByRole("button", { name: "Play", exact: true }).click();
    await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(1.79);
    await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
    const painted = await samples(page);
    if (approximate) expect(painted).toHaveLength(0);
    else {
      expect(painted.length).toBeGreaterThan(5);
      expect(Math.max(...painted.map((sample) => Math.abs(sample.maskX - sample.objectX)))).toBeLessThanOrEqual(2);
    }
  });
}

async function mockPredictionTransport(page: Page, mode: "close" | "silent" | "completed") {
  await page.addInitScript(({ mode, frame }) => {
    class FakeSocket {
      static CONNECTING = 0; static OPEN = 1;
      readyState = 1;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() {
        if (mode !== "silent") setTimeout(() => {
          this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(frame) }));
          if (mode === "close") { this.readyState = 3; this.onclose?.(); }
          else this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ status: "completed", total_frames: 7 }) }));
        }, 100);
      }
      close() { this.readyState = 3; }
    }
    window.WebSocket = FakeSocket as unknown as typeof WebSocket;
  }, { mode, frame: frames()[1] });
}

for (const mode of ["close", "silent", "completed"] as const) {
  test(`durable video result recovers missed frames after ${mode} socket`, async ({ page }) => {
    await mockPredictionTransport(page, mode);
    await setup(page);
    let polls = 0;
    await page.route("**/api/v1/predict/video**", (route) => {
      if (new URL(route.request().url()).pathname.endsWith("/video")) return json(route, { session_id: "durable-video", frame_count: 7 });
      polls++;
      if (polls === 1) return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ status: "running", session_id: "durable-video" }) });
      return json(route, { status: "completed", session_id: "durable-video", frames: frames(), total_frames: 7, model_id: "fixture-model" });
    });
    await page.getByRole("button", { name: "Track Objects", exact: true }).click();
    await expect(page.getByText("7 frames · 1 tracked objects · 7 total detections · Scroll to zoom, drag to pan")).toBeVisible();
    await expect(page.getByRole("button", { name: "Track Objects", exact: true })).toBeEnabled();
    expect(polls).toBeGreaterThanOrEqual(2);
    const stoppedAt = polls;
    await page.waitForTimeout(2100);
    expect(polls).toBe(stoppedAt);
  });
}

test("durable video failure stops processing when no socket terminal arrives", async ({ page }) => {
  await mockPredictionTransport(page, "silent");
  await setup(page);
  let polls = 0;
  await page.route("**/api/v1/predict/video**", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/video")) return json(route, { session_id: "failed-video", frame_count: 0 });
    polls++;
    return json(route, { status: "failed", session_id: "failed-video", error: "Inference input download failed" });
  });
  await page.getByRole("button", { name: "Track Objects", exact: true }).click();
  await expect(page.getByText("Inference input download failed", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Track Objects", exact: true })).toBeEnabled();
  await page.waitForTimeout(2100);
  expect(polls).toBe(1);
});

test("durable comparison failure stops polling and clears the saved pending session", async ({ page }) => {
  await setup(page);
  let polls = 0;
  await page.route("**/api/v1/comparisons/**", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/run")) return json(route, { session_id: "failed-compare" });
    polls++;
    return json(route, { status: "failed", session_id: "failed-compare", error: "Comparison input download failed", results: {
      a: { dets: [], frames: null, latency: 0, error: "Comparison input download failed" },
      b: { dets: [], frames: null, latency: 0, error: "Comparison input download failed" },
    } });
  });
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await page.getByRole("combobox").first().selectOption("__sam3.1__");
  await page.locator('input[type="file"][accept="image/*,video/*"]').setInputFiles(clip);
  await page.getByRole("button", { name: "Compare Models", exact: true }).click();
  await expect(page.getByText("Comparison input download failed", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Compare Models", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => sessionStorage.getItem("waldo_compare_session"))).toBeNull();
  await page.waitForTimeout(2100);
  expect(polls).toBe(1);
});

test("playground SVG masks match the presented decoded video frame", async ({ page }) => {
  await setup(page);
  await page.route("**/fixture.webm", (route) => route.fulfill({ contentType: "video/webm", body: readFileSync(clip) }));
  await page.route("**/api/v1/projects**", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/videos")) return json(route, [{ id: "clip", filename: "Synthetic moving object", duration_s: 2, width: 320, height: 180, fps: 30, url: "/fixture.webm" }]);
    return json(route, [{ id: "collection", name: "Synthetic footage", video_count: 1 }]);
  });
  await page.route("**/api/v1/label/preview", (route) => json(route, {
    frames: frames().map((frame) => ({ ...frame, frame_idx: frame.frame_index, width: 320, height: 180, image_b64: "", detections: frame.detections.map((detection) => ({
      bbox: detection.bbox, score: detection.confidence, label: detection.class_name, track_id: detection.track_id,
      polygon: detection.mask.flatMap(([x, y]) => [x / 320, y / 180]),
    })) })), total_detections: 7, unique_track_count: 1, fps: 30, video_duration_s: 2, mode: "window",
  }));
  await page.goto("/playground");
  await page.getByPlaceholder("pothole", { exact: true }).fill("object");
  await page.getByRole("slider", { name: "Window duration", exact: true }).fill("2");
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
  await page.locator("video").evaluate((video: HTMLVideoElement) => {
    const records: { objectX: number; maskX: number }[] = [];
    (window as Window & { svgSamples?: typeof records }).svgSamples = records;
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 180;
    const context = canvas.getContext("2d")!;
    const check = () => video.requestVideoFrameCallback(() => {
      context.drawImage(video, 0, 0);
      const polygon = video.parentElement?.querySelector("svg polygon");
      if (polygon) {
        const pixels = context.getImageData(0, 90, 320, 1).data;
        let objectX = -1;
        for (let x = 0; x < 320; x++) if (pixels[x * 4] > 240 && pixels[x * 4 + 1] > 240) { objectX = x; break; }
        const maskX = Math.min(...(polygon.getAttribute("points") || "").split(" ").map((point) => Number(point.split(",")[0])));
        if (objectX >= 0) records.push({ objectX, maskX });
      }
      check();
    });
    check();
  });
  await page.getByRole("button", { name: "Play preview", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as Window & { svgSamples?: unknown[] }).svgSamples?.length || 0), { timeout: 7000 }).toBeGreaterThan(3);
  await page.getByRole("button", { name: "Pause preview", exact: true }).click();
  const recorded = await page.evaluate(() => (window as Window & { svgSamples?: { objectX: number; maskX: number }[] }).svgSamples || []);
  expect(Math.max(...recorded.map((sample) => Math.abs(sample.maskX - sample.objectX)))).toBeLessThanOrEqual(2);
});

async function openMediaPreview(page: Page) {
  const media = {
    now: Date.parse("2026-10-09T12:00:00Z"),
    lists: 0,
    denied: 0,
    unavailable: false,
    rejectMedia: false,
    holdList: 0,
  };
  let releaseList!: () => void;
  let startedList!: () => void;
  const heldList = new Promise<void>((resolve) => { releaseList = resolve; });
  const requestedList = new Promise<void>((resolve) => { startedList = resolve; });
  await page.clock.setFixedTime(media.now);
  await page.addInitScript(() => localStorage.setItem("waldo_token", "fixture"));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return json(route, {
      id: "fixture", email: "test@example.local", display_name: "Test", avatar_url: null,
      workspace_id: "workspace", workspace_name: "Test workspace", role: "admin",
    });
    if (path === "/api/v1/projects") return json(route, [{
      id: "collection", name: "Synthetic footage", video_count: 2, created_at: "2026-10-09T12:00:00Z",
    }]);
    if (path === "/api/v1/projects/collection/videos") {
      const generation = ++media.lists;
      if (media.holdList === generation) { startedList(); await heldList; }
      if (media.unavailable) return route.fulfill({ status: 503, body: "Playback renewal unavailable" });
      return json(route, ["video-a", "video-b"].map((id) => ({
        id, filename: `${id}.webm`, duration_s: 2, width: 320, height: 180, fps: 30,
        frame_count: 60, created_at: "2026-10-09T12:00:00Z",
        url: `/fixture-preview/${id}.webm?generation=${generation}&expires=${media.now + 15 * 60 * 1000}`,
      })));
    }
    if (path === "/api/v1/label/preview") return json(route, {
      frames: frames().map((frame) => ({
        ...frame, frame_idx: frame.frame_index, width: 320, height: 180, image_b64: "",
        detections: frame.detections.map((detection) => ({
          bbox: detection.bbox, score: detection.confidence, label: detection.class_name,
          track_id: detection.track_id, polygon: detection.mask.flatMap(([x, y]) => [x / 320, y / 180]),
        })),
      })),
      total_detections: 7, unique_track_count: 1, fps: 30, video_duration_s: 2, mode: "window",
    });
    return json(route, []);
  });
  await page.route("**/fixture-preview/**", (route) => {
    const expires = Number(new URL(route.request().url()).searchParams.get("expires"));
    if (media.rejectMedia || expires <= media.now) {
      media.denied++;
      return route.fulfill({ status: 401, body: "Download capability expired" });
    }
    const bytes = readFileSync(clip);
    const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || "");
    if (!range) return route.fulfill({ contentType: "video/webm", headers: { "Accept-Ranges": "bytes" }, body: bytes });
    const start = Number(range[1]);
    const end = Math.min(bytes.length - 1, range[2] ? Number(range[2]) : bytes.length - 1);
    return route.fulfill({
      status: 206, contentType: "video/webm",
      headers: { "Accept-Ranges": "bytes", "Content-Range": `bytes ${start}-${end}/${bytes.length}` },
      body: bytes.subarray(start, end + 1),
    });
  });
  await page.goto("/playground");
  await expect(page.getByLabel("Video", { exact: true })).toHaveValue("video-a");
  await page.getByPlaceholder("pothole", { exact: true }).fill("object");
  await page.getByRole("slider", { name: "Window duration", exact: true }).fill("2");
  return { media, requestedList, releaseList };
}

test("playground renews cached capabilities after an idle page and on repeated previews", async ({ page }) => {
  const { media } = await openMediaPreview(page);
  for (const generation of [2, 3]) {
    media.now += 16 * 60 * 1000;
    await page.clock.setFixedTime(media.now);
    await page.getByRole("button", { name: "Run preview", exact: true }).click();
    await expect(page.locator("video")).toHaveAttribute("src", new RegExp(`video-a.webm\\?generation=${generation}&`));
    await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
    await expect(page.locator("svg polygon")).toHaveCount(1);
    await page.getByRole("button", { name: "Play preview", exact: true }).click();
    await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.1);
    await page.getByRole("button", { name: "Pause preview", exact: true }).click();
  }
  expect(media.denied).toBe(0);
});

test("playground recovers expired playback for the displayed source and preserves its position", async ({ page }) => {
  const { media } = await openMediaPreview(page);
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
  await page.locator("video").evaluate((video: HTMLVideoElement) => { video.currentTime = 0.9; });
  await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.seeking)).toBe(false);
  await expect(page.getByRole("slider", { name: "Preview playback time" })).toHaveValue("0.9");
  await expect(page.locator("svg polygon")).toHaveCount(1);
  await page.getByLabel("Video", { exact: true }).selectOption("video-b");
  media.now += 16 * 60 * 1000;
  await page.clock.setFixedTime(media.now);
  await page.locator("video").evaluate((video: HTMLVideoElement) => video.load());
  await expect(page.locator("video")).toHaveAttribute("src", /video-a.webm\?generation=3&/);
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
  await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeCloseTo(0.9, 1);
  await expect(page.locator("svg polygon")).toHaveCount(1);
  expect(media.denied).toBeGreaterThan(0);
});

test("playground keeps inference evidence when capability renewal fails and allows retry", async ({ page }) => {
  const { media } = await openMediaPreview(page);
  media.unavailable = true;
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Playback renewal unavailable");
  await expect(page.getByRole("button", { name: "Start full job", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry source playback", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await expect.poll(() => media.lists).toBe(3);
  await expect(page.getByRole("alert")).toContainText("Playback renewal unavailable");
  await page.waitForTimeout(500);
  expect(media.lists).toBe(3);
  media.unavailable = false;
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("playground stops automatic renewal when refreshed media also fails", async ({ page }) => {
  const { media } = await openMediaPreview(page);
  media.rejectMedia = true;
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Source video could not be loaded");
  expect(media.lists).toBe(3);
  await page.waitForTimeout(500);
  expect(media.lists).toBe(3);
  await expect(page.getByRole("button", { name: "Start full job", exact: true })).toBeVisible();
  media.rejectMedia = false;
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
});

test("a late playground capability lookup cannot replace a newer preview source", async ({ page }) => {
  const { media, requestedList, releaseList } = await openMediaPreview(page);
  media.holdList = 2;
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect.poll(() => media.lists).toBe(2);
  await requestedList;
  await expect(page.locator("svg polygon")).toHaveCount(0);
  await page.getByLabel("Video", { exact: true }).selectOption("video-b");
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("video")).toHaveAttribute("src", /video-b.webm\?generation=3&/);
  await expect(page.locator("video")).toHaveJSProperty("videoWidth", 320);
  releaseList();
  await page.waitForTimeout(100);
  await expect(page.locator("video")).toHaveAttribute("src", /video-b.webm\?generation=3&/);
});
