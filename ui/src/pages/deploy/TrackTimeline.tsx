import { useMemo } from "react";
import type { FrameResultOut } from "../../api";
import { trackColor } from "./shared";

export function TrackTimeline({ frames, currentFrame, confThreshold, classFilter, onSeek, flaggedSet }: {
  frames: FrameResultOut[];
  currentFrame: number;
  confThreshold: number;
  classFilter: Set<string>;
  onSeek: (idx: number) => void;
  flaggedSet: Set<string>;
}) {
  const sortedTracks = useMemo(() => {
    const trackKeys = new Map<string, { trackId: number; color: string; className: string }>();
    for (const fr of frames) {
      for (const d of fr.detections) {
        if (d.track_id != null && d.confidence >= confThreshold && classFilter.has(d.class_name)) {
          const key = `${d.track_id}-${d.class_name}`;
          if (!trackKeys.has(key)) {
            trackKeys.set(key, { trackId: d.track_id, color: trackColor(d.track_id), className: d.class_name });
          }
        }
      }
    }
    const trackAppearances = new Map<string, number>();
    for (const fr of frames) {
      const seen = new Set<string>();
      for (const d of fr.detections) {
        if (d.track_id != null && d.confidence >= confThreshold && classFilter.has(d.class_name)) {
          const key = `${d.track_id}-${d.class_name}`;
          if (!seen.has(key)) { seen.add(key); trackAppearances.set(key, (trackAppearances.get(key) || 0) + 1); }
        }
      }
    }
    return Array.from(trackKeys.entries())
      .filter(([key]) => (trackAppearances.get(key) || 0) >= 2)
      .sort((a, b) => a[1].trackId - b[1].trackId);
  }, [frames, confThreshold, classFilter]);

  if (sortedTracks.length === 0) return null;

  const totalFrames = frames.length;
  const rowH = 10;
  const innerH = sortedTracks.length * rowH + 4;
  const maxH = 120;
  const h = Math.min(innerH, maxH);

  return (
    <div className="mt-1 mb-2">
      <p className="text-[10px] mb-0.5" style={{ color: "var(--text-muted)" }}>{sortedTracks.length} tracks</p>
      <div className="relative rounded cursor-pointer" style={{ height: h, overflowY: innerH > maxH ? "auto" : "hidden", backgroundColor: "var(--bg-inset)" }}
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const frac = (e.clientX - rect.left) / rect.width;
          onSeek(Math.round(frac * (totalFrames - 1)));
        }}
      >
        <div style={{ height: innerH, position: "relative" }}>
          <div
            className="absolute top-0 w-px bg-blue-600 z-10"
            style={{ left: `${(currentFrame / Math.max(1, totalFrames - 1)) * 100}%`, height: innerH }}
          />
          {sortedTracks.map(([tkey, info], row) => {
            const segments: { start: number; end: number; hasFlagged: boolean }[] = [];
            let seg: { start: number; end: number; hasFlagged: boolean } | null = null;
            let gapCount = 0;
            for (let i = 0; i < totalFrames; i++) {
              const det = frames[i].detections.find(
                (d) => d.track_id === info.trackId && d.class_name === info.className && d.confidence >= confThreshold && classFilter.has(d.class_name)
              );
              if (det) {
                const key = `${i}-${info.trackId}`;
                if (!seg) { seg = { start: i, end: i, hasFlagged: flaggedSet.has(key) }; }
                else { seg.end = i; if (flaggedSet.has(key)) seg.hasFlagged = true; }
                gapCount = 0;
              } else {
                gapCount++;
                if (seg && gapCount > 2) { segments.push(seg); seg = null; gapCount = 0; }
                else if (seg) { seg.end = i; }
              }
            }
            if (seg) segments.push(seg);

            return (
              <div key={tkey} className="absolute left-0 right-0" style={{ top: row * rowH + 2, height: rowH }}>
                {segments.map((s, si) => (
                  <div
                    key={si}
                    className="absolute rounded-sm"
                    style={{
                      left: `${(s.start / totalFrames) * 100}%`,
                      width: `max(3px, ${((s.end - s.start + 1) / totalFrames) * 100}%)`,
                      height: rowH - 3,
                      top: 1,
                      backgroundColor: s.hasFlagged ? "#fca5a5" : info.color,
                      opacity: s.hasFlagged ? 0.9 : 0.7,
                    }}
                  />
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
