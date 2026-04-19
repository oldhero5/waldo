/**
 * Interactive annotation canvas with:
 * - Review mode: accept/reject existing annotations
 * - Annotate mode: click positive/negative points → SAM3 → preview → save
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AnnotationOut } from "../api";
import { segmentPoints, createAnnotation } from "../api";
import { X, ZoomIn, ZoomOut, RotateCcw, Check, XCircle, Plus, Loader2, MousePointer, Pencil, GripVertical } from "lucide-react";

interface Props {
  imageUrl: string;
  frameId: string;
  jobId: string;
  annotations: AnnotationOut[];
  classes: string[];
  onAccept: (id: string) => void;
  onReject: (id: string) => void;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  onAnnotationCreated?: () => void;
}

const COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];
function classColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
  return COLORS[((h % COLORS.length) + COLORS.length) % COLORS.length];
}

interface ClickPoint { x: number; y: number; label: number } // x,y in normalized 0-1

export default function AnnotationCanvas({
  imageUrl, frameId, jobId, annotations, classes, onAccept, onReject, onClose, onPrev, onNext, onAnnotationCreated,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [showBoxes, setShowBoxes] = useState(true);
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 });

  // Draggable sidebar
  const [sidebarPos, setSidebarPos] = useState({ x: -1, y: -1 }); // -1 = default position
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const sidebarDragging = useRef(false);
  const sidebarDragStart = useRef({ x: 0, y: 0, posX: 0, posY: 0 });

  // Annotate mode state
  const [mode, setMode] = useState<"review" | "annotate">("review");
  const [clickPoints, setClickPoints] = useState<ClickPoint[]>([]);
  const [previewPolygon, setPreviewPolygon] = useState<number[] | null>(null);
  const [segmenting, setSegmenting] = useState(false);
  const [selectedClass, setSelectedClass] = useState(classes[0] || "");
  const [newClassName, setNewClassName] = useState("");
  const [saving, setSaving] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load image
  useEffect(() => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => { imgRef.current = img; setImgLoaded(true); };
    img.onerror = () => { console.error("Failed to load image:", imageUrl); setImgLoaded(false); };
    img.src = imageUrl;
    setImgLoaded(false);
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setSelectedId(null);
    setClickPoints([]);
    setPreviewPolygon(null);
  }, [imageUrl]);

  // Memoize per-annotation polygon geometry (normalized coords, not screen space).
  // Keyed on annotation id + serialized polygon — recomputes only when annotations change,
  // NOT on every zoom/pan update (those only affect the screen-space transform applied at draw time).
  const annPolygonCache = useMemo(() => {
    const cache = new Map<string, { pts: [number, number][]; minY: number; labelNx: number }>();
    for (const ann of annotations) {
      if (!ann.polygon || ann.polygon.length < 6) continue;
      const pts: [number, number][] = [];
      let minY = Infinity;
      let labelNx = 0;
      for (let i = 0; i < ann.polygon.length; i += 2) {
        const nx = ann.polygon[i];
        const ny = ann.polygon[i + 1];
        pts.push([nx, ny]);
        if (ny < minY) { minY = ny; labelNx = nx; }
      }
      cache.set(ann.id, { pts, minY, labelNx });
    }
    return cache;
  }, [annotations]);

  // Coordinate helpers
  const getImageLayout = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return null;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width / img.width, rect.height / img.height) * zoom;
    const imgW = img.width * scale;
    const imgH = img.height * scale;
    const ox = (rect.width - imgW) / 2 + pan.x;
    const oy = (rect.height - imgH) / 2 + pan.y;
    return { rect, scale, imgW, imgH, ox, oy, natW: img.width, natH: img.height };
  }, [zoom, pan]);

  // Screen coords → normalized image coords (0-1)
  const screenToNorm = useCallback((clientX: number, clientY: number): { nx: number; ny: number } | null => {
    const layout = getImageLayout();
    if (!layout) return null;
    const mx = clientX - layout.rect.left;
    const my = clientY - layout.rect.top;
    const nx = (mx - layout.ox) / layout.imgW;
    const ny = (my - layout.oy) / layout.imgH;
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null;
    return { nx, ny };
  }, [getImageLayout]);

  // Draw
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !imgLoaded) return;

    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * devicePixelRatio;
    canvas.height = rect.height * devicePixelRatio;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(devicePixelRatio, devicePixelRatio);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const scale = Math.min(rect.width / img.width, rect.height / img.height) * zoom;
    const imgW = img.width * scale;
    const imgH = img.height * scale;
    const ox = (rect.width - imgW) / 2 + pan.x;
    const oy = (rect.height - imgH) / 2 + pan.y;

    ctx.drawImage(img, ox, oy, imgW, imgH);

    // Draw existing annotations — geometry from memoized cache, only screen transform is hot
    for (const ann of annotations) {
      const cached = annPolygonCache.get(ann.id);
      if (!cached) continue;
      const isSelected = ann.id === selectedId;
      const isHovered = ann.id === hoveredId;
      const color = ann.status === "accepted" ? "#22c55e" : ann.status === "rejected" ? "#ef4444" : classColor(ann.class_name);

      ctx.beginPath();
      for (let pi = 0; pi < cached.pts.length; pi++) {
        const px = ox + cached.pts[pi][0] * imgW;
        const py = oy + cached.pts[pi][1] * imgH;
        if (pi === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = color + (isSelected ? "44" : isHovered ? "33" : "22");
      ctx.fill();

      if (showBoxes) {
        ctx.strokeStyle = color;
        ctx.lineWidth = isSelected ? 3 : isHovered ? 2.5 : 1.5;
        if (ann.status === "rejected") ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Label — screen-space transform of cached normalized coords
        const labelX = ox + cached.labelNx * imgW;
        const minY = oy + cached.minY * imgH;
        const label = `${ann.class_name} ${ann.confidence != null ? (ann.confidence * 100).toFixed(0) + "%" : ""}`;
        ctx.font = `bold 13px system-ui, sans-serif`;
        const tw = ctx.measureText(label).width;
        const lh = 18;
        const ly = Math.max(lh, minY - 4);
        ctx.fillStyle = color + "dd";
        ctx.fillRect(labelX - 2, ly - lh, tw + 8, lh + 2);
        ctx.fillStyle = "#fff";
        ctx.fillText(label, labelX + 2, ly - 4);
      } else if (isSelected || isHovered) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }

    // Draw SAM3 preview polygon
    if (previewPolygon && previewPolygon.length >= 6) {
      ctx.beginPath();
      for (let i = 0; i < previewPolygon.length; i += 2) {
        const px = ox + previewPolygon[i] * imgW;
        const py = oy + previewPolygon[i + 1] * imgH;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = "#3b82f655";
      ctx.fill();
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }

    // Draw click points
    for (const pt of clickPoints) {
      const px = ox + pt.x * imgW;
      const py = oy + pt.y * imgH;
      const isPositive = pt.label === 1;

      ctx.beginPath();
      ctx.arc(px, py, 8, 0, Math.PI * 2);
      ctx.fillStyle = isPositive ? "#22c55e" : "#ef4444";
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.stroke();

      // + or - icon
      ctx.fillStyle = "#fff";
      ctx.font = "bold 12px monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(isPositive ? "+" : "−", px, py);
      ctx.textAlign = "start";
      ctx.textBaseline = "alphabetic";
    }
  }, [imgLoaded, zoom, pan, annPolygonCache, annotations, selectedId, hoveredId, previewPolygon, clickPoints, showBoxes]);

  useEffect(() => { draw(); }, [draw]);

  // Call SAM3 when points change
  useEffect(() => {
    if (clickPoints.length === 0 || mode !== "annotate") return;
    if (debounceRef.current) clearTimeout(debounceRef.current);

    debounceRef.current = setTimeout(async () => {
      const img = imgRef.current;
      if (!img) return;
      setSegmenting(true);
      try {
        // Convert normalized points to pixel coords for SAM3
        const pixelPoints = clickPoints.map((p) => [p.x * img.width, p.y * img.height]);
        const labels = clickPoints.map((p) => p.label);
        const result = await segmentPoints(frameId, pixelPoints, labels);
        if (result.polygons.length > 0) {
          setPreviewPolygon(result.polygons[0]);
        } else {
          setPreviewPolygon(null);
        }
      } catch {
        setPreviewPolygon(null);
      } finally {
        setSegmenting(false);
      }
    }, 400);

    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [clickPoints, frameId, mode]);

  // Hit test for review mode
  const hitTest = useCallback((clientX: number, clientY: number): AnnotationOut | null => {
    const layout = getImageLayout();
    if (!layout) return null;
    const coord = screenToNorm(clientX, clientY);
    if (!coord) return null;

    for (let ai = annotations.length - 1; ai >= 0; ai--) {
      const ann = annotations[ai];
      const cached = annPolygonCache.get(ann.id);
      if (!cached) continue;
      const { pts } = cached;
      let inside = false;
      for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
        const [xi, yi] = pts[i];
        const [xj, yj] = pts[j];
        if ((yi > coord.ny) !== (yj > coord.ny) && coord.nx < ((xj - xi) * (coord.ny - yi)) / (yj - yi) + xi)
          inside = !inside;
      }
      if (inside) return ann;
    }
    return null;
  }, [annotations, annPolygonCache, getImageLayout, screenToNorm]);

  // Mouse handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (mode === "annotate") {
      const coord = screenToNorm(e.clientX, e.clientY);
      if (!coord) return;
      const label = e.button === 2 ? 0 : 1; // right-click = negative
      setClickPoints((prev) => [...prev, { x: coord.nx, y: coord.ny, label }]);
      return;
    }

    if (e.button === 0) {
      const hit = hitTest(e.clientX, e.clientY);
      if (hit) { setSelectedId(hit.id); return; }
      dragging.current = true;
      dragStart.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
    }
  }, [mode, hitTest, pan, screenToNorm]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (dragging.current) {
      setPan({
        x: dragStart.current.panX + (e.clientX - dragStart.current.x),
        y: dragStart.current.panY + (e.clientY - dragStart.current.y),
      });
    } else if (mode === "review") {
      const hit = hitTest(e.clientX, e.clientY);
      setHoveredId(hit?.id || null);
    }
  }, [hitTest, mode]);

  const handleMouseUp = useCallback(() => { dragging.current = false; }, []);
  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    if (mode === "annotate") { e.preventDefault(); handleMouseDown(e); }
  }, [mode, handleMouseDown]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.max(0.5, Math.min(z * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 20)));
  }, []);

  // Save annotation
  const handleSave = useCallback(async () => {
    if (!previewPolygon || previewPolygon.length < 6) return;
    const className = newClassName.trim() || selectedClass;
    if (!className) return;
    setSaving(true);
    try {
      await createAnnotation({
        frame_id: frameId,
        job_id: jobId,
        class_name: className,
        polygon: previewPolygon,
        status: "accepted",
      });
      setClickPoints([]);
      setPreviewPolygon(null);
      setNewClassName("");
      onAnnotationCreated?.();
    } catch (e) {
      console.error("Failed to save annotation:", e);
    } finally {
      setSaving(false);
    }
  }, [previewPolygon, selectedClass, newClassName, frameId, jobId, onAnnotationCreated]);

  // Active annotation: hovered > selected > first pending > first
  const hoveredAnn = annotations.find((a) => a.id === hoveredId);
  const selectedAnn = annotations.find((a) => a.id === selectedId);
  const firstPending = annotations.find((a) => a.status === "pending");
  const activeAnn = hoveredAnn || selectedAnn || firstPending || annotations[0] || null;

  // Auto-highlight active annotation
  useEffect(() => {
    if (mode === "review" && !hoveredId && !selectedId && activeAnn) {
      setSelectedId(activeAnn.id);
    }
  }, [mode, hoveredId, selectedId, activeAnn]);

  // Keyboard
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        if (mode === "annotate" && clickPoints.length > 0) {
          setClickPoints([]); setPreviewPolygon(null);
        } else if (mode === "annotate") {
          setMode("review");
        } else if (zoom > 1.5) {
          setZoom(1); setPan({ x: 0, y: 0 });
        } else {
          onClose();
        }
      }
      else if (e.key === "ArrowLeft" && onPrev) onPrev();
      else if (e.key === "ArrowRight" && onNext) onNext();
      else if (e.key === "z" && mode === "review" && !e.ctrlKey && !e.metaKey) {
        // Z toggles zoom: zoom in to active annotation, or zoom out if already zoomed
        if (zoom > 1.5) {
          setZoom(1); setPan({ x: 0, y: 0 });
        } else if (activeAnn && activeAnn.polygon && activeAnn.polygon.length >= 4) {
          // Zoom to annotation bounding box
          const canvas = canvasRef.current;
          const img = imgRef.current;
          if (canvas && img) {
            const rect = canvas.getBoundingClientRect();
            let minX = 1, maxX = 0, minY = 1, maxY = 0;
            for (let i = 0; i < activeAnn.polygon.length; i += 2) {
              minX = Math.min(minX, activeAnn.polygon[i]);
              maxX = Math.max(maxX, activeAnn.polygon[i]);
              minY = Math.min(minY, activeAnn.polygon[i + 1]);
              maxY = Math.max(maxY, activeAnn.polygon[i + 1]);
            }
            const pad = 0.05;
            minX = Math.max(0, minX - pad); minY = Math.max(0, minY - pad);
            maxX = Math.min(1, maxX + pad); maxY = Math.min(1, maxY + pad);
            const centerX = (minX + maxX) / 2;
            const centerY = (minY + maxY) / 2;
            const baseScale = Math.min(rect.width / img.width, rect.height / img.height);
            const targetZoom = Math.min(0.6 / Math.max(maxX - minX, maxY - minY), 15);
            const scale = baseScale * targetZoom;
            const imgW = img.width * scale;
            const imgH = img.height * scale;
            setZoom(targetZoom);
            setPan({ x: rect.width / 2 - centerX * imgW - (rect.width - imgW) / 2, y: rect.height / 2 - centerY * imgH - (rect.height - imgH) / 2 });
          }
        }
      }
      else if (e.key === "b" && mode === "review") {
        setShowBoxes((prev) => !prev);
      }
      else if ((e.key === "a" || e.key === "r") && mode === "review" && activeAnn) {
        const targetId = activeAnn.id;
        if (e.key === "a") onAccept(targetId); else onReject(targetId);
        // Auto-select next pending annotation
        const remaining = annotations.filter((a) => a.id !== targetId && a.status === "pending");
        if (remaining.length > 0) {
          setSelectedId(remaining[0].id);
        } else {
          setSelectedId(null);
        }
      }
      else if (e.key === "Enter" && mode === "annotate" && previewPolygon) handleSave();
      else if ((e.key === "z" && (e.ctrlKey || e.metaKey)) && mode === "annotate") {
        setClickPoints((prev) => prev.slice(0, -1));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, onPrev, onNext, activeAnn, annotations, onAccept, onReject, mode, clickPoints, previewPolygon, handleSave, zoom]);

  const effectiveClass = newClassName.trim() || selectedClass;

  return (
    <div className="fixed inset-0 z-50 bg-gray-950 flex flex-col" onContextMenu={(e) => e.preventDefault()}>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-1">
          {/* Mode toggle */}
          <button
            onClick={() => { setMode("review"); setClickPoints([]); setPreviewPolygon(null); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm ${
              mode === "review" ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            <MousePointer size={14} /> Review
          </button>
          <button
            onClick={() => { setMode("annotate"); setSelectedId(null); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm ${
              mode === "annotate" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            <Pencil size={14} /> Annotate
          </button>

          <div className="w-px h-5 bg-gray-700 mx-2" />

          <button onClick={() => setZoom((z) => Math.min(z * 1.3, 20))} className="p-1.5 rounded hover:bg-gray-800 text-gray-400"><ZoomIn size={16} /></button>
          <button onClick={() => setZoom((z) => Math.max(z / 1.3, 0.5))} className="p-1.5 rounded hover:bg-gray-800 text-gray-400"><ZoomOut size={16} /></button>
          <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }} className="p-1.5 rounded hover:bg-gray-800 text-gray-400"><RotateCcw size={16} /></button>
          <span className="text-xs text-gray-500 ml-1 font-mono">{(zoom * 100).toFixed(0)}%</span>

          <div className="w-px h-5 bg-gray-700 mx-2" />

          {mode === "review" && (
            <>
              <button
                onClick={() => { if (activeAnn?.polygon) { /* zoom toggle handled by Z key */ const e = new KeyboardEvent('keydown', {key: 'z'}); window.dispatchEvent(e); } }}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs text-gray-300 hover:bg-gray-800"
                title="Zoom to annotation (Z)"
              >
                <ZoomIn size={13} /> <kbd className="text-[9px] opacity-50">Z</kbd>
              </button>
              <button
                onClick={() => setShowBoxes((prev) => !prev)}
                className={`flex items-center gap-1 px-2 py-1 rounded text-xs ${showBoxes ? "text-gray-300 hover:bg-gray-800" : "text-amber-400 hover:bg-gray-800"}`}
                title="Toggle boxes/labels (B)"
              >
                {showBoxes ? "Boxes" : "Off"} <kbd className="text-[9px] opacity-50">B</kbd>
              </button>
            </>
          )}

          {segmenting && (
            <span className="flex items-center gap-1 ml-3 text-blue-400 text-xs">
              <Loader2 size={12} className="animate-spin" /> Segmenting...
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 text-xs text-gray-500">
          {mode === "review" && <span><kbd className="px-1 py-0.5 bg-gray-800 rounded">A</kbd> accept <kbd className="px-1 py-0.5 bg-gray-800 rounded">R</kbd> reject <kbd className="px-1 py-0.5 bg-gray-800 rounded">Z</kbd> zoom <kbd className="px-1 py-0.5 bg-gray-800 rounded">B</kbd> boxes</span>}
          {mode === "annotate" && <span>Left-click = positive, Right-click = negative, <kbd className="px-1 py-0.5 bg-gray-800 rounded">Enter</kbd> save</span>}
          <button onClick={onClose} className="p-1.5 rounded hover:bg-gray-800 text-gray-400 ml-2"><X size={18} /></button>
        </div>
      </div>

      {/* Main canvas */}
      <div className="flex-1 relative overflow-hidden">
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full"
          style={{ cursor: mode === "annotate" ? "crosshair" : dragging.current ? "grabbing" : hoveredId ? "pointer" : "grab" }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          onContextMenu={handleContextMenu}
        />

        {/* Review mode: always-visible action panel */}
        {mode === "review" && activeAnn && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-gray-900/95 backdrop-blur border border-gray-700 rounded-xl px-4 py-2.5 flex items-center gap-3 shadow-xl">
            <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: classColor(activeAnn.class_name) }} />
            <span className="text-white font-medium text-sm">{activeAnn.class_name}</span>
            {activeAnn.confidence != null && <span className="text-gray-400 text-sm">{(activeAnn.confidence * 100).toFixed(0)}%</span>}
            <span className="text-gray-500 text-xs">{annotations.filter((a) => a.status === "pending").length} pending</span>
            <div className="h-5 w-px bg-gray-700" />
            <button onClick={() => { onAccept(activeAnn.id); const next = annotations.find((a) => a.id !== activeAnn.id && a.status === "pending"); setSelectedId(next?.id || null); }}
              className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${activeAnn.status === "accepted" ? "bg-green-600 text-white" : "bg-gray-800 text-gray-300 hover:bg-green-600 hover:text-white"}`}>
              <Check size={13} /> Accept <kbd className="text-[10px] opacity-50 ml-1">A</kbd>
            </button>
            <button onClick={() => { onReject(activeAnn.id); const next = annotations.find((a) => a.id !== activeAnn.id && a.status === "pending"); setSelectedId(next?.id || null); }}
              className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${activeAnn.status === "rejected" ? "bg-red-600 text-white" : "bg-gray-800 text-gray-300 hover:bg-red-600 hover:text-white"}`}>
              <XCircle size={13} /> Reject <kbd className="text-[10px] opacity-50 ml-1">R</kbd>
            </button>
          </div>
        )}

        {/* Annotate mode: class selector + save panel */}
        {mode === "annotate" && clickPoints.length > 0 && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-gray-900/95 backdrop-blur border border-gray-700 rounded-xl px-5 py-3 flex items-center gap-3 shadow-xl">
            {/* Class selector */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">Class:</span>
              {classes.length > 0 && (
                <select
                  value={selectedClass}
                  onChange={(e) => { setSelectedClass(e.target.value); setNewClassName(""); }}
                  className="bg-gray-800 text-white text-sm rounded-lg px-2 py-1.5 border border-gray-700"
                >
                  {classes.map((c) => <option key={c} value={c}>{c}</option>)}
                  <option value="">+ New class</option>
                </select>
              )}
              {(!selectedClass || classes.length === 0) && (
                <input
                  type="text"
                  value={newClassName}
                  onChange={(e) => setNewClassName(e.target.value)}
                  placeholder="New class name"
                  className="bg-gray-800 text-white text-sm rounded-lg px-2 py-1.5 border border-gray-700 w-36"
                  autoFocus
                />
              )}
            </div>

            <div className="h-6 w-px bg-gray-700" />

            <span className="text-xs text-gray-500">
              {clickPoints.length} point{clickPoints.length !== 1 ? "s" : ""}
            </span>

            <button onClick={() => { setClickPoints([]); setPreviewPolygon(null); }}
              className="px-3 py-1.5 text-sm text-gray-400 hover:text-white rounded-lg hover:bg-gray-800">
              Clear
            </button>

            <button
              onClick={handleSave}
              disabled={!previewPolygon || !effectiveClass || saving}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-40"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Save Annotation
            </button>
          </div>
        )}

        {/* Nav arrows */}
        {onPrev && <button onClick={onPrev} className="absolute left-4 top-1/2 -translate-y-1/2 w-10 h-10 bg-gray-900/80 rounded-full flex items-center justify-center text-gray-400 hover:text-white">&larr;</button>}
        {onNext && <button onClick={onNext} className="absolute right-4 top-1/2 -translate-y-1/2 w-10 h-10 bg-gray-900/80 rounded-full flex items-center justify-center text-gray-400 hover:text-white">&rarr;</button>}

        {/* Draggable annotation sidebar */}
        <div
          className="absolute w-60 bg-gray-900/90 backdrop-blur border border-gray-800 rounded-xl overflow-hidden select-none"
          style={{
            top: sidebarPos.y >= 0 ? sidebarPos.y : 8,
            right: sidebarPos.x >= 0 ? undefined : 8,
            left: sidebarPos.x >= 0 ? sidebarPos.x : undefined,
            maxHeight: sidebarCollapsed ? "auto" : "calc(100% - 80px)",
            zIndex: 20,
          }}
        >
          {/* Drag handle */}
          <div
            className="flex items-center justify-between px-2 py-1.5 cursor-grab active:cursor-grabbing border-b border-gray-800"
            onMouseDown={(e) => {
              e.stopPropagation();
              sidebarDragging.current = true;
              const rect = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect();
              sidebarDragStart.current = { x: e.clientX, y: e.clientY, posX: rect.left, posY: rect.top };
              const onMove = (ev: MouseEvent) => {
                if (!sidebarDragging.current) return;
                setSidebarPos({
                  x: sidebarDragStart.current.posX + (ev.clientX - sidebarDragStart.current.x),
                  y: sidebarDragStart.current.posY + (ev.clientY - sidebarDragStart.current.y) - 56, // offset for nav height
                });
              };
              const onUp = () => { sidebarDragging.current = false; window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
              window.addEventListener("mousemove", onMove);
              window.addEventListener("mouseup", onUp);
            }}
          >
            <div className="flex items-center gap-1">
              <GripVertical size={12} className="text-gray-600" />
              <span className="text-[10px] text-gray-500 uppercase tracking-wide">{annotations.length} annotations</span>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); setSidebarCollapsed(!sidebarCollapsed); }}
              className="text-gray-500 hover:text-gray-300 text-xs px-1"
            >
              {sidebarCollapsed ? "+" : "−"}
            </button>
          </div>

          {/* Annotation list */}
          {!sidebarCollapsed && (
            <div className="p-1.5 overflow-y-auto" style={{ maxHeight: "calc(100vh - 200px)" }}>
              {annotations.map((a) => (
                <button key={a.id}
                  onClick={(e) => { e.stopPropagation(); setMode("review"); setSelectedId(a.id === selectedId ? null : a.id); }}
                  onMouseEnter={() => setHoveredId(a.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left text-xs transition-colors ${
                    a.id === selectedId ? "bg-blue-600/30 text-white" : "text-gray-300 hover:bg-gray-800"
                  }`}
                >
                  <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: classColor(a.class_name) }} />
                  <span className="truncate flex-1">{a.class_name}</span>
                  <span className="text-gray-500 shrink-0">{a.confidence != null ? (a.confidence * 100).toFixed(0) + "%" : ""}</span>
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    a.status === "accepted" ? "bg-green-500" : a.status === "rejected" ? "bg-red-500" : "bg-gray-600"
                  }`} />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
