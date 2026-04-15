import { useCallback, useEffect, useRef, useState } from "react";
import { BarChart3, Camera, Play, Rocket } from "lucide-react";
import type { DetectionOut } from "../../api";

export const TABS = [
  { key: "endpoints", label: "Endpoints", icon: Camera },
  { key: "test", label: "Test", icon: Play },
  { key: "models", label: "Models", icon: Rocket },
  { key: "monitor", label: "Monitor", icon: BarChart3 },
] as const;

export type TabKey = (typeof TABS)[number]["key"];

export const COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];

export function trackColor(trackId: number | null): string {
  if (trackId == null) return COLORS[0];
  return COLORS[(trackId - 1) % COLORS.length];
}

export function classColor(className: string, allClasses: string[]): string {
  const idx = allClasses.indexOf(className);
  return COLORS[Math.max(0, idx) % COLORS.length];
}

export interface ZoomPan { zoom: number; panX: number; panY: number }

export function useZoomPan(canvasRef: React.RefObject<HTMLCanvasElement | null>, redraw: () => void) {
  const [zp, setZp] = useState<ZoomPan>({ zoom: 1, panX: 0, panY: 0 });
  const zpRef = useRef(zp);
  zpRef.current = zp;
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const panStart = useRef({ x: 0, y: 0 });

  const reset = useCallback(() => setZp({ zoom: 1, panX: 0, panY: 0 }), []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const sx = canvas.width / rect.width;
      const sy = canvas.height / rect.height;
      const canvasX = cx * sx;
      const canvasY = cy * sy;

      setZp((prev) => {
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        const newZoom = Math.max(1, Math.min(prev.zoom * factor, 20));
        const newPanX = canvasX - (canvasX - prev.panX) * (newZoom / prev.zoom);
        const newPanY = canvasY - (canvasY - prev.panY) * (newZoom / prev.zoom);
        return { zoom: newZoom, panX: newPanX, panY: newPanY };
      });
    };

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      dragging.current = true;
      dragStart.current = { x: e.clientX, y: e.clientY };
      panStart.current = { x: zpRef.current.panX, y: zpRef.current.panY };
      canvas.style.cursor = "grabbing";
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const rect = canvas.getBoundingClientRect();
      const sx = canvas.width / rect.width;
      const sy = canvas.height / rect.height;
      const dx = (e.clientX - dragStart.current.x) * sx;
      const dy = (e.clientY - dragStart.current.y) * sy;
      setZp((prev) => ({ ...prev, panX: panStart.current.x + dx, panY: panStart.current.y + dy }));
    };

    const onMouseUp = () => {
      dragging.current = false;
      canvas.style.cursor = zpRef.current.zoom > 1 ? "grab" : "default";
    };

    const onDblClick = () => setZp({ zoom: 1, panX: 0, panY: 0 });

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("dblclick", onDblClick);

    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      canvas.removeEventListener("dblclick", onDblClick);
    };
  }, [canvasRef]);

  useEffect(() => { redraw(); }, [zp, redraw]);

  return { ...zp, reset };
}

export function drawDetections(
  ctx: CanvasRenderingContext2D,
  detections: DetectionOut[],
  confThreshold: number,
  canvasW: number,
  canvasH: number,
  srcW: number,
  srcH: number,
  classFilter?: Set<string>,
  zoom = 1,
) {
  const scaleX = canvasW / srcW;
  const scaleY = canvasH / srcH;
  let filtered = detections.filter((d) => d.confidence >= confThreshold);
  if (classFilter && classFilter.size > 0) {
    filtered = filtered.filter((d) => classFilter.has(d.class_name));
  }

  const lw = Math.max(2, 3 / zoom);
  const fontSize = Math.max(10, Math.round(15 / zoom));

  for (const det of filtered) {
    const color = trackColor(det.track_id);
    const [x1, y1, x2, y2] = det.bbox.map((v, i) => v * (i % 2 === 0 ? scaleX : scaleY));

    if (det.mask && det.mask.length > 0) {
      ctx.fillStyle = color + "55";
      ctx.beginPath();
      ctx.moveTo(det.mask[0][0] * scaleX, det.mask[0][1] * scaleY);
      for (const [x, y] of det.mask.slice(1)) ctx.lineTo(x * scaleX, y * scaleY);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = color + "aa";
      ctx.lineWidth = lw * 0.75;
      ctx.stroke();
    }

    ctx.strokeStyle = color;
    ctx.lineWidth = lw;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

    const label = `${det.class_name} ${(det.confidence * 100).toFixed(0)}%${det.track_id != null ? ` #${det.track_id}` : ""}`;
    ctx.font = `bold ${fontSize}px system-ui, sans-serif`;
    const tw = ctx.measureText(label).width;
    const labelH = fontSize + 6;
    const labelY = y1 - labelH > 0 ? y1 - labelH : y1;
    ctx.fillStyle = color + "dd";
    ctx.fillRect(x1, labelY, tw + 8, labelH);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, x1 + 4, labelY + fontSize);
  }
}

export function applyZoomPan(ctx: CanvasRenderingContext2D, zoom: number, panX: number, panY: number) {
  ctx.translate(panX, panY);
  ctx.scale(zoom, zoom);
}

export function ZoomIndicator({ zoom, onReset }: { zoom: number; onReset: () => void }) {
  if (zoom <= 1.01) return null;
  return (
    <div className="absolute top-2 right-2 flex items-center gap-2 bg-black/60 text-white text-xs px-2 py-1 rounded">
      <span>{zoom.toFixed(1)}x</span>
      <button onClick={onReset} className="hover:text-gray-300">Reset</button>
    </div>
  );
}
