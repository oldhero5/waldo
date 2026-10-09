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

interface VideoSeek {
  time: number;
  sourceTimestamp: number | null;
  promise: Promise<void>;
  cancel: () => void;
}

const videoSeeks = new WeakMap<HTMLVideoElement, { source: string; current?: VideoSeek; unconfirmed: boolean }>();

/** Caller cancellation stops capture, while bounded decoder observation can serve a same-frame retry. */
export function seekDecodedVideo(video: HTMLVideoElement, time: number, signal: AbortSignal, sourceTimestamp: number | null): Promise<void> {
  const abortError = () => new DOMException("Video seek cancelled", "AbortError");
  if (signal.aborted) return Promise.reject(abortError());
  let state = videoSeeks.get(video);
  if (!state || state.source !== video.currentSrc) {
    state?.current?.cancel();
    state = { source: video.currentSrc, unconfirmed: false };
    videoSeeks.set(video, state);
  }
  const sameTime = Math.abs(video.currentTime - time) < 0.00001;
  let current = state.current;
  if (!current || current.time !== time || current.sourceTimestamp !== sourceTimestamp || !sameTime) {
    current?.cancel();
    const owner = state;
    const seek: VideoSeek = { time, sourceTimestamp, promise: Promise.resolve(), cancel: () => {} };
    owner.current = seek;
    if (!owner.unconfirmed && sameTime && !video.seeking && video.readyState >= 2) current = seek;
    else {
      owner.unconfirmed = true;
      seek.promise = new Promise<void>((resolve, reject) => {
        const hasFrameCallbacks = typeof video.requestVideoFrameCallback === "function";
        let callback = 0, seeked = false, presented = !hasFrameCallbacks, active = true;
        const cleanup = () => {
          active = false;
          clearTimeout(timeout);
          if (callback) video.cancelVideoFrameCallback(callback);
          video.removeEventListener("seeked", done);
          video.removeEventListener("error", failed);
        };
        const fail = (error: Error) => {
          if (!active) return;
          cleanup();
          if (owner.current === seek) owner.current = undefined;
          reject(error);
        };
        const done = () => {
          if (!active || video.seeking || video.readyState < 2) return;
          seeked = true;
          if (!presented) return;
          owner.unconfirmed = false;
          cleanup(); resolve();
        };
        const nextPresentation = () => {
          callback = video.requestVideoFrameCallback((_, metadata) => {
            if (!active) return;
            callback = 0;
            // Approximate timestamps support navigation, but carry no exact PTS/alignment claim.
            presented = sourceTimestamp == null || Math.abs(metadata.mediaTime - sourceTimestamp) < 0.001;
            if (presented && seeked) done();
            else if (!presented) nextPresentation();
          });
        };
        const failed = () => fail(new Error("Unable to decode the requested video frame"));
        const timeout = setTimeout(() => fail(new Error("Video seeking timed out")), 10_000);
        seek.cancel = () => fail(abortError());
        video.addEventListener("seeked", done);
        video.addEventListener("error", failed);
        if (hasFrameCallbacks) nextPresentation();
        video.currentTime = time;
      });
      current = seek;
    }
  }
  return new Promise((resolve, reject) => {
    const aborted = () => { signal.removeEventListener("abort", aborted); reject(abortError()); };
    signal.addEventListener("abort", aborted, { once: true });
    current.promise.then(() => {
      signal.removeEventListener("abort", aborted);
      if (!signal.aborted) resolve();
    }, (error: Error) => {
      signal.removeEventListener("abort", aborted);
      if (!signal.aborted) reject(error);
    });
    if (signal.aborted) aborted();
  });
}
