import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { listJobs } from "../api";
import Nav from "../components/Nav";
import { ArrowRight } from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-700",
  extracting: "bg-blue-100 text-blue-700",
  labeling: "bg-yellow-100 text-yellow-700",
  converting: "bg-purple-100 text-purple-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

const FILTERS = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

const RUNNING_STATUSES = new Set(["pending", "extracting", "labeling", "converting"]);

const PAGE_SIZE = 25;

export default function JobsPage() {
  const [filter, setFilter] = useState("all");
  const [showCount, setShowCount] = useState(PAGE_SIZE);

  const { data: jobs, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => listJobs(),
    refetchInterval: 5000,
  });

  const filteredJobs = jobs
    ? jobs
        .filter((job) => {
          if (filter === "all") return true;
          if (filter === "running") return RUNNING_STATUSES.has(job.status);
          return job.status === filter;
        })
        .reverse()
    : [];

  const visibleJobs = filteredJobs.slice(0, showCount);
  const hasMore = filteredJobs.length > showCount;

  return (
    <div className="min-h-screen">
      <Nav />
      <div className="max-w-4xl mx-auto mt-8 px-4">
        <div className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Jobs</h1>
          {jobs && (
            <span className="text-sm text-gray-400">{jobs.length} total</span>
          )}
        </div>

        {/* Filter pills */}
        <div className="flex gap-1 mb-5">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => { setFilter(f.value); setShowCount(PAGE_SIZE); }}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                filter === f.value
                  ? "bg-gray-900 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {f.label}
              {jobs && (
                <span className="ml-1.5 opacity-60">
                  {f.value === "all"
                    ? jobs.length
                    : f.value === "running"
                      ? jobs.filter((j) => RUNNING_STATUSES.has(j.status)).length
                      : jobs.filter((j) => j.status === f.value).length}
                </span>
              )}
            </button>
          ))}
        </div>

        {isLoading && <p className="text-gray-500">Loading...</p>}

        <div className="space-y-2">
          {visibleJobs.map((job) => {
            const isCompleted = job.status === "completed";
            return (
              <Link
                key={job.job_id}
                to={isCompleted ? `/review/${job.job_id}` : `/label/${job.video_id}`}
                className="block border rounded-lg p-4 hover:bg-gray-50 transition"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {job.text_prompt || "Exemplar"}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[job.status] || "bg-gray-100"}`}
                    >
                      {job.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-sm text-gray-500">
                    <span>
                      {job.processed_frames}/{job.total_frames} frames
                    </span>
                    <span className="flex items-center gap-1 text-xs text-gray-400">
                      <ArrowRight size={12} />
                      {isCompleted ? "Review" : "Continue"}
                    </span>
                  </div>
                </div>
                {!isCompleted && job.status !== "failed" && (
                  <div className="mt-2 w-full bg-gray-200 rounded-full h-1.5">
                    <div
                      className="bg-gray-900 h-1.5 rounded-full"
                      style={{ width: `${(job.progress || 0) * 100}%` }}
                    />
                  </div>
                )}
                {job.error_message && (
                  <p className="mt-1 text-red-600 text-xs">
                    {job.error_message}
                  </p>
                )}
              </Link>
            );
          })}

          {hasMore && (
            <button
              onClick={() => setShowCount((c) => c + PAGE_SIZE)}
              className="w-full py-3 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-50 rounded-lg border border-dashed"
            >
              Show more ({filteredJobs.length - showCount} remaining)
            </button>
          )}

          {!isLoading && filteredJobs.length === 0 && (
            <p className="text-gray-500 text-center py-8">
              {filter === "all" ? (
                <>
                  No jobs yet.{" "}
                  <Link to="/upload" className="text-blue-600 hover:underline">
                    Upload a video
                  </Link>{" "}
                  to get started.
                </>
              ) : (
                <>No {filter} jobs found.</>
              )}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
