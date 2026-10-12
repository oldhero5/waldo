import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getSourcePlayback, type AnnotationOut, type SavedSource } from "../api";
import { findingTime } from "../lib/findingTime";
import { useAuth } from "../contexts/authState";

interface Props {
  jobId: string;
  annotation: AnnotationOut;
  source?: SavedSource;
}
interface Timeline {
  origin: number;
  start: number;
  end: number;
}
export default function SourceVideoPlayer(props: Props) {
  const { user, token } = useAuth();
  const identity = JSON.stringify([
    token,
    user?.id,
    user?.workspace_id,
    props.jobId,
    props.annotation.source_video_id,
  ]);
  return <Playback key={identity} {...props} />;
}

function Playback({ jobId, annotation, source }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const episode = useMemo(
    () => ({
      active: false,
      revision: 0,
      pending: false,
      renewed: false,
      position: null as number | null,
      paused: true,
      restore: false,
      decoded: false,
      target: null as number | null,
    }),
    [],
  );
  const [media, setMedia] = useState({ url: "", generation: 0, loading: true, error: "" });
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [seekError, setSeekError] = useState("");
  const timing = findingTime(annotation);
  const sourceId = annotation.source_video_id;
  const unavailable = !sourceId || source?.has_source_video === false;
  const sourceDuration = source?.duration_s;
  // For a fresh, paused static file, the native initial position is a candidate
  // container origin. Confirm it against the full range and saved duration.
  // The first video frame is not the origin: an audio stream can start earlier.
  const mapped =
    timeline != null &&
    sourceDuration != null &&
    Number.isFinite(sourceDuration) &&
    sourceDuration > 0 &&
    Number.isFinite(timeline.origin) &&
    timeline.origin >= timeline.start &&
    timeline.origin < timeline.end &&
    Math.abs(timeline.end - timeline.origin - sourceDuration) <= 0.1;
  const span = mapped ? timeline.end - timeline.origin : null;
  const outside = timing.time != null && span != null && timing.time > span;

  const request = useCallback(
    async (restore: boolean) => {
      if (!episode.active || episode.pending || !sourceId) return;
      const video = videoRef.current;
      if (restore && video && video.readyState >= 1 && episode.decoded && !episode.restore) {
        episode.position = video.currentTime;
        episode.paused = video.paused;
      }
      episode.pending = true;
      episode.restore = restore;
      const revision = ++episode.revision;
      setMedia((previous) => ({ ...previous, loading: true, error: "" }));
      try {
        const result = await getSourcePlayback(jobId, sourceId);
        if (!episode.active || revision !== episode.revision) return;
        if (result.video_id !== sourceId || !result.url)
          throw new Error("Source video unavailable.");
        episode.decoded = false;
        episode.target = null;
        setTimeline(null);
        setSeekError("");
        setMedia((previous) => ({
          url: result.url,
          generation: previous.generation + 1,
          loading: false,
          error: "",
        }));
      } catch (error) {
        if (!episode.active || revision !== episode.revision) return;
        setMedia((previous) => ({
          ...previous,
          loading: false,
          error: error instanceof Error ? error.message : "Source video unavailable.",
        }));
      } finally {
        if (revision === episode.revision) episode.pending = false;
      }
    },
    [episode, jobId, sourceId],
  );

  useEffect(() => {
    episode.active = true;
    if (!unavailable) void request(false);
    return () => {
      episode.active = false;
      episode.revision++;
      // StrictMode runs setup again after cleanup in the development runtime.
      episode.pending = false;
    };
  }, [episode, request, unavailable]);

  const seek = useCallback(() => {
    const video = videoRef.current;
    if (!video || !episode.decoded || !timeline) return;
    const restore = episode.restore && episode.position != null;
    const target = restore
      ? episode.position
      : mapped && timing.time != null && !outside
        ? timeline.origin + timing.time
        : null;
    const resume = restore && !episode.paused;
    episode.restore = false;
    setSeekError("");
    // Restoring observed native playback needs only the new native range;
    // annotation seeks still require the validated container-time mapping.
    const start = restore ? timeline.start : timeline.origin;
    if (
      target == null ||
      !Number.isFinite(start) ||
      !Number.isFinite(timeline.end) ||
      target < start ||
      target > timeline.end
    )
      return;
    if (!restore) {
      video.pause();
      episode.paused = true;
    }
    episode.target = target;
    episode.position = target;
    video.currentTime = target;
    if (resume) void video.play().catch(() => {});
  }, [episode, mapped, outside, timeline, timing.time]);

  // A new finding seeks in the same clip; URL renewal instead restores position.
  useEffect(() => {
    seek();
  }, [annotation.id, seek]);

  const remember = () => {
    const video = videoRef.current;
    if (!video) return;
    if (mapped && timeline && video.currentTime > timeline.end + 0.1) {
      setSeekError("The browser could not seek to this saved time. Use manual playback.");
      video.pause();
      return;
    }
    if (media.loading || !episode.decoded || episode.restore || episode.target != null) return;
    episode.position = video.currentTime;
    episode.paused = video.paused;
  };
  const loaded = () => {
    const video = videoRef.current;
    if (!video || episode.decoded) return;
    episode.decoded = true;
    if (video.paused && video.seekable.length === 1) {
      setTimeline({
        origin: video.currentTime,
        start: video.seekable.start(0),
        end: video.seekable.end(0),
      });
    } else setTimeline({ origin: NaN, start: 0, end: 0 });
  };
  const seeked = () => {
    const video = videoRef.current;
    if (!video || episode.target == null) return;
    if (Math.abs(video.currentTime - episode.target) > 0.05) {
      setSeekError("The browser could not seek to this saved time. Use manual playback.");
      video.pause();
    }
    episode.target = null;
    remember();
  };
  const failed = () => {
    if (!episode.active || episode.pending) return;
    if (!episode.renewed) {
      episode.renewed = true;
      void request(true);
    } else
      setMedia((previous) => ({
        ...previous,
        loading: false,
        error:
          "Source video could not be loaded. The object may be missing or this browser may not support its codec.",
      }));
  };

  return (
    <section aria-label="Source playback" className="p-4 text-gray-200 space-y-3">
      <h2 className="font-semibold">Source playback</h2>
      <p className="text-sm break-all">{source?.filename || sourceId || "Source unknown"}</p>
      <p className="text-sm">Selected finding: {annotation.class_name}</p>
      <p className="text-sm">{timing.label}</p>
      {timeline && !mapped && (
        <p role="status" className="text-amber-300">
          Timing mapping unavailable for this source. Use manual playback.
        </p>
      )}
      {outside && (
        <p role="status" className="text-amber-300">
          Saved time is outside this source duration. No automatic seek.
        </p>
      )}
      {seekError && (
        <p role="status" className="text-amber-300">
          {seekError}
        </p>
      )}
      <p className="text-xs text-gray-400">
        Masks belong to the saved frame. Playback does not show aligned masks. Findings cover
        sampled frames; other times remain unknown.
      </p>
      {unavailable ? (
        <p>Source video unavailable for this image. Review the saved frame.</p>
      ) : (
        <>
          {media.url && (
            <video
              key={media.generation}
              ref={videoRef}
              src={media.url}
              aria-label="Source video"
              controls
              preload="auto"
              className="w-full"
              onLoadedData={loaded}
              onSeeked={seeked}
              onTimeUpdate={remember}
              onPlay={remember}
              onPause={remember}
              onError={failed}
            />
          )}
          {media.loading && <p role="status">Loading source video…</p>}
          {media.error && (
            <div role="alert" className="text-sm space-y-2">
              <p>{media.error}</p>
              <button
                type="button"
                onClick={() => {
                  void request(true);
                }}
                aria-label="Retry source playback"
                className="underline"
              >
                Retry source playback
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
