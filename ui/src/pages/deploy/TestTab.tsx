import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { listModels, type ServeStatus } from "../../api";
import { classColor } from "./shared";
import { ImageDemo } from "./ImageDemo";
import { VideoDemo } from "./VideoDemo";
import { CompareDemo } from "./CompareDemo";

export function TestTab({ status }: { status: ServeStatus | undefined }) {
  const [mode, setMode] = useState<"image" | "video" | "compare">("image");
  const [confThreshold, setConfThreshold] = useState(0.25);
  const [checkedClasses, setCheckedClasses] = useState<Set<string>>(new Set());
  const [classSearch, setClassSearch] = useState("");
  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    refetchIntervalInBackground: false,
  });

  const classKey = status?.class_names?.join(",") || "";
  useEffect(() => {
    if (status?.class_names && checkedClasses.size === 0) {
      setCheckedClasses(new Set(status.class_names));
    }
  }, [classKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleClass = (cls: string) => {
    setCheckedClasses((prev) => {
      const next = new Set(prev);
      if (next.has(cls)) next.delete(cls);
      else next.add(cls);
      return next;
    });
  };

  const allClasses = status?.class_names || [];
  const classFilterArr = useMemo(
    () => allClasses.length > 0 ? allClasses.filter((c) => checkedClasses.has(c)) : [],
    [allClasses, checkedClasses],
  );

  const filteredClasses = useMemo(() => {
    if (!classSearch) return allClasses;
    const q = classSearch.toLowerCase();
    return allClasses.filter((c) => c.toLowerCase().includes(q));
  }, [allClasses, classSearch]);

  const showSearch = allClasses.length > 12;

  return (
    <div>
      {status && !status.loaded && mode !== "compare" && (
        <div className="flex items-center gap-2 rounded-lg p-3 mb-4 text-sm" style={{ backgroundColor: "var(--warning-soft)", border: "1px solid var(--warning)", color: "var(--warning)" }}>
          <AlertTriangle size={16} className="shrink-0" />
          <span>
            No model loaded. Switch to the <strong>Models</strong> tab and activate a model first.
          </span>
        </div>
      )}

      <div className="flex items-center gap-4 mb-6 flex-wrap">
        <div className="flex p-1 rounded-lg" style={{ backgroundColor: "var(--bg-inset)" }}>
          {(["image", "video", "compare"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} className="px-4 py-1.5 text-sm rounded-md transition-all duration-150"
              style={{
                backgroundColor: mode === m ? "var(--bg-surface)" : "transparent",
                color: mode === m ? "var(--text-primary)" : "var(--text-muted)",
                fontWeight: mode === m ? 500 : 400,
                boxShadow: mode === m ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}>
              {m === "compare" ? "Compare" : m.charAt(0).toUpperCase() + m.slice(1)}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm" style={{ color: "var(--text-secondary)" }}>Confidence:</label>
          <input type="range" min={0} max={1} step={0.05} value={confThreshold} onChange={(e) => setConfThreshold(Number(e.target.value))} className="w-32" />
          <span className="text-sm font-mono w-12">{(confThreshold * 100).toFixed(0)}%</span>
        </div>
      </div>

      {mode !== "compare" && allClasses.length > 0 && (
        <div className="mb-6">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
              Classes <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>({checkedClasses.size}/{allClasses.length})</span>
            </p>
            {showSearch && (
              <input
                type="text"
                value={classSearch}
                onChange={(e) => setClassSearch(e.target.value)}
                placeholder="Search classes..."
                className="px-2 py-1 text-xs rounded border w-40"
                style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}
              />
            )}
          </div>
          <div className="flex flex-wrap gap-3 max-h-32 overflow-y-auto">
            {filteredClasses.map((cls) => (
              <label key={cls} className="flex items-center gap-1.5 text-sm cursor-pointer" title={cls}>
                <input
                  type="checkbox"
                  checked={checkedClasses.has(cls)}
                  onChange={() => toggleClass(cls)}
                  className="rounded"
                />
                <span className="inline-block w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: classColor(cls, allClasses) }} />
                <span className="truncate max-w-[120px]">{cls}</span>
              </label>
            ))}
            <button onClick={() => setCheckedClasses(new Set(allClasses))} className="text-xs hover:underline" style={{ color: "var(--accent)" }}>All</button>
            <button onClick={() => setCheckedClasses(new Set())} className="text-xs hover:underline" style={{ color: "var(--accent)" }}>None</button>
          </div>
        </div>
      )}

      {mode === "image" && <ImageDemo confThreshold={confThreshold} classFilter={checkedClasses} />}
      {mode === "video" && <VideoDemo confThreshold={confThreshold} classFilter={checkedClasses} classFilterArr={classFilterArr} modelId={status?.model_id || null} />}
      {mode === "compare" && <CompareDemo confThreshold={confThreshold} models={models} />}
    </div>
  );
}
