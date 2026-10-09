export function ZoomIndicator({ zoom, onReset }: { zoom: number; onReset: () => void }) {
  if (zoom <= 1.01) return null;
  return (
    <div className="absolute top-2 right-2 flex items-center gap-2 bg-black/60 text-white text-xs px-2 py-1 rounded">
      <span>{zoom.toFixed(1)}x</span>
      <button onClick={onReset} className="hover:text-gray-300">Reset</button>
    </div>
  );
}
