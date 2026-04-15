import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ImageIcon, Loader2, Move, ZoomIn } from "lucide-react";
import { predictImage, type DetectionOut } from "../../api";
import { applyZoomPan, drawDetections, useZoomPan, ZoomIndicator } from "./shared";

export function ImageDemo({ confThreshold, classFilter }: { confThreshold: number; classFilter: Set<string> }) {
  const [file, setFile] = useState<File | null>(null);
  const [detections, setDetections] = useState<DetectionOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !img.naturalWidth) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.save();
    applyZoomPan(ctx, zpRef.current.zoom, zpRef.current.panX, zpRef.current.panY);
    ctx.drawImage(img, 0, 0);
    drawDetections(ctx, detections, confThreshold, img.naturalWidth, img.naturalHeight, img.naturalWidth, img.naturalHeight, classFilter, zpRef.current.zoom);
    ctx.restore();
  }, [detections, confThreshold, classFilter]);

  const { zoom, panX, panY, reset } = useZoomPan(canvasRef, redraw);
  const zpRef = useRef({ zoom, panX, panY });
  zpRef.current = { zoom, panX, panY };

  useEffect(() => { redraw(); }, [redraw]);

  const handlePredict = useCallback(async () => {
    if (!file) return;
    setLoading(true);
    setError("");
    try {
      const result = await predictImage(file, confThreshold);
      setDetections(result.detections);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [file, confThreshold]);

  const previewUrl = useMemo(() => file ? URL.createObjectURL(file) : null, [file]);
  useEffect(() => { return () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }; }, [previewUrl]);

  const visibleCount = useMemo(
    () => detections.filter((d) => d.confidence >= confThreshold && classFilter.has(d.class_name)).length,
    [detections, confThreshold, classFilter],
  );

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <label className="flex items-center gap-2 px-4 py-2 border-2 border-dashed rounded-lg text-sm cursor-pointer" style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
          <ImageIcon size={16} />
          Choose Image
          <input type="file" accept="image/*" className="hidden" onChange={(e) => { setFile(e.target.files?.[0] || null); setDetections([]); reset(); }} />
        </label>
        {file && (
          <button onClick={handlePredict} disabled={loading} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm disabled:opacity-40">
            {loading ? "Predicting..." : "Predict"}
          </button>
        )}
      </div>
      {error && <p className="text-sm mb-3" style={{ color: "var(--danger)" }}>{error}</p>}
      {!previewUrl && !error && (
        <div className="border-2 border-dashed rounded-xl p-12 text-center" style={{ borderColor: "var(--border-subtle)" }}>
          <ImageIcon size={48} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <p className="text-sm mb-1" style={{ color: "var(--text-muted)" }}>Upload an image to see predictions</p>
          <p className="text-xs flex items-center justify-center gap-3" style={{ color: "var(--text-muted)" }}>
            <span className="flex items-center gap-1"><ZoomIn size={12} /> Scroll to zoom</span>
            <span className="flex items-center gap-1"><Move size={12} /> Drag to pan</span>
          </p>
        </div>
      )}
      {previewUrl && (
        <div className="relative inline-block">
          <img ref={imgRef} src={previewUrl} alt="preview" className="hidden" onLoad={redraw} />
          <canvas
            ref={canvasRef}
            className="max-w-full rounded-lg"
            style={{ cursor: zoom > 1 ? "grab" : "default", border: "1px solid var(--border-subtle)" }}
          />
          <ZoomIndicator zoom={zoom} onReset={reset} />
          {loading && (
            <div className="absolute inset-0 bg-black/40 rounded-lg flex flex-col items-center justify-center">
              <Loader2 size={32} className="text-white animate-spin mb-2" />
              <p className="text-white text-sm font-medium">Running inference...</p>
            </div>
          )}
        </div>
      )}
      {detections.length > 0 && (
        <p className="text-sm mt-2" style={{ color: "var(--text-muted)" }}>
          {visibleCount} detections shown &middot; Scroll to zoom, drag to pan, double-click to reset
        </p>
      )}
    </div>
  );
}
