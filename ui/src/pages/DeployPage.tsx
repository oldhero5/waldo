/**
 * Deploy page — endpoints, testing, model registry, and monitoring.
 *
 * The five tabs each live in pages/deploy/*.tsx. The previous "API" tab was
 * folded into the Reference section of EndpointsTab to remove duplication.
 */
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Server } from "lucide-react";

import { getServeStatus } from "../api";
import { ServerStatusHeader } from "./deploy/ServerStatusHeader";
import { TabBar } from "./deploy/TabBar";
import { TABS, type TabKey } from "./deploy/shared";

// Lazy-load each tab so a user opening Endpoints doesn't pay for the
// 700-line CompareDemo or the inference monitoring chart bundle.
const EndpointsTab = lazy(() => import("./deploy/EndpointsTab").then((m) => ({ default: m.EndpointsTab })));
const TestTab = lazy(() => import("./deploy/TestTab").then((m) => ({ default: m.TestTab })));
const ModelsTab = lazy(() => import("./deploy/ModelsTab").then((m) => ({ default: m.ModelsTab })));
const MonitorTab = lazy(() => import("./deploy/MonitorTab").then((m) => ({ default: m.MonitorTab })));

export default function DeployPage() {
  const { tab } = useParams<{ tab?: string }>();
  const navigate = useNavigate();
  const [justActivated, setJustActivated] = useState(false);

  // Resolve tab from URL. The legacy "api" key now redirects to endpoints.
  const rawTab = tab === "api" ? "endpoints" : tab;
  const activeTab: TabKey = TABS.some((t) => t.key === rawTab) ? (rawTab as TabKey) : "endpoints";

  const { data: status } = useQuery({
    queryKey: ["serve-status"],
    queryFn: getServeStatus,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

  const handleTabChange = (t: TabKey) => {
    navigate(`/deploy/${t}`, { replace: true });
  };

  return (
    <div className="max-w-5xl mx-auto mt-6 px-4 sm:px-6 pb-16">
      <div className="mb-5">
        <p className="eyebrow" style={{ marginBottom: 4 }}>Production</p>
        <h1
          className="text-2xl font-bold flex items-center gap-2"
          style={{ color: "var(--text-primary)" }}
        >
          <Server size={24} />
          Deploy
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Activate models, test inference, monitor performance, and integrate via API.
        </p>
      </div>

      <ServerStatusHeader status={status} />

      {justActivated && (
        <div
          className="flex items-center justify-between rounded-lg p-4 mb-5"
          style={{ backgroundColor: "var(--success-soft)", border: "1px solid var(--success)" }}
        >
          <span className="text-sm font-medium" style={{ color: "var(--success)" }}>
            Model activated and ready for inference.
          </span>
          <button
            onClick={() => { handleTabChange("test"); setJustActivated(false); }}
            className="px-4 py-1.5 text-white rounded-lg text-sm font-medium"
            style={{ backgroundColor: "var(--success)" }}
          >
            Try it now
          </button>
        </div>
      )}

      <TabBar activeTab={activeTab} onTabChange={handleTabChange} />

      <Suspense fallback={<div className="text-center py-8 text-sm" style={{ color: "var(--text-muted)" }}>Loading…</div>}>
        {activeTab === "endpoints" && <EndpointsTab />}
        {activeTab === "test" && <TestTab status={status} />}
        {activeTab === "models" && <ModelsTab onActivated={() => setJustActivated(true)} />}
        {activeTab === "monitor" && <MonitorTab />}
      </Suspense>
    </div>
  );
}
