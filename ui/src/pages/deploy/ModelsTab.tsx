import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Rocket, Search } from "lucide-react";
import {
  completeExperiment,
  createExperiment,
  listExperiments,
  listModels,
  promoteModel,
  type ModelOut,
} from "../../api";
import { ModelCard } from "./ModelCard";

export function ModelsTab({ onActivated }: { onActivated: () => void }) {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");
  const [showExperiment, setShowExperiment] = useState(false);
  const [expChampion, setExpChampion] = useState("");
  const [expChallenger, setExpChallenger] = useState("");
  const [expSplit, setExpSplit] = useState(20);

  const { data: models, isLoading: modelsLoading } = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    refetchIntervalInBackground: false,
  });

  const { data: experiments } = useQuery({
    queryKey: ["experiments"],
    queryFn: listExperiments,
    refetchIntervalInBackground: false,
  });

  const handleCreateExperiment = async () => {
    if (!expChampion || !expChallenger) return;
    try {
      await createExperiment({
        name: `${models?.find((m) => m.id === expChampion)?.name} vs ${models?.find((m) => m.id === expChallenger)?.name}`,
        champion_model_id: expChampion,
        challenger_model_id: expChallenger,
        split_pct: expSplit,
      });
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
      setShowExperiment(false);
    } catch (e: any) {
      console.error(e.message);
    }
  };

  const handleCompleteExperiment = async (expId: string, winner: string) => {
    await completeExperiment(expId, winner);
    queryClient.invalidateQueries({ queryKey: ["experiments"] });
    queryClient.invalidateQueries({ queryKey: ["models"] });
    queryClient.invalidateQueries({ queryKey: ["serve-status"] });
    if (winner === "challenger") onActivated();
  };

  const activateMut = useMutation({
    mutationFn: (modelId: string) => promoteModel(modelId, "champion"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["serve-status"] });
      onActivated();
    },
  });

  const { sortedGroups, bestModelId, filteredModels } = useMemo(() => {
    const filtered = models?.filter((m) => {
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        m.name.toLowerCase().includes(q) ||
        m.model_variant.toLowerCase().includes(q) ||
        m.task_type.toLowerCase().includes(q)
      );
    });

    const grouped = new Map<string, ModelOut[]>();
    filtered?.forEach((m) => {
      const group = grouped.get(m.task_type) || [];
      group.push(m);
      grouped.set(m.task_type, group);
    });

    const sorted = Array.from(grouped.entries()).sort(([, a], [, b]) => {
      const aHasActive = a.some((m) => m.is_active);
      const bHasActive = b.some((m) => m.is_active);
      if (aHasActive && !bHasActive) return -1;
      if (!aHasActive && bHasActive) return 1;
      return 0;
    });

    for (const [, group] of sorted) {
      group.sort((a, b) => (b.is_active ? 1 : 0) - (a.is_active ? 1 : 0));
    }

    const best = filtered?.reduce<{ id: string; score: number } | null>((acc, m) => {
      const mAP = m.metrics?.["metrics/mAP50(B)"] ?? m.metrics?.["metrics/mAP50(M)"] ?? 0;
      if (!acc || mAP > acc.score) return { id: m.id, score: mAP };
      return acc;
    }, null)?.id;

    return { sortedGroups: sorted, bestModelId: best, filteredModels: filtered };
  }, [models, searchQuery]);

  const runningExps = experiments?.filter((e) => e.status === "running") || [];

  return (
    <div>
      {runningExps.length > 0 && (
        <div className="mb-6">
          {runningExps.map((exp) => (
            <div key={exp.id} className="surface p-4 mb-3" style={{ border: "1px solid var(--warning)" }}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-yellow-500 animate-pulse" />
                  <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                    Blue-Green Experiment Running
                  </span>
                </div>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full" style={{ backgroundColor: "var(--warning-soft)", color: "var(--warning)" }}>
                  {exp.split_pct}% challenger traffic
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div className="rounded-lg p-3" style={{ backgroundColor: "var(--success-soft)" }}>
                  <span className="text-[10px] uppercase tracking-wide block mb-0.5" style={{ color: "var(--success)" }}>Champion</span>
                  <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{exp.champion_name || "Unknown"}</span>
                </div>
                <div className="rounded-lg p-3" style={{ backgroundColor: "var(--warning-soft)" }}>
                  <span className="text-[10px] uppercase tracking-wide block mb-0.5" style={{ color: "var(--warning)" }}>Challenger</span>
                  <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{exp.challenger_name || "Unknown"}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => handleCompleteExperiment(exp.id, "champion")}
                  className="px-3 py-1.5 text-xs rounded-lg font-medium" style={{ backgroundColor: "var(--success-soft)", color: "var(--success)" }}>
                  Keep Champion
                </button>
                <button onClick={() => handleCompleteExperiment(exp.id, "challenger")}
                  className="px-3 py-1.5 text-xs rounded-lg font-medium text-white" style={{ backgroundColor: "var(--warning)" }}>
                  Promote Challenger
                </button>
                <span className="text-[10px] ml-auto" style={{ color: "var(--text-muted)" }}>
                  Started {exp.started_at ? new Date(exp.started_at).toLocaleDateString() : ""}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between mb-4">
        <h2 className="font-semibold" style={{ color: "var(--text-primary)" }}>Model Registry</h2>
        <div className="flex items-center gap-2">
          {models && models.length >= 2 && (
            <button onClick={() => setShowExperiment(!showExperiment)}
              className="px-3 py-1.5 text-xs rounded-lg font-medium"
              style={{ backgroundColor: "var(--warning-soft)", color: "var(--warning)" }}>
              {showExperiment ? "Cancel" : "Start Experiment"}
            </button>
          )}
          {models && models.length > 5 && (
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter models..."
                className="pl-8 pr-3 py-1.5 border rounded-lg text-sm w-48"
                style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}
              />
            </div>
          )}
        </div>
      </div>

      {showExperiment && (
        <div className="surface p-5 mb-5">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>New Blue-Green Experiment</h3>
          <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
            Split traffic between two models to compare real-world performance before switching.
          </p>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div>
              <label className="text-[10px] uppercase tracking-wide mb-1 block" style={{ color: "var(--text-muted)" }}>Champion (current)</label>
              <select value={expChampion} onChange={(e) => setExpChampion(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border" style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}>
                <option value="">Select...</option>
                {models?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wide mb-1 block" style={{ color: "var(--text-muted)" }}>Challenger (new)</label>
              <select value={expChallenger} onChange={(e) => setExpChallenger(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border" style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}>
                <option value="">Select...</option>
                {models?.filter((m) => m.id !== expChampion).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wide mb-1 block" style={{ color: "var(--text-muted)" }}>Challenger Traffic %</label>
              <div className="flex items-center gap-2">
                <input type="range" min={5} max={50} step={5} value={expSplit} onChange={(e) => setExpSplit(Number(e.target.value))} className="flex-1" />
                <span className="text-sm font-mono w-10">{expSplit}%</span>
              </div>
            </div>
          </div>
          <button onClick={handleCreateExperiment} disabled={!expChampion || !expChallenger}
            className="px-4 py-2 text-white rounded-lg text-sm disabled:opacity-40" style={{ backgroundColor: "var(--warning)" }}>
            Start Experiment
          </button>
        </div>
      )}

      {modelsLoading ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>Loading models...</p>
      ) : filteredModels && filteredModels.length > 0 ? (
        <div className="space-y-6">
          {sortedGroups.map(([taskType, group]) => (
            <div key={taskType}>
              <h3 className="text-sm font-medium uppercase tracking-wide mb-2" style={{ color: "var(--text-muted)" }}>
                {taskType} ({group.length})
              </h3>
              <div className="space-y-3">
                {group.map((m) => (
                  <ModelCard key={m.id} model={m} onActivate={(id) => activateMut.mutate(id)} isBest={m.id === bestModelId} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : searchQuery ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>No models matching "{searchQuery}".</p>
      ) : (
        <div className="text-center py-12">
          <Rocket size={32} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>No models yet.</p>
          <Link to="/experiments" className="text-sm hover:underline mt-1 inline-block" style={{ color: "var(--accent)" }}>
            Train a model first
          </Link>
        </div>
      )}
    </div>
  );
}
