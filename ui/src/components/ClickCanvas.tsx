import { useRef } from "react";

interface Point {
  x: number;
  y: number;
  label: number; // 1=positive, 0=negative
}

interface Props {
  imageUrl: string;
  width: number;
  height: number;
  points: Point[];
  onAddPoint: (point: Point) => void;
}

export default function ClickCanvas({
  imageUrl,
  width,
  height,
  points,
  onAddPoint,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  const handleClick = (e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = ((e.clientX - rect.left) / rect.width) * width;
    const y = ((e.clientY - rect.top) / rect.height) * height;
    const label = e.button === 2 ? 0 : 1; // right-click = negative
    onAddPoint({ x, y, label });
  };

  return (
    <div
      ref={containerRef}
      className="relative inline-block cursor-crosshair"
      onClick={handleClick}
      onContextMenu={(e) => {
        e.preventDefault();
        handleClick(e);
      }}
    >
      <img src={imageUrl} width={width} height={height} className="block" />
      <svg
        className="absolute inset-0"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
      >
        {points.map((p, i) => (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r={6}
            fill={p.label === 1 ? "#22c55e" : "#ef4444"}
            stroke="white"
            strokeWidth={2}
          />
        ))}
      </svg>
    </div>
  );
}
