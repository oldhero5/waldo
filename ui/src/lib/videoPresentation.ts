/** Detection timestamps describe decoded source frames, not intervals between sampled inference. */
export interface TimedEvidence {
  timestamp_s: number;
  frame_index?: number;
  frame_idx?: number;
  frame_duration_s?: number | null;
  source_width?: number | null;
  source_height?: number | null;
  timestamp_method?: string | null;
}

export function sourceFrameDuration(frames: readonly TimedEvidence[], index: number): number {
  const explicit = frames[index]?.frame_duration_s;
  if (explicit && explicit > 0) return explicit;
  // Older CFR results carry source indices. Never mistake the sampling gap for a frame duration.
  for (let i = 1; i < frames.length; i++) {
    const a = frames[i - 1], b = frames[i];
    const first = a.frame_index ?? a.frame_idx, second = b.frame_index ?? b.frame_idx;
    if (first != null && second != null && second > first && b.timestamp_s > a.timestamp_s) {
      return (b.timestamp_s - a.timestamp_s) / (second - first);
    }
  }
  return 0;
}

export function assessedFrameAt(frames: readonly TimedEvidence[], time: number): number {
  if (!Number.isFinite(time)) return -1;
  // Allow only timestamp rounding; masks must not leak onto adjacent unassessed source frames.
  const epsilon = 0.001;
  let lo = 0, hi = frames.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (frames[mid].timestamp_s <= time + epsilon) lo = mid + 1; else hi = mid;
  }
  const index = lo - 1;
  if (index < 0) return -1;
  const frame = frames[index];
  if (frame.timestamp_method && frame.timestamp_method !== "source_pts") return -1;
  const duration = sourceFrameDuration(frames, index);
  return time >= frame.timestamp_s - epsilon && time < frame.timestamp_s + Math.max(duration, epsilon * 2) - epsilon ? index : -1;
}

export function compatibleSourceSize(frame: TimedEvidence, width: number, height: number): boolean {
  if (!frame.source_width || !frame.source_height) return true;
  return Math.abs(frame.source_width / frame.source_height - width / height) < 0.005;
}

/** Observe the decoded presentation clock. currentTime/rAF can be ahead of the visible frame. */
export function watchVideoPresentation(video: HTMLVideoElement, draw: (time: number) => void): () => void {
  let stopped = false, callback = 0, animation = 0;
  const ready = () => { if (!video.seeking && video.readyState >= 2) draw(video.currentTime); };
  const hasFrameCallbacks = typeof video.requestVideoFrameCallback === "function";
  const next = () => {
    callback = video.requestVideoFrameCallback((_, metadata) => {
      if (stopped) return;
      if (!video.seeking) draw(metadata.mediaTime);
      next();
    });
  };
  const fallback = () => {
    if (stopped) return;
    ready();
    animation = requestAnimationFrame(fallback);
  };
  video.addEventListener("seeked", ready);
  video.addEventListener("loadeddata", ready);
  queueMicrotask(() => { if (!stopped) ready(); });
  if (hasFrameCallbacks) next(); else fallback();
  return () => {
    stopped = true;
    if (hasFrameCallbacks) video.cancelVideoFrameCallback(callback);
    cancelAnimationFrame(animation);
    video.removeEventListener("seeked", ready);
    video.removeEventListener("loadeddata", ready);
  };
}

/** Await decoder completion; timeout fails instead of painting the previous decoded frame. */
export function seekDecodedVideo(video: HTMLVideoElement, time: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timeout);
      video.removeEventListener("seeked", done);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
    };
    const done = () => { if (video.seeking || video.readyState < 2) return; cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error("Unable to decode the requested video frame")); };
    const aborted = () => { cleanup(); reject(new DOMException("Video seek cancelled", "AbortError")); };
    const timeout = setTimeout(() => { cleanup(); reject(new Error("Video seeking timed out")); }, 10_000);
    video.addEventListener("seeked", done);
    video.addEventListener("error", failed);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) { aborted(); return; }
    if (Math.abs(video.currentTime - time) < 0.00001 && !video.seeking && video.readyState >= 2) { done(); return; }
    video.currentTime = time;
  });
}
