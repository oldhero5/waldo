import { useCallback, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { uploadVideo, uploadVideoBatch } from "../api";
import Nav from "../components/Nav";
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
      <Nav />
      <div className="max-w-xl mx-auto mt-16 px-4">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Upload Video</h1>
        <p className="text-gray-500 mb-8">
          Upload one or more videos to extract frames and start labeling.
        </p>

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Collection Name
          </label>
          <input
            type="text"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="e.g. parking-lot-cam"
            className="border rounded-lg px-4 py-2 w-full"
          />
          <p className="text-xs text-gray-400 mt-1">
            Groups videos together. Defaults to "default" if left blank.
          </p>
        </div>

        <div
          className={`border-2 border-dashed rounded-xl p-12 text-center transition ${
            dragOver
              ? "border-blue-500 bg-blue-50"
              : completed
                ? "border-green-300 bg-green-50"
                : "border-gray-300 hover:border-gray-400"
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
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
              <CheckCircle size={40} className="mx-auto mb-3 text-green-600" />
              <p className="text-green-700 font-medium mb-1">Upload successful</p>
              <p className="text-sm text-gray-500 mb-4">{completed.filename}</p>
              <div className="flex gap-3 justify-center">
                <button
                  onClick={() => navigate(`/label/${completed.videoId}`)}
                  className="px-6 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium"
                >
                  Continue to Label
                </button>
                <button
                  onClick={() => setCompleted(null)}
                  className="px-4 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50"
                >
                  Upload Another
                </button>
              </div>
            </div>
          ) : uploading ? (
            <div>
              <Upload size={32} className="mx-auto mb-3 text-gray-400 animate-pulse" />
              <p className="text-gray-600">Uploading...</p>
              {uploadProgress && (
                <p className="text-sm text-gray-400 mt-1">
                  {uploadProgress.done}/{uploadProgress.total} files
                </p>
              )}
            </div>
          ) : (
            <>
              <Upload size={32} className="mx-auto mb-3 text-gray-400" />
              <p className="text-gray-600 mb-4">
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
                className="inline-block px-6 py-2 bg-blue-600 text-white rounded-lg cursor-pointer hover:bg-blue-700"
              >
                Choose Files
              </label>
              <p className="text-xs text-gray-400 mt-4">
                Supports MP4, AVI, MOV, MKV &middot; Multiple files for batch upload
              </p>
            </>
          )}
        </div>

        {error && (
          <p className="mt-4 text-red-600 text-sm">{error}</p>
        )}
      </div>
    </div>
  );
}
