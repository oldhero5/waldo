import { classColor, hslToHex } from "../lib/annotationColors";

interface Props {
  polygon: number[];
  color?: string;
  status?: string;
  className?: string;
  label?: string;
  highlight?: boolean;
}

export default function AnnotationOverlay({
  polygon,
  color,
  status = "pending",
  className,
  label,
  highlight = false,
}: Props) {
  if (!polygon || polygon.length < 6) return null;

  const points = [];
  for (let i = 0; i < polygon.length; i += 2) {
    points.push(`${polygon[i]},${polygon[i + 1]}`);
  }

  const baseColor = color
    ? color
    : className
      ? hslToHex(classColor(className))
      : status === "accepted"
        ? "#22c55e"
        : status === "rejected"
          ? "#ef4444"
          : "#3b82f6";

  // Find top-most point for label
  let minY = 1;
  let minYx = 0;
  for (let i = 0; i < polygon.length; i += 2) {
    if (polygon[i + 1] < minY) {
      minY = polygon[i + 1];
      minYx = polygon[i];
    }
  }

  return (
    <g>
      <polygon
        points={points.join(" ")}
        fill={`${baseColor}${highlight ? "55" : "33"}`}
        stroke={baseColor}
        strokeWidth={highlight ? 0.004 : 0.003}
      />
      {label && (
        <>
          <rect
            x={minYx - 0.002}
            y={minY - 0.028}
            width={Math.max(0.04, label.length * 0.008)}
            height={0.025}
            rx={0.003}
            fill={baseColor}
            opacity={0.85}
          />
          <text
            x={minYx + 0.003}
            y={minY - 0.008}
            fontSize={0.016}
            fill="white"
            fontFamily="system-ui, sans-serif"
            fontWeight="600"
          >
            {label}
          </text>
        </>
      )}
    </g>
  );
}
