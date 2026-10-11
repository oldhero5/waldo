import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
// Synthetic H.264 fixtures from moving-object.webm: no-edit-list nonzero start, malformed offset, and audio 1s before video.
const ordinary = readFileSync(
  fileURLToPath(new URL("./fixtures/moving-object.webm", import.meta.url)),
);
const nonzero = readFileSync(
  fileURLToPath(new URL("./fixtures/nonzero-start.mp4", import.meta.url)),
);
const audioFirst = readFileSync(
  fileURLToPath(new URL("./fixtures/audio-first.mp4", import.meta.url)),
);
const malformed = readFileSync(
  fileURLToPath(new URL("./fixtures/malformed-timing.mp4", import.meta.url)),
);
const image = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFklEQVR4nGPUqDjxnwEPYMInOXwUAABKSgJ4RfEpNAAAAABJRU5ErkJggg==",
  "base64",
);
const json = (route: Route, body: unknown) =>
  route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
const finding = (id: string, source: string, time: number | null, extra = {}) => ({
  id,
  frame_id: `frame-${id}`,
  source_video_id: source,
  frame_url: "/saved.png",
  class_name: "car",
  class_index: 0,
  polygon: [0.1, 0.1, 0.3, 0.1, 0.3, 0.3],
  bbox: null,
  track_id: 1,
  confidence: 0.9,
  status: "pending",
  timestamp_s: time,
  timestamp_method: "source_pts",
  ...extra,
});
async function setup(
  page: Page,
  annotations = [
    finding("a", "clip-a", 0.6),
    finding("b", "clip-a", 1.2),
    finding("c", "clip-b", 0.6),
  ],
  nonzeroStart: boolean | "audio" | "malformed" = false,
) {
  const state = {
    requests: [] as string[],
    changes: [] as string[],
    broken: false,
    metadataError: false,
  };
  await page.addInitScript(() => {
    if (!localStorage.getItem("waldo_token")) localStorage.setItem("waldo_token", "A");
  });
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me"))
      return json(route, {
        id: "user",
        email: "test@example.test",
        display_name: "Test",
        avatar_url: null,
        workspace_id: "workspace",
        workspace_name: "Test workspace",
        role: "admin",
      });
    if (path.endsWith("/auth/login"))
      return json(route, { access_token: "B", refresh_token: "refresh-B" });
    if (path === "/api/v1/status/job")
      return json(route, {
        job_id: "job",
        status: "completed",
        text_prompt: "car",
        result_url: null,
        processing_summary: null,
      });
    if (path.endsWith("/sources"))
      return state.metadataError
        ? route.fulfill({ status: 503, body: "Unavailable" })
        : json(route, [
            {
              id: "clip-a",
              filename: "A.mp4",
              duration_s: nonzeroStart === "audio" ? 3 : 2,
              has_source_video: true,
            },
            {
              id: "clip-b",
              filename: "B.mp4",
              duration_s: nonzeroStart === "audio" ? 3 : 2,
              has_source_video: true,
            },
            {
              id: "still",
              filename: "__standalone_images__",
              duration_s: null,
              has_source_video: false,
            },
          ]);
    if (path.endsWith("/playback")) {
      const source = path.split("/").at(-2)!;
      state.requests.push(source);
      return json(route, {
        video_id: source,
        url: `/source/${source}?generation=${state.requests.length}`,
      });
    }
    if (path.endsWith("/stats"))
      return json(route, {
        total_annotations: annotations.length,
        total_frames: annotations.length,
        annotated_frames: annotations.length,
        empty_frames: 0,
        annotation_density: 1,
        by_class: [{ name: "car", count: annotations.length }],
        by_status: { pending: annotations.length, accepted: 0, rejected: 0 },
      });
    if (path.endsWith("/annotations")) return json(route, annotations);
    if (path.startsWith("/api/v1/annotations/")) {
      const id = path.split("/").at(-1)!;
      state.changes.push(id);
      return json(
        route,
        annotations.find((a) => a.id === id),
      );
    }
    if (path.endsWith("/segment-points"))
      return json(route, {
        polygons: [[0.1, 0.1, 0.3, 0.1, 0.3, 0.3]],
        scores: [0.9],
        bboxes: [[0.1, 0.1, 0.3, 0.3]],
      });
    return json(route, []);
  });
  await page.route("**/saved.png", (route) =>
    route.fulfill({ contentType: "image/png", body: image }),
  );
  await page.route("**/source/**", (route) => {
    if (state.broken) return route.fulfill({ status: 404, body: "Missing source" });
    const bytes =
        nonzeroStart === "audio"
          ? audioFirst
          : nonzeroStart === "malformed"
            ? malformed
            : nonzeroStart
              ? nonzero
              : ordinary,
      range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || "");
    const start = range ? Number(range[1]) : 0,
      end = Math.min(bytes.length - 1, range?.[2] ? Number(range[2]) : bytes.length - 1);
    return route.fulfill({
      status: range ? 206 : 200,
      contentType: nonzeroStart ? "video/mp4" : "video/webm",
      headers: {
        "Accept-Ranges": "bytes",
        ...(range ? { "Content-Range": `bytes ${start}-${end}/${bytes.length}` } : {}),
      },
      body: bytes.subarray(start, end + 1),
    });
  });
  await page.goto("/review/job");
  return state;
}
const inspect = (page: Page, id: string) =>
  page.getByRole("button", { name: `Inspect finding ${id}`, exact: true }).click();
const source = (page: Page) => page.getByLabel("Source video", { exact: true });
async function decodedX(page: Page) {
  return source(page).evaluate((video: HTMLVideoElement) => {
    const canvas = document.createElement("canvas");
    canvas.width = 320;
    canvas.height = 180;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    const row = ctx.getImageData(0, 100, 320, 1).data;
    for (let x = 0; x < 320; x++)
      if (row[x * 4] > 230 && row[x * 4 + 1] > 230 && row[x * 4 + 2] > 230) return x;
    return -1;
  });
}
async function atTime(page: Page, time: number) {
  await expect(source(page)).toHaveJSProperty("videoWidth", 320);
  await expect
    .poll(() => source(page).evaluate((video: HTMLVideoElement) => video.currentTime))
    .toBeCloseTo(time, 2);
  await expect
    .poll(() => source(page).evaluate((video: HTMLVideoElement) => video.seeking))
    .toBe(false);
}
for (const nonzeroStart of [false, true])
  test(`saved finding seeks decoded ${nonzeroStart ? "nonzero-start" : "ordinary"} source and reopens after reload`, async ({
    page,
  }) => {
    await setup(page, undefined, nonzeroStart);
    await inspect(page, "a");
    await atTime(page, nonzeroStart ? 0.666667 : 0.6);
    if (!nonzeroStart && process.env.WALDO_SCREENSHOT_DIR) {
      mkdirSync(process.env.WALDO_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({
        path: `${process.env.WALDO_SCREENSHOT_DIR}/synthetic-inspector-desktop.png`,
      });
    }
    await expect.poll(() => decodedX(page)).toBeGreaterThanOrEqual(88);
    expect(await decodedX(page)).toBeLessThanOrEqual(96);
    await page.getByRole("button", { name: "Next frame", exact: true }).click();
    await atTime(page, nonzeroStart ? 1.266667 : 1.2);
    await expect.poll(() => decodedX(page)).toBeGreaterThanOrEqual(160);
    await page.getByRole("button", { name: "Next frame", exact: true }).click();
    await atTime(page, nonzeroStart ? 0.666667 : 0.6);
    await expect(source(page)).toHaveAttribute("src", /clip-b/);
    await page.reload();
    await inspect(page, "a");
    await atTime(page, nonzeroStart ? 0.666667 : 0.6);
  });
test("row Inspect selects its finding in the saved frame", async ({ page }) => {
  await setup(page, [
    finding("a", "clip-a", 0.6),
    finding("person", "clip-a", 0.6, { frame_id: "frame-a", class_name: "person", class_index: 1 }),
  ]);
  await inspect(page, "person");
  await expect(page.getByRole("button", { name: /person.*90%/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByRole("region", { name: "Source playback" })).toContainText("person");
});
for (const [time, method, label, seek] of [
  [0, "source_pts", "Recorded time", 0],
  [0.6, "frame_index/fps", "Approximate time", 0.6],
  [0.6, "resampled_ordinal/fps", "Approximate time", 0.6],
  [0.6, "unknown", "Time unknown", 0],
  [null, "source_pts", "Time unknown", 0],
  [-1, "source_pts", "Invalid time", 0],
  [50, "source_pts", "outside this source", 0],
] as const)
  test(`timing ${time}/${method} has explicit seek behavior`, async ({ page }) => {
    await setup(page, [finding("a", "clip-a", time, { timestamp_method: method })]);
    await inspect(page, "a");
    await atTime(page, seek);
    await expect(page.getByRole("region", { name: "Source playback" })).toContainText(label);
  });
test("source and class filters compose with confidence/status and scope bulk review", async ({
  page,
}) => {
  const state = await setup(page, [
    finding("a", "clip-a", 0.6),
    finding("b", "clip-b", 0.6),
    finding("person", "clip-a", 0.6, { class_name: "person" }),
    finding("low", "clip-a", 0.6, { confidence: 0.3 }),
    finding("accepted", "clip-a", 0.6, { status: "accepted" }),
  ]);
  await page.getByLabel("Source clip", { exact: true }).selectOption("clip-a");
  await page.getByLabel("Class", { exact: true }).selectOption("car");
  await page.getByRole("slider").fill("0.5");
  await page.getByRole("button", { name: "Pending1", exact: true }).click();
  await expect(page.getByRole("button", { name: /Inspect finding/ })).toHaveCount(1);
  await page.getByRole("button", { name: /Accept All/ }).click();
  await expect.poll(() => state.changes).toEqual(["a"]);
  await page.getByLabel("Class", { exact: true }).selectOption("person");
  await page.getByRole("slider").fill("1");
  await expect(page.getByText(/No annotations match/)).toBeVisible();
});
test("standalone images keep the frame inspector available", async ({ page }) => {
  const state = await setup(page, [finding("a", "still", 0)]);
  await inspect(page, "a");
  await expect(page.getByText(/Source video unavailable for this image/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Annotate", exact: true })).toBeVisible();
  expect(state.requests).toEqual([]);
});
test("metadata failure keeps authoritative findings and source filters", async ({ page }) => {
  const state = await setup(page);
  state.metadataError = true;
  await page.reload();
  await expect(page.getByRole("button", { name: /Inspect finding/ })).toHaveCount(3);
  await page.getByLabel("Source clip", { exact: true }).selectOption("clip-b");
  await expect(page.getByRole("button", { name: /Inspect finding/ })).toHaveCount(1);
  await inspect(page, "c");
  await atTime(page, 0);
  await expect(page.getByRole("region", { name: "Source playback" })).toContainText(
    "Timing mapping unavailable",
  );
});
test("media failure has bounded renewal and manual retry preserves annotation points", async ({
  page,
}) => {
  const state = await setup(page);
  state.broken = true;
  await inspect(page, "a");
  await expect(
    page.getByRole("button", { name: "Retry source playback", exact: true }),
  ).toBeVisible();
  expect(state.requests).toEqual(["clip-a", "clip-a"]);
  await page.getByRole("button", { name: "Annotate", exact: true }).click();
  await page
    .locator("canvas")
    .last()
    .click({ position: { x: 200, y: 200 } });
  await expect(page.getByText("1 point", { exact: true })).toBeVisible();
  await page.waitForTimeout(300);
  expect(state.requests).toHaveLength(2);
  state.broken = false;
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await atTime(page, 0.6);
  await expect(page.getByText("1 point", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Annotation", exact: true })).toBeEnabled();
});
test("same-source renewal preserves position and paused state", async ({ page }) => {
  const state = await setup(page);
  await inspect(page, "a");
  await atTime(page, 0.6);
  await source(page).evaluate((video: HTMLVideoElement) => {
    video.currentTime = 1.1;
  });
  await atTime(page, 1.1);
  await source(page).dispatchEvent("error");
  await expect.poll(() => state.requests.length).toBe(2);
  await atTime(page, 1.1);
  await expect(source(page)).toHaveJSProperty("paused", true);
  await source(page).evaluate((video: HTMLVideoElement) => video.play());
  await expect(source(page)).toHaveJSProperty("paused", false);
  await source(page).dispatchEvent("error");
  await expect(
    page.getByRole("button", { name: "Retry source playback", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await expect(source(page)).toHaveJSProperty("paused", false);
});
test("late source request cannot replace another source", async ({ page }) => {
  await setup(page);
  let pending: Route | undefined;
  await page.route("**/api/v1/jobs/job/sources/clip-a/playback", (route) => {
    pending = route;
  });
  await inspect(page, "a");
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole("button", { name: "Next frame", exact: true }).click();
  await page.getByRole("button", { name: "Next frame", exact: true }).click();
  await atTime(page, 0.6);
  await expect(source(page)).toHaveAttribute("src", /clip-b/);
  await json(pending!, { video_id: "clip-a", url: "/source/obsolete" });
  await page.waitForTimeout(150);
  await expect(source(page)).toHaveAttribute("src", /clip-b/);
});
test("player keyboard input leaves annotation review unchanged at narrow width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 600, height: 900 });
  const state = await setup(page);
  await inspect(page, "a");
  await atTime(page, 0.6);
  await source(page).focus();
  await source(page).press("a");
  await source(page).press("ArrowRight");
  expect(state.changes).toEqual([]);
  await expect(
    page.getByRole("button", { name: "Close annotation editor", exact: true }),
  ).toBeInViewport();
  await expect(source(page)).toBeInViewport();
  await expect(page.getByRole("button", { name: "Annotate", exact: true })).toBeInViewport();
  if (process.env.WALDO_SCREENSHOT_DIR) {
    mkdirSync(process.env.WALDO_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({
      path: `${process.env.WALDO_SCREENSHOT_DIR}/synthetic-inspector-narrow.png`,
    });
  }
});

test("nonfinite time is invalid and does not seek", async ({ page }) => {
  await setup(page);
  await page.route("**/api/v1/jobs/job/annotations?*", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([finding("a", "clip-a", 0)]).replace(
        '"timestamp_s":0',
        '"timestamp_s":1e400',
      ),
    }),
  );
  await page.reload();
  await inspect(page, "a");
  await atTime(page, 0);
  await expect(page.getByRole("region", { name: "Source playback" })).toContainText("Invalid time");
});

test("unknown timing after failed initial load never invents a zero seek", async ({ page }) => {
  await page.addInitScript(() => {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "currentTime")!;
    const seeks: number[] = [];
    Object.assign(window, { sourceSeeks: seeks });
    Object.defineProperty(HTMLMediaElement.prototype, "currentTime", {
      ...descriptor,
      set(value: number) {
        seeks.push(value);
        descriptor.set!.call(this, value);
      },
    });
  });
  const state = await setup(page, [finding("a", "clip-a", null)]);
  state.broken = true;
  await inspect(page, "a");
  await expect(
    page.getByRole("button", { name: "Retry source playback", exact: true }),
  ).toBeVisible();
  state.broken = false;
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await atTime(page, 0);
  expect(
    await page.evaluate(() => (window as Window & { sourceSeeks: number[] }).sourceSeeks),
  ).toEqual([]);
});

test("late previous-account capability cannot replace current account source", async ({ page }) => {
  await setup(page);
  let pending: Route | undefined;
  await page.route("**/api/v1/jobs/job/sources/clip-a/playback", (route) => {
    if (route.request().headers().authorization === "Bearer A") {
      pending = route;
      return;
    }
    return json(route, { video_id: "clip-a", url: "/source/account-B" });
  });
  await inspect(page, "a");
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole("button", { name: "Close annotation editor", exact: true }).click();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Sign Out", exact: true }).click();
  await page.locator('input[type="email"]').fill("B@example.test");
  await page.locator('input[type="password"]').fill("password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await page.goto("/review/job");
  await inspect(page, "a");
  await atTime(page, 0.6);
  await json(pending!, { video_id: "clip-a", url: "/source/account-obsolete" });
  await page.waitForTimeout(100);
  await expect(source(page)).toHaveAttribute("src", "/source/account-B");
});

test("audio leading video keeps the container time origin", async ({ page }) => {
  await setup(page, [finding("a", "clip-a", 1.6)], "audio");
  await inspect(page, "a");
  await atTime(page, 1.6);
  await expect.poll(() => decodedX(page)).toBeGreaterThanOrEqual(88);
  expect(await decodedX(page)).toBeLessThanOrEqual(96);
});
for (const duration of [null, 1])
  test(`unconfirmed source duration ${duration} leaves playback manual`, async ({ page }) => {
    await setup(page);
    await page.route("**/api/v1/jobs/job/sources", (route) =>
      json(route, [
        { id: "clip-a", filename: "A.mp4", duration_s: duration, has_source_video: true },
      ]),
    );
    await page.reload();
    await inspect(page, "a");
    await atTime(page, 0);
    await expect(page.getByRole("region", { name: "Source playback" })).toContainText(
      "Timing mapping unavailable",
    );
  });
test("malformed source timeline does not silently clamp the finding", async ({ page }) => {
  await setup(page, [finding("a", "clip-a", 0.6)], "malformed");
  await inspect(page, "a");
  await expect(source(page)).toHaveJSProperty("videoWidth", 320);
  await expect(page.getByRole("region", { name: "Source playback" })).toContainText(
    /Timing mapping unavailable|browser could not seek/,
  );
});
test("explicit finding selection owns Accept after hovering another polygon", async ({ page }) => {
  const state = await setup(page, [
    finding("a", "clip-a", 0.6),
    finding("person", "clip-a", 0.6, {
      frame_id: "frame-a",
      class_name: "person",
      class_index: 1,
      polygon: [0.4, 0.4, 0.7, 0.4, 0.7, 0.7],
    }),
  ]);
  await inspect(page, "person");
  await atTime(page, 0.6);
  const canvas = page.locator("canvas").last(),
    box = (await canvas.boundingBox())!,
    size = Math.min(box.width, box.height);
  await canvas.hover({
    position: { x: (box.width - size) / 2 + size * 0.24, y: (box.height - size) / 2 + size * 0.15 },
  });
  await page.getByRole("button", { name: "Accept A", exact: true }).dispatchEvent("click");
  await expect.poll(() => state.changes).toEqual(["person"]);
});

test("training bar keeps the global pending count under source filters", async ({ page }) => {
  await setup(page, [
    finding("accepted-a", "clip-a", 0.6, { status: "accepted" }),
    finding("accepted-b", "clip-a", 1.2, { status: "accepted" }),
    finding("pending-c", "clip-b", 0.6),
  ]);
  await page.route("**/api/v1/status/job", (route) =>
    json(route, {
      job_id: "job",
      status: "completed",
      text_prompt: "car",
      result_url: "/export.zip",
      processing_summary: null,
    }),
  );
  await page.reload();
  await page.getByLabel("Source clip", { exact: true }).selectOption("clip-a");
  await expect(page.getByRole("button", { name: /Accept All/ })).toBeDisabled();
  await expect(page.getByText(/2 accepted, 1 remaining to review/)).toBeVisible();
  await expect(page.getByText(/All reviewed!/)).toHaveCount(0);
});

test("manual playback position survives renewal when saved time mapping is unavailable", async ({
  page,
}) => {
  const state = await setup(page, [finding("a", "clip-a", null)]);
  await page.route("**/api/v1/jobs/job/sources", (route) =>
    json(route, [{ id: "clip-a", filename: "A.mp4", duration_s: null, has_source_video: true }]),
  );
  await page.reload();
  await inspect(page, "a");
  await atTime(page, 0);
  await source(page).evaluate((video: HTMLVideoElement) => {
    video.currentTime = 1.1;
  });
  await atTime(page, 1.1);
  await source(page).dispatchEvent("error");
  await expect.poll(() => state.requests.length).toBe(2);
  await atTime(page, 1.1);
  await expect(source(page)).toHaveJSProperty("paused", true);
  await expect(page.getByRole("region", { name: "Source playback" })).toContainText(
    "Timing mapping unavailable",
  );
  await source(page).evaluate((video: HTMLVideoElement) => video.play());
  await expect(source(page)).toHaveJSProperty("paused", false);
  await source(page).dispatchEvent("error");
  await page.getByRole("button", { name: "Retry source playback", exact: true }).click();
  await expect(source(page)).toHaveAttribute("src", "/source/clip-a?generation=3");
  await expect
    .poll(() => source(page).evaluate((video: HTMLVideoElement) => video.readyState))
    .toBeGreaterThanOrEqual(2);
  await expect(source(page)).toHaveJSProperty("paused", false);
});
