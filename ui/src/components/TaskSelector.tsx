const TASKS = [
  { value: "segment", label: "Segmentation", description: "Pixel-level masks for each object" },
  { value: "detect", label: "Detection", description: "Bounding boxes around objects" },
  { value: "classify", label: "Classification", description: "Classify entire frames" },
  { value: "obb", label: "Oriented BBox", description: "Rotated bounding boxes for angled objects" },
  { value: "pose", label: "Pose", description: "Skeleton keypoints on detected objects" },
];

export default function TaskSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const selected = TASKS.find((t) => t.value === value);
  return (
    <div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="border rounded px-3 py-2 text-sm bg-white"
      >
        {TASKS.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </select>
      {selected && (
        <p className="text-xs text-gray-400 mt-1">{selected.description}</p>
      )}
    </div>
  );
}
