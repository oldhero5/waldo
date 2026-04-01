import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects, listProjectVideos, type ProjectOut } from "../api";
import PageLayout from "../components/PageLayout";
import { useState } from "react";

function ProjectCard({ project }: { project: ProjectOut }) {
  const [expanded, setExpanded] = useState(false);

  const { data: videos } = useQuery({
    queryKey: ["project-videos", project.id],
    queryFn: () => listProjectVideos(project.id),
    enabled: expanded,
  });

  return (
    <div className="border rounded-lg p-4">
      <div className="flex justify-between items-center">
        <div>
          <h3 className="font-semibold text-gray-900">{project.name}</h3>
          <p className="text-sm text-gray-500">
            {project.video_count} video{project.video_count !== 1 ? "s" : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="px-3 py-1.5 text-sm border rounded-lg hover:bg-gray-50"
          >
            {expanded ? "Collapse" : "Videos"}
          </button>
          <Link
            to={`/label/collection/${project.id}`}
            className="px-3 py-1.5 text-sm bg-gray-900 text-white rounded-lg hover:bg-gray-800"
          >
            Label All
          </Link>
        </div>
      </div>

      {expanded && videos && (
        <div className="mt-3 space-y-2">
          {videos.map((v) => (
            <div
              key={v.id}
              className="flex justify-between items-center bg-gray-50 rounded px-3 py-2 text-sm"
            >
              <span className="text-gray-700">{v.filename}</span>
              <div className="flex items-center gap-3 text-gray-500">
                {v.duration_s != null && <span>{v.duration_s.toFixed(1)}s</span>}
                {v.width != null && v.height != null && (
                  <span>
                    {v.width}x{v.height}
                  </span>
                )}
                <Link
                  to={`/label/${v.id}`}
                  className="text-blue-600 hover:underline"
                >
                  Label
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CollectionsPage() {
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });

  return (
    <PageLayout maxWidth="3xl" title="Collections" subtitle="Browse video collections. Label an entire collection at once.">
        {isLoading && <p className="text-gray-400">Loading...</p>}

        {projects && projects.length === 0 && (
          <p className="text-gray-400">
            No collections yet.{" "}
            <Link to="/upload" className="text-blue-600 hover:underline">
              Upload videos
            </Link>{" "}
            to get started.
          </p>
        )}

        <div className="space-y-3">
          {projects?.map((p) => (
            <ProjectCard key={p.id} project={p} />
          ))}
        </div>
    </PageLayout>
  );
}
