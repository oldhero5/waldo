import type { JobStats } from "../api";

export default function StatsPanel({ stats }: { stats: JobStats }) {
  return (
    <div className="bg-gray-50 rounded-lg p-4 space-y-3 text-sm">
      <h3 className="font-semibold text-gray-900">Dataset Stats</h3>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <span className="text-gray-500">Annotations</span>
          <p className="font-medium">{stats.total_annotations}</p>
        </div>
        <div>
          <span className="text-gray-500">Frames</span>
          <p className="font-medium">
            {stats.annotated_frames}/{stats.total_frames}
          </p>
        </div>
        <div>
          <span className="text-gray-500">Density</span>
          <p className="font-medium">{stats.annotation_density}/frame</p>
        </div>
        <div>
          <span className="text-gray-500">Empty</span>
          <p className="font-medium">{stats.empty_frames}</p>
        </div>
      </div>

      {stats.by_class.length > 0 && (
        <div>
          <span className="text-gray-500">Classes</span>
          {stats.by_class.map((c) => (
            <div key={c.name} className="flex justify-between">
              <span>{c.name}</span>
              <span className="font-medium">{c.count}</span>
            </div>
          ))}
        </div>
      )}

      <div>
        <span className="text-gray-500">Review Status</span>
        <div className="flex gap-3 mt-1">
          <span className="text-green-600">
            {stats.by_status.accepted || 0} accepted
          </span>
          <span className="text-red-600">
            {stats.by_status.rejected || 0} rejected
          </span>
          <span className="text-gray-600">
            {stats.by_status.pending || 0} pending
          </span>
        </div>
      </div>
    </div>
  );
}
