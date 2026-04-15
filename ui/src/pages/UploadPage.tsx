import { useCallback, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { uploadVideo, uploadVideoBatch } from "../api";
import { Upload, CheckCircle } from "lucide-react";

interface UploadResult {
  videoId: string;
  filename: string;
}

export default function UploadPage() {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [searchParams] = useSearchParams();
  const [projectName, setProjectName] = useState(searchParams.get("collection") || "");
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null);
  const [completed, setCompleted] = useState<UploadResult | null>(null);
  const navigate = useNavigate();

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setUploading(true);
      setError("");
      setUploadProgress(null);
      setCompleted(null);

      try {
        if (files.length === 1) {
          const result = await uploadVideo(files[0], projectName || "default");
          setCompleted({ videoId: result.video_id, filename: files[0].name });
        } else {
          setUploadProgress({ done: 0, total: files.length });
          await uploadVideoBatch(files, projectName || "default");
          setUploadProgress({ done: files.length, total: files.length });
          navigate(`/collections`);
        }
      } catch (e: any) {
        setError(e.message || "Upload failed");
      } finally {
        setUploading(false);
        setUploadProgress(null);
      }
    },
    [navigate, projectName]
  );

  return (
    <div className="min-h-screen">

      <div className="max-w-xl mx-auto mt-16 px-4">
        <p className="eyebrow" style={{ marginBottom: 4 }}>Data ingestion</p>
        <h1 className="text-3xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>Upload Video</h1>
        <p className="mb-8" style={{ color: "var(--text-secondary)" }}>
          Upload one or more videos to extract frames and start labeling.
        </p>

        <div className="mb-4">
          <span className="eyebrow block mb-1">Collection Name</span>
          <input
            type="text"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="e.g. parking-lot-cam"
            className="rounded-lg px-4 py-2 w-full outline-none"
            style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
            onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
            onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border-default)")}
          />
          <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Groups videos together. Defaults to "default" if left blank.
          </p>
        </div>

        <div
          className="rounded-xl p-12 text-center transition"
          style={{
            border: dragOver ? "2px dashed var(--accent)" : completed ? "2px dashed var(--success)" : "2px dashed var(--border-default)",
            backgroundColor: dragOver ? "var(--accent-soft)" : completed ? "var(--success-soft)" : "transparent",
          }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) handleFiles(files);
          }}
        >
          {completed ? (
            <div>
              <CheckCircle size={40} className="mx-auto mb-3" style={{ color: "var(--success)" }} />
              <p className="font-medium mb-1" style={{ color: "var(--success)" }}>Upload successful</p>
              <p className="text-sm mb-4" style={{ color: "var(--text-muted)" }}>{completed.filename}</p>
              <div className="flex gap-3 justify-center">
                <button
                  onClick={() => navigate(`/label/${completed.videoId}`)}
                  className="px-6 py-2 text-white rounded-lg font-medium"
                  style={{ backgroundColor: "var(--success)" }}
                >
                  Continue to Label
                </button>
                <button
                  onClick={() => setCompleted(null)}
                  className="px-4 py-2 rounded-lg text-sm"
                  style={{ border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}
                >
                  Upload Another
                </button>
              </div>
            </div>
          ) : uploading ? (
            <div>
              <Upload size={32} className="mx-auto mb-3 animate-pulse" style={{ color: "var(--text-muted)" }} />
              <p style={{ color: "var(--text-secondary)" }}>Uploading...</p>
              {uploadProgress && (
                <p style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
                  {uploadProgress.done}/{uploadProgress.total} files
                </p>
              )}
            </div>
          ) : (
            <>
              <Upload size={32} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
              <p className="mb-4" style={{ color: "var(--text-secondary)" }}>
                Drag and drop video files here, or click to browse
              </p>
              <input
                type="file"
                accept="video/*"
                multiple
                className="hidden"
                id="file-input"
                onChange={(e) => {
                  const files = Array.from(e.target.files || []);
                  if (files.length > 0) handleFiles(files);
                }}
              />
              <label
                htmlFor="file-input"
                className="inline-block px-6 py-2 text-white rounded-lg cursor-pointer"
                style={{ backgroundColor: "var(--accent)" }}
              >
                Choose Files
              </label>
              <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 16 }}>
                Supports MP4, AVI, MOV, MKV &middot; Multiple files for batch upload
              </p>
            </>
          )}
        </div>

        {error && (
          <p className="mt-4 text-sm" style={{ color: "var(--danger)" }}>{error}</p>
        )}
      </div>
    </div>
  );
}
